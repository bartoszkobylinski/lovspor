"""AR4a: prepare one publication transaction offline, and recover an interrupted one (#738).

:func:`prepare_publication` takes a sealed AR2 plan, the sealed AR3 quarantine
assessment it honours, one batch id, the FC8b rows and private artifacts, the
publication path (A or B) and the expected base commit, plus a local source
repository and a private work directory. In order, the first failure stopping:

1. **Bindings.** The plan carries the planner's seal and its own digest, and
   honours a quarantine assessment; that assessment carries the quarantine
   verifier's seal, from the same trust root, with exactly the digest the plan
   names. The batch is the plan's. Every batch document has its row, whose
   selection and binding digests are the planned ones, and no batch document
   is quarantined. Anything else halts: a stale plan or a changed binding
   never becomes a proposal.
2. **Base.** The source's ``main`` is exactly the expected base commit.
3. **Isolated clone** (:mod:`lovspor.forarbeider.publication_clone`), locked
   with the B1b ``flock`` lock. A pending journal refuses
   (``checkout_pending_change``): recovery settles it first. A prepared
   journal of exactly this transaction is ``unchanged``.
4. **Re-evaluation and rendering** (:mod:`lovspor.forarbeider.publication_proposal`).
   Document-local failures are returned as AR3 stage failures and nothing is
   written (``refused_local``); one global failure halts, whatever local ones
   there are (``halted``): a global failure is never downgraded.
5. **The proposal** over the base tree, then the journaled write
   (:mod:`lovspor.forarbeider.publication_stage`) and the read-back. A
   read-back that finds an unexpected change, other bytes or a moved ``HEAD``
   rolls the proposal back and halts: no partial proposal is ever valid.

A halt carries a :class:`~lovspor.forarbeider.publication_models.PreparationHalt`
for the AR5 stop handling. No stop record is written here.

:func:`recover_preparation` settles what a crash left. It reconstructs a
proposal only when every write had landed, the caller expects exactly the
journaled transaction and the read-back proves it; otherwise it rolls back.
It never goes forward from ``rolling_back``, never overwrites a file edited by
hand, and never publishes: nothing in this package can commit or push.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal, NamedTuple

from lovspor.forarbeider import publication_stage
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_clone import (
    changed_paths,
    main_of,
    make_clone,
    require_clone,
    work_directory,
)
from lovspor.forarbeider.publication_models import (
    PreparationHalt,
    PreparationJournal,
    PublicationTransaction,
)
from lovspor.forarbeider.publication_proposal import (
    HaltError,
    Rendered,
    Target,
    evaluate,
    propose,
    stage_payloads,
)
from lovspor.forarbeider.release_attribution import StageFailure
from lovspor.forarbeider.release_contract_models import PlanEvidence
from lovspor.forarbeider.release_lock import recovered_lock, release_lock
from lovspor.forarbeider.release_plan import Row
from lovspor.forarbeider.release_plan_models import PlannedBatch, PlannedDocument, ReleasePlan
from lovspor.forarbeider.release_provenance import authentic
from lovspor.forarbeider.release_quarantine_models import GlobalFailure, QuarantineEvidence
from lovspor.forarbeider.version_release_models import digest_of

#: Read-back findings that halt and roll the proposal back.
_READ_BACK = frozenset({"head", "unexpected_change", "proposal_bytes"})
_RECONSTRUCTIBLE = frozenset({"manifest_written", "reading_back", "prepared"})


class PreparationRequest(NamedTuple):
    """One preparation's inputs; none of them is trusted until verified."""

    plan: PlanEvidence
    quarantine: QuarantineEvidence
    batch_id: str
    rows: tuple[Row, ...]
    artifacts: Mapping[str, bytes]
    path: Literal["A", "B"]
    base_commit: str


class PreparationOutcome(NamedTuple):
    """Prepared (or unchanged), refused for document-local failures, or halted."""

    status: Literal["prepared", "unchanged", "refused_local", "halted"]
    transaction: PublicationTransaction | None
    local: tuple[StageFailure, ...]
    halts: tuple[GlobalFailure, ...]
    clone: Path | None
    halt: PreparationHalt | None = None


class RecoveryOutcome(NamedTuple):
    status: Literal["nothing_pending", "rolled_back", "reconstructed"]
    transaction_id: str | None


class _Checked(NamedTuple):
    target: Target
    documents: tuple[tuple[PlannedDocument, Row], ...]


def clone_path(work: Path, request: PreparationRequest) -> Path:
    """Where the clone for ``request`` lives below ``work``: one per batch and path."""
    return work / f"{request.batch_id}-{request.path}"


