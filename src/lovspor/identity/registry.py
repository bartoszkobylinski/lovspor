"""The slug ownership registry, ``slugs.json`` (#871, ADR-0023 draft point 2).

Every slug the corpus has published maps to the first ``doc_id`` that held
it. An entry is never deleted and its owner never changes, whatever the
owner's status: a removed document keeps its address, and a later document
whose natural slug is taken gets a distinct one by the existing collision
rule (``resolve_collisions``; owner decision U1, 2026-11-11).

The file sits at the corpus root beside ``manifest.json``: like the manifest
it is corpus-wide membership state, not a per-dataset view. Slugs are unique
per directory, not across directories (``lover/`` and ``forskrifter/`` can
share one), so entries are grouped by directory.

The sync only *extends* a registry that exists. It never bootstraps one: a
corpus written before #871 holds reused slugs, and bootstrapping from it
would register the later holder as owner, permanently. The offline
migration (``lovspor.identity.migration``) builds the first registry from
the full manifest history.
"""

import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import ParseError, SlugOwnershipError
from lovspor.storage.manifest import Manifest, ManifestRecord

REGISTRY_FILENAME = "slugs.json"
REGISTRY_SCHEMA_VERSION = 1
#: The corpus directory of each tracked dataset (``document_io`` writes there).
DIRECTORY_OF_DATASET = {
    "gjeldende-lover": "lover",
    "gjeldende-sentrale-forskrifter": "forskrifter",
}


class SlugOwner(BaseModel):
    """Who owns one slug, and since which manifest (its ``generated_at``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str
    assigned_at: str


class SlugRegistry(BaseModel):
    """``directory -> slug -> owner``; append-only, owners immutable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = REGISTRY_SCHEMA_VERSION
    slugs: dict[str, dict[str, SlugOwner]] = {}

    def owner_of(self, directory: str, slug: str) -> str | None:
        entry = self.slugs.get(directory, {}).get(slug)
        return None if entry is None else entry.owner

    def ownership(self, directory: str) -> dict[str, str]:
        """``slug -> owner`` for one directory, the shape ``resolve_collisions`` takes."""
        return {slug: entry.owner for slug, entry in self.slugs.get(directory, {}).items()}

    def assign(self, directory: str, slug: str, owner: SlugOwner) -> "SlugRegistry":
        """This registry with ``slug`` owned by ``owner``; refuses an owner change."""
        held = self.owner_of(directory, slug)
        if held == owner.owner:
            return self
        if held is not None:
            raise SlugOwnershipError(
                f"{directory}/{slug} is owned by {held}; refusing to assign it to "
                f"{owner.owner} — a slug never changes owner (#871)",
            )
        entries = {**self.slugs.get(directory, {}), slug: owner}
        return self.model_copy(update={"slugs": {**self.slugs, directory: entries}})


def record_address(record: ManifestRecord) -> tuple[str, str]:
    """``(directory, slug)`` of a manifest record; a slug-less record uses its file stem."""
    path = PurePosixPath(record.markdown_path)
    return path.parts[0], record.slug or path.stem


def with_manifest(registry: SlugRegistry, manifest: Manifest) -> SlugRegistry:
    """``registry`` extended with every unregistered slug ``manifest`` holds.

    Records are taken in ``doc_id`` order, so of two records first seen on
    one slug the smallest id owns it. A *current* record on a slug another
    document owns is refused: it would serve one document at another's
    address. A *removed* record there is a later holder from before the
    registry existed and is left as it is (the migration report lists it).
    """
    since = manifest.generated_at.isoformat()
    for doc_id in sorted(manifest.documents):
        record = manifest.documents[doc_id]
        directory, slug = record_address(record)
        held = registry.owner_of(directory, slug)
        if held is None or record.status == "current":
            registry = registry.assign(directory, slug, SlugOwner(owner=doc_id, assigned_at=since))
    return registry


def ensure_append_only(prior: SlugRegistry, new: SlugRegistry) -> None:
    """Raise unless ``new`` keeps every entry of ``prior`` with the same owner."""
    for directory, entries in sorted(prior.slugs.items()):
        for slug, entry in sorted(entries.items()):
            held = new.owner_of(directory, slug)
            if held != entry.owner:
                raise SlugOwnershipError(
                    f"{directory}/{slug}: owner {entry.owner} would become {held} — "
                    "a registry entry is never deleted and never changes owner (#871)",
                )


def read_registry(repo: Path) -> SlugRegistry | None:
    """The corpus registry, or ``None`` when the corpus has none yet."""
    path = repo / REGISTRY_FILENAME
    if not path.exists():
        return None
    try:
        return SlugRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ParseError(f"{path}: unreadable slug registry: {exc}") from exc


def registry_json(registry: SlugRegistry) -> str:
    """``slugs.json`` as written: sorted keys, two-space indent, final newline."""
    data = registry.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def replace_registry(registry: SlugRegistry, repo: Path) -> Path:
    """Write ``registry`` unless it drops or re-owns an entry of the file it replaces."""
    prior = read_registry(repo)
    if prior is not None:
        ensure_append_only(prior, registry)
    path = repo / REGISTRY_FILENAME
    atomic_write_text(path, registry_json(registry))
    return path


def update_slug_registry(repo: Path, manifest: Manifest) -> list[Path]:
    """Extend an existing registry with ``manifest``'s slugs; the paths to stage.

    No registry, no write: see the module docstring on bootstrapping.
    """
    registry = read_registry(repo)
    if registry is None:
        return []
    return [replace_registry(with_manifest(registry, manifest), repo)]


def registered_ownership(
    repo: Path,
    owners_by_dataset: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    """``owners_by_dataset`` with the registry's owners laid over it.

    The registry names the first holder, so it wins over ownership derived
    from the current manifest, which cannot see a record the manifest lost.
    """
    merged = {dataset: dict(owners) for dataset, owners in owners_by_dataset.items()}
    registry = read_registry(repo)
    if registry is None:
        return merged
    for dataset, directory in DIRECTORY_OF_DATASET.items():
        merged[dataset] = {**merged.get(dataset, {}), **registry.ownership(directory)}
    return merged
