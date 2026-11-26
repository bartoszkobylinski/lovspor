"""AR5b: owner STOP and owner RESUME of publication, anchored and auditable (#738; §9.9 AR5).

**STOP** (:func:`record_stop`). The newest of the owner's control records the
caller offers must be a ``stop`` that
:func:`~lovspor.forarbeider.release_control.verify_control` accepts: the
owner's key, signature, namespace and window, the chain's order and links. The
latch is then set at once, in the workspace, whatever the witness says: a STOP
takes effect even while the witness is down. Only then are the control record
and the latch transition anchored, when the witness can. A STOP the caller
merely offers is in force without this step too: the ``stop_control`` check
denies on it, and once anchored, withholding it is ``local_rolled_back``.

**RESUME** (:func:`resume`) clears the latch only when all of these hold:

- the newest control record is an owner ``resume`` that ``verify_control``
  accepts, leaving the policy ``not_stopped``: it names a stop it resumes,
  within that stop's scope, inside its lease, with no later stop; a bot key, a
  forged signature, another policy, a stale sequence or a revoked owner key is
  refused there;
- the owner records extend the anchored control history exactly
  (:func:`~lovspor.forarbeider.publication_anchors.sync_control`); a replayed
  older history is ``local_rolled_back``;
- the resume was signed after the latch was set;
- the cause is resolved, by verified evidence: the latch's transaction has
  anchored attempts and receipts, no attempt left at ``prepared_to_send`` or
  ``outcome_unknown`` (an unknown outcome is resolved by AR4b-2 recovery
  first), and no receipt other than ``published_authorized``; an unverifiable
  history or a receipt conflict is never resolved here;
- the clearing, which names the RESUME and the resolution, is appended after
  the ``set`` (never replacing it) and anchored.

Ordering: STOP, RESUME, execution and recovery all hold the workspace lock, so
they never interleave. The second of two concurrent calls is refused at once
(``corpus_locked: workspace``) and decides nothing. An execution that holds
the lock first completes; a STOP recorded after it denies every later one.
Under the workspace lock only the witness's lock (a leaf) is taken.

Since AR5d-1 the latch is anchored in the scope of the workspace's own
anchored identity, so a STOP or RESUME in one workspace never reads or moves
another workspace's latch, even through one witness. The owner's control
history stays per policy: an owner STOP binds every workspace of the policy.
"""

from datetime import datetime
from hashlib import sha256
from typing import Literal, NamedTuple

from lovspor.forarbeider import publication_anchors, publication_attempts, publication_receipts
from lovspor.forarbeider.control_plane_anchor import verify
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.publication_execute import ExecutionRequest
from lovspor.forarbeider.publication_execution_models import LatchEvent
from lovspor.forarbeider.release_control import ControlRequest, verify_control
from lovspor.forarbeider.release_control_models import ReleaseControl

_UNRESOLVED_STATES = frozenset({"prepared_to_send", "outcome_unknown"})
_NEVER_RESOLVED = ("remote_history_unverifiable", "receipt_conflict")


class ControlOutcome(NamedTuple):
    status: Literal["stop_recorded", "already_stopped", "resumed"]
    latch: LatchEvent | None
    anchored: bool
    reasons: tuple[str, ...]


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, detail)


def _policy_id(request: ExecutionRequest) -> str:
    plan = request.inputs.plan.plan
    if plan is None:
        raise _refused("policy_unknown")
    return plan.basis.policy.policy_id


def _newest(
    request: ExecutionRequest, now: datetime, action: Literal["stop", "resume"]
) -> tuple[SignedRecord, ReleaseControl, str]:
    """The newest owner record, which must be ``action``, and the state the chain leaves."""
    history = request.inputs.control
    request_ = ControlRequest(history, _policy_id(request), now)
    evidence = verify_control(request_, request.root)
    if not history or evidence.status != "verified" or evidence.reasons:
        raise _refused(f"{action}_not_verified:{','.join(evidence.reasons) or evidence.status}")
    record = ReleaseControl.model_validate_json(history[-1].record)
    if record.action != action:
        raise _refused(f"{action}_not_verified:newest_is_{record.action}")
    return history[-1], record, evidence.state


def _anchored(request: ExecutionRequest, family: str) -> bool:
    authority = publication_anchors.authority_of(request)
    if authority is None:
        return False
    if family == "control_history":
        query = publication_anchors.control_query(request.inputs, _policy_id(request))
        authority = publication_anchors.attempts_witness(authority, request.ledger)
    else:
        try:
            scope = publication_anchors.scope_of(request)
        except ForarbeiderRefusedError:
            return False
        query = publication_anchors.workspace_query(
            request.inputs, request.workspace, "stop_latch", scope
        )
    return verify(query, authority).status == "verified"


