"""Release preflight records, version 1 (#738, ADR-0018 PR3B-1).

Nothing here can publish. A dossier lists private evidence files by digest, an
authorization is the owner's private declaration about exact chapters, and a
report is a plan: ``publication_authorized``, ``writes_corpus`` and
``asserts_complete_proposition`` are ``False`` by type, whatever the outcome.
Code checks that evidence exists, is bound to a chapter and has the bytes it
declares; whether it is legally sufficient is a person's judgement, which
``legal_sufficiency_decided_by_code`` states on every record.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from lovspor.forarbeider.chapter_models import ChapterResult
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.identity import DocumentType, parse_source_url
from lovspor.forarbeider.models import JSON_LAYOUT, ForarbeidRecord
from lovspor.forarbeider.review_models import Reviewer

SHA256 = r"^[0-9a-f]{64}$"
_Digest = Annotated[str, Field(pattern=SHA256)]


class EvidenceKind(StrEnum):
    PUBLICATION_PURPOSE = "publication_purpose"
    PRIVACY_PROCESSING_BASIS = "privacy_processing_basis"
    PRIVACY_NO_PERSONAL_DATA = "privacy_no_personal_data"
    THIRD_PARTY_SCREENING = "third_party_screening"
    SOURCE_RIGHTS = "source_rights"
    SOURCE_COMPLETENESS = "source_completeness"


class ReleaseGate(StrEnum):
    """What every chapter needs evidence for; privacy takes either finding."""

    PUBLICATION_PURPOSE = "publication_purpose"
    PRIVACY = "privacy"
    THIRD_PARTY_SCREENING = "third_party_screening"
    SOURCE_RIGHTS = "source_rights"
    SOURCE_COMPLETENESS = "source_completeness"


GATE_KINDS: dict[ReleaseGate, frozenset[EvidenceKind]] = {
    ReleaseGate.PUBLICATION_PURPOSE: frozenset({EvidenceKind.PUBLICATION_PURPOSE}),
    ReleaseGate.PRIVACY: frozenset(
        {EvidenceKind.PRIVACY_PROCESSING_BASIS, EvidenceKind.PRIVACY_NO_PERSONAL_DATA}
    ),
    ReleaseGate.THIRD_PARTY_SCREENING: frozenset({EvidenceKind.THIRD_PARTY_SCREENING}),
    ReleaseGate.SOURCE_RIGHTS: frozenset({EvidenceKind.SOURCE_RIGHTS}),
    ReleaseGate.SOURCE_COMPLETENESS: frozenset({EvidenceKind.SOURCE_COMPLETENESS}),
}


class RevocationCondition(StrEnum):
    """When an authorization stops holding. Preflight checks each one."""

    AUTHORIZATION_REVOKED = "authorization_revoked"
    DOSSIER_CHANGED = "dossier_changed"
    REVIEW_SUPERSEDED = "review_superseded"
    SOURCE_CHANGED = "source_changed"
    TEXT_CHANGED = "text_changed"
    VALIDITY_ENDED = "validity_ended"


ALL_CONDITIONS = tuple(RevocationCondition)


class DenyReason(StrEnum):
    AUTHORIZATION_CHAPTER_MISMATCH = "authorization_chapter_mismatch"
    AUTHORIZATION_DOCUMENT_MISMATCH = "authorization_document_mismatch"
    AUTHORIZATION_EXPIRED = "authorization_expired"
    AUTHORIZATION_MISSING = "authorization_missing"
    AUTHORIZATION_NOT_YET_VALID = "authorization_not_yet_valid"
    AUTHORIZATION_REVOKED = "authorization_revoked"
    AUTHORIZED_BEFORE_APPROVAL = "authorized_before_approval"
    AUTHORIZED_BEFORE_DOSSIER = "authorized_before_dossier"
    BODY_WITHHELD = "body_withheld"
    CHAPTER_DUPLICATE = "chapter_duplicate"
    CHAPTER_FOREIGN = "chapter_foreign"
    CHAPTER_NOT_IN_INVENTORY = "chapter_not_in_inventory"
    CHECKPOINT_MISMATCH = "checkpoint_mismatch"
    CHECKPOINT_MISSING = "checkpoint_missing"
    DOSSIER_CHAPTER_MISMATCH = "dossier_chapter_mismatch"
    DOSSIER_CHAPTER_MISSING = "dossier_chapter_missing"
    DOSSIER_DOCUMENT_MISMATCH = "dossier_document_mismatch"
    DOSSIER_MISSING = "dossier_missing"
    EVALUATED_BEFORE_APPROVAL = "evaluated_before_approval"
    EVALUATED_BEFORE_DECISION = "evaluated_before_decision"
    EVIDENCE_DIGEST_MISMATCH = "evidence_digest_mismatch"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    METADATA_NOT_REPRODUCIBLE = "metadata_not_reproducible"
    METADATA_PAGE_MISSING = "metadata_page_missing"
    NOT_AUTHORIZED = "not_authorized"
    OBSERVATION_MISSING = "observation_missing"
    OBSERVATION_NOT_REPRODUCIBLE = "observation_not_reproducible"
    PRIVACY_FINDING_CONTRADICTED = "privacy_finding_contradicted"
    PURPOSE_EVIDENCE_MISSING = "purpose_evidence_missing"
    PURPOSE_NOT_AUTHORIZED = "purpose_not_authorized"
    REVIEW_LEDGER_CORRUPT = "review_ledger_corrupt"
    REVIEW_MISSING = "review_missing"
    REVIEW_NOT_APPROVED = "review_not_approved"
    REVIEW_SUPERSEDED = "review_superseded"
    SOURCE_PAGE_MISSING = "source_page_missing"


class DocumentIdentity(BaseModel):
    """The one proposition every record of a release is about."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^regjeringen-[1-9]\d{0,11}$")
    document_type: DocumentType
    session: str = Field(pattern=r"^\d{4}-\d{4}$")
    proposition_number: int = Field(ge=1)
    source_document_id: int = Field(ge=1)
    source_url: str


