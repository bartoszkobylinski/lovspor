"""The private batch plan, version 1 (#738, contract Amendment 1 points 2, 3, 4, 7).

A batch plan lists, for at most 25 propositions, every staged chapter, its
class and either the clearance a later, separately confirmed release could
carry or the reasons it cannot. It is a plan only: ``publication_authorized``,
``writes_corpus`` and ``applies`` are ``False`` by type, the owner's batch
confirmation is ``required_not_given``, and nothing in this slice applies it.

The plan's sample is a deterministic C3 spot check of policy-cleared chapters
(ADR-0018 Amendment 1 §5), seeded by the inputs digest: it is listed for a
person to read, and ``sample_is_gate`` says it decides nothing. The validators
recompute the sample, the counts and the coverage, so an edited plan is not a
valid one.

The request is the operator's private file and holds paths; the plan holds
none of them, only ids, digests, codes and public source URLs.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from lovspor.forarbeider.batch_classify import WITHHELD, ChapterClass, needs_review
from lovspor.forarbeider.corpus_models import ChapterClearance, PolicyClearance, ReviewClearance
from lovspor.forarbeider.policy_models import PolicyFault
from lovspor.forarbeider.policy_source import SourceFault
from lovspor.forarbeider.release_models import (
    SHA256,
    Coverage,
    DenyReason,
    DocumentIdentity,
    destination_of,
)

MAX_DOCUMENTS = 25
SAMPLE_SIZE = 5
TARGET_BRANCH = re.compile(r"forarbeider/[a-z0-9][a-z0-9-]{0,62}")

_Digest = Annotated[str, Field(pattern=SHA256)]
_FROZEN = ConfigDict(frozen=True, extra="forbid")


class BatchReason(StrEnum):
    """Why a chapter is excluded that no preflight, policy or source fault states."""

    PAGE_NOT_FETCHED = "page_not_fetched"
    POLICY_SCOPE_MISMATCH = "policy_scope_mismatch"
    PREFLIGHT_CHANGED = "preflight_changed"
    PREFLIGHT_MISSING = "preflight_missing"
    PREFLIGHT_NOT_ELIGIBLE = "preflight_not_eligible"
    REVIEW_VETO = "review_veto"
    WITHHELD = "withheld"


ExclusionReason = DenyReason | PolicyFault | SourceFault | BatchReason


class ReviewRequest(BaseModel):
    """The eligible preflight report the owner ran for a document's flagged chapters."""

    model_config = _FROZEN

    report: _Digest
    checkpoints: tuple[str, ...]


class DocumentRequest(BaseModel):
    """One proposition's staged private files, as a preflight takes them."""

    model_config = _FROZEN

    metadata: Path
    observations: tuple[Path, ...] = Field(min_length=1)
    pages: tuple[Path, ...]
    review: ReviewRequest | None = None


class BatchRequest(BaseModel):
    """The operator's private request: never stored, its paths never copied."""

    model_config = _FROZEN

    schema_version: Literal[1]
    record_type: Literal["forarbeider_batch_request"]
    target_branch: str
    documents: tuple[DocumentRequest, ...]


class ChapterInput(BaseModel):
    model_config = _FROZEN

    result_sha256: _Digest
    page_sha256: _Digest | None
    review_revision: int
    review_head_sha256: _Digest | None


class DocumentInput(BaseModel):
    model_config = _FROZEN

    document_id: str
    metadata_sha256: _Digest
    metadata_page_sha256: _Digest | None
    chapters: tuple[ChapterInput, ...]
    report_sha256: _Digest | None
    checkpoints: tuple[str, ...]


class BatchInputs(BaseModel):
    """What the inputs digest covers: every staged byte and named record, by digest."""

    model_config = _FROZEN

    at: AwareDatetime
    target_branch: str
    policy_sha256: _Digest
    source_proof_sha256: _Digest
    documents: tuple[DocumentInput, ...]


def _sorted_once(reasons: tuple[str, ...]) -> tuple[str, ...]:
    if list(reasons) != sorted(set(reasons)):
        raise ValueError("reasons must be sorted, once each")
    return reasons


class PlannedChapter(BaseModel):
    """One staged chapter: its identity, class, and a clearance or the reasons against it."""

    model_config = _FROZEN

    chapter_number: int = Field(ge=1)
    chapter_id: str
    source_page_url: str
    source_sha256: _Digest
    text_sha256: _Digest | None
    extractor_version: Literal[1]
    normalizer_version: Literal[1]
    hold_reasons: tuple[str, ...]
    classification: ChapterClass
    included: bool
    clearance: ChapterClearance | None
    reasons: tuple[ExclusionReason, ...]

    @field_validator("reasons")
    @classmethod
    def _ordered(cls, value: tuple[ExclusionReason, ...]) -> tuple[ExclusionReason, ...]:
        _sorted_once(tuple(str(item) for item in value))
        return value

    @model_validator(mode="after")
    def _one_path(self) -> Self:
        if self.included != (self.clearance is not None) or self.included == bool(self.reasons):
            raise ValueError("a chapter is included exactly when it has a clearance and no reason")
        if self.included and not _clearance_fits(self.classification, self.clearance):
            raise ValueError("a clearance must be the one path this class may take")
        if self.included and (self.text_sha256 is None or WITHHELD & set(self.hold_reasons)):
            raise ValueError("a withheld chapter is never included")
        return self


