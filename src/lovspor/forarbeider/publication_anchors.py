"""AR5b: the anchored control state a publication authorization requires (#738; §9.9 AR5).

Before a simulated publication is authorized, the verifier itself reads and
verifies, against the independent witness (AR5a), three families:

- ``control_history`` for the plan's exact policy: the owner's control records
  the caller offers must be exactly the anchored ones. A withheld newest STOP
  is ``local_rolled_back``; another history is ``diverged``;
- ``stop_latch``: the workspace's latch transitions, so a deleted latch is not
  a cleared one; since AR5d-1 anchored in the scope of the workspace's own
  anchored identity, and denied (``workspace_identity:<why>``) without one;
- ``publication_attempts`` for the transaction, on the transaction's
  authoritative witness (:func:`attempts_witness`).

**The authoritative attempt history (AR5d-3).** A transaction reserved in a
budget ledger has one attempt history, anchored on the ledger's witness: the
witness that anchors its reservation. A workspace that keeps another witness
for its own families (AR5c) still anchors the transaction's attempts there, so
a ``prepared_to_send`` it records is visible to every workspace that verifies
the transaction's attempts, and a workspace whose local records lack it reads
``local_rolled_back``. A send is begun only once its ``prepared_to_send`` is
anchored on that witness (AR4b-1), so a writer that cannot anchor there never
sends. Without a ledger, or with a ledger that names no witness of its own,
it is the request's witness, as before.

Anything but ``verified`` (missing, stale, rolled back, divergent, unavailable,
not independent) fails the ``ar5_control_anchor`` check, which no simulation
may waive. Nothing a caller passes is taken as "verified": the verdicts are
computed here, from the bytes and the witness.

Anchoring new records is narrow:

- :func:`sync_control` anchors owner control records beyond the anchored head
  only when :func:`~lovspor.forarbeider.release_control.verify_control` accepts
  the whole chain (owner signatures, order, links, scopes), and only when they
  extend the anchored head;
- :func:`settle` anchors the one record an append just wrote. A witness that
  fails leaves the family ``pending_anchor``, which denies until completed.
"""

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from lovspor.forarbeider import publication_attempts
from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorQuery,
    AnchorVerdict,
    anchor_pending,
    initialize,
    verify,
    workspace_scope,
)
from lovspor.forarbeider.control_plane_anchor_models import Family
from lovspor.forarbeider.control_plane_chains import control_chain, workspace_chain
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_execution_models import LatchEvent
from lovspor.forarbeider.release_control import ControlRequest, verify_control
from lovspor.forarbeider.release_control_models import ReleaseControl

if TYPE_CHECKING:
    from lovspor.forarbeider.publication_authorize import AuthorizationInputs
    from lovspor.forarbeider.publication_execute import ExecutionRequest
    from lovspor.forarbeider.publication_ledger import BudgetLedger

REQUIRED: tuple[Family, ...] = ("control_history", "stop_latch", "publication_attempts")
#: A lapsed resume lease leaves the chain intact (it only stops again).
_INTACT = frozenset({"resume_expired"})


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, detail)


def authority_of(request: "ExecutionRequest") -> AnchorAuthority | None:
    """The request's witness, bound to the request's own workspace."""
    found = request.anchoring
    return None if found is None else found._replace(workspace=request.workspace)


def attempts_witness(authority: AnchorAuthority, ledger: "BudgetLedger | None") -> AnchorAuthority:
    """The authority of a transaction's attempts: on the ledger's witness when it names one.

    The workspace is kept (the attempt records are the workspace's); only the
    witness the chain is anchored on is the ledger's, never the caller's choice
    of another.
    """
    if ledger is None or ledger.witness is None:
        return authority
    return authority._replace(witness=ledger.witness)


def attempts_authority_of(request: "ExecutionRequest") -> AnchorAuthority | None:
    """The request's authority for the transaction's attempts (AR5d-3)."""
    authority = authority_of(request)
    return None if authority is None else attempts_witness(authority, request.ledger)


def control_query(
    inputs: "AuthorizationInputs", policy_id: str, count: int | None = None
) -> AnchorQuery:
    records = inputs.control if count is None else inputs.control[:count]
    chain = control_chain(records, policy_id)
    return AnchorQuery("control_history", inputs.settings.repository, policy_id, chain)


