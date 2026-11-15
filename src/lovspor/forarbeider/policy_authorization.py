"""FC7b: fail-closed verification of a policy's signed authorization (#738; D6, D11).

:func:`verify_authorization` takes the exact bytes of a proposed policy, the
signed records a caller offers (a policy approval, an automated-publication
approval, revocations), a check time and a trust root. It trusts none of the
records: each must be canonical, signed in the owner's policy namespace by a
key the trust root lists for that role, unrevoked and inside its window at the
time the record states, and bound to exactly this policy's id, version,
digest, source and document types. The automated-publication approval must
also bind the exact bytes of the policy approval it builds on.

Each record is ``missing`` (absent, or its signature absent), ``invalid``
(present but failing a check, with every reason) or ``verified``. The verdict
is ``invalid`` if any record is invalid, else ``missing_evidence`` if any is
missing, else ``signatures_verified``. That is the strongest verdict, and it
is still not an authorization: D6 and D11 are proposed, not accepted; the
legal assessment (D10) has no verifiable mechanism, so a stated reference is
recorded as stated; a trust root other than the engine's pinned one is a
blocker; and release integration does not exist. ``authorized`` and
``release_eligible`` are always ``False``.

A bot key cannot approve anything: the trust root lists it only for the
release and publication namespaces, so its signature in the policy namespace
is refused by role, and a release-namespace signature is refused by namespace.

:func:`authorization_gate` sets an FC7 decision, rebuilt from the evidence,
beside this verdict. It changes neither and makes nothing eligible.
"""

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.document_models import text_digest
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization_models import (
    ApprovalRevocation,
    ApprovalScope,
    AuthorizationGate,
    AuthorizationStatus,
    AutomatedPublicationApproval,
    CheckStatus,
    PolicyApproval,
    PolicyAuthorization,
    RecordCheck,
    RecordKind,
    SignatureEvidence,
)
from lovspor.forarbeider.policy_evaluation import decision_text, evaluate_policy
from lovspor.forarbeider.policy_trust import (
    POLICY_NAMESPACE,
    SignatureCheck,
    SignedBytes,
    TrustRoot,
    check_signature,
)
from lovspor.forarbeider.publication_policy import PublicationPolicy, parse_policy, policy_digest
from lovspor.forarbeider.version_manifest import Evidence as SourceEvidence
from lovspor.forarbeider.version_models import canonical_text

VERIFIER_VERSION = "policy-auth/1"
#: Blockers no signature can lift: they need owner decisions and later slices.
ALWAYS_BLOCKING = (
    "d6_signatures_not_accepted",
    "d11_automated_publication_not_accepted",
    "d10_legal_assessment_not_verifiable",
    "release_integration_not_implemented",
)


class SignedRecord(NamedTuple):
    """A record's exact bytes and, if offered, its detached signature."""

    record: bytes
    signature: bytes | None


@dataclass(frozen=True)
class AuthorizationRequest:
    """Everything a caller offers; none of it is trusted until verified."""

    policy: bytes
    checked_at: datetime
    policy_approval: SignedRecord | None = None
    automated_publication: SignedRecord | None = None
    revocations: tuple[SignedRecord, ...] = field(default=())


class _Context(NamedTuple):
    root: TrustRoot
    policy: PublicationPolicy
    at: datetime


_Signed = PolicyApproval | AutomatedPublicationApproval | ApprovalRevocation


class _Checked(NamedTuple):
    check: RecordCheck
    record: _Signed | None


class _Found(NamedTuple):
    record_id: str | None = None
    signature: SignatureEvidence | None = None


_NOTHING_FOUND = _Found()


def verify_authorization(request: AuthorizationRequest, root: TrustRoot) -> PolicyAuthorization:
    """The authorization verdict for the policy in ``request``; never an authorization.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid``: the policy bytes are not a
            canonical policy; ``invalid_timestamp``: the check time is not UTC.
    """
    ctx = _Context(root, parse_policy(request.policy), _utc(request.checked_at))
    revocations = tuple(_revocation(ctx, item) for item in request.revocations)
    revoked = _revoked_ids(revocations)
    approval = _approval(ctx, request.policy_approval, revoked)
    automated = _automated(ctx, request, approval, revoked)
    return _verdict(ctx, (approval.check, automated.check), revocations, automated.record)


def authorization_text(authorization: PolicyAuthorization) -> str:
    return canonical_text(authorization)


def parse_authorization(raw: bytes) -> PolicyAuthorization:
    """The verdict in ``raw``, which must be its canonical bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` for anything else, including
            a record claiming authorization or eligibility.
    """
    try:
        authorization = PolicyAuthorization.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError) as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization") from error
    if authorization_text(authorization).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization")
    return authorization