def _clearance_fits(kind: ChapterClass, clearance: ChapterClearance | None) -> bool:
    if needs_review(kind):
        return isinstance(clearance, ReviewClearance) and clearance.provenance == "recorded"
    return kind is ChapterClass.CLEAN and isinstance(clearance, PolicyClearance)


def coverage_of(listed: Sequence[int], chapters: Sequence[PlannedChapter]) -> Coverage:
    included = [item.chapter_number for item in chapters if item.included]
    if not included:
        return "none"
    return "every_listed_chapter" if included == list(listed) else "partial_curated_excerpt"


class PlannedDocument(BaseModel):
    """One proposition: where a release could write it and what each chapter would carry."""

    model_config = _FROZEN

    document: DocumentIdentity
    destination: str
    listed_chapters: tuple[int, ...]
    report_sha256: _Digest | None
    chapters: tuple[PlannedChapter, ...] = Field(min_length=1)
    coverage: Coverage

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.destination != destination_of(self.document):
            raise ValueError("destination is not this document's scoped path")
        numbers = [item.chapter_number for item in self.chapters]
        if numbers != sorted(set(numbers)):
            raise ValueError("chapters must be in source order, once each")
        prefix = f"{self.document.id}-ch"
        if any(not item.chapter_id.startswith(prefix) for item in self.chapters):
            raise ValueError("a chapter belongs to another document")
        if self.coverage != coverage_of(self.listed_chapters, self.chapters):
            raise ValueError("coverage does not match the included chapters")
        return self


class SampleEntry(BaseModel):
    model_config = _FROZEN

    chapter_id: str
    source_page_url: str
    key_sha256: _Digest


class BatchCounts(BaseModel):
    model_config = _FROZEN

    documents: int
    chapters: int
    standing_policy: int
    owner_review: int
    excluded: int


def sample_of(documents: Sequence[PlannedDocument], seed: str) -> tuple[SampleEntry, ...]:
    """The lowest keys among policy-cleared chapters, key ``sha256(seed \\n chapter_id)``."""
    entries = [
        SampleEntry(
            chapter_id=chapter.chapter_id,
            source_page_url=chapter.source_page_url,
            key_sha256=hashlib.sha256(f"{seed}\n{chapter.chapter_id}".encode()).hexdigest(),
        )
        for document in documents
        for chapter in document.chapters
        if isinstance(chapter.clearance, PolicyClearance)
    ]
    return tuple(sorted(entries, key=lambda entry: entry.key_sha256)[:SAMPLE_SIZE])


def counts_of(documents: Sequence[PlannedDocument]) -> BatchCounts:
    chapters = [chapter for document in documents for chapter in document.chapters]
    clearances = [chapter.clearance for chapter in chapters]
    return BatchCounts(
        documents=len(documents),
        chapters=len(chapters),
        standing_policy=sum(isinstance(item, PolicyClearance) for item in clearances),
        owner_review=sum(isinstance(item, ReviewClearance) for item in clearances),
        excluded=sum(item is None for item in clearances),
    )


class BatchPlan(BaseModel):
    """A private plan of at most 25 propositions. It authorizes and applies nothing."""

    model_config = _FROZEN

    schema_version: Literal[1]
    record_type: Literal["forarbeider_batch_plan"]
    inputs_sha256: _Digest
    at: AwareDatetime
    target_branch: str
    policy_sha256: _Digest
    source_proof_sha256: _Digest
    documents: tuple[PlannedDocument, ...] = Field(min_length=1, max_length=MAX_DOCUMENTS)
    sample: tuple[SampleEntry, ...]
    counts: BatchCounts
    publication_authorized: Literal[False]
    writes_corpus: Literal[False]
    applies: Literal[False]
    owner_confirmation: Literal["required_not_given"]
    legal_sufficiency_decided_by_code: Literal[False]
    sample_is_gate: Literal[False]

    @field_validator("at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if not valid_branch(self.target_branch):
            raise ValueError("target branch is outside the forarbeider/ grammar")
        _require_distinct(self.documents)
        if self.sample != sample_of(self.documents, self.inputs_sha256):
            raise ValueError("the sample is not the one the inputs digest selects")
        if self.counts != counts_of(self.documents):
            raise ValueError("the counts are not the plan's")
        return self


def valid_branch(branch: str) -> bool:
    return TARGET_BRANCH.fullmatch(branch) is not None


def _require_distinct(documents: Sequence[PlannedDocument]) -> None:
    ids = [item.document.id for item in documents]
    if ids != sorted(set(ids)):
        raise ValueError("documents must be sorted by id, once each")
    sources = {item.document.source_document_id for item in documents}
    destinations = {item.destination for item in documents}
    if len(sources) != len(ids) or len(destinations) != len(ids):
        raise ValueError("a document is planned twice")
    chapters = [chapter.chapter_id for item in documents for chapter in item.chapters]
    if len(set(chapters)) != len(chapters):
        raise ValueError("a chapter is planned twice")
