"""Assemble the identity-migration plan for a corpus at a base commit (#871).

Read-only: the corpus is reached through ``lovspor.identity.corpus_git``.
See ``lovspor.identity.migration`` for what each step decides.
"""

import json
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.errors import IdentityMigrationError
from lovspor.identity.corpus_git import (
    MANIFEST_PATH,
    list_files,
    read_blob,
    read_blobs,
    resolve_commit,
)
from lovspor.identity.migration import (
    Collision,
    LaterHolder,
    RegistryConflict,
    Rename,
    RestoredTombstone,
    ReusedSlug,
    build_registry,
    renamed_record,
    restored_record,
    restored_tombstones,
    u1_renames,
)
from lovspor.identity.registry import (
    DIRECTORY_OF_DATASET,
    REGISTRY_FILENAME,
    SlugOwner,
    SlugRegistry,
    record_address,
)
from lovspor.identity.walk import ManifestWalk, walk_manifests
from lovspor.storage.manifest import Manifest

_EMBEDDINGS = "embeddings"


class HistoryMismatch(BaseModel):
    """A ``history/<slug>.json`` whose ``doc_id`` is not the address's owner."""

    model_config = ConfigDict(frozen=True)

    path: str
    doc_id_in_file: str | None
    owner: str | None


class BaseState(BaseModel):
    """The corpus at the base commit, as the plan reads it."""

    model_config = ConfigDict(frozen=True)

    commit: str
    manifest: Manifest
    registry: SlugRegistry | None
    history: dict[str, str | None]


class MigrationPlan(BaseModel):
    """Everything the repair would change, and everything it found."""

    model_config = ConfigDict(frozen=True)

    base: BaseState
    manifest_revisions: int
    restored: list[RestoredTombstone]
    reused: list[ReusedSlug]
    renames: list[Rename]
    collisions: list[Collision]
    history_mismatches: list[HistoryMismatch]
    unrecoverable: list[str]
    registry_conflicts: list[RegistryConflict]
    planned_manifest: Manifest
    planned_registry: SlugRegistry
    changed_paths: list[str]

    @property
    def nothing_to_do(self) -> bool:
        """True when the repair would change nothing at the base (idempotence)."""
        return (
            self.planned_manifest.documents == self.base.manifest.documents
            and self.planned_registry == self.base.registry
        )

    @property
    def blocked(self) -> bool:
        """True when the walk cannot answer for every identity the corpus names."""
        return bool(self.unrecoverable or self.registry_conflicts)


def _json_blob(blob: bytes, what: str) -> object:
    try:
        return json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise IdentityMigrationError(f"unreadable {what}: {exc}") from exc


def _read_manifest(repo: Path, commit: str) -> Manifest:
    blob = read_blob(repo, commit, MANIFEST_PATH)
    if blob is None:
        raise IdentityMigrationError(f"{commit}: no {MANIFEST_PATH} at the base")
    try:
        return Manifest.model_validate(_json_blob(blob, f"{commit}:{MANIFEST_PATH}"))
    except ValidationError as exc:
        raise IdentityMigrationError(f"{commit}: invalid {MANIFEST_PATH}: {exc}") from exc


def _read_registry(repo: Path, commit: str) -> SlugRegistry | None:
    blob = read_blob(repo, commit, REGISTRY_FILENAME)
    if blob is None:
        return None
    try:
        return SlugRegistry.model_validate(_json_blob(blob, f"{commit}:{REGISTRY_FILENAME}"))
    except ValidationError as exc:
        raise IdentityMigrationError(f"{commit}: invalid {REGISTRY_FILENAME}: {exc}") from exc