# --- 1. bindings ---------------------------------------------------------------------------


def _plan(request: PreparationRequest) -> ReleasePlan:
    evidence = request.plan
    plan = evidence.plan
    sealed = evidence.status == "verified" and authentic(evidence, "batch_plan")
    if plan is None or not sealed or evidence.plan_sha256 != digest_of(plan):
        raise HaltError("plan_unverified", "trust")
    if plan.basis.quarantine_sha256 is None:
        raise HaltError("plan_quarantine_unassessed", "unknown")
    return plan


def _require_quarantine(request: PreparationRequest, plan: ReleasePlan) -> None:
    found = request.quarantine
    sealed = found.status == "verified" and authentic(found, "quarantine_assessment")
    same = (
        found.quarantine is not None
        and found.quarantine_sha256 == digest_of(found.quarantine)
        and found.quarantine_sha256 == plan.basis.quarantine_sha256
        and found.trust_root_sha256 == request.plan.trust_root_sha256
    )
    if not (sealed and same):
        raise HaltError("quarantine_mismatch", "trust")


def _batch(plan: ReleasePlan, batch_id: str) -> PlannedBatch:
    found = next((item for item in plan.batches if item.batch_id == batch_id), None)
    if found is None:
        raise HaltError("batch_not_in_plan", "unknown")
    return found


def _document(planned: PlannedDocument, rows: Mapping[str, Row], quarantined: set[str]) -> Row:
    document_id = planned.document_id
    row = rows.get(document_id)
    if row is None:
        raise HaltError("row_missing", "unknown", document_id)
    if digest_of(row.selection) != planned.selection_sha256:
        raise HaltError("selection_mismatch", "unknown", document_id)
    if digest_of(row.binding) != planned.binding_sha256:
        raise HaltError("binding_mismatch", "unknown", document_id)
    if document_id in quarantined:
        raise HaltError("quarantined_document_in_batch", "unknown", document_id)
    return row


def _checked(request: PreparationRequest) -> _Checked:
    plan = _plan(request)
    _require_quarantine(request, plan)
    batch = _batch(plan, request.batch_id)
    found = request.quarantine.quarantine
    quarantined = {item.document_id for item in found.documents} if found else set()
    rows = {row.selection.document_id: row for row in request.rows}
    by_id = {item.document_id: item for item in plan.documents}
    documents = tuple(
        (by_id[key], _document(by_id[key], rows, quarantined)) for key in batch.document_ids
    )
    return _Checked(Target(plan, batch, request.path, request.base_commit), documents)


# --- outcomes ------------------------------------------------------------------------------


def _halted(
    request: PreparationRequest,
    halts: tuple[GlobalFailure, ...],
    clone: Path | None = None,
    local: tuple[StageFailure, ...] = (),
) -> PreparationOutcome:
    ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "", item.code)))
    plan = request.plan.plan
    record = PreparationHalt(
        plan_id=plan.plan_id if plan else None,
        batch_id=request.batch_id,
        publication_path=request.path,
        base_commit=request.base_commit,
        halts=tuple(sorted({item.code for item in ordered})),
        documents=tuple(sorted({item.document_id for item in ordered if item.document_id})),
    )
    return PreparationOutcome("halted", None, local, ordered, clone, record)


# --- 2-5. base, clone, evaluation, proposal ----------------------------------------------


def _clone(request: PreparationRequest, source: Path, work: Path) -> Path:
    if main_of(source) != request.base_commit:
        raise HaltError("base_commit_mismatch", "repository")
    clone = clone_path(work_directory(work, source), request)
    if clone.exists():
        _require_reusable(clone, request.base_commit)
    else:
        make_clone(source, clone, request.base_commit)
    return clone


def _require_reusable(clone: Path, base_commit: str) -> None:
    """An existing clone is reused only at this base; one journaled at another is pending.

    Raises:
        ForarbeiderRefusedError: ``checkout_pending_change`` or ``checkout_unverifiable``.
    """
    journal = publication_stage.pending_journal(clone)
    if journal is not None and journal.transaction.content.publication.base_commit != base_commit:
        raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
    require_clone(clone, base_commit)


def prepare_publication(
    request: PreparationRequest, source: Path, work: Path
) -> PreparationOutcome:
    """Prepare the exact proposal of one batch in an isolated clone; never publish it.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked`` while another preparation holds
            the clone; ``checkout_pending_change`` for a journal recovery must
            settle first; a rollback that cannot complete (the journal stays).
    """
    try:
        checked = _checked(request)
        clone = _clone(request, source, work)
    except HaltError as found:
        return _halted(request, (found.failure,))
    with release_lock(clone):
        return _locked(request, checked, clone)


