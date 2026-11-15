"""FC7b records: owner-signed approvals and the authorization verdict (#738; D6, D11).

Three record kinds are signed, each in the owner's policy namespace
(``lovspor-forarbeider-policy``) and each naming its own ``domain``, so the
bytes of one kind never verify as another and a bot key, listed only for the
release and publication namespaces, can sign none of them:

- ``PolicyApproval``: binds one policy by id, version and SHA-256 digest, its
  source and document types, a validity window and the time it states it was
  signed;
- ``AutomatedPublicationApproval`` (D11): binds the same policy *and* the exact
  bytes of one policy approval, and may name a legal-assessment reference. The
  reference is text the signer typed; nothing verifies it, so it is recorded
  as stated and never as a completed D10 assessment;
- ``ApprovalRevocation``: withdraws approvals by id.

None of them has a field for an acceptance, a status, an eligibility or a
legal conclusion; extra fields are refused.

``PolicyAuthorization`` is the verifier's output. ``authorized`` and
``release_eligible`` have the single value ``False``: D6 and D11 are
proposed, not accepted, D10 has no verifiable mechanism, and release
integration does not exist. A record that says otherwise does not parse.
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from lovspor.forarbeider.identity import DocumentType
from lovspor.forarbeider.policy_trust import Origin, Role


def _utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("times are stated in UTC")
    return value


Instant = Annotated[AwareDatetime, AfterValidator(_utc)]
_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Id = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")]
_Text = Annotated[str, Field(pattern=r"\S", max_length=500)]
CheckStatus = Literal["missing", "invalid", "verified"]
RecordKind = Literal["policy_approval", "automated_publication_approval", "revocation"]
AuthorizationStatus = Literal["missing_evidence", "invalid", "signatures_verified"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ApprovalScope(_Frozen):
    """The one policy an approval names, and the source and families it covers."""

    policy_id: _Id
    policy_version: int = Field(ge=1)
    policy_sha256: _Digest
    source_provider: Literal["regjeringen.no"] = "regjeringen.no"
    document_types: tuple[DocumentType, ...] = Field(min_length=1)


class _Window(_Frozen):
    signed_at: Instant
    valid_from: Instant
    valid_until: Instant

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.valid_from < self.valid_until:
            raise ValueError("valid_from must precede valid_until")
        if self.signed_at >= self.valid_until:
            raise ValueError("an approval cannot be signed after it expires")
        return self


class PolicyApproval(_Window):
    """The owner's approval of one exact policy (signed; D6)."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_policy_approval"] = "forarbeider_policy_approval"
    domain: Literal["lovspor.forarbeider.policy-approval.v1"] = (
        "lovspor.forarbeider.policy-approval.v1"
    )
    approval_id: _Id
    scope: ApprovalScope


class AutomatedPublicationApproval(_Window):
    """The owner's approval of automated publication under one approved policy (signed; D11)."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_automated_publication_approval"] = (
        "forarbeider_automated_publication_approval"
    )
    domain: Literal["lovspor.forarbeider.automated-publication-approval.v1"] = (
        "lovspor.forarbeider.automated-publication-approval.v1"
    )
    approval_id: _Id
    scope: ApprovalScope
    policy_approval_sha256: _Digest
    automated_publication: Literal[True] = True
    max_documents_per_batch: int = Field(ge=1, le=100)
    legal_assessment_reference: _Text | None


class ApprovalRevocation(_Frozen):
    """The owner's withdrawal of approvals by id (signed)."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_approval_revocation"] = "forarbeider_approval_revocation"
    domain: Literal["lovspor.forarbeider.approval-revocation.v1"] = (
        "lovspor.forarbeider.approval-revocation.v1"
    )
    revocation_id: _Id
    revokes: tuple[_Id, ...] = Field(min_length=1)
    signed_at: Instant


class SignatureEvidence(_Frozen):
    """What one signature check found.

    ``signature_verified`` means only that a key the trust root lists for this
    namespace signed these exact bytes at the stated time. It does not say who
    held the key, and it is no legal clearance.
    """

    record_sha256: _Digest
    signature_sha256: _Digest | None
    namespace: str
    signature_verified: bool
    reason: str
    principal: str | None
    role: Role | None
    key_fingerprint: str | None


class RecordCheck(_Frozen):
    """One signed record's verdict: missing, invalid or verified, and every reason."""

    kind: RecordKind
    status: CheckStatus
    record_id: str | None
    reasons: tuple[str, ...] = Field(min_length=1)
    signature: SignatureEvidence | None


class Governance(_Frozen):
    """The owner decisions as the engine knows them; none is accepted."""

    d6_signatures: Literal["proposed_not_accepted"] = "proposed_not_accepted"
    d10_legal_assessment: Literal["unresolved_not_verifiable"] = "unresolved_not_verifiable"
    d11_automated_publication: Literal["proposed_not_accepted"] = "proposed_not_accepted"


class PolicyAuthorization(_Frozen):
    """The verified state of one policy's authorization; never an authorization itself."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_policy_authorization"] = "forarbeider_policy_authorization"
    verifier_version: Literal["policy-auth/1"] = "policy-auth/1"
    policy_id: str
    policy_version: int = Field(ge=1)
    policy_sha256: _Digest
    policy_status: Literal["proposed"] = "proposed"
    checked_at: Instant
    trust_root_sha256: _Digest
    trust_root_origin: Origin
    trust_root_signers: int = Field(ge=0)
    signature_meaning: Literal["listed_key_signed_exact_bytes_only"] = (
        "listed_key_signed_exact_bytes_only"
    )
    policy_approval: RecordCheck
    automated_publication: RecordCheck
    revocations: tuple[RecordCheck, ...]
    legal_assessment: Literal["not_verifiable"] = "not_verifiable"
    legal_assessment_reference_stated: str | None
    governance: Governance = Governance()
    status: AuthorizationStatus
    blockers: tuple[str, ...] = Field(min_length=1)
    authorized: Literal[False] = False
    release_eligible: Literal[False] = False


class AuthorizationGate(_Frozen):
    """An FC7 decision beside its authorization verdict; neither is changed, nothing is eligible."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_authorization_gate"] = "forarbeider_authorization_gate"
    document_id: str
    version_id: _Digest
    policy_sha256: _Digest
    policy_decision_sha256: _Digest
    authorization_sha256: _Digest
    rules_satisfied: bool
    policy_blockers: tuple[str, ...]
    authorization_status: AuthorizationStatus
    authorization_blockers: tuple[str, ...] = Field(min_length=1)
    release_eligible: Literal[False] = False
    published: Literal[False] = False
