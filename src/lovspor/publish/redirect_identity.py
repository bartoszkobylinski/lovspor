"""Document identity on the rename walk's lineage (#939).

A 301 says the old URL's document now lives at the target (url-contract §1,
§4). The rename walk reads that from git's rename detection, which misses
one move and over-reads another:

- **A move onto an occupied path.** Git pairs a deletion only with an
  addition, so a document moved onto a path whose old file it replaces in
  the same commit shows as a modification plus a deletion. The move is
  still proven, by identity rather than similarity: the deleted file and
  the replaced path carry the same front-matter ``id`` across that one
  commit, the path carried another document's id before it, and the
  commit's manifest records the document at that path.
- **An identity-migrated corpus.** There ``slugs.json`` names each
  address's owner, the document its 301 or 410 names (#921). A move made
  at or after the migration commit (the oldest commit adding
  ``identity.json``) of a document off an address it does not own is the
  migration's U1 rename or its like: no lineage of the address, which
  keeps naming its owner. Moves before the migration commit stand as the
  walk reads them: the registry did not govern them, and they may already
  have answered 301 in a release.
"""

import json
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.errors import IdentitySchemaError
from lovspor.history import frontmatter_id
from lovspor.identity.corpus_git import (
    MANIFEST_PATH,
    first_commit_adding,
    is_ancestor,
    read_revision_blobs,
)
from lovspor.identity.registry import SlugRegistry
from lovspor.identity.schema import (
    IDENTITY_FILENAME,
    LEGACY_IDENTITY_SCHEMA,
    REGISTRY_FILENAME,
    parse_declaration,
)
from lovspor.publish.inventory import PublishError

_DIRECTORY_ROUTES = {"lover": "lov", "forskrifter": "forskrift"}

_BLOB_CHUNK = 256
"""Deleted files read per ``cat-file --batch``: thousands of retirements
must never sit in memory at once."""

Move = tuple[str, str]
"""``(commit, path)``: a deletion, or the path a document arrived on."""


class AddressOwners(BaseModel):
    """The owner of each ``(route, slug)`` of a migrated corpus, and its migration commit."""

    model_config = ConfigDict(frozen=True)

    migration: str
    owners: dict[tuple[str, str], str]


def _doc_id(blob: bytes | None) -> str | None:
    return None if blob is None else frontmatter_id(blob.decode("utf-8", errors="replace"))


def occupied_moves(corpus: Path, deletions: list[Move], live: frozenset[str]) -> dict[Move, str]:
    """``(commit, deleted path) -> path`` for each deleted file whose document,
    now live, arrived over another document's file in that same commit."""
    arrivals = _arrivals(corpus, deletions, live)
    moves: dict[Move, str] = {}
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


def _deleted_documents(corpus: Path, deletions: list[Move]) -> Iterator[tuple[Move, str]]:
    """Each deletion with the front-matter ``id`` of the file it deleted."""
    for start in range(0, len(deletions), _BLOB_CHUNK):
        chunk = deletions[start : start + _BLOB_CHUNK]
        blobs = read_revision_blobs(corpus, [f"{sha}^:{path}" for sha, path in chunk])
        for deletion, blob in zip(chunk, blobs, strict=True):
            if (doc_id := _doc_id(blob)) is not None:
                yield deletion, doc_id


def _proven_arrivals(corpus: Path, arrivals: list[tuple[Move, str, str]]) -> dict[Move, str]:
    """The arrivals whose path carries the document's ``id`` after the commit and
    another document's ``id`` before it."""
    specs = [spec for (sha, _), _, path in arrivals for spec in (f"{sha}:{path}", f"{sha}^:{path}")]
    blobs = iter(read_revision_blobs(corpus, specs))
    proven: dict[Move, str] = {}
    for deletion, doc_id, path in arrivals:
        after, replaced = _doc_id(next(blobs)), _doc_id(next(blobs))
        if after == doc_id and replaced not in (None, doc_id):
            proven[deletion] = path
    return proven


@lru_cache(maxsize=8)
def _manifest_paths(corpus: Path, sha: str) -> dict[str, str]:
    """``doc_id -> markdown_path`` of the manifest at ``sha``; empty without one."""
    blob = read_revision_blobs(corpus, [f"{sha}:{MANIFEST_PATH}"])[0]
    if blob is None:
        return {}
    documents = json.loads(blob).get("documents", {})
    return {doc_id: str(record.get("markdown_path")) for doc_id, record in documents.items()}


def address_owners(corpus: Path, sha: str) -> AddressOwners | None:
    """The registry's owners at ``sha``; ``None`` for a legacy (unmigrated) corpus."""
    declaration, registry = read_revision_blobs(
        corpus, [f"{sha}:{IDENTITY_FILENAME}", f"{sha}:{REGISTRY_FILENAME}"]
    )
    if parse_declaration(declaration, f"{sha}:{IDENTITY_FILENAME}") == LEGACY_IDENTITY_SCHEMA:
        return None
    migration = first_commit_adding(corpus, sha, IDENTITY_FILENAME)
    if registry is None or migration is None:
        raise PublishError(f"{sha}: the identity schema is declared without its registry")
    try:
        slugs = SlugRegistry.model_validate_json(registry).slugs
    except ValidationError as exc:
        raise IdentitySchemaError(f"{sha}:{REGISTRY_FILENAME}: unreadable: {exc}") from exc
    owners = {
        (_DIRECTORY_ROUTES[directory], slug): entry.owner
        for directory, entries in slugs.items()
        if directory in _DIRECTORY_ROUTES
        for slug, entry in entries.items()
    }
    return AddressOwners(migration=migration, owners=owners)


def leaves_its_owner(
    corpus: Path,
    owners: AddressOwners | None,
    address: tuple[str, str],
    move: Move,
) -> bool:
    """True when ``move`` (retiring commit, document it leads to) took a document
    other than the address's owner off it at or after the migration commit."""
    if owners is None:
        return False
    owner = owners.owners.get(address)
    sha, doc_id = move
    return owner not in (None, doc_id) and is_ancestor(corpus, owners.migration, sha)
