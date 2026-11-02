"""The forarbeider corpus manifest, version 1 (#738, ADR-0018 PR3B-2).

``forarbeider/manifest.json`` is the dataset's own membership list, apart from
the Lovdata root manifest, and the only thing that makes a file a member: a
Markdown file it does not name is a defect, never a document. Every label a
reader needs is fixed by type: a forarbeid is not law (``normative=false``),
its basis is the statutory exclusion in åndsverkloven § 14, and
``source_license`` is ``null`` because no licence is asserted.

Times stay on their own axes. ``source_published_at`` is only what the PR1
metadata stated, the review, owner and release moments are the operator's
unauthenticated word, and the moment the corpus records a change is its Git
commit time, which this file never states. There is no ``valid_at``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import JSON_LAYOUT
from lovspor.forarbeider.release_models import (
    SHA256,
    ChapterIdentity,
    DocumentIdentity,
    destination_of,
    digest,
)

DATASET_ROOT = "forarbeider"
MANIFEST_PATH = f"{DATASET_ROOT}/manifest.json"
INDEX_PATH = f"{DATASET_ROOT}/INDEX.md"
RENDERER_VERSION = 1
DOCUMENT_PATH = re.compile(r"forarbeider/\d{4}-\d{4}/prop-ls?/[1-9]\d{0,11}\.md")

_Digest = Annotated[str, Field(pattern=SHA256)]
_Text = Annotated[str, Field(pattern=r"\S")]
ReleaseStatus = Literal["released", "withdrawn"]


class ChapterReceipt(BaseModel):
    """What one released chapter was: its page, bytes, text and review, by digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_number: int = Field(ge=1)
    chapter_id: str
    chapter_title: _Text
    source_page_url: str
    source_sha256: _Digest
    text_sha256: _Digest
    extractor_version: Literal[1]
    normalizer_version: Literal[1]
    retrieved_at: AwareDatetime
    review_decided_at: AwareDatetime

    @field_validator("retrieved_at", "review_decided_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _one_chapter(self) -> Self:
        ChapterIdentity.model_validate(
            self.model_dump(exclude={"chapter_title", "retrieved_at", "review_decided_at"})
        )
        return self


class DocumentTimes(BaseModel):
    """Separate axes, none invented: the Git commit records the corpus time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_published_at: date | None
    reviewed_at: AwareDatetime
    owner_decided_at: AwareDatetime
    released_at: AwareDatetime
    withdrawn_at: AwareDatetime | None
    time_basis: Literal["operator_supplied_unauthenticated"]
    recorded_at: Literal["git_commit_time"]

    @field_validator("reviewed_at", "owner_decided_at", "released_at", "withdrawn_at")
    @classmethod
    def _in_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _in_order(self) -> Self:
        moments = [self.reviewed_at, self.owner_decided_at, self.released_at]
        if self.withdrawn_at is not None:
            moments.append(self.withdrawn_at)
        if moments != sorted(moments):
            raise ValueError("review, owner decision, release and withdrawal are out of order")
        return self


class ManifestDocument(BaseModel):
    """One proposition's membership: identity, labels, released chapters and times."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document: DocumentIdentity
    title: _Text
    ministry: _Text | None
    source_document_type: Literal["forarbeid"]
    normative: Literal[False]
    source_provider: Literal["regjeringen.no"]
    rights_basis: Literal["åndsverkloven § 14"]
    source_license: None
    asserts_complete_proposition: Literal[False]
    release_status: ReleaseStatus
    path: str
    markdown_sha256: _Digest | None
    listed_chapters: tuple[int, ...] = Field(min_length=1)
    chapters: tuple[ChapterReceipt, ...] = Field(min_length=1)
    coverage: Literal["partial_curated_excerpt", "every_listed_chapter"]
    times: DocumentTimes

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.path != destination_of(self.document) or not DOCUMENT_PATH.fullmatch(self.path):
            raise ValueError("path is not this document's scoped path")
        _require_chapters_of(self)
        if (self.release_status == "released") != (self.times.withdrawn_at is None):
            raise ValueError("only a withdrawn document has a withdrawal time")
        if (self.release_status == "released") != (self.markdown_sha256 is not None):
            raise ValueError("a released document has a rendering, a withdrawn one none")
        return self


def _require_chapters_of(member: ManifestDocument) -> None:
    listed = member.listed_chapters
    numbers = tuple(item.chapter_number for item in member.chapters)
    if listed != tuple(range(1, len(listed) + 1)):
        raise ValueError("listed chapters are the source's 1..N")
    if numbers != tuple(sorted(set(numbers))) or not set(numbers) <= set(listed):
        raise ValueError("released chapters are listed ones, in source order, once each")
    prefix = f"{member.document.id}-ch"
    if any(not item.chapter_id.startswith(prefix) for item in member.chapters):
        raise ValueError("a chapter belongs to another document")
    source = member.document.source_url
    if any(not item.source_page_url.startswith(source) for item in member.chapters):
        raise ValueError("a chapter belongs to another document")
    expected = "every_listed_chapter" if numbers == listed else "partial_curated_excerpt"
    if member.coverage != expected:
        raise ValueError("coverage does not match the released chapters")
    if any(item.review_decided_at > member.times.owner_decided_at for item in member.chapters):
        raise ValueError("a chapter was approved after the owner decided")


class ForarbeiderManifest(BaseModel):
    """The dataset's authoritative membership list, sorted by path, once each."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_corpus_manifest"]
    dataset: Literal["forarbeider"]
    renderer_version: Literal[1]
    documents: tuple[ManifestDocument, ...]

    @model_validator(mode="after")
    def _once_each(self) -> Self:
        paths = [member.path for member in self.documents]
        if paths != sorted(set(paths)):
            raise ValueError("documents must be sorted by path, once each")
        ids = [member.document.id for member in self.documents]
        sources = [member.document.source_document_id for member in self.documents]
        if len(set(ids)) != len(ids) or len(set(sources)) != len(sources):
            raise ValueError("a document is listed twice")
        return self


EMPTY_MANIFEST = ForarbeiderManifest(
    schema_version=1,
    record_type="forarbeider_corpus_manifest",
    dataset="forarbeider",
    renderer_version=1,
    documents=(),
)


def manifest_text(manifest: ForarbeiderManifest) -> str:
    """The manifest as written: sorted keys, two-space indent, UTF-8, one final newline."""
    return json.dumps(manifest.model_dump(mode="json"), **JSON_LAYOUT) + "\n"


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("a key is given twice")
    return dict(pairs)


def parse_manifest(raw: bytes) -> ForarbeiderManifest:
    """The manifest in ``raw``, which must be exactly as :func:`manifest_text` writes it.

    Raises:
        ForarbeiderRefusedError: ``manifest_incompatible`` for another version,
            ``manifest_invalid`` for anything else that is not this schema.
    """
    invalid = ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "manifest")
    try:
        payload = json.loads(raw.decode(), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as error:
        raise invalid from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "manifest")
    try:
        manifest = ForarbeiderManifest.model_validate(payload)
    except ValidationError as error:
        raise invalid from error
    if manifest_text(manifest).encode() != raw:
        raise invalid
    return manifest


def member_files(manifest: ForarbeiderManifest) -> dict[str, str]:
    """Every document file the manifest makes a member, with the digest it must have."""
    return {
        member.path: member.markdown_sha256
        for member in manifest.documents
        if member.markdown_sha256 is not None
    }


def verify_members(manifest: ForarbeiderManifest, files: Mapping[str, bytes]) -> None:
    """The dataset's document files are exactly the released members, with their bytes.

    ``files`` holds every file under ``forarbeider/`` but the manifest and the index.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch``: a dangling member, a
            file no member names, or bytes another than the manifest states.
    """
    expected = member_files(manifest)
    for path in sorted(set(expected) | set(files)):
        payload = files.get(path)
        if payload is None or digest(payload) != expected.get(path):
            raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, _shown(path))


def _shown(path: str) -> str:
    """A corpus path is public, but a stray one is printed only if it has the dataset's grammar."""
    return path if DOCUMENT_PATH.fullmatch(path) else "forarbeider"
