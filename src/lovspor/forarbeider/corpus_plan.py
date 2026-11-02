"""The bounded, inspectable plan of one corpus change (#738, ADR-0018 PR3B-2).

A plan is a private record: the files a release or a withdrawal would write
and remove in ``forarbeider/``, by path and digest, the dataset state it was
made against and the preflight it rests on. Making one writes nothing to any
checkout, and it is not permission to publish: ``publication_authorized`` and
``writes_corpus`` are ``False`` by type. Only the synthetic simulator applies
one, and only to its own sandbox.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, NamedTuple, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from lovspor.forarbeider.corpus_models import (
    DOCUMENT_PATH,
    INDEX_PATH,
    MANIFEST_PATH,
    DocumentTimes,
    ForarbeiderManifest,
    ManifestDocument,
    manifest_text,
)
from lovspor.forarbeider.corpus_render import Rendering, member_of, render_document, render_index
from lovspor.forarbeider.corpus_tree import Dataset
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_models import SHA256, DocumentIdentity, digest

_Digest = Annotated[str, Field(pattern=SHA256)]


def dataset_path(path: str) -> bool:
    return path in {MANIFEST_PATH, INDEX_PATH} or DOCUMENT_PATH.fullmatch(path) is not None


class PlannedFile(BaseModel):
    """One file a plan writes, by path in the dataset and digest of its bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: _Digest
    size_bytes: int = Field(ge=1)

    @model_validator(mode="after")
    def _in_the_dataset(self) -> Self:
        if not dataset_path(self.path):
            raise ValueError("a plan writes only the dataset's own files")
        return self


class PromotionPlan(BaseModel):
    """What one change would do, and what it was made against. It does nothing itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_promotion_plan"]
    action: Literal["release", "withdraw"]
    document: DocumentIdentity
    report_sha256: _Digest | None
    authorization_sha256: _Digest | None
    checkpoints: tuple[str, ...]
    at: AwareDatetime
    base_manifest_sha256: _Digest | None
    writes: tuple[PlannedFile, ...] = Field(min_length=2)
    removes: tuple[str, ...]
    renderer_version: Literal[1]
    synthetic_inputs: bool
    publication_authorized: Literal[False]
    writes_corpus: Literal[False]
    legal_sufficiency_decided_by_code: Literal[False]

    @field_validator("at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        paths = [item.path for item in self.writes]
        if paths != sorted(set(paths)) or not {MANIFEST_PATH, INDEX_PATH} <= set(paths):
            raise ValueError("writes are sorted, once each, with the manifest and the index")
        if any(DOCUMENT_PATH.fullmatch(path) is None for path in self.removes):
            raise ValueError("a plan removes only document files")
        if set(self.removes) & set(paths) or list(self.removes) != sorted(set(self.removes)):
            raise ValueError("a file is either written or removed, once")
        released = self.report_sha256 is not None and self.authorization_sha256 is not None
        if (self.action == "release") != released:
            raise ValueError("a release rests on a preflight report and an authorization")
        return self


class Draft(NamedTuple):
    """The bytes a change writes, by dataset path, and the document files it removes."""

    writes: dict[str, bytes]
    removes: tuple[str, ...]


def planned_files(draft: Draft) -> tuple[PlannedFile, ...]:
    return tuple(
        PlannedFile(path=path, sha256=digest(payload), size_bytes=len(payload))
        for path, payload in sorted(draft.writes.items())
    )


def _member(manifest: ForarbeiderManifest, document_id: str) -> ManifestDocument | None:
    return next((item for item in manifest.documents if item.document.id == document_id), None)


def _with(manifest: ForarbeiderManifest, member: ManifestDocument) -> ForarbeiderManifest:
    others = [item for item in manifest.documents if item.document.id != member.document.id]
    documents = tuple(sorted([*others, member], key=lambda item: item.path))
    return manifest.model_copy(update={"documents": documents})


def _draft(
    manifest: ForarbeiderManifest, files: dict[str, bytes], removes: tuple[str, ...]
) -> Draft:
    checked = ForarbeiderManifest.model_validate(manifest.model_dump())
    writes = {
        **files,
        INDEX_PATH: render_index(checked).encode(),
        MANIFEST_PATH: manifest_text(checked).encode(),
    }
    return Draft(writes, removes)


def release_draft(dataset: Dataset, rendering: Rendering) -> Draft:
    """The dataset with this rendering as its document's member, released.

    A later release of a document replaces its member: a forward change. A
    withdrawn document stays withdrawn: releasing it again is a new owner
    decision outside this command.

    Raises:
        ForarbeiderRefusedError: ``document_not_released`` for a withdrawn one,
            or ``render_unsafe``.
    """
    markdown = render_document(rendering)
    member = member_of(rendering, markdown)
    existing = _member(dataset.manifest, member.document.id)
    if existing is not None and existing.release_status == "withdrawn":
        raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, member.document.id)
    return _draft(_with(dataset.manifest, member), {member.path: markdown.encode()}, ())


def withdraw_draft(dataset: Dataset, document_id: str, at: datetime) -> Draft:
    """The dataset with ``document_id`` withdrawn: its member kept and marked, its file removed.

    Raises:
        ForarbeiderRefusedError: ``document_not_released``: no such member, or
            one already withdrawn; ``manifest_invalid`` for a time before its release.
    """
    member = _member(dataset.manifest, document_id)
    if member is None or member.release_status != "released":
        raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
    try:
        times = DocumentTimes.model_validate({**member.times.model_dump(), "withdrawn_at": at})
    except ValueError as error:
        raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "withdrawn_at") from error
    withdrawn = member.model_copy(
        update={"release_status": "withdrawn", "markdown_sha256": None, "times": times}
    )
    return _draft(_with(dataset.manifest, withdrawn), {}, (member.path,))