def scope_of(request: "ExecutionRequest") -> str:
    """The request workspace's verified identity scope (AR5d-1).

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible``: ``anchor_authority_missing``
            or ``workspace_identity:<why>``.
    """
    authority = authority_of(request)
    if authority is None:
        raise _refused("anchor_authority_missing")
    return workspace_scope(request.workspace, request.inputs.settings.repository, authority)


def identified(request: "ExecutionRequest") -> bool:
    """Whether the request may write to its workspace (AR5d-2).

    A request without a witness is AR4's: it anchors nothing, so nothing it
    writes becomes current. With a witness, only a workspace whose identity the
    witness anchored is written to; any other, a pre-AR5d workspace above all,
    is read-only.
    """
    if request.anchoring is None:
        return True
    try:
        scope_of(request)
    except ForarbeiderRefusedError:
        return False
    return True


def workspace_query(
    inputs: "AuthorizationInputs", workspace: Path, family: Family, scope: str
) -> AnchorQuery:
    chain = workspace_chain(family, workspace, scope)
    return AnchorQuery(family, inputs.settings.repository, scope, chain)


def anchor_reasons(
    anchoring: tuple[AnchorAuthority | None, "BudgetLedger | None"],
    workspace: Path | None,
    inputs: "AuthorizationInputs",
    policy_id: str | None,
) -> tuple[str, ...]:
    """Why the required families are not all verified; empty when they are.

    ``anchoring`` is the request's witness and its budget ledger. The
    transaction's attempts are verified on their authoritative witness
    (:func:`attempts_witness`), the other families on the workspace's.
    """
    authority, ledger = anchoring
    if authority is None or workspace is None:
        return ("anchor_authority_missing",)
    if policy_id is None:
        return ("anchor_policy_unknown",)
    held = authority._replace(workspace=workspace)
    tid = inputs.transaction.transaction_id
    attempts = workspace_query(inputs, workspace, "publication_attempts", tid)
    queries = [(control_query(inputs, policy_id), held)]
    identity: tuple[str, ...] = ()
    try:
        scope = workspace_scope(workspace, inputs.settings.repository, held)
        queries.append((workspace_query(inputs, workspace, "stop_latch", scope), held))
    except ForarbeiderRefusedError as error:
        identity = (error.detail,)
    queries.append((attempts, attempts_witness(held, ledger)))
    found = ((query.family, verify(query, by).status) for query, by in queries)
    return identity + tuple(f"{f}:{status}" for f, status in found if status != "verified")


def _owner_chain_intact(request: "ExecutionRequest", policy_id: str, now: datetime) -> bool:
    evidence = verify_control(ControlRequest(request.inputs.control, policy_id, now), request.root)
    return evidence.status == "verified" and set(evidence.reasons) <= _INTACT


def sync_control(request: "ExecutionRequest", policy_id: str, now: datetime) -> AnchorVerdict:
    """Anchor the owner control records the witness has not seen yet, one by one.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible``: no witness
            (``anchor_authority_missing``), a chain the owner's signatures do not
            carry (``control_chain_not_verified``), or one that does not extend
            the anchored head (``anchor:control_history:<status>``).
    """
    authority = authority_of(request)
    if authority is None:
        raise _refused("anchor_authority_missing")
    if not _owner_chain_intact(request, policy_id, now):
        raise _refused("control_chain_not_verified")
    full = control_query(request.inputs, policy_id)
    verdict = verify(full, authority)
    if verdict.status == "verified":
        return verdict
    if verdict.status not in ("pending_anchor", "unanchored_tail") or verdict.latest is None:
        raise _refused(f"anchor:control_history:{verdict.status}")
    for count in range(verdict.latest.record_count + 1, len(request.inputs.control) + 1):
        anchor_pending(control_query(request.inputs, policy_id, count), authority)
    return verify(full, authority)