def verify_authorization_record(
    raw: bytes, request: AuthorizationRequest, root: TrustRoot
) -> PolicyAuthorization:
    """The verdict in ``raw``, when ``request`` and ``root`` reproduce exactly those bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` when they do not.
    """
    authorization = parse_authorization(raw)
    if authorization_text(verify_authorization(request, root)).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "authorization not reproduced")
    return authorization


def authorization_gate(
    evidence: SourceEvidence, request: AuthorizationRequest, root: TrustRoot
) -> AuthorizationGate:
    """The FC7 decision for ``evidence`` beside the authorization verdict; nothing eligible."""
    decision = evaluate_policy(evidence, parse_policy(request.policy))
    authorization = verify_authorization(request, root)
    return AuthorizationGate(
        document_id=decision.document_id,
        version_id=decision.version_id,
        policy_sha256=decision.policy_sha256,
        policy_decision_sha256=text_digest(decision_text(decision)),
        authorization_sha256=text_digest(authorization_text(authorization)),
        rules_satisfied=decision.document.rules_satisfied,
        policy_blockers=decision.document.blockers,
        authorization_status=authorization.status,
        authorization_blockers=authorization.blockers,
    )


def _utc(at: datetime) -> datetime:
    offset = at.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "check time must be UTC")
    return at


# --- one signed record ---------------------------------------------------------------


def _signed(
    ctx: _Context, kind: RecordKind, offered: SignedRecord | None, model: type[_Signed]
) -> _Checked:
    """Parse and verify one record's signature; scope is checked by the caller."""
    if offered is None:
        return _Checked(_check(kind, "missing", ("record_missing",)), None)
    record, refusal = _parsed(model, offered.record)
    if record is None:
        return _Checked(_check(kind, "invalid", (refusal,)), None)
    if offered.signature is None:
        return _Checked(_check(kind, "missing", ("signature_missing",), _Found(_id(record))), None)
    signed = SignedBytes(offered.record, offered.signature)
    found = check_signature(ctx.root, signed, POLICY_NAMESPACE, record.signed_at)
    proof = _Found(_id(record), _evidence(signed, found))
    if not found.verified:
        return _Checked(_check(kind, "invalid", (found.reason,), proof), None)
    return _Checked(_check(kind, "verified", ("signature_verified",), proof), record)


def _parsed(model: type[_Signed], raw: bytes) -> tuple[_Signed | None, str]:
    try:
        record = model.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError):
        return None, "record_malformed"
    if canonical_text(record).encode() != raw:
        return None, "record_noncanonical"
    return record, ""


def _id(record: _Signed) -> str:
    return record.revocation_id if isinstance(record, ApprovalRevocation) else record.approval_id


def _check(
    kind: RecordKind, status: CheckStatus, reasons: tuple[str, ...], found: _Found = _NOTHING_FOUND
) -> RecordCheck:
    return RecordCheck(
        kind=kind,
        status=status,
        record_id=found.record_id,
        reasons=reasons,
        signature=found.signature,
    )


def _evidence(signed: SignedBytes, found: SignatureCheck) -> SignatureEvidence:
    entry = found.entry
    return SignatureEvidence(
        record_sha256=sha256(signed.message).hexdigest(),
        signature_sha256=sha256(signed.signature).hexdigest(),
        namespace=found.namespace or POLICY_NAMESPACE,
        signature_verified=found.verified,
        reason=found.reason,
        principal=entry.principal if entry else None,
        role=entry.role if entry else None,
        key_fingerprint=entry.fingerprint if entry else None,
    )


def _rejected(checked: _Checked, reasons: list[str]) -> _Checked:
    """The verified ``checked`` downgraded to invalid by scope or time ``reasons``."""
    if not reasons or checked.check.status != "verified":
        return checked
    check = checked.check.model_copy(update={"status": "invalid", "reasons": tuple(reasons)})
    return _Checked(check, None)


# --- approvals ------------------------------------------------------------------------


def _approval(ctx: _Context, offered: SignedRecord | None, revoked: frozenset[str]) -> _Checked:
    checked = _signed(ctx, "policy_approval", offered, PolicyApproval)
    record = checked.record
    if not isinstance(record, PolicyApproval):
        return checked
    return _rejected(checked, _approval_reasons(ctx, record, revoked))


def _automated(
    ctx: _Context, request: AuthorizationRequest, approval: _Checked, revoked: frozenset[str]
) -> _Checked:
    offered = request.automated_publication
    checked = _signed(ctx, "automated_publication_approval", offered, AutomatedPublicationApproval)
    record = checked.record
    if not isinstance(record, AutomatedPublicationApproval):
        return checked
    reasons = _approval_reasons(ctx, record, revoked)
    reasons += _binding_reasons(record, request.policy_approval, approval.check)
    if reasons == ["bound_policy_approval_missing"]:
        check = checked.check.model_copy(update={"status": "missing", "reasons": tuple(reasons)})
        return _Checked(check, None)
    return _rejected(checked, reasons)


