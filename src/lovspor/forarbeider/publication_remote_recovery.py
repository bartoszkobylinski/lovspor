"""AR4b-2: recovery from the remote history (#738; §9.3a).

:func:`recover` decides what happened to a transaction from the **remote
``main``**, never from the local attempt records or an API answer alone, and
it never sends: it holds no transport call, no push and no execution.

1. The adapter is proved sandboxed (AR4b-1) and the remote history after the
   transaction's base is read, oldest first. ``main`` that does not descend
   from the base, a merge, an unreadable commit or a history too long to read
   halts: ``halted_history_unverifiable``, the stop latch is set.
2. Nothing after the base: ``not_published``. An attempt left at
   ``prepared_to_send`` or ``outcome_unknown`` is closed as ``refused``
   (provably nothing published), so a later attempt may run once every other
   check, the stop latch included, allows it.
3. The first commit after the base is classified by **integrity** and,
   separately, **authorization** (:mod:`~lovspor.forarbeider.publication_integrity`):
   - integrity verified: it is recorded as **published** (a receipt, and a
     ``published`` attempt record if the journal has none), never republished
     and counted once; any authorization other than ``authorized`` sets the latch;
   - integrity failed: ``halted_integrity_failed`` when it names this
     transaction, ``halted_foreign_commit`` otherwise; the latch is set.
4. Any commit after a published one is not this transaction's: it is
   receipted as foreign and the latch is set.

``main`` is read before and after the walk; one that moved, or that cannot be
read the second time, is ``halted_history_unverifiable`` too, with no receipt.
The whole recovery, from the first remote read through the receipts, the
attempt records and the latch, runs under the workspace lock
(:func:`~lovspor.forarbeider.publication_attempts.workspace_lock`), which an
execution holds as well: a second recovery, or an execution, is refused
(``corpus_locked``) rather than interleaved, and retried later it finds the
receipts already written.

Receipts are deterministic from these facts. A receipt already on record is
re-derived and must match exactly; one the remote contradicts is refused
(``receipt_conflict``) and sets the latch. Recovery may run while a STOP is in
force and while the latch is set; it never clears either (AR5 resumes).

AR5c: with a durable ledger, a ``published_authorized`` receipt is settled
exactly once (:func:`~lovspor.forarbeider.publication_ledger.settle`), under
the ledger lock taken inside the workspace lock; any other classification is
withheld and its reservation stays open. A repeated recovery settles nothing
twice and sends nothing.
"""

from datetime import datetime
from typing import Any, Literal, NamedTuple