def document_of(record: ForarbeidRecord | ChapterResult) -> DocumentIdentity:
    return DocumentIdentity(
        id=record.id.split("-ch")[0],
        document_type=record.document_type,
        session=record.session,
        proposition_number=record.proposition_number,
        source_document_id=record.source_document_id,
        source_url=record.source_url,
    )


def destination_of(document: DocumentIdentity) -> str:
    """The one path a later, separately approved release could write: never the root manifest."""
    return (
        f"forarbeider/{document.session}/{document.document_type}/{document.source_document_id}.md"
    )


class ChapterIdentity(BaseModel):
    """One chapter as observed: page, bytes, text and the code that read them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_id: str
    chapter_number: int = Field(ge=1)
    source_page_url: str
    source_sha256: _Digest
    text_sha256: _Digest
    extractor_version: Literal[1]
    normalizer_version: Literal[1]

    @model_validator(mode="after")
    def _one_chapter(self) -> Self:
        try:
            page = parse_source_url(self.source_page_url)
        except ForarbeiderRefusedError as error:
            raise ValueError(str(error)) from error
        expected = f"regjeringen-{page.document_id}-ch{self.chapter_number}"
        if page.chapter != self.chapter_number or self.chapter_id != expected:
            raise ValueError("identity fields do not state one chapter")
        return self


def chapter_identity(result: ChapterResult) -> ChapterIdentity | None:
    """``None`` for a chapter without a body: there is nothing a release could carry."""
    if result.text_sha256 is None:
        return None
    return ChapterIdentity(
        chapter_id=result.id,
        chapter_number=result.chapter_number,
        source_page_url=result.source_page_url,
        source_sha256=result.source_sha256,
        text_sha256=result.text_sha256,
        extractor_version=result.extractor_version,
        normalizer_version=result.normalizer_version,
    )


class EvidenceArtifact(BaseModel):
    """A private file, by path and digest, and the chapters it speaks for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EvidenceKind
    path: str = Field(pattern=r"^/")
    sha256: _Digest
    size_bytes: int = Field(ge=1)
    chapters: tuple[int, ...] = Field(min_length=1)

    @field_validator("chapters")
    @classmethod
    def _sorted_once(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("chapters must be sorted, once each")
        return value


def _require_ordered_chapters(numbers: tuple[int, ...]) -> None:
    if tuple(sorted(set(numbers))) != numbers:
        raise ValueError("chapters must be in source order, once each")


class ReleaseDossier(BaseModel):
    """The case-specific evidence a reviewer assembled for exact chapters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_release_dossier"]
    document: DocumentIdentity
    chapters: tuple[ChapterIdentity, ...] = Field(min_length=1)
    evidence: tuple[EvidenceArtifact, ...] = Field(min_length=1)
    reviewer: Reviewer
    reviewed_at: AwareDatetime
    legal_sufficiency_decided_by_code: Literal[False]

    @field_validator("reviewed_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _bound(self) -> Self:
        numbers = tuple(chapter.chapter_number for chapter in self.chapters)
        _require_ordered_chapters(numbers)
        _require_one_document(self.document, self.chapters)
        if any(not set(item.chapters) <= set(numbers) for item in self.evidence):
            raise ValueError("evidence names a chapter the dossier does not hold")
        keys = [(item.kind, item.sha256, item.path) for item in self.evidence]
        if keys != sorted(set(keys)):
            raise ValueError("evidence must be sorted, once each")
        return self


def _require_one_document(
    document: DocumentIdentity, chapters: tuple[ChapterIdentity, ...]
) -> None:
    for chapter in chapters:
        if not chapter.chapter_id.startswith(f"{document.id}-ch"):
            raise ValueError("a chapter belongs to another document")
        if not chapter.source_page_url.startswith(document.source_url):
            raise ValueError("a chapter belongs to another document")


class ReviewPin(BaseModel):
    """The PR3A chain head the owner saw: a later decision makes it stale."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_key: _Digest
    revision: int = Field(ge=1)
    head_sha256: _Digest


class AuthorizedChapter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter: ChapterIdentity
    review: ReviewPin


class ReleaseAuthorization(BaseModel):
    """The owner's private candidate decision: a declaration, not proof of who made it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_release_authorization_candidate"]
    document: DocumentIdentity
    chapters: tuple[AuthorizedChapter, ...] = Field(min_length=1)
    dossier_sha256: _Digest
    purpose_sha256: _Digest
    destination: str
    owner: Reviewer
    decided_at: AwareDatetime
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    revocation_conditions: tuple[RevocationCondition, ...]
    grants_publication: Literal[False]
    identity_authenticated: Literal[False]

    @field_validator("decided_at", "valid_from", "valid_until")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _scoped(self) -> Self:
        numbers = tuple(item.chapter.chapter_number for item in self.chapters)
        _require_ordered_chapters(numbers)
        _require_one_document(self.document, tuple(item.chapter for item in self.chapters))
        if self.valid_until <= self.valid_from:
            raise ValueError("validity must end after it starts")
        if self.destination != destination_of(self.document):
            raise ValueError("destination is not this document's scoped path")
        if self.revocation_conditions != ALL_CONDITIONS:
            raise ValueError("every revocation condition applies")
        return self


class ReleaseRevocation(BaseModel):
    """The owner withdrew one authorization. Nothing reinstates it: authorize anew."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_release_revocation"]
    authorization_sha256: _Digest
    owner: Reviewer
    revoked_at: AwareDatetime

    @field_validator("revoked_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class ChapterOutcome(BaseModel):
    """One chapter's verdict, in codes and digests only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_number: int = Field(ge=1)
    chapter_id: str
    source_sha256: _Digest | None
    text_sha256: _Digest | None
    hold_reasons: tuple[str, ...]
    missing_evidence: tuple[ReleaseGate, ...]
    reasons: tuple[DenyReason, ...]
    eligible: bool

    @model_validator(mode="after")
    def _eligible_only_without_reasons(self) -> Self:
        if self.eligible and (self.reasons or self.missing_evidence):
            raise ValueError("an eligible chapter has no reason against it")
        return self


Coverage = Literal["none", "partial_curated_excerpt", "every_listed_chapter"]


class PreflightReport(BaseModel):
    """A private plan. Even ``eligible_preview`` authorizes nothing and writes nothing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_release_preflight"]
    outcome: Literal["deny", "eligible_preview"]
    publication_authorized: Literal[False]
    writes_corpus: Literal[False]
    asserts_complete_proposition: Literal[False]
    legal_sufficiency_decided_by_code: Literal[False]
    checkpoint_basis: Literal["operator_supplied_unauthenticated"]
    evaluated_at: AwareDatetime
    document: DocumentIdentity
    listed_chapters: tuple[int, ...]
    dossier_sha256: _Digest | None
    authorization_sha256: _Digest | None
    coverage: Coverage
    proposed_destination: str | None
    planned_chapters: tuple[int, ...]
    document_reasons: tuple[DenyReason, ...]
    chapters: tuple[ChapterOutcome, ...]

    @field_validator("evaluated_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _plan_only_when_eligible(self) -> Self:
        eligible = tuple(item.chapter_number for item in self.chapters if item.eligible)
        if self.planned_chapters != eligible:
            raise ValueError("the plan is exactly the eligible chapters")
        preview = not self.document_reasons and bool(eligible)
        if (self.outcome == "eligible_preview") != preview:
            raise ValueError("a preview needs eligible chapters and no document reason")
        if (self.proposed_destination is None) == preview:
            raise ValueError("only a preview proposes a destination")
        if (self.coverage == "none") == preview:
            raise ValueError("only a preview covers chapters")
        return self


def record_text(record: BaseModel) -> str:
    """The record as written: sorted keys, two-space indent, UTF-8, one final newline."""
    return json.dumps(record.model_dump(mode="json"), **JSON_LAYOUT) + "\n"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
