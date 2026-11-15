"""A migrated address names its registry owner, in the redirect map (#939).

In an identity-migrated corpus ``slugs.json`` names each address's owner,
the document its 301 or 410 names (``served_owner_doc_id``, #921). A move
made at or after the migration commit (the oldest commit adding
``identity.json``) of a document off an address it does not own is the
migration's U1 rename or its like: no lineage of the address, which keeps
naming its owner. Moves before the migration commit stand as the walk reads
them: the registry did not govern them, and they may already have answered
301 in a release. An address the registry does not hold names no one, so it
gets no 301 at all.

The registry is read at the pinned commit as strictly as the sync reads it
(``corpus_identity``): a registry no declaration covers, a declaration
without a registry, a repeated key, a registry missing an address of the
manifest, an undeclared corpus whose history shows a migration, and a
shallow clone (whose oldest commit would pass for the migration) are each
refused with ``IdentitySchemaError``.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.errors import IdentitySchemaError, ParseError
from lovspor.identity.corpus_git import (
    first_commit_adding,
    is_ancestor,
    is_shallow,
    read_revision_blobs,
)
from lovspor.identity.history_marker import refuse_downgrade
from lovspor.identity.registry import (
    SlugRegistry,
    parse_registry,
    require_consistent,
    require_registry_covers,
)
from lovspor.identity.schema import (
    IDENTITY_FILENAME,
    LEGACY_IDENTITY_SCHEMA,
    REGISTRY_FILENAME,
    parse_declaration,
)
from lovspor.storage.manifest import Manifest

_DIRECTORY_ROUTES = {"lover": "lov", "forskrifter": "forskrift"}


class AddressOwners(BaseModel):
    """The owner of each ``(route, slug)`` of a migrated corpus, and its migration commit."""

    model_config = ConfigDict(frozen=True)

    migration: str
    owners: dict[tuple[str, str], str]


def address_owners(corpus: Path, sha: str, manifest: Manifest) -> AddressOwners | None:
    """The registry's owners at ``sha``; ``None`` for a corpus never migrated."""
    declaration, blob = read_revision_blobs(
        corpus, [f"{sha}:{IDENTITY_FILENAME}", f"{sha}:{REGISTRY_FILENAME}"]
    )
    schema = parse_declaration(declaration, f"{sha}:{IDENTITY_FILENAME}")
    require_consistent(schema, blob is not None, f"{corpus} at {sha}")
    if schema == LEGACY_IDENTITY_SCHEMA:
        refuse_downgrade(corpus, sha)
        return None
    registry = _registry(blob or b"", f"{sha}:{REGISTRY_FILENAME}")
    require_registry_covers(registry, manifest, f"{corpus} at {sha}")
    return AddressOwners(migration=_migration_commit(corpus, sha), owners=_owners(registry))


def _registry(blob: bytes, where: str) -> SlugRegistry:
    try:
        return parse_registry(blob, where)
    except ParseError as exc:
        raise IdentitySchemaError(str(exc)) from exc


def _migration_commit(corpus: Path, sha: str) -> str:
    """The oldest commit reachable from ``sha`` that added ``identity.json``."""
    if is_shallow(corpus):
        raise IdentitySchemaError(
            f"{corpus}: a shallow clone, whose oldest commit would pass for the identity "
            f"migration; fetch the full history (`git fetch --unshallow`) (#939)",
        )
    migration = first_commit_adding(corpus, sha, IDENTITY_FILENAME)
    if migration is None:
        raise IdentitySchemaError(f"{corpus}: no commit reachable from {sha} added it")
    return migration


def _owners(registry: SlugRegistry) -> dict[tuple[str, str], str]:
    return {
        (_DIRECTORY_ROUTES[directory], slug): entry.owner
        for directory, entries in registry.slugs.items()
        if directory in _DIRECTORY_ROUTES
        for slug, entry in entries.items()
    }


def leaves_its_owner(
    corpus: Path,
    owners: AddressOwners | None,
    address: tuple[str, str],
    move: tuple[str, str],
) -> bool:
    """True when the address may not redirect along ``move`` (retiring commit,
    document it leads to): it has no owner in a migrated corpus, or the move took
    a document other than its owner off it at or after the migration commit."""
    if owners is None:
        return False
    owner = owners.owners.get(address)
    sha, doc_id = move
    if owner is None:
        return True
    return owner != doc_id and is_ancestor(corpus, owners.migration, sha)
