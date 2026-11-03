"""The opt-in, read-only reader of a synthetic sandbox's forarbeider dataset (#738, PR3B-3).

Until the owner authorizes a real pilot behind its legal, privacy and
source-terms gates, the only dataset this reader opens is one in a sandbox
``sandbox-create`` made, and the only documents it serves are the bundled
synthetic propositions. It is invoked with the sandbox named, never found
by default, and no other reader (``get_law``, MCP, search, site, indexes,
embeddings) imports it.

The manifest is the only membership authority, and every file under
``forarbeider/`` must be exactly what it lists (:func:`read_dataset_files`).
A member is then rendered again from the bundled synthetic pages: its
manifest entry and its file must be byte-identical to that rendering, so a
forged entry, an injected line or a real document is refused as not
synthetic. Only a ``released`` member is served; a withdrawn one never is.
The bytes served are the ones whose digest was checked, read once.

Nothing here writes, caches, starts a process or opens a socket.
"""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.corpus_models import ManifestDocument
from lovspor.forarbeider.corpus_render import (
    ReleasedChapter,
    Rendering,
    member_of,
    render_document,
)
from lovspor.forarbeider.corpus_transaction import JOURNAL, LOCK
from lovspor.forarbeider.corpus_tree import read_dataset_files, require_sandbox
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.page import build_record
from lovspor.forarbeider.synthetic import PROPOSITIONS, RETRIEVED_AT, page_bytes


class Lookup(NamedTuple):
    """One document, named exactly: its government id, session and type."""

    document_id: str
    session: str
    document_type: str


class Member(NamedTuple):
    """A released member, proved to be the synthetic rendering it states."""

    entry: ManifestDocument
    rendering: Rendering


class Shelf(NamedTuple):
    """What a verified sandbox serves, and the documents it holds as withdrawn."""

    manifest_sha256: str | None
    members: tuple[Member, ...]
    withdrawn: frozenset[Lookup]


def _not_synthetic() -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.INPUT_NOT_SYNTHETIC, "document")


def lookup_of(entry: ManifestDocument) -> Lookup:
    document = entry.document
    return Lookup(document.id, document.session, document.document_type)


def synthetic_rendering(entry: ManifestDocument) -> Rendering:
    """The rendering the bundled synthetic pages give for ``entry``'s chapters and times.

    Raises:
        ForarbeiderRefusedError: ``input_not_synthetic``: a type or chapter the
            bundled propositions do not have.
    """
    kind = entry.document.document_type
    pages = PROPOSITIONS.get(kind, {})
    numbers = {item.chapter_number for item in entry.chapters}
    if not pages or not numbers <= set(pages):
        raise _not_synthetic()
    first = min(pages)
    metadata = build_record(page_bytes(kind, first), pages[first], RETRIEVED_AT)
    chapters = tuple(
        ReleasedChapter(
            build_chapter_result(
                page_bytes(kind, item.chapter_number), pages[item.chapter_number], RETRIEVED_AT
            ),
            item.review_decided_at,
        )
        for item in entry.chapters
    )
    times = entry.times.model_copy(update={"withdrawn_at": None})
    return Rendering(metadata, chapters, times)


def _verified(entry: ManifestDocument, files: dict[str, bytes]) -> Member | None:
    """``entry`` as a servable member, ``None`` when withdrawn; refused unless synthetic.

    Raises:
        ForarbeiderRefusedError: ``input_not_synthetic``.
    """
    rendering = synthetic_rendering(entry)
    try:
        markdown = render_document(rendering)
        expected = member_of(rendering, markdown)
    except (ForarbeiderRefusedError, ValidationError) as error:
        raise _not_synthetic() from error
    stated = {
        "release_status": entry.release_status,
        "markdown_sha256": entry.markdown_sha256,
        "times": entry.times,
    }
    if expected.model_copy(update=stated) != entry:
        raise _not_synthetic()
    if entry.release_status != "released":
        return None
    if files.get(entry.path) != markdown.encode():
        raise _not_synthetic()
    return Member(entry, rendering)


def open_shelf(raw: Path, forbidden: Sequence[Path]) -> Shelf:
    """The synthetic sandbox at ``raw``, verified whole before anything is served.

    Raises:
        ForarbeiderRefusedError: not a sandbox, a change in progress
            (``corpus_locked``), an unsafe or inconsistent tree, or a member
            that is not the synthetic rendering it states.
    """
    sandbox = require_sandbox(raw, forbidden)
    if any(os.path.lexists(sandbox / name) for name in (LOCK, JOURNAL)):
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_LOCKED, "sandbox")
    dataset, files = read_dataset_files(sandbox)
    entries = dataset.manifest.documents
    found = [(entry, _verified(entry, files)) for entry in entries]
    members = tuple(member for _, member in found if member is not None)
    withdrawn = frozenset(lookup_of(entry) for entry, member in found if member is None)
    return Shelf(dataset.manifest_sha256, members, withdrawn)


def find(shelf: Shelf, lookup: Lookup) -> Member:
    """The released member named exactly by ``lookup``.

    Raises:
        ForarbeiderRefusedError: ``document_not_released`` for a withdrawn
            one, ``document_unknown`` for anything else.
    """
    for member in shelf.members:
        if lookup_of(member.entry) == lookup:
            return member
    if lookup in shelf.withdrawn:
        raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_NOT_RELEASED, "document")
    raise ForarbeiderRefusedError(RefusalReason.DOCUMENT_UNKNOWN, "document")


def released_chapter(member: Member, number: int) -> ReleasedChapter:
    """One released chapter of ``member``.

    Raises:
        ForarbeiderRefusedError: ``chapter_not_released``, held or unlisted alike.
    """
    for item in member.rendering.chapters:
        if item.result.chapter_number == number:
            return item
    raise ForarbeiderRefusedError(RefusalReason.CHAPTER_NOT_RELEASED, "chapter")
