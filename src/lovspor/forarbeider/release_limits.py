"""AR1b: fail-closed verification of the owner-signed release limits (#738; §9.3, §9.9 AR1).

:func:`verify_limits` takes the exact bytes of the policy, the limits records
and revocations a source offers, a check time and the trust root. It trusts
none of them. A record is verified only when it is canonical, signed in the
limits namespace by an owner-role key the root lists, unrevoked, inside the
key's window at both the signing and the check time
(:func:`verify_owner_record`), names the key's principal, binds exactly this
policy's id, version, digest, source and document types (FC7b's scope check),
is inside its own validity window and is not withdrawn by a verified
revocation.

The evidence is:

- ``missing`` when there is no policy, no record or no signature;
- ``invalid`` when any record or revocation fails, or when more than one
  record verifies (``limits_conflict``: the engine never picks one);
- ``verified`` with exactly one record, sealed as ``publication_limits``.

A verified record from an unpinned trust root stays verified here and names
its root's digest; the gates block it on the root (``authorization``).
"""

from collections.abc import Iterable
from datetime import datetime
from typing import NamedTuple

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization import SignedRecord, _scope_reasons
from lovspor.forarbeider.policy_authorization_models import ApprovalRevocation
from lovspor.forarbeider.policy_trust import LIMITS_NAMESPACE, POLICY_NAMESPACE, TrustRoot
from lovspor.forarbeider.publication_policy import PublicationPolicy, parse_policy, policy_digest
from lovspor.forarbeider.release_contract_models import LimitsEvidence
from lovspor.forarbeider.release_limits_models import ReleaseLimits
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.release_signed import OwnerCheck, RecordSpec, verify_owner_record
from lovspor.forarbeider.version_release_models import PolicyReference

_LIMITS = RecordSpec(ReleaseLimits, LIMITS_NAMESPACE)
_REVOCATION = RecordSpec(ApprovalRevocation, POLICY_NAMESPACE)


class LimitsMaterial(NamedTuple):
    """What a limits source offers: signed records and signed revocations. Not evidence."""

    records: tuple[SignedRecord, ...] = ()
    revocations: tuple[SignedRecord, ...] = ()


class LimitsRequest(NamedTuple):
    policy: bytes | None
    material: LimitsMaterial
    checked_at: datetime


class _Context(NamedTuple):
    root: TrustRoot
    policy: PublicationPolicy
    at: datetime
    revoked: frozenset[str]


def verify_limits(request: LimitsRequest, root: TrustRoot) -> LimitsEvidence:
    """The limits evidence for ``request``; never trusts a status it is given.

    Raises:
        ForarbeiderRefusedError: ``invalid_timestamp`` when the check time is not
            UTC; ``schema_invalid`` when the policy bytes are not a policy.
    """
    at = utc(request.checked_at)
    if request.policy is None:
        return _missing(("limits_policy_unavailable",))
    if not request.material.records:
        return _missing(("signed_limits_missing",))
    revocations = [
        verify_owner_record(root, r, _REVOCATION, at) for r in request.material.revocations
    ]
    ctx = _Context(root, parse_policy(request.policy), at, _revoked_ids(revocations))
    checks = [_limits(ctx, offered) for offered in request.material.records]
    unverified = (
        ["revocation_not_verified"] if any(c.status != "verified" for c in revocations) else []
    )
    return _verdict(ctx, checks, unverified)


def utc(at: datetime) -> datetime:
    offset = at.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
    return at


def _missing(reasons: tuple[str, ...]) -> LimitsEvidence:
    return LimitsEvidence(status="missing", source=None, constraints=None, reasons=reasons)


def _revoked_ids(revocations: Iterable[OwnerCheck]) -> frozenset[str]:
    ids: set[str] = set()
    for check in revocations:
        if isinstance(check.record, ApprovalRevocation):
            ids.update(check.record.revokes)
    return frozenset(ids)


def _limits(ctx: _Context, offered: SignedRecord) -> OwnerCheck:
    check = verify_owner_record(ctx.root, offered, _LIMITS, ctx.at)
    record = check.record
    if not isinstance(record, ReleaseLimits):
        return check
    reasons = _scope_reasons(record.scope, ctx.policy)
    if ctx.at < record.valid_from:
        reasons.append("limits_not_yet_valid")
    if ctx.at >= record.valid_until:
        reasons.append("limits_expired")
    if record.limits_id in ctx.revoked:
        reasons.append("limits_revoked")
    return (
        check._replace(status="invalid", reasons=tuple(reasons), record=None) if reasons else check
    )


def _verdict(ctx: _Context, checks: list[OwnerCheck], extra: list[str]) -> LimitsEvidence:
    good = [c for c in checks if c.status == "verified"]
    reasons = set(extra) | {r for c in checks for r in c.reasons}
    if len(good) > 1:
        reasons.add("limits_conflict")
    if extra or len(good) > 1 or any(c.status == "invalid" for c in checks):
        return LimitsEvidence(
            status="invalid", source=None, constraints=None, reasons=tuple(sorted(reasons))
        )
    if not good:
        return _missing(tuple(sorted(reasons)))
    return _sealed(ctx, good[0])


def _sealed(ctx: _Context, check: OwnerCheck) -> LimitsEvidence:
    record, entry = check.record, check.entry
    if not isinstance(record, ReleaseLimits) or entry is None:
        return _missing(("limits_unverifiable",))
    evidence = LimitsEvidence(
        status="verified",
        source="owner_signed_policy_limits",
        reasons=(),
        constraints=record.constraints,
        policy=PolicyReference(
            policy_id=ctx.policy.policy_id,
            policy_version=ctx.policy.policy_version,
            policy_sha256=policy_digest(ctx.policy),
        ),
        limits_id=record.limits_id,
        limits_sha256=check.record_sha256,
        trust_root_sha256=ctx.root.sha256,
        signer_fingerprint=entry.fingerprint,
        signer_role=entry.role,
    )
    return issue_provenance(evidence, "publication_limits")
