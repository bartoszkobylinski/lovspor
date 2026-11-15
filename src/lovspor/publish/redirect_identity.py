"""Document identity on the rename walk's lineage (#939).

A 301 says the old URL's document now lives at the target (url-contract §1,
§4). The rename walk reads that from git's rename detection, which misses
one move:

- **A move onto an occupied path.** Git pairs a deletion only with an
  addition, so a document moved onto a path whose old file it replaces in
  the same commit shows as a modification plus a deletion. The move is
  still proven, by identity rather than similarity: the deleted file and
  the replaced path carry the same front-matter ``id`` across that one
  commit, the path carried another document's id before it, and the
  commit's manifest records the document at that path.

The registry side, a migrated address naming its owner, is ``redirect_owners``.
"""

from collections import Counter
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from lovspor.errors import CorpusStateError
from lovspor.history import frontmatter_id
from lovspor.identity.corpus_git import MANIFEST_PATH, read_revision_blobs
from lovspor.storage.manifest import Manifest

_BLOB_CHUNK = 256
"""Deleted files read per ``cat-file --batch``: thousands of retirements
must never sit in memory at once."""

Move = tuple[str, str]
"""``(commit, path)``: a deletion, or the path a document arrived on."""


def _doc_id(blob: bytes | None) -> str | None:
    return None if blob is None else frontmatter_id(blob.decode("utf-8", errors="replace"))


def occupied_moves(
    corpus: Path, deletions: list[Move], live: frozenset[str]
) -> dict[Move, tuple[str, str]]:
    """``(commit, deleted path) -> (path, doc_id)`` for each deleted file whose
    document, now live, arrived over another document's file in that same commit."""
    arrivals = _unambiguous(_arrivals(corpus, deletions, live))
    moves: dict[Move, tuple[str, str]] = {}
    for start in range(0, len(arrivals), _BLOB_CHUNK):
        moves.update(_proven_arrivals(corpus, arrivals[start : start + _BLOB_CHUNK]))
    return moves


def _arrivals(
    corpus: Path, deletions: list[Move], live: frozenset[str]
) -> list[tuple[Move, str, str]]:
    """``(deletion, doc_id, path)`` for each deleted live document that the
    deletion's commit's manifest records at another path."""
    arrivals: list[tuple[Move, str, str]] = []
    for deletion, doc_id in _deleted_documents(corpus, deletions):
        path = _manifest_paths(corpus, deletion[0]).get(doc_id) if doc_id in live else None
        if path is not None and path != deletion[1]:
            arrivals.append((deletion, doc_id, path))
    return arrivals


def _unambiguous(arrivals: list[tuple[Move, str, str]]) -> list[tuple[Move, str, str]]:
    """The arrivals of documents that one commit deleted from exactly one path.

    Two deleted copies of one ``id`` in a commit cannot both be the document
    the target now holds, and nothing says which one is, so neither is."""
    deleted = Counter((sha, doc_id) for (sha, _), doc_id, _ in arrivals)
    return [arrival for arrival in arrivals if deleted[(arrival[0][0], arrival[1])] == 1]


def _deleted_documents(corpus: Path, deletions: list[Move]) -> Iterator[tuple[Move, str]]:
    """Each deletion with the front-matter ``id`` of the file it deleted."""
    for start in range(0, len(deletions), _BLOB_CHUNK):
        chunk = deletions[start : start + _BLOB_CHUNK]
        blobs = read_revision_blobs(corpus, [f"{sha}^:{path}" for sha, path in chunk])
        for deletion, blob in zip(chunk, blobs, strict=True):
            if (doc_id := _doc_id(blob)) is not None:
                yield deletion, doc_id


def _proven_arrivals(
    corpus: Path, arrivals: list[tuple[Move, str, str]]
) -> dict[Move, tuple[str, str]]:
    """The arrivals whose path carries the document's ``id`` after the commit and
    another document's ``id`` before it."""
    specs = [spec for (sha, _), _, path in arrivals for spec in (f"{sha}:{path}", f"{sha}^:{path}")]
    blobs = iter(read_revision_blobs(corpus, specs))
    proven: dict[Move, tuple[str, str]] = {}
    for deletion, doc_id, path in arrivals:
        after, replaced = _doc_id(next(blobs)), _doc_id(next(blobs))
        if after == doc_id and replaced not in (None, doc_id):
            proven[deletion] = (path, doc_id)
    return proven


@lru_cache(maxsize=8)
def _manifest_paths(corpus: Path, sha: str) -> dict[str, str]:
    """``doc_id -> markdown_path`` of the manifest at ``sha``; empty without one.
    A manifest the engine cannot read proves nothing and is refused."""
    blob = read_revision_blobs(corpus, [f"{sha}:{MANIFEST_PATH}"])[0]
    if blob is None:
        return {}
    try:
        manifest = Manifest.model_validate_json(blob)
    except ValidationError as exc:
        raise CorpusStateError(f"{sha}:{MANIFEST_PATH}: unreadable manifest: {exc}") from exc
    return {doc_id: record.markdown_path for doc_id, record in manifest.documents.items()}
