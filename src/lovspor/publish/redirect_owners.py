"""A migrated address names its registry owner, in the redirect map (#939).

In an identity-migrated corpus ``slugs.json`` names each address's owner,
the document its 301 or 410 names (``served_owner_doc_id``, #921). An
address answers 301 only to its owner. A move of another document off it
(the migration's U1 rename, a later move, or a move made before the
migration commit, the oldest commit adding ``identity.json``) is no lineage
of the address, which keeps naming its owner: 200 while the owner is live
there, else 410. A 301 never leads to a different document (owner decision,
P7 E7). An address the registry does not hold names no one, so it gets no
301 at all.

The registry is read at the pinned commit as strictly as the sync reads it
(``corpus_identity``): a registry no declaration covers, a declaration
without a registry, a repeated key, a registry missing an address of the
manifest, an undeclared corpus whose history shows a migration, and a
shallow clone (whose truncated history would hide moves the owner check
judges) are each refused with ``IdentitySchemaError``.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.errors import IdentitySchemaError, ParseError
from lovspor.identity.corpus_git import is_shallow, read_revision_blobs
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
    """The owner of each ``(route, slug)`` of a migrated corpus."""

    model_config = ConfigDict(frozen=True)

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
    _refuse_shallow(corpus)
    return AddressOwners(owners=_owners(registry))


def _registry(blob: bytes, where: str) -> SlugRegistry:
    try:
        return parse_registry(blob, where)
    except ParseError as exc:
        raise IdentitySchemaError(str(exc)) from exc


def _refuse_shallow(corpus: Path) -> None:
    """A shallow clone truncates the rename walk whose terminal document the owner check judges."""
    if is_shallow(corpus):
        raise IdentitySchemaError(
            f"{corpus}: a shallow clone, whose truncated history hides moves the owner check "
            f"must judge; fetch the full history (`git fetch --unshallow`) (#939)",
        )


def _owners(registry: SlugRegistry) -> dict[tuple[str, str], str]:
    return {
        (_DIRECTORY_ROUTES[directory], slug): entry.owner
        for directory, entries in registry.slugs.items()
        if directory in _DIRECTORY_ROUTES
        for slug, entry in entries.items()
    }


def leaves_its_owner(owners: AddressOwners | None, address: tuple[str, str], doc_id: str) -> bool:
    """True when the address may not redirect to ``doc_id``, the live document
    its retiring move leads to: it has no owner in a migrated corpus, or its
    owner is another document, whenever the move was made."""
    if owners is None:
        return False
    return owners.owners.get(address) != doc_id
