"""The files a batch release or a withdrawal writes, manifest schema 2 (#738, B1b).

Pure: no I/O, no clock, no Git. Given the dataset as found and what a
confirmed batch carries, it returns the bytes of every file the change
writes, by dataset path, through the existing deterministic renderer
(:func:`~lovspor.forarbeider.corpus_render.render_document`,
:func:`~lovspor.forarbeider.corpus_render.render_index`) and the manifest
version 2 writer (:func:`~lovspor.forarbeider.corpus_models.manifest_v2_text`).

- Only the chapters handed in are rendered, each with the clearance its plan
  states: ``standing_policy`` (no person read it) or ``owner_review``. A
  chapter a plan excluded or withheld never reaches this module.
- Every label is fixed by type: ``forarbeid``, ``normative: false``,
  ``åndsverkloven § 14``, ``source_license: null``, NOT enacted law.
- A batch only adds documents. A document the manifest already lists,
  released or withdrawn, is a conflicting release and refuses the whole
  batch: a later version of a document, or one withdrawn, is a new owner
  decision outside a batch.
- A withdrawal keeps the member, its chapter receipts and their clearances
  as provenance, marks it ``withdrawn`` with the time, drops its rendering
  and removes its file; the index no longer lists it.

Times stay on their axes: the policy's signature, the last review decision,
and the owner's batch confirmation, which is also the release moment the
manifest states. The publication itself is the owner's lovverk merge, which
Git records; no time is invented.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.chapter_models import ChapterResult
from lovspor.forarbeider.corpus_models import (
    INDEX_PATH,
    MANIFEST_PATH,
    ChapterClearance,
    DocumentTimesV2,
    ForarbeiderManifestV2,
    ManifestDocumentV2,
    PolicyClearance,
    ReviewClearance,
    manifest_v2_text,
)
from lovspor.forarbeider.corpus_plan import Draft
from lovspor.forarbeider.corpus_render import (
    ReleasedChapter,
    Rendering,
    member_v2_of,
    render_document,
    render_index,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import ForarbeidRecord
from lovspor.forarbeider.release_models import destination_of, digest, document_of


class ChapterRelease(NamedTuple):
    """One included chapter: its staged result and the clearance the plan states for it."""

    result: ChapterResult
    clearance: ChapterClearance


class DocumentRelease(NamedTuple):
    """One proposition's metadata and its included chapters, in source order."""

    metadata: ForarbeidRecord
    chapters: tuple[ChapterRelease, ...]


class Decision(NamedTuple):
    """When the standing policy was signed and when the owner confirmed the batch."""

    policy_decided_at: datetime
    owner_decided_at: datetime


def _invalid(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, detail)


def times_of(release: DocumentRelease, decision: Decision) -> DocumentTimesV2:
    """The document's time axes: ``null`` where no chapter took that path, none invented.

    Raises:
        ForarbeiderRefusedError: ``release_invalid: decided`` for a confirmation
            before the policy or a review it rests on.
    """
    clearances = [item.clearance for item in release.chapters]
    reviews = [item.review_decided_at for item in clearances if isinstance(item, ReviewClearance)]
    policy = any(isinstance(item, PolicyClearance) for item in clearances)
    try:
        return DocumentTimesV2(
            source_published_at=release.metadata.published_at,
            reviewed_at=max(reviews) if reviews else None,
            policy_decided_at=decision.policy_decided_at if policy else None,
            owner_decided_at=decision.owner_decided_at,
            released_at=decision.owner_decided_at,
            withdrawn_at=None,
            time_basis="operator_supplied_unauthenticated",
            recorded_at="git_commit_time",
        )
    except ValidationError as error:
        raise _invalid("decided") from error


def rendering_of(release: DocumentRelease, decision: Decision) -> Rendering:
    """What the renderer needs; a policy-cleared chapter has no review time."""
    chapters = tuple(
        ReleasedChapter(
            item.result,
            item.clearance.review_decided_at
            if isinstance(item.clearance, ReviewClearance)
            else None,
        )
        for item in release.chapters
    )
    return Rendering(release.metadata, chapters, times_of(release, decision))


