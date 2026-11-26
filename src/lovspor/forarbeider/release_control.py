"""AR1b: the stop state from the owner-signed control chain (#738; §9.9 AR1, AR5).

:func:`verify_control` recomputes the stop state for one policy from the
control records a source offers. It takes no state from anyone. The chain
starts stopped (the *initial stop*) and:

- every record must verify as owner-signed in the control namespace
  (:func:`verify_owner_record`, which also refuses a key expired or revoked by
  the check time and a record signed after it);
- the records must form one chain: sequences 1, 2, 3... in order, each naming
  the digest of the one before, times strictly rising, ids unique;
- a ``stop`` adds an active stop for its scope; a ``resume`` clears only the
  stop it names (or the initial stop), only for that stop's exact scope, and
  only while its lease (``valid_until``) holds at the check time.

Missing history, any unverifiable or conflicting record, a broken, reordered
or forked chain, a resume naming an unknown stop or another scope: the state
is ``stopped``. A stop overrides every approval: the ``stop_control`` gate
fails while any stop covering the policy is active.

Even a verified ``not_stopped`` chain cannot prove it is *complete*: whoever
holds the history can withhold its newest stop. The evidence is therefore
``unanchored``, and the gate stays failed until AR5 anchors the chain's head
outside the runner.
"""

from datetime import datetime
from hashlib import sha256
from typing import NamedTuple

from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import CONTROL_NAMESPACE, TrustRoot
from lovspor.forarbeider.release_contract_models import ControlEvidence, EvidenceStatus
from lovspor.forarbeider.release_control_models import ControlScope, ReleaseControl
from lovspor.forarbeider.release_limits import utc
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.release_signed import RecordSpec, verify_owner_record

INITIAL_STOP = "initial_stop"
_SPEC = RecordSpec(ReleaseControl, CONTROL_NAMESPACE)


class ControlRequest(NamedTuple):
    history: tuple[SignedRecord, ...]
    policy_id: str | None
    checked_at: datetime


class _Walk(NamedTuple):
    stops: dict[str, ControlScope]
    leases: dict[str, datetime]
    reasons: tuple[str, ...]


def verify_control(request: ControlRequest, root: TrustRoot) -> ControlEvidence:
    """The stop state for ``request.policy_id``; stopped unless the chain proves otherwise.

    Raises:
        ForarbeiderRefusedError: ``invalid_timestamp`` when the check time is not UTC.
    """
    at = utc(request.checked_at)
    if not request.history:
        return _stopped(request, "missing", ("control_history_missing",))
    checks = [verify_owner_record(root, item, _SPEC, at) for item in request.history]
    records = [c.record for c in checks if isinstance(c.record, ReleaseControl)]
    if len(records) != len(checks):
        failed = {r for c in checks for r in c.reasons} or {"control_record_unverified"}
        return _stopped(request, "invalid", tuple(sorted(failed)))
    digests = [sha256(item.record).hexdigest() for item in request.history]
    walk = _walk(records, digests)
    reasons = _chain(records, digests) + walk.reasons
    if reasons:
        return _stopped(request, "invalid", tuple(sorted(set(reasons))))
    return _verified(request, root, walk, digests)


def _stopped(
    request: ControlRequest, status: EvidenceStatus, reasons: tuple[str, ...]
) -> ControlEvidence:
    return ControlEvidence(
        status=status,
        source=None,
        reasons=reasons,
        policy_id=request.policy_id,
        state="stopped",
        records=len(request.history),
    )


def _chain(records: list[ReleaseControl], digests: list[str]) -> tuple[str, ...]:
    reasons: list[str] = []
    for index, record in enumerate(records):
        if record.sequence != index + 1:
            reasons.append("control_sequence_gap")
        if index and record.previous_control_sha256 != digests[index - 1]:
            reasons.append("control_chain_broken")
        if index and record.signed_at <= records[index - 1].signed_at:
            reasons.append("control_reordered")
    if len({record.control_id for record in records}) != len(records):
        reasons.append("control_duplicate")
    return tuple(reasons)


def _walk(records: list[ReleaseControl], digests: list[str]) -> _Walk:
    stops = {INITIAL_STOP: ControlScope(policy_id=None)}
    leases: dict[str, datetime] = {}
    reasons: list[str] = []
    for record, digest in zip(records, digests, strict=True):
        if record.action == "stop":
            stops[digest] = record.scope
            continue
        target = record.resumes_control_sha256 or INITIAL_STOP
        if target not in stops:
            reasons.append("resume_target_unknown")
        elif stops[target] != record.scope:
            reasons.append("resume_scope_mismatch")
        elif record.valid_until is not None:
            leases[target] = record.valid_until
    return _Walk(stops, leases, tuple(reasons))


def _verified(
    request: ControlRequest, root: TrustRoot, walk: _Walk, digests: list[str]
) -> ControlEvidence:
    at, policy_id = request.checked_at, request.policy_id
    covering = [t for t, scope in walk.stops.items() if scope.covers(policy_id)]
    active = sorted(t for t in covering if not (t in walk.leases and at < walk.leases[t]))
    expired = [t for t in active if t in walk.leases]
    evidence = ControlEvidence(
        status="verified",
        source="owner_signed_control",
        reasons=("resume_expired",) if expired else (),
        policy_id=policy_id,
        state="stopped" if active else "not_stopped",
        active_stops=tuple(active),
        records=len(digests),
        head_sha256=digests[-1],
        trust_root_sha256=root.sha256,
    )
    return issue_provenance(evidence, "release_control")
