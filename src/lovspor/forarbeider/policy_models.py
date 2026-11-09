"""The standing release policy, version 1 (#738, contract Amendment 1).

One owner record for one source and the document types it names. It binds,
by digest, the private files the owner decided on, one file per kind: source
rights, the personal-data assessment, publication purpose with the takedown
contact, retention and deindexing, the acceptance of Git-history permanence,
the acceptance that a chapter no detector flags is published without a person
reading it, and the robots.txt and terms pages the owner read. An acceptance
is the owner's file, never a value set in code. It clears unflagged chapters
only, and only through a batch; a flagged chapter still needs the per-chapter
path. It is a declaration, not proof of who made it: ``identity_authenticated``
is ``False`` by type.

At most one policy is effective for a source: two unrevoked policies whose
types intersect and whose windows overlap deny every evaluation of that source,
and a revocation recorded after a policy was decided suppresses every
overlapping policy, so revoking one never brings another back.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from itertools import combinations
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
    UNFLAGGED_UNREAD_ACCEPTANCE = "unflagged_unread_acceptance"


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
        if len({item.sha256 for item in value}) != len(value):
            raise ValueError("evidence binds one file for two kinds")
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
    POLICY_AMBIGUOUS = "policy_ambiguous"
    POLICY_EVIDENCE_CHANGED = "policy_evidence_changed"
    POLICY_EVIDENCE_UNAVAILABLE = "policy_evidence_unavailable"
    POLICY_EXPIRED = "policy_expired"
    POLICY_NOT_YET_VALID = "policy_not_yet_valid"
    POLICY_OVERLAPS_REVOKED = "policy_overlaps_revoked"
    POLICY_REVOKED = "policy_revoked"


class RegisteredPolicy(BaseModel):
    """A stored policy, by digest, and its revocation if it has one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: _Digest
    policy: StandingPolicy
    revocation: PolicyRevocation | None


class PolicyObservation(BaseModel):
    """What an evaluation read: each evidence path's digest now, and every stored policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: dict[str, str | None]
    policies: tuple[RegisteredPolicy, ...]


def overlaps(first: StandingPolicy, second: StandingPolicy) -> bool:
    """Same source, a document type in common and a moment both windows hold."""
    return (
        first.source_provider == second.source_provider
        and not set(first.document_types).isdisjoint(second.document_types)
        and first.valid_from < second.valid_until
        and second.valid_from < first.valid_until
    )


def policy_faults(
    entry: RegisteredPolicy, observed: PolicyObservation, at: datetime
) -> tuple[PolicyFault, ...]:
    """Every fault ``entry`` has at ``at``, given what ``observed`` read."""
    faults = _window_faults(entry.policy, at) | _register_faults(entry, observed.policies)
    if entry.revocation is not None:
        faults.add(PolicyFault.POLICY_REVOKED)
    faults |= _evidence_faults(entry.policy, observed)
    return tuple(sorted(faults))


def _evidence_faults(policy: StandingPolicy, observed: PolicyObservation) -> set[PolicyFault]:
    faults: set[PolicyFault] = set()
    for item in policy.evidence:
        actual = observed.evidence.get(item.path)
        if actual is None:
            faults.add(PolicyFault.POLICY_EVIDENCE_UNAVAILABLE)
        elif actual != item.sha256:
            faults.add(PolicyFault.POLICY_EVIDENCE_CHANGED)
    return faults


def _register_faults(
    entry: RegisteredPolicy, register: Iterable[RegisteredPolicy]
) -> set[PolicyFault]:
    """Ambiguity anywhere in the source, and a later revocation of an overlapping policy."""
    others = {item.sha256: item for item in register if item.sha256 != entry.sha256}
    faults: set[PolicyFault] = set()
    live = [item.policy for item in (entry, *others.values()) if item.revocation is None]
    source = [item for item in live if item.source_provider == entry.policy.source_provider]
    if any(overlaps(first, second) for first, second in combinations(source, 2)):
        faults.add(PolicyFault.POLICY_AMBIGUOUS)
    if any(suppresses(item, entry.policy) for item in others.values()):
        faults.add(PolicyFault.POLICY_OVERLAPS_REVOKED)
    return faults


def suppresses(other: RegisteredPolicy, policy: StandingPolicy) -> bool:
    """A revocation of an overlapping policy recorded after ``policy`` was decided.

    Sign refuses while an overlapping policy is unrevoked, so a policy decided
    before such a revocation was never signed through the supported path: it is
    a second record placed by hand, or an older one a newer revocation retires.
    """
    revocation = other.revocation
    return (
        revocation is not None
        and overlaps(other.policy, policy)
        and revocation.revoked_at > policy.decided_at
    )


def _window_faults(policy: StandingPolicy, at: datetime) -> set[PolicyFault]:
    faults: set[PolicyFault] = set()
    if at < policy.decided_at:
        faults.add(PolicyFault.EVALUATED_BEFORE_POLICY)
    if at < policy.valid_from:
        faults.add(PolicyFault.POLICY_NOT_YET_VALID)
    if at >= policy.valid_until:
        faults.add(PolicyFault.POLICY_EXPIRED)
    return faults