def settle(
    request: "ExecutionRequest", family: Family, scope: str | None = None
) -> AnchorVerdict | None:
    """Anchor the record an append just wrote; ``None`` when that cannot be done now.

    Without ``scope`` the family is a workspace family, anchored in the
    workspace's verified identity scope; an unverified identity anchors nothing.
    The transaction's attempts are anchored on their authoritative witness
    (:func:`attempts_witness`).
    """
    authority = authority_of(request)
    if authority is None:
        return None
    if family == "publication_attempts":
        authority = attempts_witness(authority, request.ledger)
    if scope is None:
        try:
            scope = scope_of(request)
        except ForarbeiderRefusedError:
            return None
    query = workspace_query(request.inputs, request.workspace, family, scope)
    if family == "stop_latch" and clearing_reasons(request, query, authority):
        return None
    try:
        return anchor_pending(query, authority)
    except ForarbeiderRefusedError:
        return None


def initialize_attempts(request: "ExecutionRequest") -> AnchorVerdict:
    """Bootstrap: anchor one transaction's empty attempts on their authoritative witness, once.

    The owner's explicit operation before a transaction's first attempt, on the
    ledger's witness when the ledger names one (AR5d-3). It never adopts or
    resets a history: a record on disk, or a chain the witness already anchored,
    refuses it.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: workspace``; ``release_not_eligible``
            with ``anchor_authority_missing``, ``already_anchored`` or ``anchor:<status>``.
    """
    authority = attempts_authority_of(request)
    if authority is None:
        raise _refused("anchor_authority_missing")
    tid = request.inputs.transaction.transaction_id
    with publication_attempts.workspace_lock(request.workspace):
        query = workspace_query(request.inputs, request.workspace, "publication_attempts", tid)
        return initialize(query, authority)


def clearing_reasons(
    request: "ExecutionRequest", query: AnchorQuery, authority: AnchorAuthority
) -> tuple[str, ...]:
    """Why a pending latch clearing is not the owner's; empty when it is or none is pending.

    A ``cleared`` event lifts a latch only the owner may lift, so the witness
    anchors one only on proof (#1027): it names the newest record of the
    request's owner control history, that history verifies now as a RESUME
    leaving the policy not stopped and is itself anchored, and that RESUME was
    signed after the ``set`` it clears, whose cause is verifiably resolved now
    (``resolution_of``). A ``set`` is anchored without proof:
    it only ever stops.
    """
    events = publication_attempts.latch_events(request.workspace)
    if not events or events[-1].kind != "cleared":
        return ()
    if verify(query, authority).status != "pending_anchor":
        return ()
    before = events[-2] if len(events) > 1 else None
    return _resume_reasons(request, (before, events[-1]), authority)


def _cause_reasons(
    request: "ExecutionRequest", before: LatchEvent | None, signed: datetime
) -> list[str]:
    """The RESUME follows the ``set``, whose cause is verifiably resolved now."""
    if before is None or before.kind != "set" or signed <= before.at:
        return ["latch_clearing_resume_predates_latch"]
    from lovspor.forarbeider.publication_stop_resume import (  # noqa: PLC0415 - builds on this module
        resolution_of,
    )

    try:
        resolution_of(request, before)
    except ForarbeiderRefusedError:
        return ["latch_clearing_cause_unresolved"]
    return []


def _resume_reasons(
    request: "ExecutionRequest",
    latch: tuple[LatchEvent | None, LatchEvent],
    authority: AnchorAuthority,
) -> tuple[str, ...]:
    """Why the request's owner history is not the anchored RESUME a clearing names."""
    before, cleared = latch
    plan = request.inputs.plan.plan
    history = request.inputs.control
    if plan is None or not history:
        return ("latch_clearing_without_resume",)
    policy_id = plan.basis.policy.policy_id
    found = verify_control(ControlRequest(history, policy_id, cleared.at), request.root)
    record = ReleaseControl.model_validate_json(history[-1].record)
    reasons = []
    if found.status != "verified" or found.reasons or found.state != "not_stopped":
        reasons.append("latch_clearing_resume_not_verified")
    digest = sha256(history[-1].record).hexdigest()
    if record.action != "resume" or digest != cleared.resume_control_sha256:
        reasons.append("latch_clearing_not_the_newest_resume")
    reasons += _cause_reasons(request, before, record.signed_at)
    if verify(control_query(request.inputs, policy_id), authority).status != "verified":
        reasons.append("latch_clearing_resume_unanchored")
    return tuple(reasons)
