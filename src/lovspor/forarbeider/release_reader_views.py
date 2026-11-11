"""The versioned JSON records the real-release reader prints (#738, B2).

Apart from the synthetic reader's records by type: ``synthetic: false``,
``publication: committed_on_origin_main``, and a notice that the owner's
records are integrity-checked, not authenticated. Only what the released
dataset publishes is shown, with the digests the reader verified: identity,
labels, coverage, chapter receipts and their clearances, the time axes. Never
an owner handle, evidence, dossier, private path or raw page. Chapter text
appears only in the ``chapter`` record, for the one chapter asked for.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider.corpus_models import ChapterReceiptV2, ManifestDocumentV2
from lovspor.forarbeider.reader_views import NOTICE, Labels, Times
from lovspor.forarbeider.release_models import DocumentIdentity
from lovspor.forarbeider.release_reader import ReleaseShelf, VerifiedRelease

TRUST_NOTICE = (
    "Served from a clean lovverk main equal to the last-fetched origin/main, checked against"
    " the owner's private release records. Those checks prove integrity, not identity:"
    " a digest proves bytes, not who made them."
)
PARTIAL_NOTICE = "Partial curated excerpt: not every chapter the source lists is included."
_TYPE_LABELS = {"prop-l": "L", "prop-ls": "LS"}
_EN_DASH = "\u2013"
_FROZEN = ConfigDict(frozen=True, extra="forbid")


class _Record(BaseModel):
    model_config = _FROZEN

    schema_version: Literal[1] = 1
    synthetic: Literal[False] = False
    publication: Literal["committed_on_origin_main"] = "committed_on_origin_main"
    notice: str = NOTICE
    trust_notice: str = TRUST_NOTICE


class Receipts(BaseModel):
    """The owner's records the reader verified for one document, by digest."""

    model_config = _FROZEN

    confirmation_sha256: str
    receipt_sha256: str
    plan_sha256: str
    verification: Literal["owner_records_integrity_unauthenticated"] = (
        "owner_records_integrity_unauthenticated"
    )


class ReleaseSummary(BaseModel):
    model_config = _FROZEN

    document: DocumentIdentity
    reference: str
    title: str
    coverage: str
    partial_curated_excerpt: bool
    released_chapters: tuple[int, ...]
    path: str


class ReleaseListRecord(_Record):
    record_type: Literal["forarbeider_release_list"] = "forarbeider_release_list"
    head_commit: str
    manifest_sha256: str | None
    documents: tuple[ReleaseSummary, ...]


class ReleaseDocumentRecord(_Record):
    record_type: Literal["forarbeider_release_document"] = "forarbeider_release_document"
    head_commit: str
    document: DocumentIdentity
    reference: str
    title: str
    ministry: str | None
    labels: Labels
    coverage: str
    partial_curated_excerpt: bool
    partial_notice: str | None
    listed_chapters: tuple[int, ...]
    chapters: tuple[ChapterReceiptV2, ...]
    times: Times
    path: str
    markdown_sha256: str | None


class ReleaseChapterRecord(_Record):
    record_type: Literal["forarbeider_release_chapter"] = "forarbeider_release_chapter"
    document: DocumentIdentity
    reference: str
    labels: Labels
    coverage: str
    partial_curated_excerpt: bool
    partial_notice: str | None
    chapter: ChapterReceiptV2
    markdown: str


class ReleaseProvenanceRecord(_Record):
    record_type: Literal["forarbeider_release_provenance"] = "forarbeider_release_provenance"
    head_commit: str
    manifest_sha256: str | None
    document: DocumentIdentity
    labels: Labels
    chapters: tuple[ChapterReceiptV2, ...]
    times: Times
    path: str
    markdown_sha256: str | None
    receipts: Receipts


def reference_of(member: ManifestDocumentV2) -> str:
    document = member.document
    session = document.session.replace("-", _EN_DASH)
    label = _TYPE_LABELS[document.document_type]
    return f"Prop. {document.proposition_number} {label} ({session})"


def _partial(member: ManifestDocumentV2) -> bool:
    return member.coverage == "partial_curated_excerpt"


def _notice(member: ManifestDocumentV2) -> str | None:
    return PARTIAL_NOTICE if _partial(member) else None


def _times(member: ManifestDocumentV2) -> Times:
    return Times.model_validate(member.times.model_dump(exclude={"withdrawn_at"}))


def release_list_record(shelf: ReleaseShelf) -> ReleaseListRecord:
    summaries = tuple(
        ReleaseSummary(
            document=item.member.document,
            reference=reference_of(item.member),
            title=item.member.title,
            coverage=item.member.coverage,
            partial_curated_excerpt=_partial(item.member),
            released_chapters=tuple(c.chapter_number for c in item.member.chapters),
            path=item.member.path,
        )
        for item in shelf.releases
    )
    return ReleaseListRecord(
        head_commit=shelf.state.head_commit,
        manifest_sha256=shelf.manifest_sha256,
        documents=summaries,
    )


def release_document_record(shelf: ReleaseShelf, release: VerifiedRelease) -> ReleaseDocumentRecord:
    member = release.member
    return ReleaseDocumentRecord(
        head_commit=shelf.state.head_commit,
        document=member.document,
        reference=reference_of(member),
        title=member.title,
        ministry=member.ministry,
        labels=Labels(),
        coverage=member.coverage,
        partial_curated_excerpt=_partial(member),
        partial_notice=_notice(member),
        listed_chapters=member.listed_chapters,
        chapters=member.chapters,
        times=_times(member),
        path=member.path,
        markdown_sha256=member.markdown_sha256,
    )


def release_chapter_record(
    release: VerifiedRelease, chapter: ChapterReceiptV2, markdown: str
) -> ReleaseChapterRecord:
    member = release.member
    return ReleaseChapterRecord(
        document=member.document,
        reference=reference_of(member),
        labels=Labels(),
        coverage=member.coverage,
        partial_curated_excerpt=_partial(member),
        partial_notice=_notice(member),
        chapter=chapter,
        markdown=markdown,
    )


def release_provenance_record(
    shelf: ReleaseShelf, release: VerifiedRelease
) -> ReleaseProvenanceRecord:
    member = release.member
    return ReleaseProvenanceRecord(
        head_commit=shelf.state.head_commit,
        manifest_sha256=shelf.manifest_sha256,
        document=member.document,
        labels=Labels(),
        chapters=member.chapters,
        times=_times(member),
        path=member.path,
        markdown_sha256=member.markdown_sha256,
        receipts=Receipts(
            confirmation_sha256=release.confirmation_sha256,
            receipt_sha256=release.receipt_sha256,
            plan_sha256=release.plan_sha256,
        ),
    )