def _anchor_stop(request: ExecutionRequest, now: datetime) -> bool:
    """Anchor the STOP and the latch when the witness can; the STOP is in force either way."""
    try:
        publication_anchors.sync_control(request, _policy_id(request), now)
    except ForarbeiderRefusedError:
        return False
    publication_anchors.settle(request, "stop_latch")
    return _anchored(request, "control_history") and _anchored(request, "stop_latch")


def record_stop(request: ExecutionRequest, now: datetime) -> ControlOutcome:
    """Record the owner's newest STOP: the latch is set now, the anchors follow when they can.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: workspace``; ``release_not_eligible:
            stop_not_verified:<why>`` for anything but a verified owner STOP in force.
    """
    with publication_attempts.workspace_lock(request.workspace):
        stop, _, state = _newest(request, now, "stop")
        if state != "stopped":
            raise _refused("stop_not_verified:not_in_force")
        already = publication_attempts.latched(request.workspace)
        if not already:
            tid = request.inputs.transaction.transaction_id
            reason = f"owner_stop:{sha256(stop.record).hexdigest()}"
            publication_attempts.latch(request.workspace, (reason,), now, tid)
        anchored = _anchor_stop(request, now)
        latch = publication_attempts.effective_latch(request.workspace)
    status: Literal["stop_recorded", "already_stopped"] = (
        "already_stopped" if already else "stop_recorded"
    )
    return ControlOutcome(status, latch, anchored, ())


# --- resume ---------------------------------------------------------------------------------------


def _verified(request: ExecutionRequest, family: str, scope: str) -> None:
    authority = (
        publication_anchors.attempts_authority_of(request)
        if family == "attempts"
        else publication_anchors.authority_of(request)
    )
    if authority is None:
        raise _refused("anchor_authority_missing")
    query = publication_anchors.workspace_query(
        request.inputs,
        request.workspace,
        "publication_attempts" if family == "attempts" else "recovery_receipts",
        scope,
    )
    status = verify(query, authority).status
    if status != "verified":
        raise _refused(f"unresolved:{family}_{status}")


def resolution_of(request: ExecutionRequest, latch: LatchEvent) -> tuple[str, ...]:
    """The verified evidence that the latch's cause is resolved, or why it is not."""
    if any(reason.startswith(_NEVER_RESOLVED) for reason in latch.reasons):
        raise _refused(f"unresolved:{latch.reasons[0]}")
    tid = latch.transaction_id
    _verified(request, "attempts", tid)
    _verified(request, "receipts", publication_anchors.scope_of(request))
    records = publication_attempts.records(request.workspace, tid)
    last = records[-1].state if records else "none"
    if last in _UNRESOLVED_STATES:
        raise _refused(f"unresolved:attempt_{last}")
    receipts = [
        r for r in publication_receipts.receipts(request.workspace) if r.transaction_id == tid
    ]
    for receipt in receipts:
        if receipt.classification != "published_authorized":
            raise _refused(f"unresolved:{receipt.classification}")
    return (f"attempt:{last}", *(f"receipt:published_authorized:{r.commit}" for r in receipts))


def _clear(
    request: ExecutionRequest, resume_record: tuple[SignedRecord, ReleaseControl], now: datetime
) -> ControlOutcome:
    signed, record = resume_record
    latch = publication_attempts.effective_latch(request.workspace)
    if latch is None:
        events = publication_attempts.latch_events(request.workspace)
        if events and _anchored(request, "stop_latch"):
            return ControlOutcome("resumed", events[-1], True, ("already_cleared",))
        raise _refused("not_latched")
    if record.signed_at <= latch.at:
        raise _refused("resume_predates_latch")
    resolution = resolution_of(request, latch)
    digest = sha256(signed.record).hexdigest()
    event = publication_attempts.clear_latch(request.workspace, now, digest, resolution)
    if publication_anchors.settle(request, "stop_latch") is None:
        raise _refused("anchor:stop_latch_clearing_unanchored")
    return ControlOutcome("resumed", event, True, resolution)


def resume(request: ExecutionRequest, now: datetime) -> ControlOutcome:
    """Clear the latch on a verified owner RESUME, once the cause is resolved; append-only.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: workspace``; ``release_not_eligible``
            with ``resume_not_verified:<why>``, ``anchor:...``, ``resume_predates_latch``,
            ``unresolved:<why>`` or ``not_latched``.
    """
    if publication_anchors.authority_of(request) is None:
        raise _refused("anchor_authority_missing")
    with publication_attempts.workspace_lock(request.workspace):
        publication_anchors.settle(request, "stop_latch")
        signed, record, state = _newest(request, now, "resume")
        if state != "not_stopped":
            raise _refused("resume_not_verified:still_stopped")
        publication_anchors.sync_control(request, _policy_id(request), now)
        return _clear(request, (signed, record), now)