def _locked(request: PreparationRequest, checked: _Checked, clone: Path) -> PreparationOutcome:
    journal = publication_stage.pending_journal(clone)
    if journal is not None and journal.state != "prepared":
        raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
    if journal is None:
        publication_stage.clear_orphan_staging(clone)
    policy = checked.target.plan.basis.policy
    evaluation = evaluate(checked.documents, request.artifacts, policy)
    if evaluation.halts:
        return _halted(request, evaluation.halts, clone, evaluation.local)
    if evaluation.local:
        return PreparationOutcome("refused_local", None, evaluation.local, (), clone)
    try:
        return _proposed(checked.target, evaluation.rendered, clone, journal)
    except HaltError as found:
        return _halted(request, (found.failure,), clone)


def _proposed(
    target: Target, rendered: tuple[Rendered, ...], clone: Path, journal: PreparationJournal | None
) -> PreparationOutcome:
    try:
        base = publication_stage.base_files(clone, journal)
    except ForarbeiderRefusedError as error:
        raise HaltError("base_tree_inconsistent", "repository") from error
    transaction, payloads = propose(target, rendered, base)
    if journal is not None:
        return _unchanged(clone, journal, transaction)
    if changed_paths(clone):
        raise HaltError("unexpected_change", "repository")
    staged = dict(stage_payloads(payloads, base))
    return _written(clone, transaction, staged)


def _unchanged(
    clone: Path, journal: PreparationJournal, transaction: PublicationTransaction
) -> PreparationOutcome:
    if journal.transaction != transaction:
        raise ForarbeiderRefusedError(RefusalReason.CHECKOUT_PENDING_CHANGE, "journal")
    publication_stage.verify_proposal(clone, transaction)
    return PreparationOutcome("unchanged", transaction, (), (), clone)


def _written(
    clone: Path, transaction: PublicationTransaction, staged: Mapping[str, bytes]
) -> PreparationOutcome:
    journal = publication_stage.begin(clone, transaction, staged)
    try:
        journal = publication_stage.write_all(clone, journal)
        publication_stage.read_back(clone, journal)
    except ForarbeiderRefusedError as error:
        detail = error.detail if error.detail in _READ_BACK else "write_refused"
        return _abandoned(clone, detail)
    except OSError:
        return _abandoned(clone, "write_failed")
    return PreparationOutcome("prepared", transaction, (), (), clone)


def _abandoned(clone: Path, detail: str) -> PreparationOutcome:
    """Roll the proposal back, then halt: no partial proposal is ever valid."""
    journal = publication_stage.pending_journal(clone)
    if journal is not None:
        publication_stage.roll_back(clone, journal)
    raise HaltError(detail, "repository")


# --- recovery ------------------------------------------------------------------------------


def recover_preparation(clone: Path, expected: PublicationTransaction | None) -> RecoveryOutcome:
    """Reconstruct and verify the exact proposal, or roll it back; never publish.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked`` while a live preparation holds
            the clone; ``corpus_tree_mismatch`` for a forged journal, a moved
            ``HEAD`` or a file edited by hand (nothing is touched, the journal stays).
    """
    with recovered_lock(clone):
        journal = publication_stage.pending_journal(clone)
        if journal is None:
            publication_stage.clear_orphan_staging(clone)
            return RecoveryOutcome("nothing_pending", None)
        if (
            require_head(clone, journal)
            and _matches(journal, expected)
            and _verified(clone, journal)
        ):
            publication_stage.advance(clone, journal, "prepared")
            return RecoveryOutcome("reconstructed", journal.transaction.transaction_id)
        publication_stage.roll_back(clone, journal)
        return RecoveryOutcome("rolled_back", journal.transaction.transaction_id)


def require_head(clone: Path, journal: PreparationJournal) -> bool:
    """``HEAD`` is still the journaled base: recovery never settles a clone someone committed in.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: head``.
    """
    try:
        require_clone(clone, journal.transaction.content.publication.base_commit)
    except ForarbeiderRefusedError as error:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "head") from error
    return True


def _matches(journal: PreparationJournal, expected: PublicationTransaction | None) -> bool:
    return journal.state in _RECONSTRUCTIBLE and expected == journal.transaction


def _verified(clone: Path, journal: PreparationJournal) -> bool:
    try:
        publication_stage.verify_proposal(clone, journal.transaction)
    except ForarbeiderRefusedError:
        return False
    return True
