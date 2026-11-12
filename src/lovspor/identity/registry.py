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

Whether the registry is enforced is decided by the corpus's declared
identity schema (``lovspor.identity.schema``), read through
``corpus_identity``: never by whether ``slugs.json`` happens to exist.
"""

import json
import logging
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import IdentitySchemaError, ParseError, SlugOwnershipError
from lovspor.identity.schema import (
    ENFORCED_IDENTITY_SCHEMA,
    IDENTITY_FILENAME,
    LEGACY_IDENTITY_SCHEMA,
    declared_schema,
)
from lovspor.storage.manifest import Manifest, ManifestRecord

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = "slugs.json"
REGISTRY_SCHEMA_VERSION = 1
#: The corpus directory of each tracked dataset (``document_io`` writes there).
DIRECTORY_OF_DATASET = {
    "gjeldende-lover": "lover",
    "gjeldende-sentrale-forskrifter": "forskrifter",
}
#: The owner held the slug first.
FIRST_HOLDER: Final = "first-holder"
#: The owner was set by the migration's U1 threshold although an earlier
#: document held the slug: a migration exception, not a claim of first holding.
THRESHOLD_EXCEPTION: Final = "threshold-exception"
OwnershipBasis = Literal["first-holder", "threshold-exception"]


class PriorHolder(BaseModel):
    """A document that held a slug before its owner, with the manifest evidence.

    Recorded when the migration's U1 threshold keeps a slug with the document
    that held it at the threshold although an earlier document held it first:
    the earlier identity violation is kept on record, never dropped (#871).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    first_commit: str
    last_commit: str


class SlugOwner(BaseModel):
    """Who owns one slug, since which manifest (its ``generated_at``), on what
    basis, and who held it before the owner (``prior_holders``, oldest first).

    ``basis`` is ``first-holder`` for an owner that held the slug first and
    ``threshold-exception`` for one the migration's U1 threshold kept although
    the recorded prior holders came first. The two go together: prior holders
    exist exactly when the basis is the threshold exception.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str
    assigned_at: str
    basis: OwnershipBasis = FIRST_HOLDER
    prior_holders: tuple[PriorHolder, ...] = ()

    @model_validator(mode="after")
    def _basis_matches_prior_holders(self) -> "SlugOwner":
        if (self.basis == THRESHOLD_EXCEPTION) != bool(self.prior_holders):
            raise ValueError(
                f"basis {self.basis!r} does not match {len(self.prior_holders)} prior "
                f"holder(s): only a {THRESHOLD_EXCEPTION} has prior holders",
            )
        return self


class SlugRegistry(BaseModel):
    """``directory -> slug -> owner``; append-only, owners immutable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = REGISTRY_SCHEMA_VERSION
    slugs: dict[str, dict[str, SlugOwner]] = {}

    @field_validator("schema_version")
    @classmethod
    def _known_schema(cls, value: int) -> int:
        if value != REGISTRY_SCHEMA_VERSION:
            raise ValueError(f"unknown slug registry schema_version {value}")
        return value

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
    """Raise unless ``new`` keeps every entry of ``prior`` unchanged.

    An entry is immutable as a whole: its owner, its ``assigned_at`` and its
    recorded prior holders.
    """
    for directory, entries in sorted(prior.slugs.items()):
        for slug, entry in sorted(entries.items()):
            kept = new.slugs.get(directory, {}).get(slug)
            if kept != entry:
                held = None if kept is None else kept.owner
                raise SlugOwnershipError(
                    f"{directory}/{slug}: entry owned by {entry.owner} would become {held} "
                    "or change — a registry entry is never deleted or rewritten (#871)",
                )


def read_registry(repo: Path) -> SlugRegistry | None:
    """The registry file as it is on disk, or ``None`` when there is none.

    A raw read for writers that compare against the file they replace; whether
    the corpus must have one is ``corpus_identity``'s question.
    """
    path = repo / REGISTRY_FILENAME
    if not path.exists():
        return None
    try:
        return SlugRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ParseError(f"{path}: unreadable slug registry: {exc}") from exc


def registry_json(registry: SlugRegistry) -> str:
    """``slugs.json`` as written: sorted keys, two-space indent, final newline.

    An entry without prior holders omits the key, so the common entry stays
    ``{"assigned_at": …, "owner": …}``.
    """
    data = registry.model_dump(mode="json")
    for entries in data["slugs"].values():
        for entry in entries.values():
            if not entry["prior_holders"]:
                del entry["prior_holders"]
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def replace_registry(registry: SlugRegistry, repo: Path) -> Path:
    """Write ``registry`` unless it drops or re-owns an entry of the file it replaces."""
    prior = read_registry(repo)
    if prior is not None:
        ensure_append_only(prior, registry)
    path = repo / REGISTRY_FILENAME
    atomic_write_text(path, registry_json(registry))
    return path


class LegacyCorpus(BaseModel):
    """A corpus at identity schema 0 (no ``identity.json``): no registry is enforced."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[0] = LEGACY_IDENTITY_SCHEMA


class EnforcedCorpus(BaseModel):
    """A corpus at identity schema 1: its registry is enforced."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = ENFORCED_IDENTITY_SCHEMA
    registry: SlugRegistry


CorpusIdentity = LegacyCorpus | EnforcedCorpus


def require_consistent(schema: int, has_registry: bool, where: str) -> None:
    """Refuse a registry the declaration does not cover, or a declaration without one."""
    if schema == LEGACY_IDENTITY_SCHEMA and has_registry:
        raise IdentitySchemaError(
            f"{where}: {REGISTRY_FILENAME} exists but no {IDENTITY_FILENAME} declares the "
            f"identity schema; refusing to treat a registry as unenforced (#871)",
        )
    if schema == ENFORCED_IDENTITY_SCHEMA and not has_registry:
        raise IdentitySchemaError(
            f"{where}: {IDENTITY_FILENAME} declares identity schema {schema} but there is "
            f"no {REGISTRY_FILENAME}; refusing to run without slug ownership (#871)",
        )


def corpus_identity(repo: Path) -> CorpusIdentity:
    """The corpus's declared identity schema, with its registry when enforced.

    Fails closed (``IdentitySchemaError``) on an unknown or unreadable
    declaration, a declared corpus without a readable registry, and a registry
    no declaration covers.
    """
    schema = declared_schema(repo)
    try:
        registry = read_registry(repo)
    except ParseError as exc:
        if schema == LEGACY_IDENTITY_SCHEMA:
            registry = None
        else:
            raise IdentitySchemaError(f"{exc}; identity schema {schema} requires it") from exc
    require_consistent(schema, (repo / REGISTRY_FILENAME).exists(), str(repo))
    if registry is None:
        return LegacyCorpus()
    return EnforcedCorpus(registry=registry)


def update_slug_registry(repo: Path, manifest: Manifest) -> list[Path]:
    """Extend an enforced corpus's registry with ``manifest``'s slugs; the paths to stage.

    A legacy corpus gets no registry: see the module docstring on bootstrapping.
    """
    identity = corpus_identity(repo)
    if isinstance(identity, LegacyCorpus):
        return []
    return [replace_registry(with_manifest(identity.registry, manifest), repo)]


def registered_ownership(
    repo: Path,
    owners_by_dataset: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    """``owners_by_dataset`` with the registry's owners laid over it.

    The registry names the first holder, so it wins over ownership derived
    from the current manifest, which cannot see a record the manifest lost.
    """
    merged = {dataset: dict(owners) for dataset, owners in owners_by_dataset.items()}
    identity = corpus_identity(repo)
    if isinstance(identity, LegacyCorpus):
        logger.info(
            "legacy corpus: no %s, identity schema 0; slug ownership is not enforced "
            "until the #871 migration declares schema %d",
            IDENTITY_FILENAME,
            ENFORCED_IDENTITY_SCHEMA,
        )
        return merged
    for dataset, directory in DIRECTORY_OF_DATASET.items():
        merged[dataset] = {**merged.get(dataset, {}), **identity.registry.ownership(directory)}
    return merged