from lovspor.forarbeider import (
    publication_anchors,
    publication_attempts,
    publication_ledger,
    publication_receipts,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_authorize import batch_usage
from lovspor.forarbeider.publication_execute import (
    Environment,
    ExecutionRequest,
    Simulation,
    attempt_id_of,
)
from lovspor.forarbeider.publication_execution_models import AttemptRecord, AttemptState
from lovspor.forarbeider.publication_integrity import (
    AUTHORIZATION_UNAVAILABLE,
    Verdict,
    authorization,
    integrity_checks,
    ours,
)
from lovspor.forarbeider.publication_ledger import Settlement
from lovspor.forarbeider.publication_recovery_models import (
    Classification,
    RecoveryReceipt,
    classification_of,
    integrity_of,
)
from lovspor.forarbeider.publication_remote import (
    Reader,
    RemoteCommit,
    bare_reader,
    github_reader,
    history,
)
from lovspor.forarbeider.release_ledger import plan_entry
from lovspor.forarbeider.release_ledger_models import LedgerEntry, LedgerOperation, LedgerPlan
from lovspor.forarbeider.release_limits import LimitsRequest, verify_limits

OutcomeClass = (
    Classification | Literal["not_published", "already_receipted", "halted_history_unverifiable"]
)
_DERIVED_ONLY = {"sequence", "previous_sha256"}


class RecoveryOutcome(NamedTuple):
    classification: OutcomeClass
    receipt: RecoveryReceipt | None
    ledger: LedgerPlan | None
    reasons: tuple[str, ...]
    #: AR5c: the durable settlement of a verified publication, when a ledger is given.
    settlement: Settlement | None = None


def _not_eligible(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, detail)


def _reader(request: ExecutionRequest, environment: Environment) -> Reader:
    if not isinstance(environment, Simulation):
        raise _not_eligible("recovery_needs_a_sandboxed_adapter")
    settings = request.inputs.settings
    if request.inputs.transaction.content.publication.path == "A":
        if environment.transport is None:
            raise _not_eligible("adapter")
        return github_reader(environment.transport, settings)
    if environment.remote is None:
        raise _not_eligible("adapter")
    return bare_reader(environment.remote, request.root, settings)


def _halt(request: ExecutionRequest, reasons: tuple[str, ...], at: datetime) -> None:
    tid = request.inputs.transaction.transaction_id
    publication_attempts.latch(request.workspace, reasons, at, tid)
    publication_anchors.settle(request, "stop_latch")


def _settle_pending(request: ExecutionRequest) -> None:
    """Complete a record a crash left unanchored (AR5b), before recovery appends any."""
    tid = request.inputs.transaction.transaction_id
    publication_anchors.settle(request, "publication_attempts", tid)
    publication_anchors.settle(request, "recovery_receipts")
    publication_anchors.settle(request, "stop_latch")


# --- deriving receipts from the remote -------------------------------------------------------


def _recovered(records: list[AttemptRecord], commit: str) -> bool:
    """Whether no execution recorded this commit as published before recovery saw it."""
    return not any(
        r.state == "published" and r.commit == commit and "recovered_from_remote" not in r.reasons
        for r in records
    )


def _verdict(
    request: ExecutionRequest, commit: RemoteCommit, records: list[AttemptRecord]
) -> Verdict:
    if not ours(commit, request.inputs):
        return Verdict("authorization_unknown", ("not_a_pipeline_commit",), None)
    return authorization(request.inputs, request.root, records, commit.event_at)


def _authorization_fields(verdict: Verdict) -> dict[str, Any]:
    attempt = verdict.attempt
    return {
        "attempt_id": attempt.attempt_id if attempt else None,
        "attempt_at": attempt.recorded_at if attempt else None,
        "authorization": verdict.authorization,
        "authorization_reasons": verdict.reasons,
        "authorization_waived": AUTHORIZATION_UNAVAILABLE,
    }


def _draft(
    request: ExecutionRequest, commit: RemoteCommit, records: list[AttemptRecord]
) -> dict[str, Any]:
    """Every receipt field but its place in the chain, from verified facts only."""
    content = request.inputs.transaction.content
    integrity, waived = integrity_of(checks := integrity_checks(commit, request.inputs))
    verdict = _verdict(request, commit, records)
    mine = ours(commit, request.inputs)
    return _authorization_fields(verdict) | {
        "transaction_id": request.inputs.transaction.transaction_id,
        "release_manifest_sha256": content.release_manifest_sha256,
        "path": content.publication.path,
        "base_commit": content.publication.base_commit,
        "commit": commit.oid,
        "parent": commit.parents[0] if len(commit.parents) == 1 else None,
        "integrity": integrity,
        "integrity_checks": checks,
        "waived": waived,
        "remote_event_at": commit.event_at,
        "ours": mine,
        "classification": classification_of(integrity, verdict.authorization, mine),
        "recovered_from_remote": _recovered(records, commit.oid),
    }


def _drafts(
    request: ExecutionRequest, commits: tuple[RemoteCommit, ...], records: list[AttemptRecord]
) -> list[dict[str, Any]]:
    """The first commit, and after a publication the next one; recovery stops at a halt."""
    drafts: list[dict[str, Any]] = []
    for commit in commits:
        draft = _draft(request, commit, records)
        if drafts:
            draft = draft | {"ours": False, "classification": "halted_foreign_commit"}
        drafts.append(draft)
        if draft["integrity"] != "integrity_verified" or len(drafts) > 1:
            break
    return drafts


# --- reconciling with the receipts on record ---------------------------------------------------


def _conflict(request: ExecutionRequest, at: datetime) -> ForarbeiderRefusedError:
    _halt(request, ("receipt_conflict",), at)
    return _not_eligible("receipt_conflict")


def _reconcile(
    request: ExecutionRequest, drafts: list[dict[str, Any]], at: datetime
) -> list[RecoveryReceipt | dict[str, Any]]:
    """Each draft, as the receipt on record when there is one; a contradiction refuses."""
    tid = request.inputs.transaction.transaction_id
    held = [r for r in publication_receipts.receipts(request.workspace) if r.transaction_id == tid]
    by_commit = {r.commit: r for r in held}
    if set(by_commit) - {d["commit"] for d in drafts}:
        raise _conflict(request, at)
    found: list[RecoveryReceipt | dict[str, Any]] = []
    for draft in drafts:
        known = by_commit.get(draft["commit"])
        if known is not None and known.model_dump(exclude=_DERIVED_ONLY) != _dumped(draft):
            raise _conflict(request, at)
        found.append(known if known is not None else draft)
    return found


def _dumped(draft: dict[str, Any]) -> dict[str, Any]:
    probe = RecoveryReceipt.model_validate({**draft, "sequence": 1, "previous_sha256": None})
    return probe.model_dump(exclude=_DERIVED_ONLY)


def _written(request: ExecutionRequest, draft: dict[str, Any]) -> RecoveryReceipt:
    sequence, previous = publication_receipts.head(request.workspace)
    fields = {**draft, "sequence": sequence, "previous_sha256": previous}
    receipt = RecoveryReceipt.model_validate(fields)
    publication_receipts.append(request.workspace, receipt)
    publication_anchors.settle(request, "recovery_receipts")
    return receipt


# --- consequences ------------------------------------------------------------------------------


def _attempt(
    request: ExecutionRequest, state: AttemptState, extra: dict[str, Any], at: datetime
) -> None:
    tid = request.inputs.transaction.transaction_id
    content = request.inputs.transaction.content
    sequence, previous = publication_attempts.head(request.workspace, tid)
    found = publication_attempts.records(request.workspace, tid)
    last = found[-1] if found else None
    fields = {
        "transaction_id": tid,
        "release_manifest_sha256": content.release_manifest_sha256,
        "base_commit": content.publication.base_commit,
        "path": content.publication.path,
        "attempt_id": last.attempt_id if last else attempt_id_of(tid, sequence),
        "sequence": sequence,
        "state": state,
        "recorded_at": at,
        "authorization_sha256": last.authorization_sha256 if last else None,
        "previous_sha256": previous,
        "commit": None,
    }
    _append_attempt(request, AttemptRecord.model_validate(fields | extra))


def _append_attempt(request: ExecutionRequest, record: AttemptRecord) -> None:
    publication_attempts.append(request.workspace, record)
    publication_anchors.settle(request, "publication_attempts", record.transaction_id)


def _record_published(request: ExecutionRequest, receipt: RecoveryReceipt, at: datetime) -> None:
    tid = request.inputs.transaction.transaction_id
    found = publication_attempts.records(request.workspace, tid)
    if any(r.state == "published" and r.commit == receipt.commit for r in found):
        return
    extra = {"commit": receipt.commit, "reasons": ("recovered_from_remote",)}
    _attempt(request, "published", extra, at)


def _reservation(request: ExecutionRequest) -> LedgerEntry | None:
    tid = request.inputs.transaction.transaction_id
    reserved = [
        entry
        for raw in request.inputs.ledger
        if (entry := _entry(raw)) is not None
        and (entry.operation, entry.release_id) == ("reserve", tid)
    ]
    return reserved[0] if len(reserved) == 1 else None


def _ledger(request: ExecutionRequest, receipt: RecoveryReceipt) -> LedgerPlan:
    """The budget settlement for a published batch: planned once, already applied after."""
    inputs = request.inputs
    slot = _reservation(request)
    if slot is None:
        return LedgerPlan(outcome="refused", reasons=("ledger_reservation_missing",), entry=None)
    at = max(receipt.remote_event_at or receipt.attempt_at or slot.recorded_at, slot.recorded_at)
    operation = LedgerOperation(
        operation="commit",
        ledger_id=slot.ledger_id,
        day=slot.day,
        release_id=inputs.transaction.transaction_id,
        batch_id=slot.batch_id,
        amounts=batch_usage(inputs.transaction),
        recorded_at=at,
    )
    limits = verify_limits(LimitsRequest(inputs.policy.policy, inputs.limits, at), request.root)
    return plan_entry(tuple(inputs.ledger), operation, limits)


def _entry(raw: bytes) -> LedgerEntry | None:
    try:
        return LedgerEntry.model_validate_json(raw)
    except ValueError:
        return None


# --- recovery ----------------------------------------------------------------------------------


def _not_published(request: ExecutionRequest, at: datetime) -> RecoveryOutcome:
    tid = request.inputs.transaction.transaction_id
    found = publication_attempts.records(request.workspace, tid)
    if found and found[-1].state in ("prepared_to_send", "outcome_unknown"):
        _attempt(request, "refused", {"reasons": ("recovered_not_published",)}, at)
    return RecoveryOutcome("not_published", None, None, ())


def _settle(
    request: ExecutionRequest, receipts: list[RecoveryReceipt], fresh: bool, at: datetime
) -> RecoveryOutcome:
    published = next((r for r in receipts if r.published), None)
    halts = [r for r in receipts if r.classification != "published_authorized"]
    if published is not None:
        _record_published(request, published, at)
    if halts:
        reasons = tuple(f"{r.classification}:{r.commit}" for r in halts)
        _halt(request, reasons, at)
    ledger = _ledger(request, published) if published is not None else None
    settlement = None
    if published is not None and request.ledger is not None:
        settlement = publication_ledger.settle(request, published, at)
    primary = published or receipts[0]
    chosen = halts[-1].classification if halts else primary.classification
    classification: OutcomeClass = chosen if fresh else "already_receipted"
    return RecoveryOutcome(classification, primary, ledger, (), settlement)


def recover(request: ExecutionRequest, environment: Environment) -> RecoveryOutcome:
    """Classify the transaction from the remote history; never send, never clear the latch.

    Raises:
        ForarbeiderRefusedError: an adapter not sandboxed (``release_not_eligible``);
            another execution or recovery holding the workspace
            (``corpus_locked: workspace``); forged or truncated attempt records or receipts
            (``corpus_tree_mismatch``); a receipt the remote contradicts
            (``release_not_eligible: receipt_conflict``, latch set); with a
            witness, a workspace without its anchored identity
            (``release_not_eligible: workspace_identity:<why>``, nothing written).
    """
    reader = _reader(request, environment)
    assert isinstance(environment, Simulation)  # noqa: S101 - _reader proved it
    with publication_attempts.workspace_lock(request.workspace):
        return _recover(request, environment, reader)


def _recover(request: ExecutionRequest, environment: Simulation, reader: Reader) -> RecoveryOutcome:
    """:func:`recover` under the workspace lock, from the first remote read to the last record."""
    if request.anchoring is not None:
        publication_anchors.scope_of(request)  # AR5d-2: an unidentified workspace is read-only
    _settle_pending(request)
    at = environment.clock()
    tid = request.inputs.transaction.transaction_id
    records = publication_attempts.records(request.workspace, tid)
    found = history(reader, request.inputs.transaction.content.publication.base_commit)
    if found.broken is not None:
        _halt(request, (f"remote_history_unverifiable:{found.broken}",), at)
        return RecoveryOutcome("halted_history_unverifiable", None, None, (found.broken,))
    drafts = _drafts(request, found.commits, records)
    reconciled = _reconcile(request, drafts, at)
    if not reconciled:
        return _not_published(request, at)
    fresh = any(isinstance(item, dict) for item in reconciled)
    receipts = [
        item if isinstance(item, RecoveryReceipt) else _written(request, item)
        for item in reconciled
    ]
    return _settle(request, receipts, fresh, at)