def _history_doc_id(blob: bytes | None) -> str | None:
    """The ``doc_id`` a history file names; ``None`` for an unreadable one."""
    try:
        data = json.loads((blob or b"").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    value = data.get("doc_id") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def _read_history(repo: Path, commit: str) -> dict[str, str | None]:
    """``history/<slug>.json`` path -> the ``doc_id`` it names, for every tracked directory."""
    directories = [f"{d}/history" for d in sorted(DIRECTORY_OF_DATASET.values())]
    paths = [
        path
        for path in list_files(repo, commit, directories)
        if len(PurePosixPath(path).parts) == 3 and path.endswith(".json")  # noqa: PLR2004
    ]
    return {path: _history_doc_id(blob) for path, blob in read_blobs(repo, commit, paths).items()}


def read_base(repo: Path, base_ref: str) -> BaseState:
    """The manifest, registry and slug histories at ``base_ref``."""
    commit = resolve_commit(repo, base_ref)
    return BaseState(
        commit=commit,
        manifest=_read_manifest(repo, commit),
        registry=_read_registry(repo, commit),
        history=_read_history(repo, commit),
    )


class _Repair(BaseModel):
    """What the repair decides at the base: restored records, owners and renames."""

    model_config = ConfigDict(frozen=True)

    restored: list[RestoredTombstone]
    conflicts: list[RegistryConflict]
    renames: list[Rename]
    manifest: Manifest
    registry: SlugRegistry


def _later_holders(holders: list[str], owner: str, repair: _Repair) -> list[LaterHolder]:
    restored = {tomb.doc_id for tomb in repair.restored}
    return [
        LaterHolder(doc_id=h, status=repair.manifest.documents[h].status, restored=h in restored)
        for h in holders
        if h != owner
    ]


def _reused(walk: ManifestWalk, repair: _Repair) -> list[ReusedSlug]:
    """Every address more than one document held, with its owner and later holders."""
    reused: list[ReusedSlug] = []
    for directory, by_slug in sorted(walk.holders.items()):
        for slug, holders in sorted(by_slug.items()):
            owner = repair.registry.owner_of(directory, slug)
            later = [] if owner is None else _later_holders(holders, owner, repair)
            if owner is not None and later:
                since = walk.first_holder[directory][slug].commit
                reused.append(
                    ReusedSlug(
                        directory=directory,
                        slug=slug,
                        owner=owner,
                        owner_since_commit=since,
                        later_holders=later,
                    ),
                )
    return reused


def _collisions(restored: list[RestoredTombstone], base: Manifest) -> list[Collision]:
    """Restored tombstones whose address the base gives another document."""
    by_address: dict[tuple[str, str], list[str]] = {}
    for doc_id in sorted(base.documents):
        by_address.setdefault(record_address(base.documents[doc_id]), []).append(doc_id)
    return [
        Collision(
            restored_doc_id=tomb.doc_id,
            directory=tomb.directory,
            slug=tomb.slug,
            holder_at_base=holder,
            holder_status=base.documents[holder].status,
        )
        for tomb in restored
        for holder in by_address.get((tomb.directory, tomb.slug), [])
    ]


def _history_mismatches(
    history: dict[str, str | None], registry: SlugRegistry
) -> list[HistoryMismatch]:
    mismatches: list[HistoryMismatch] = []
    for path, doc_id in sorted(history.items()):
        parts = PurePosixPath(path).parts
        owner = registry.owner_of(parts[0], PurePosixPath(path).stem)
        if owner is None or owner != doc_id:
            mismatches.append(HistoryMismatch(path=path, doc_id_in_file=doc_id, owner=owner))
    return mismatches


def _planned_manifest(base: BaseState, walk: ManifestWalk, renames: list[Rename]) -> Manifest:
    """The base manifest with every lost record restored and every U1 rename applied."""
    documents = dict(base.manifest.documents)
    for doc_id in sorted(set(walk.last_seen) - set(documents)):
        documents[doc_id] = restored_record(doc_id, walk, base.commit)
    for rename in renames:
        documents[rename.doc_id] = renamed_record(documents[rename.doc_id], rename)
    return base.manifest.model_copy(update={"documents": dict(sorted(documents.items()))})


def _registry_with_renames(
    registry: SlugRegistry, renames: list[Rename], since: str
) -> SlugRegistry:
    for rename in renames:
        owner = SlugOwner(owner=rename.doc_id, assigned_at=since)
        registry = registry.assign(rename.directory, rename.to_slug, owner)
    return registry


def _repair(base: BaseState, walk: ManifestWalk) -> _Repair:
    registry, conflicts = build_registry(walk, base.registry)
    renames = u1_renames(base.manifest, registry)
    since = base.manifest.generated_at.isoformat()
    return _Repair(
        restored=restored_tombstones(walk, base.manifest, base.commit),
        conflicts=conflicts,
        renames=renames,
        manifest=_planned_manifest(base, walk, renames),
        registry=_registry_with_renames(registry, renames, since),
    )


def _rename_paths(repo: Path, commit: str, rename: Rename) -> set[str]:
    """The Markdown files a rename moves, and its embedding sidecar when there is one."""
    paths = {f"{rename.directory}/{slug}.md" for slug in (rename.from_slug, rename.to_slug)}
    sidecar = f"{rename.directory}/{_EMBEDDINGS}/{rename.from_slug}.bin"
    if read_blob(repo, commit, sidecar) is not None:
        paths.update({sidecar, f"{rename.directory}/{_EMBEDDINGS}/{rename.to_slug}.bin"})
    return paths


def _changed_paths(repo: Path, base: BaseState, repair: _Repair) -> list[str]:
    """Every corpus path the repair writes, moves or deletes, sorted."""
    paths: set[str] = set()
    if repair.manifest.documents != base.manifest.documents:
        paths.add(MANIFEST_PATH)
    if repair.registry != base.registry:
        paths.add(REGISTRY_FILENAME)
    for rename in repair.renames:
        paths |= _rename_paths(repo, base.commit, rename)
    return sorted(paths)


def plan_migration(repo: Path, base_ref: str) -> MigrationPlan:
    """The full repair plan for ``repo`` at ``base_ref``; reads, never writes."""
    base = read_base(repo, base_ref)
    walk = walk_manifests(repo, base.commit)
    repair = _repair(base, walk)
    return MigrationPlan(
        base=base,
        manifest_revisions=len(walk.revisions),
        restored=repair.restored,
        reused=_reused(walk, repair),
        renames=repair.renames,
        collisions=_collisions(repair.restored, base.manifest),
        history_mismatches=_history_mismatches(base.history, repair.registry),
        unrecoverable=sorted({i for i in base.history.values() if i and i not in walk.last_seen}),
        registry_conflicts=repair.conflicts,
        planned_manifest=repair.manifest,
        planned_registry=repair.registry,
        changed_paths=_changed_paths(repo, base, repair),
    )
