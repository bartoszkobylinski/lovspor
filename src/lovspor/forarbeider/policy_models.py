"""The standing release policy, version 1 (#738, contract Amendment 1).

One owner record for one source and the document types it names. It binds,
by digest, the private files the owner decided on: source rights, publication
purpose with the takedown contact, retention and deindexing, the acceptance of
Git-history permanence, the acceptance that a chapter no detector flags is
published without a person reading it, and the robots.txt and terms pages the
owner read. It clears unflagged chapters only, and only through a batch; a
flagged chapter still needs the per-chapter path. It is a declaration, not
proof of who made it: ``identity_authenticated`` is ``False`` by type.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from lovspor.forarbeider.identity import DocumentType
from lovspor.forarbeider.release_models import SHA256
from lovspor.forarbeider.review_models import Reviewer

_Digest = Annotated[str, Field(pattern=SHA256)]


class PolicyEvidenceKind(StrEnum):
    """Every kind a policy binds, once each, in this order."""

    GIT_PERMANENCE_ACCEPTED = "git_permanence_accepted"
    PRIVACY_BASIS_CLEAN = "privacy_basis_clean"
    PUBLICATION_PURPOSE = "publication_purpose"
    RETENTION_DEINDEXING = "retention_deindexing"
    ROBOTS_TXT = "robots_txt"
    SOURCE_RIGHTS = "source_rights"
    TERMS_PAGE = "terms_page"


class PolicyEvidence(BaseModel):
    """A private file, by path and digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PolicyEvidenceKind
    path: str = Field(pattern=r"^/")
    sha256: _Digest
    size_bytes: int = Field(ge=1)


class StandingPolicy(BaseModel):
    """The owner's once-signed decision to release unflagged chapters of one source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_standing_policy"]
    source_provider: Literal["regjeringen.no"]
    document_types: tuple[DocumentType, ...] = Field(min_length=1)
    evidence: tuple[PolicyEvidence, ...]
    owner: Reviewer
    decided_at: AwareDatetime
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    clears: Literal["unflagged_chapters"]
    unflagged_published_unread: Literal[True]
    git_permanence_accepted: Literal[True]
    legal_sufficiency_decided_by_code: Literal[False]
    identity_authenticated: Literal[False]

    @field_validator("decided_at", "valid_from", "valid_until")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("document_types")
    @classmethod
    def _types_once(cls, value: tuple[DocumentType, ...]) -> tuple[DocumentType, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("document types must be sorted, once each")
        return value

    @field_validator("evidence")
    @classmethod
    def _every_kind_once(cls, value: tuple[PolicyEvidence, ...]) -> tuple[PolicyEvidence, ...]:
        if tuple(item.kind for item in value) != tuple(PolicyEvidenceKind):
            raise ValueError("evidence must hold every kind, once each, in order")
        return value

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.valid_until <= self.valid_from:
            raise ValueError("validity must end after it starts")
        return self


class PolicyRevocation(BaseModel):
    """The owner withdrew the policy. Nothing reinstates it: sign anew."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_standing_policy_revocation"]
    policy_sha256: _Digest
    owner: Reviewer
    revoked_at: AwareDatetime

    @field_validator("revoked_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class PolicyFault(StrEnum):
    """Why a policy does not hold at a moment. Any one and it clears nothing."""

    EVALUATED_BEFORE_POLICY = "evaluated_before_policy"
    POLICY_EVIDENCE_CHANGED = "policy_evidence_changed"
    POLICY_EVIDENCE_UNAVAILABLE = "policy_evidence_unavailable"
    POLICY_EXPIRED = "policy_expired"
    POLICY_NOT_YET_VALID = "policy_not_yet_valid"
    POLICY_REVOKED = "policy_revoked"


def policy_faults(
    policy: StandingPolicy, revoked: bool, evidence: Mapping[str, str | None], at: datetime
) -> tuple[PolicyFault, ...]:
    """Every fault at ``at``; ``evidence`` maps a path to its actual digest, ``None`` if gone."""
    faults = _window_faults(policy, at)
    if revoked:
        faults.add(PolicyFault.POLICY_REVOKED)
    for item in policy.evidence:
        actual = evidence.get(item.path)
        if actual is None:
            faults.add(PolicyFault.POLICY_EVIDENCE_UNAVAILABLE)
        elif actual != item.sha256:
            faults.add(PolicyFault.POLICY_EVIDENCE_CHANGED)
    return tuple(sorted(faults))


def _window_faults(policy: StandingPolicy, at: datetime) -> set[PolicyFault]:
    faults: set[PolicyFault] = set()
    if at < policy.decided_at:
        faults.add(PolicyFault.EVALUATED_BEFORE_POLICY)
    if at < policy.valid_from:
        faults.add(PolicyFault.POLICY_NOT_YET_VALID)
    if at >= policy.valid_until:
        faults.add(PolicyFault.POLICY_EXPIRED)
    return faults