def _approval_reasons(
    ctx: _Context, record: PolicyApproval | AutomatedPublicationApproval, revoked: frozenset[str]
) -> list[str]:
    reasons = _scope_reasons(record.scope, ctx.policy)
    if record.signed_at > ctx.at:
        reasons.append("signed_after_check_time")
    if ctx.at < record.valid_from:
        reasons.append("approval_not_yet_valid")
    if ctx.at >= record.valid_until:
        reasons.append("approval_expired")
    if record.approval_id in revoked:
        reasons.append("approval_revoked")
    return reasons


def _scope_reasons(scope: ApprovalScope, policy: PublicationPolicy) -> list[str]:
    reasons = []
    if scope.policy_id != policy.policy_id:
        reasons.append("policy_id_mismatch")
    if scope.policy_version != policy.policy_version:
        reasons.append("policy_version_mismatch")
    if scope.policy_sha256 != policy_digest(policy):
        reasons.append("policy_digest_mismatch")
    if (scope.source_provider, scope.document_types) != (
        policy.source_provider,
        policy.document_types,
    ):
        reasons.append("policy_scope_mismatch")
    return reasons


def _binding_reasons(
    record: AutomatedPublicationApproval, bound: SignedRecord | None, check: RecordCheck
) -> list[str]:
    if bound is None:
        return ["bound_policy_approval_missing"]
    reasons = []
    if record.policy_approval_sha256 != sha256(bound.record).hexdigest():
        reasons.append("policy_approval_binding_mismatch")
    if check.status != "verified":
        reasons.append("bound_policy_approval_not_verified")
    return reasons


# --- revocations ----------------------------------------------------------------------


def _revocation(ctx: _Context, offered: SignedRecord) -> _Checked:
    checked = _signed(ctx, "revocation", offered, ApprovalRevocation)
    record = checked.record
    if not isinstance(record, ApprovalRevocation):
        return checked
    late = ["signed_after_check_time"] if record.signed_at > ctx.at else []
    return _rejected(checked, late)


def _revoked_ids(revocations: tuple[_Checked, ...]) -> frozenset[str]:
    ids: set[str] = set()
    for item in revocations:
        if isinstance(item.record, ApprovalRevocation):
            ids.update(item.record.revokes)
    return frozenset(ids)


# --- verdict --------------------------------------------------------------------------


def _verdict(
    ctx: _Context,
    checks: tuple[RecordCheck, RecordCheck],
    revocations: tuple[_Checked, ...],
    automated: _Signed | None,
) -> PolicyAuthorization:
    every = (*checks, *(item.check for item in revocations))
    reference = _reference(automated)
    return PolicyAuthorization(
        policy_id=ctx.policy.policy_id,
        policy_version=ctx.policy.policy_version,
        policy_sha256=policy_digest(ctx.policy),
        checked_at=ctx.at,
        trust_root_sha256=ctx.root.sha256,
        trust_root_origin=ctx.root.origin,
        trust_root_signers=len(ctx.root.entries),
        policy_approval=checks[0],
        automated_publication=checks[1],
        revocations=tuple(item.check for item in revocations),
        legal_assessment_reference_stated=reference,
        status=_status(every),
        blockers=_blockers(ctx.root, checks, every, reference),
    )


def _reference(automated: _Signed | None) -> str | None:
    if isinstance(automated, AutomatedPublicationApproval):
        return automated.legal_assessment_reference
    return None


def _status(checks: tuple[RecordCheck, ...]) -> AuthorizationStatus:
    statuses = {check.status for check in checks}
    if "invalid" in statuses:
        return "invalid"
    if "missing" in statuses:
        return "missing_evidence"
    return "signatures_verified"


def _blockers(
    root: TrustRoot,
    checks: tuple[RecordCheck, RecordCheck],
    every: tuple[RecordCheck, ...],
    reference: str | None,
) -> tuple[str, ...]:
    found = list(ALWAYS_BLOCKING)
    if root.origin != "engine_pinned":
        found.append("trust_root_not_engine_pinned")
    found += [f"{c.kind}_{c.status}" for c in checks if c.status != "verified"]
    if any(c.kind == "revocation" and c.status != "verified" for c in every):
        found.append("revocation_not_verified")
    if checks[1].status == "verified" and reference is None:
        found.append("legal_assessment_reference_missing")
    return tuple(found)
