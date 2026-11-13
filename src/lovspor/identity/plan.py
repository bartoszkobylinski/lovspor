"""Assemble the identity-migration plan for a corpus at a base commit (#871).

Read-only: the corpus is reached through ``lovspor.identity.corpus_git``.
See ``lovspor.identity.migration`` for what each step decides.
"""

import json
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.errors import IdentityMigrationError, IdentitySchemaError
from lovspor.identity.audit import CollisionAudit, PlanFacts, SlugFate, collision_audit, slug_fates
from lovspor.identity.corpus_git import (
    MANIFEST_PATH,
    list_files,
    read_blob,
    read_blobs,
    resolve_commit,
)
from lovspor.identity.history_marker import refuse_downgrade
from lovspor.identity.migration import (
    RegistryConflict,
    Rename,
    RestoredTombstone,
    build_registry,
    renamed_record,
    restored_record,
    restored_tombstones,
    u1_renames,
)
from lovspor.identity.registry import (
    DIRECTORY_OF_DATASET,
    SlugOwner,
    SlugRegistry,
    require_consistent,
    require_registry_covers,
)
from lovspor.identity.registry_history import require_registry_history
from lovspor.identity.repair_plan import HistoryRepair, RepairTarget, plan_history_repair
from lovspor.identity.schema import (
    ENFORCED_IDENTITY_SCHEMA,
    IDENTITY_FILENAME,
    LEGACY_IDENTITY_SCHEMA,
    REGISTRY_FILENAME,
    parse_declaration,
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
    identity_schema: int
    manifest: Manifest
    registry: SlugRegistry | None
    history: dict[str, str | None]


class Threshold(BaseModel):
    """The U1 threshold as given, resolved, and placed in the manifest walk."""

    model_config = ConfigDict(frozen=True)

    ref: str | None
    commit: str | None
    position: int
    problem: str | None


class MigrationPlan(BaseModel):
    """Everything the repair would change, and everything it found."""

    model_config = ConfigDict(frozen=True)

    base: BaseState
    threshold: Threshold
    manifest_revisions: int
    restored: list[RestoredTombstone]
    fates: list[SlugFate]
    renames: list[Rename]
    collisions: list[CollisionAudit]
    history_mismatches: list[HistoryMismatch]
    orphan_history_doc_ids: list[str]
    unrecoverable: list[str]
    registry_conflicts: list[RegistryConflict]
    planned_manifest: Manifest
    planned_registry: SlugRegistry
    history_repair: HistoryRepair
    changed_paths: list[str]

    @property
    def nothing_to_do(self) -> bool:
        """True when the repair would change nothing at the base (idempotence)."""
        return (
            self.planned_manifest.documents == self.base.manifest.documents
            and self.planned_registry == self.base.registry
            and self.base.identity_schema == ENFORCED_IDENTITY_SCHEMA
            and not self.history_repair.changed
        )

    @property
    def blocked(self) -> bool:
        """True when the walk cannot answer for every identity the corpus names,
        the U1 threshold is not a manifest revision of the walk, or the history
        repair would rewrite an append-only event or break its invariant."""
        history = self.history_repair
        return bool(
            self.unrecoverable
            or self.registry_conflicts
            or self.threshold.problem
            or history.conflicts
            or history.invariant_violations
        )

    @property
    def orphans_not_restored(self) -> list[str]:
        """Orphan-history ``doc_id``s the plan does not restore (expected: none)."""
        restored = {tomb.doc_id for tomb in self.restored}
        return [doc_id for doc_id in self.orphan_history_doc_ids if doc_id not in restored]


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


def _read_identity_schema(repo: Path, commit: str, registry: SlugRegistry | None) -> int:
    """The identity schema the base declares, consistent with its registry.

    The same rules the sync enforces: a declared base without ``slugs.json``,
    a ``slugs.json`` no declaration covers, an unknown declaration and an
    undeclared base whose history shows it was migrated (a reverted or deleted
    declaration) are refused, so the plan never builds on a corpus the sync
    would not run on.
    """
    where = f"{commit}:{IDENTITY_FILENAME}"
    try:
        schema = parse_declaration(read_blob(repo, commit, IDENTITY_FILENAME), where)
        require_consistent(schema, registry is not None, commit)
        if schema == LEGACY_IDENTITY_SCHEMA:
            refuse_downgrade(repo, commit)
    except IdentitySchemaError as exc:
        raise IdentityMigrationError(str(exc)) from exc
    return schema


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


def _require_covered(
    repo: Path, registry: SlugRegistry | None, manifest: Manifest, commit: str
) -> None:
    """Refuse a base registry that misses an address of the base manifest (truncation),
    or drops or rewrites an entry committed since the migration."""
    if registry is None:
        return
    try:
        require_registry_covers(registry, manifest, commit)
        require_registry_history(repo, registry, commit)
    except IdentitySchemaError as exc:
        raise IdentityMigrationError(str(exc)) from exc


def read_base(repo: Path, base_ref: str) -> BaseState:
    """The manifest, registry and slug histories at ``base_ref``."""
    commit = resolve_commit(repo, base_ref)
    registry = _read_registry(repo, commit)
    schema = _read_identity_schema(repo, commit, registry)
    manifest = _read_manifest(repo, commit)
    _require_covered(repo, registry, manifest, commit)
    return BaseState(
        commit=commit,
        identity_schema=schema,
        manifest=manifest,
        registry=registry,
        history=_read_history(repo, commit),
    )


def resolve_threshold(repo: Path, ref: str | None, walk: ManifestWalk) -> Threshold:
    """Place ``ref`` among the walked manifest revisions; a miss is a problem, not an error.

    ``None`` grandfathers nothing (position -1): every reuse falls under U1.
    A threshold that is not a manifest revision reachable from the base is
    recorded as a problem, which blocks the plan; the plan is still computed
    without grandfathering so the report shows what is at stake.
    """
    if ref is None:
        return Threshold(ref=None, commit=None, position=-1, problem=None)
    try:
        commit = resolve_commit(repo, ref)
    except IdentityMigrationError:
        return Threshold(ref=ref, commit=None, position=-1, problem=f"{ref} is not a commit")
    if commit not in walk.revisions:
        problem = f"{commit} is not a manifest revision reachable from the base"
        return Threshold(ref=ref, commit=commit, position=-1, problem=problem)
    return Threshold(ref=ref, commit=commit, position=walk.position(commit), problem=None)


class _Repair(BaseModel):
    """What the repair decides at the base: restored records, owners and renames."""

    model_config = ConfigDict(frozen=True)

    restored: list[RestoredTombstone]
    conflicts: list[RegistryConflict]
    renames: list[Rename]
    manifest: Manifest
    registry: SlugRegistry


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


def _repair(base: BaseState, walk: ManifestWalk, threshold: Threshold) -> _Repair:
    registry, conflicts = build_registry(walk, base.registry, threshold.position)
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
    if base.identity_schema != ENFORCED_IDENTITY_SCHEMA:
        paths.add(IDENTITY_FILENAME)
    for rename in repair.renames:
        paths |= _rename_paths(repo, base.commit, rename)
    return sorted(paths)


def _orphans(base: BaseState) -> list[str]:
    """``doc_id``s a slug history names that have no manifest record at the base."""
    named = {doc_id for doc_id in base.history.values() if doc_id is not None}
    return sorted(named - set(base.manifest.documents))


class _Findings(BaseModel):
    """What the plan reports beside the repair itself."""

    model_config = ConfigDict(frozen=True)

    fates: list[SlugFate]
    collisions: list[CollisionAudit]
    history_mismatches: list[HistoryMismatch]
    orphan_history_doc_ids: list[str]
    unrecoverable: list[str]


def _findings(base: BaseState, walk: ManifestWalk, repair: _Repair, threshold: int) -> _Findings:
    facts = PlanFacts(
        walk=walk,
        registry=repair.registry,
        planned=repair.manifest,
        restored=repair.restored,
        renames=repair.renames,
        threshold=threshold,
    )
    return _Findings(
        fates=slug_fates(facts),
        collisions=collision_audit(facts, base.manifest),
        history_mismatches=_history_mismatches(base.history, repair.registry),
        orphan_history_doc_ids=_orphans(base),
        unrecoverable=sorted(set(_orphans(base)) - set(walk.last_seen)),
    )


def _repair_targets(mismatches: list[HistoryMismatch], renames: list[Rename]) -> list[RepairTarget]:
    """The mismatched slug histories the plan rebuilds: every one whose slug has an owner."""
    targets: list[RepairTarget] = []
    for mismatch in mismatches:
        directory, slug = PurePosixPath(mismatch.path).parts[0], PurePosixPath(mismatch.path).stem
        if mismatch.owner is None:
            continue
        moved = {
            r.doc_id: r.to_slug for r in renames if (r.directory, r.from_slug) == (directory, slug)
        }
        target = RepairTarget(
            directory=directory,
            slug=slug,
            owner=mismatch.owner,
            file_doc_id=mismatch.doc_id_in_file,
            renamed=moved,
        )
        targets.append(target)
    return targets


def plan_migration(repo: Path, base_ref: str, threshold_ref: str | None = None) -> MigrationPlan:
    """The full repair plan for ``repo`` at ``base_ref``; reads, never writes."""
    base = read_base(repo, base_ref)
    walk = walk_manifests(repo, base.commit)
    threshold = resolve_threshold(repo, threshold_ref, walk)
    repair = _repair(base, walk, threshold)
    findings = _findings(base, walk, repair, threshold.position)
    targets = _repair_targets(findings.history_mismatches, repair.renames)
    history = plan_history_repair(repo, base.commit, targets)
    return MigrationPlan(
        base=base,
        threshold=threshold,
        manifest_revisions=len(walk.revisions),
        restored=repair.restored,
        renames=repair.renames,
        registry_conflicts=repair.conflicts,
        planned_manifest=repair.manifest,
        planned_registry=repair.registry,
        history_repair=history,
        changed_paths=sorted({*_changed_paths(repo, base, repair), *history.changed}),
        **dict(findings),
    )
