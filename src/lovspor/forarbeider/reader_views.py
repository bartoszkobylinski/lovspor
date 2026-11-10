"""The versioned JSON records the forarbeider reader prints (#738, ADR-0018 PR3B-3).

Every record names itself (``schema_version``, ``record_type``), says it is
synthetic, and carries the notice that a forarbeid is preparatory material,
not enacted law. Only what the dataset itself publishes is shown: identity,
labels, receipts, digests and the separate time axes. No reviewer handle,
dossier, evidence, authorization, raw page or path outside the dataset.
Chapter text appears only in the ``chapter`` record, for the one chapter asked for.

The document, chapter and provenance records are version 2: each receipt
carries the clearance its manifest states (a version 1 manifest is lifted to
``owner_review`` with ``provenance: manifest_v1``), and the times carry the
policy's decision as its own axis.
"""

import json
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider.corpus_models import ChapterReceiptV2
from lovspor.forarbeider.corpus_reader import Member, Shelf
from lovspor.forarbeider.corpus_render import ReleasedChapter, _chapter, reference
from lovspor.forarbeider.models import JSON_LAYOUT
from lovspor.forarbeider.release_models import DocumentIdentity

NOTICE = (
    "Forarbeid: preparatory material, NOT enacted law. It does not state whether the"
    " proposal was adopted, and it is not a source of law in force."
)
SYNTHETIC_NOTICE = (
    "Synthetic sandbox: invented text from the bundled fixtures. This is not a release,"
    " and not approval to publish or host real forarbeider text."
)
_FROZEN = ConfigDict(frozen=True, extra="forbid")


class _Record(BaseModel):
    model_config = _FROZEN

    schema_version: Literal[1] = 1
    synthetic: Literal[True] = True
    notice: str = NOTICE
    synthetic_notice: str = SYNTHETIC_NOTICE


class Labels(BaseModel):
    """The labels every forarbeid carries, fixed by type."""

    model_config = _FROZEN

    source_document_type: Literal["forarbeid"] = "forarbeid"
    normative: Literal[False] = False
    source_provider: Literal["regjeringen.no"] = "regjeringen.no"
    rights_basis: Literal["åndsverkloven § 14"] = "åndsverkloven § 14"
    source_license: None = None
    asserts_complete_proposition: Literal[False] = False


class Times(BaseModel):
    """The axes kept apart: the source's, the operator's (unauthenticated) and Git's."""

    model_config = _FROZEN

    source_published_at: date | None
    reviewed_at: datetime | None
    policy_decided_at: datetime | None
    owner_decided_at: datetime
    released_at: datetime
    time_basis: Literal["operator_supplied_unauthenticated"]
    recorded_at: Literal["git_commit_time"]


class Summary(BaseModel):
    model_config = _FROZEN

    document: DocumentIdentity
    reference: str
    title: str
    release_status: Literal["released"]
    coverage: str
    released_chapters: tuple[int, ...]
    path: str


class ListRecord(_Record):
    record_type: Literal["forarbeider_reader_list"] = "forarbeider_reader_list"
    manifest_sha256: str | None
    documents: tuple[Summary, ...]


class DocumentRecord(_Record):
    schema_version: Literal[2] = 2  # type: ignore[assignment]
    record_type: Literal["forarbeider_reader_document"] = "forarbeider_reader_document"
    manifest_sha256: str | None
    document: DocumentIdentity
    reference: str
    title: str
    ministry: str | None
    labels: Labels
    release_status: Literal["released"]
    coverage: str
    partial_curated_excerpt: bool
    listed_chapters: tuple[int, ...]
    chapters: tuple[ChapterReceiptV2, ...]
    times: Times
    path: str
    markdown_sha256: str | None


class ChapterRecord(_Record):
    schema_version: Literal[2] = 2  # type: ignore[assignment]
    record_type: Literal["forarbeider_reader_chapter"] = "forarbeider_reader_chapter"
    document: DocumentIdentity
    reference: str
    labels: Labels
    coverage: str
    partial_curated_excerpt: bool
    chapter: ChapterReceiptV2
    markdown: str


class ProvenanceRecord(_Record):
    schema_version: Literal[2] = 2  # type: ignore[assignment]
    record_type: Literal["forarbeider_reader_provenance"] = "forarbeider_reader_provenance"
    manifest_sha256: str | None
    document: DocumentIdentity
    labels: Labels
    source_url: str
    chapters: tuple[ChapterReceiptV2, ...]
    times: Times
    path: str
    markdown_sha256: str | None
    renderer_version: Literal[1] = 1
    verification: Literal["synthetic_rerender_byte_identical"] = "synthetic_rerender_byte_identical"


def text_of(record: BaseModel) -> str:
    """A record as printed: sorted keys, two-space indent, UTF-8."""
    return json.dumps(record.model_dump(mode="json"), **JSON_LAYOUT)


def _reference(member: Member) -> str:
    return reference(member.rendering.metadata)


def _times(member: Member) -> Times:
    return Times.model_validate(member.entry.times.model_dump(exclude={"withdrawn_at"}))


def list_record(shelf: Shelf) -> ListRecord:
    summaries = tuple(
        Summary(
            document=member.entry.document,
            reference=_reference(member),
            title=member.entry.title,
            release_status="released",
            coverage=member.entry.coverage,
            released_chapters=tuple(item.chapter_number for item in member.entry.chapters),
            path=member.entry.path,
        )
        for member in shelf.members
    )
    return ListRecord(manifest_sha256=shelf.manifest_sha256, documents=summaries)


def document_record(shelf: Shelf, member: Member) -> DocumentRecord:
    entry = member.entry
    return DocumentRecord(
        manifest_sha256=shelf.manifest_sha256,
        document=entry.document,
        reference=_reference(member),
        title=entry.title,
        ministry=entry.ministry,
        labels=Labels(),
        release_status="released",
        coverage=entry.coverage,
        partial_curated_excerpt=entry.coverage == "partial_curated_excerpt",
        listed_chapters=entry.listed_chapters,
        chapters=entry.chapters,
        times=_times(member),
        path=entry.path,
        markdown_sha256=entry.markdown_sha256,
    )


def chapter_record(member: Member, chapter: ReleasedChapter) -> ChapterRecord:
    entry = member.entry
    number = chapter.result.chapter_number
    receipt = next(item for item in entry.chapters if item.chapter_number == number)
    return ChapterRecord(
        document=entry.document,
        reference=_reference(member),
        labels=Labels(),
        coverage=entry.coverage,
        partial_curated_excerpt=entry.coverage == "partial_curated_excerpt",
        chapter=receipt,
        markdown="\n".join(_chapter(chapter.result)) + "\n",
    )


def provenance_record(shelf: Shelf, member: Member) -> ProvenanceRecord:
    entry = member.entry
    return ProvenanceRecord(
        manifest_sha256=shelf.manifest_sha256,
        document=entry.document,
        labels=Labels(),
        source_url=entry.document.source_url,
        chapters=entry.chapters,
        times=_times(member),
        path=entry.path,
        markdown_sha256=entry.markdown_sha256,
    )
