"""Review decisions on staged chapters, version 1 (#738, ADR-0018 PR3A).

A decision is a record about one exact observation, never a change to it: the
:class:`ReviewSubject` names the source bytes, the text and the extractor, so
other bytes are another subject with no decisions at all and an approval cannot
reach them. No field takes free text. Reasons are codes and evidence references
are a closed grammar, because a note beside a chapter is where a name would
land, and ``grants_publication`` and ``grants_crawling`` are ``False`` by type:
an approval records what a reviewer attested and permits nothing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from lovspor.forarbeider.chapter_models import ChapterResult
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.identity import parse_source_url
from lovspor.forarbeider.models import JSON_LAYOUT

_SHA256 = r"^[0-9a-f]{64}$"
EvidenceReference = Annotated[
    str,
    Field(pattern=r"^(adr:\d{4}|(issue|pr):[1-9]\d{0,6}|sha256:[0-9a-f]{64})$"),
]
Reviewer = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,38}$")]


class ReviewStatus(StrEnum):
    PENDING = "pending"
    HELD = "held"
    REJECTED = "rejected"
    APPROVED = "approved"


class ReviewReason(StrEnum):
    ATTESTATIONS_COMPLETE = "attestations_complete"
    AWAITING_EVIDENCE = "awaiting_evidence"
    CONTACT_DETAILS = "contact_details"
    EXTRACTION_DEFECT = "extraction_defect"
    OUT_OF_SCOPE = "out_of_scope"
    PRIVATE_INDIVIDUAL = "private_individual"
    REOPENED = "reopened"
    RIGHTS_UNCERTAIN = "rights_uncertain"
    SOURCE_INCOMPLETE = "source_incomplete"
    SPECIAL_CATEGORY_DATA = "special_category_data"
    THIRD_PARTY_WORK = "third_party_work"
    UNVERIFIED_ALLEGATION = "unverified_allegation"


class AttestationKind(StrEnum):
    PRIVACY_BASIS = "privacy_basis"
    SOURCE_COMPLETENESS = "source_completeness"
    SOURCE_RIGHTS_POLICY = "source_rights_policy"
    THIRD_PARTY_CHECKS = "third_party_checks"


ALL_ATTESTATIONS = tuple(AttestationKind)

_FOLLOWERS: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    ReviewStatus.PENDING: frozenset(
        {ReviewStatus.HELD, ReviewStatus.REJECTED, ReviewStatus.APPROVED}
    ),
    ReviewStatus.HELD: frozenset(ReviewStatus),
    ReviewStatus.REJECTED: frozenset({ReviewStatus.PENDING}),
    ReviewStatus.APPROVED: frozenset(
        {ReviewStatus.PENDING, ReviewStatus.HELD, ReviewStatus.REJECTED}
    ),
}


def may_follow(current: ReviewStatus, following: ReviewStatus) -> bool:
    """Rejected goes back through pending, so a rejection is never approved in one step."""
    return following in _FOLLOWERS[current]


class ReviewSubject(BaseModel):
    """The one observation a decision is about. Any other value is another subject."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_page_url: str
    source_sha256: str = Field(pattern=_SHA256)
    chapter_number: int = Field(ge=1)
    text_sha256: str | None = Field(pattern=_SHA256)
    extractor_version: Literal[1]
    normalizer_version: Literal[1]

    @model_validator(mode="after")
    def _chapter_of_its_page(self) -> Self:
        try:
            page = parse_source_url(self.source_page_url)
        except ForarbeiderRefusedError as error:
            raise ValueError(str(error)) from error
        if page.chapter != self.chapter_number:
            raise ValueError("the page does not state this chapter")
        return self

    @property
    def chapter_id(self) -> str:
        document = parse_source_url(self.source_page_url).document_id
        return f"regjeringen-{document}-ch{self.chapter_number}"


def subject_of(result: ChapterResult) -> ReviewSubject:
    return ReviewSubject(
        source_page_url=result.source_page_url,
        source_sha256=result.source_sha256,
        chapter_number=result.chapter_number,
        text_sha256=result.text_sha256,
        extractor_version=result.extractor_version,
        normalizer_version=result.normalizer_version,
    )


def subject_key(subject: ReviewSubject) -> str:
    """The digest that names a subject's chain and that a reviewer pins."""
    canonical = json.dumps(subject.model_dump(mode="json"), **JSON_LAYOUT)
    return hashlib.sha256(canonical.encode()).hexdigest()


class Attestation(BaseModel):
    """One positive statement by the reviewer, with what supports it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AttestationKind
    attested: Literal[True]
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1)


class ReviewDecision(BaseModel):
    """One immutable revision in a subject's chain of decisions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    subject: ReviewSubject
    revision: int = Field(ge=1)
    previous_decision_sha256: str | None = Field(pattern=_SHA256)
    reviewer: Reviewer
    decided_at: AwareDatetime
    status: ReviewStatus
    reason_codes: tuple[ReviewReason, ...] = Field(min_length=1)
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1)
    attestations: tuple[Attestation, ...]
    grants_publication: Literal[False]
    grants_crawling: Literal[False]

    @field_validator("decided_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("reason_codes", "evidence")
    @classmethod
    def _sorted_once(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("values must be sorted, once each")
        return value

    @model_validator(mode="after")
    def _linked_and_attested(self) -> Self:
        if (self.revision == 1) != (self.previous_decision_sha256 is None):
            raise ValueError("only the first revision has no predecessor")
        kinds = tuple(attestation.kind for attestation in self.attestations)
        if self.status is ReviewStatus.APPROVED:
            self._require_a_complete_approval(kinds)
        elif kinds or ReviewReason.ATTESTATIONS_COMPLETE in self.reason_codes:
            raise ValueError("attestations belong only to an approval")
        return self

    def _require_a_complete_approval(self, kinds: tuple[AttestationKind, ...]) -> None:
        if tuple(sorted(kinds)) != tuple(sorted(ALL_ATTESTATIONS)):
            raise ValueError("an approval needs every attestation, once each")
        if self.reason_codes != (ReviewReason.ATTESTATIONS_COMPLETE,):
            raise ValueError("an approval states that its attestations are complete")
        if self.subject.text_sha256 is None:
            raise ValueError("a chapter without a body cannot be approved")


def review_text(decision: ReviewDecision) -> str:
    """The decision as written: sorted keys, two-space indent, UTF-8, one final newline."""
    return json.dumps(decision.model_dump(mode="json"), **JSON_LAYOUT) + "\n"