def member_v2_of_release(
    release: DocumentRelease, decision: Decision, markdown: str
) -> ManifestDocumentV2:
    """The version 2 member listing ``markdown``, each chapter with its plan's clearance.

    Raises:
        ForarbeiderRefusedError: ``release_invalid: member`` when the result is not one.
    """
    clearances = [item.clearance for item in release.chapters]
    try:
        return member_v2_of(rendering_of(release, decision), markdown, clearances)
    except ValidationError as error:
        raise _invalid("member") from error


def _conflicts(manifest: ForarbeiderManifestV2, metadata: ForarbeidRecord) -> bool:
    document = document_of(metadata)
    path = destination_of(document)
    return any(
        member.document.id == document.id
        or member.document.source_document_id == document.source_document_id
        or member.path == path
        for member in manifest.documents
    )


def manifest_draft(
    manifest: ForarbeiderManifestV2, files: dict[str, bytes], removes: tuple[str, ...]
) -> Draft:
    """The draft of ``manifest`` re-validated: its files, its index and its canonical bytes.

    Raises:
        ForarbeiderRefusedError: ``manifest_invalid`` when the result is not one manifest.
    """
    try:
        checked = ForarbeiderManifestV2.model_validate(manifest.model_dump())
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "manifest") from error
    writes = {
        **files,
        INDEX_PATH: render_index(checked).encode(),
        MANIFEST_PATH: manifest_v2_text(checked).encode(),
    }
    return Draft(writes, removes)


def _sorted(documents: Sequence[ManifestDocumentV2]) -> tuple[ManifestDocumentV2, ...]:
    return tuple(sorted(documents, key=lambda member: member.path))


def release_batch_draft(
    base: ForarbeiderManifestV2, releases: Sequence[DocumentRelease], decision: Decision
) -> Draft:
    """The dataset with every document in ``releases`` added as a released member.

    Raises:
        ForarbeiderRefusedError: ``release_conflict: <id>`` for a document the
            manifest already lists, ``render_unsafe``, ``release_invalid`` or
            ``manifest_invalid``.
    """
    files: dict[str, bytes] = {}
    members = list(base.documents)
    for release in releases:
        if _conflicts(base, release.metadata):
            raise ForarbeiderRefusedError(RefusalReason.RELEASE_CONFLICT, release.metadata.id)
        markdown = render_document(rendering_of(release, decision))
        member = member_v2_of_release(release, decision, markdown)
        files[member.path] = markdown.encode()
        members.append(member)
    return manifest_draft(base.model_copy(update={"documents": _sorted(members)}), files, ())


def withdrawn_member(member: ManifestDocumentV2, at: datetime) -> ManifestDocumentV2:
    """``member`` marked withdrawn at ``at``: its receipts and clearances kept, no rendering.

    Raises:
        ForarbeiderRefusedError: ``document_not_released`` for one already withdrawn,
            ``manifest_invalid: withdrawn_at`` for a time before its release.
    """
    if member.release_status != "released":
        raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
    try:
        times = DocumentTimesV2.model_validate({**member.times.model_dump(), "withdrawn_at": at})
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, "withdrawn_at") from error
    return member.model_copy(
        update={"release_status": "withdrawn", "markdown_sha256": None, "times": times}
    )


def withdraw_v2_draft(base: ForarbeiderManifestV2, document_id: str, at: datetime) -> Draft:
    """The dataset with ``document_id`` withdrawn and its file removed, forward only.

    Raises:
        ForarbeiderRefusedError: ``document_not_released`` for no such released member.
    """
    member = next((item for item in base.documents if item.document.id == document_id), None)
    if member is None:
        raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
    withdrawn = withdrawn_member(member, at)
    others = [item for item in base.documents if item.document.id != document_id]
    manifest = base.model_copy(update={"documents": _sorted([*others, withdrawn])})
    return manifest_draft(manifest, {}, (member.path,))


def released_digests(draft: Draft) -> dict[str, str]:
    """Every file a draft writes, by path, with the digest of its bytes."""
    return {path: digest(payload) for path, payload in sorted(draft.writes.items())}
