"""End-to-end sync pipeline: download -> extract -> diff -> render -> commit.

``run_sync(settings)`` is the single public entry point. It composes:

- ``LovdataClient``      — download the current tarballs
- ``iter_tarball_xml``   — extract XML members safely
- ``hash_normalized_xml``— per-document content fingerprint
- ``read_manifest`` /
  ``write_manifest``     — change-detection state
- ``detect_changes``     — new / changed / removed / unchanged partition
- ``render_full_document`` + ``write_document`` / ``delete_document``
- ``git_commit``         — stage and commit

Both empty-corpus seed and incremental updates flow through the same
function: the change detector treats a missing manifest as "everything
is new", so ``seed`` is just ``sync`` on an empty manifest.

Commit strategy (decisions.md §12a + §12d):

- ``settings.git_commit_mode == "per-document"`` (default): one commit
  per add/update/rename/remove, then one final commit bundling
  manifest + INDEX + per-act history (``sync: update manifest, index,
  and history``). Per-doc commits land first so ``git log --follow``
  can see them when the history phase runs.
- ``settings.git_commit_mode == "single"``: one bulk docs+meta commit
  (``sync: N new, M changed, K renamed, L removed``) followed by one
  ``sync: update history for N documents`` follow-up. Two commits are
  required because history can only be extracted once the docs commit
  exists (chicken-and-egg).
- **Sprint 4 migration override**: when any rename has ``prior.slug
  is None`` (legacy Sprint-3 manifest), the orchestrator forces a
  single ``migration: rename N documents to slug-based filenames``
  commit + a history follow-up. Same chicken-and-egg constraint as
  single mode.
- **Sprint 5 history migration**: when the corpus has prior current
  docs but no ``<dataset>/history/`` dir on disk, ``run_sync`` first
  emits a standalone ``migration: generate history for N documents``
  commit BEFORE doing any regular sync work for that day. Triggers
  once on the first sync after PR-B ships; subsequent syncs see
  populated history dirs and skip.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.errors import ConfigError
from lovspor.extraction.tarball import iter_tarball_xml
from lovspor.history import HistoryRecord, extract_history, write_history
from lovspor.parsing.xml_normalizer import hash_normalized_xml
from lovspor.rendering.document import (
    FrontmatterContext,
    extract_xml_metadata,
)
from lovspor.rendering.slug import derive_slug, resolve_collisions
from lovspor.settings import Settings
from lovspor.sources.lovdata import LovdataArchive, LovdataClient
from lovspor.storage.manifest import (
    Manifest,
    ManifestRecord,
    read_manifest,
    write_manifest,
)
from lovspor.sync.change_detector import detect_changes
from lovspor.sync.document_io import (
    dataset_dir,
    delete_document,
    doc_type_for_dataset,
    document_path,
    generate_index,
    render_full_document,
    write_document,
)
from lovspor.sync.git_commit import add as git_add
from lovspor.sync.git_commit import commit as git_commit_msg
from lovspor.sync.git_commit import has_staged_changes

_TRACKED_DATASETS = (
    "gjeldende-lover",
    "gjeldende-sentrale-forskrifter",
)
_MANIFEST_FILENAME = "manifest.json"


class SyncReport(BaseModel):
    """Outcome of a ``run_sync`` invocation."""

    model_config = ConfigDict(frozen=True)

    new_count: int
    changed_count: int
    removed_count: int
    unchanged_count: int


@dataclass(frozen=True)
class _UpstreamDoc:
    doc_id: str
    source_dataset: str
    xml_bytes: bytes
    xml_hash: str
    slug: str
    title: str


@dataclass(frozen=True)
class _DocAction:
    """One sync-action against the corpus, used for per-document commits.

    ``doc_id`` lets the post-commit history phase look up the matching
    manifest record without having to re-scan all upstream metadata.
    """

    action: str  # "add" | "update" | "rename" | "remove"
    doc_type: str  # "lov" | "forskrift"
    doc_id: str
    slug: str
    paths: tuple[Path, ...]

    @property
    def commit_message(self) -> str:
        return f"{self.action}({self.doc_type}): {self.slug}"


def run_sync(settings: Settings) -> SyncReport:
    """Execute a full sync cycle against the configured lovverk repo.

    If upstream has no new / changed / removed documents, the manifest
    on disk is not rewritten and no commit is created — the sync is a
    true no-op at the filesystem and git layers. This is the contract
    the scheduled workflow relies on to detect 'nothing to do' runs.
    """
    _ensure_corpus_git_repo(settings.lovverk_repo_path)
    manifest_path = settings.lovverk_repo_path / _MANIFEST_FILENAME
    prior = _load_or_empty_manifest(manifest_path)

    # Sprint 5 history migration: triggers once on the first sync after
    # PR-B ships, when the corpus has prior docs but no history/ dirs.
    # Generates a standalone "migration: generate history for N
    # documents" commit BEFORE any regular sync work today. Subsequent
    # syncs see populated history/ dirs and skip this branch.
    if _needs_sprint5_history_migration(settings.lovverk_repo_path, prior):
        prior = _run_sprint5_history_migration(
            settings.lovverk_repo_path,
            manifest_path,
            prior,
            datetime.now(UTC),
        )

    cache_dir = settings.data_dir / "cache" / "archives"
    cache_dir.mkdir(parents=True, exist_ok=True)
    upstream = _collect_upstream(settings, cache_dir)
    upstream_hashes = {doc_id: u.xml_hash for doc_id, u in upstream.items()}
    changes = detect_changes(upstream_hashes, prior)

    renamed = _identify_renames(changes.unchanged, prior, upstream)
    if not (changes.new or changes.changed or changes.removed or renamed):
        return SyncReport(
            new_count=0,
            changed_count=0,
            removed_count=0,
            unchanged_count=len(changes.unchanged),
        )

    now = datetime.now(UTC)
    new_records = _carry_unchanged(prior, changes.unchanged)
    actions: list[_DocAction] = []

    for doc_id in changes.new:
        upstream_doc = upstream[doc_id]
        record, path = _write_one(settings, upstream_doc, now)
        new_records[doc_id] = record
        actions.append(
            _DocAction(
                action="add",
                doc_type=record.doc_type,
                doc_id=doc_id,
                slug=upstream_doc.slug,
                paths=(path,),
            ),
        )

    for doc_id in changes.changed:
        upstream_doc = upstream[doc_id]
        prior_record = prior.documents[doc_id]
        old_path = settings.lovverk_repo_path / prior_record.markdown_path
        record, new_path = _write_one(settings, upstream_doc, now)
        new_records[doc_id] = record
        paths: tuple[Path, ...]
        if old_path != new_path:
            delete_document(old_path)
            paths = (old_path, new_path)
        else:
            paths = (new_path,)
        actions.append(
            _DocAction(
                action="update",
                doc_type=record.doc_type,
                doc_id=doc_id,
                slug=upstream_doc.slug,
                paths=paths,
            ),
        )

    for doc_id in renamed:
        upstream_doc = upstream[doc_id]
        prior_record = prior.documents[doc_id]
        old_path = settings.lovverk_repo_path / prior_record.markdown_path
        delete_document(old_path)
        record, new_path = _write_one(settings, upstream_doc, now)
        new_records[doc_id] = record
        actions.append(
            _DocAction(
                action="rename",
                doc_type=record.doc_type,
                doc_id=doc_id,
                slug=upstream_doc.slug,
                paths=(old_path, new_path),
            ),
        )

    for doc_id in changes.removed:
        prior_record = prior.documents[doc_id]
        path = _delete_one(settings, prior, doc_id)
        new_records[doc_id] = _tombstone(prior_record)
        actions.append(
            _DocAction(
                action="remove",
                doc_type=prior_record.doc_type,
                doc_id=doc_id,
                slug=prior_record.slug or doc_id,
                paths=(path,),
            ),
        )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=manifest_path,
        actions=actions,
        new_records=new_records,
        now=now,
        is_sprint4_migration=_is_migration(prior, renamed),
    )

    return SyncReport(
        new_count=len(changes.new),
        changed_count=len(changes.changed),
        removed_count=len(changes.removed),
        unchanged_count=len(changes.unchanged),
    )


def _ensure_corpus_git_repo(path: Path) -> None:
    if not (path / ".git").exists():
        raise ConfigError(
            f"{path} is not a git repository (expected .git/ inside it)",
        )


def _load_or_empty_manifest(path: Path) -> Manifest:
    if not path.exists():
        return Manifest(
            generated_at=datetime.fromtimestamp(0, tz=UTC),
            documents={},
        )
    return read_manifest(path)


def _collect_upstream(
    settings: Settings,
    cache_dir: Path,
) -> dict[str, _UpstreamDoc]:
    """Download both tarballs and return doc_id -> _UpstreamDoc.

    Slug collisions are resolved **per dataset**, not globally. Each
    dataset writes into its own subdirectory (``lover/``, ``forskrifter/``)
    so the same bare slug across datasets is fine — they cannot conflict
    on the filesystem. Resolving globally would force unnecessary
    ``-2`` suffixes and cause avoidable rename history when, for example,
    a law and a regulation share a kortform.
    """
    client = LovdataClient(
        timeout_seconds=settings.http_timeout_seconds,
        user_agent=settings.http_user_agent,
    )
    by_dataset: dict[str, list[_UpstreamDoc]] = {}
    with client:
        catalogue = {a.filename: a for a in client.list_datasets()}
        for dataset in _TRACKED_DATASETS:
            filename = f"{dataset}.tar.bz2"
            archive = _pick_archive(catalogue, filename)
            tar_path = client.download(archive, cache_dir).path
            by_dataset[dataset] = _index_tarball(tar_path, dataset)

    upstream: dict[str, _UpstreamDoc] = {}
    for docs in by_dataset.values():
        base_slugs = {doc.doc_id: doc.slug for doc in docs}
        final_slugs = resolve_collisions(base_slugs)
        for doc in docs:
            upstream[doc.doc_id] = _with_slug(doc, final_slugs[doc.doc_id])
    return upstream


def _with_slug(doc: _UpstreamDoc, slug: str) -> _UpstreamDoc:
    return _UpstreamDoc(
        doc_id=doc.doc_id,
        source_dataset=doc.source_dataset,
        xml_bytes=doc.xml_bytes,
        xml_hash=doc.xml_hash,
        slug=slug,
        title=doc.title,
    )


def _pick_archive(
    catalogue: dict[str, LovdataArchive],
    filename: str,
) -> LovdataArchive:
    if filename not in catalogue:
        raise ConfigError(
            f"upstream catalogue is missing expected archive {filename!r}",
        )
    return catalogue[filename]


def _index_tarball(tar_path: Path, dataset: str) -> list[_UpstreamDoc]:
    docs: list[_UpstreamDoc] = []
    for member in iter_tarball_xml(tar_path):
        doc_id = Path(member.name).stem
        metadata = extract_xml_metadata(member.content)
        base_slug = derive_slug(
            metadata["short_title"],
            metadata["title"],
            doc_id,
        )
        docs.append(
            _UpstreamDoc(
                doc_id=doc_id,
                source_dataset=dataset,
                xml_bytes=member.content,
                xml_hash=hash_normalized_xml(member.content),
                slug=base_slug,
                title=metadata["title"],
            ),
        )
    return docs


def _write_one(
    settings: Settings,
    upstream: _UpstreamDoc,
    now: datetime,
) -> tuple[ManifestRecord, Path]:
    path = document_path(
        settings.lovverk_repo_path,
        upstream.source_dataset,
        upstream.slug,
    )
    doc_type = doc_type_for_dataset(upstream.source_dataset)
    context = FrontmatterContext(
        doc_id=upstream.doc_id,
        slug=upstream.slug,
        doc_type=doc_type,
        xml_hash=upstream.xml_hash,
        source_dataset=upstream.source_dataset,
        retrieved_at=now,
    )
    write_document(path, render_full_document(upstream.xml_bytes, context))
    record = ManifestRecord(
        doc_type=doc_type,
        xml_hash=upstream.xml_hash,
        markdown_path=str(path.relative_to(settings.lovverk_repo_path)),
        source_dataset=upstream.source_dataset,
        last_seen=now,
        status="current",
        slug=upstream.slug,
        title=upstream.title,
    )
    return record, path


def _delete_one(
    settings: Settings,
    prior: Manifest,
    doc_id: str,
) -> Path:
    prior_record = prior.documents[doc_id]
    path = settings.lovverk_repo_path / prior_record.markdown_path
    delete_document(path)
    return path


def _identify_renames(
    unchanged_ids: list[str],
    prior: Manifest,
    upstream: dict[str, _UpstreamDoc],
) -> list[str]:
    """Find unchanged-content docs whose slug (and therefore path) changed.

    Triggers on: (a) prior.slug is None — legacy Sprint 3 manifest with no
    slug field; (b) prior.slug differs from the current upstream slug —
    Lovdata renamed the kortform. Either way we need to delete the old
    file and write the new path even though the content hash matches.
    """
    renames: list[str] = []
    for doc_id in unchanged_ids:
        prior_record = prior.documents[doc_id]
        if prior_record.slug != upstream[doc_id].slug:
            renames.append(doc_id)
    return renames


def _carry_unchanged(
    prior: Manifest,
    unchanged_ids: list[str],
) -> dict[str, ManifestRecord]:
    """Preserve records for unchanged docs verbatim.

    ``last_seen`` is kept as the prior value because the semantic is
    'the last time we observed this specific content'. An unchanged sync
    observes no new content for these docs. Bumping the timestamp would
    change the manifest JSON byte-for-byte on every run and defeat the
    'no upstream change -> no commit' invariant.
    """
    return {doc_id: prior.documents[doc_id] for doc_id in unchanged_ids}


def _tombstone(old: ManifestRecord) -> ManifestRecord:
    """Mark a record as removed. Preserves all original fields so the
    audit trail remains intact: same xml_hash, same markdown_path,
    same last_seen (when the content was last observed), same slug and
    title (for cross-reference and historical INDEX inspection), only
    status flips."""
    return ManifestRecord(
        doc_type=old.doc_type,
        xml_hash=old.xml_hash,
        markdown_path=old.markdown_path,
        source_dataset=old.source_dataset,
        last_seen=old.last_seen,
        status="removed",
        slug=old.slug,
        title=old.title,
    )


def _is_migration(prior: Manifest, renamed: list[str]) -> bool:
    """Sprint 3 -> Sprint 4 slug migration detected when any renamed
    record has prior.slug=None (legacy field-less record).
    """
    return any(prior.documents[doc_id].slug is None for doc_id in renamed)


def _commit_with_history(
    settings: Settings,
    *,
    repo: Path,
    manifest_path: Path,
    actions: list[_DocAction],
    new_records: dict[str, ManifestRecord],
    now: datetime,
    is_sprint4_migration: bool,
) -> None:
    """Mode-aware commit pipeline that bundles per-act history.

    History generation has a chicken-and-egg constraint: it walks
    ``git log --follow`` which can only see commits that already
    exist. Per-document mode satisfies this naturally — per-doc
    commits land first, then history sees them, then one final commit
    bundles manifest + INDEX + history. Single mode and the Sprint 4
    rename migration cannot satisfy it in a single commit, so for
    those modes we do two commits: docs+meta first, then a follow-up
    ``sync: update history for N documents`` once history catches up.
    The follow-up rewrites the manifest with the now-known
    ``total_changes`` / ``last_changed`` fields.
    """
    if not actions:
        return  # pragma: no cover - run_sync early-returns when nothing changed

    target_doc_ids = [a.doc_id for a in actions if a.action != "remove"]

    if is_sprint4_migration or settings.git_commit_mode == "single":
        message = _migration_message(actions) if is_sprint4_migration else _single_message(actions)
        manifest = Manifest(generated_at=now, documents=new_records)
        write_manifest(manifest, manifest_path)
        index_paths = [generate_index(repo, dataset, manifest) for dataset in _TRACKED_DATASETS]
        _commit_bulk(repo, actions, [manifest_path, *index_paths], message)
        _commit_history_followup(repo, manifest_path, new_records, target_doc_ids, now)
        return

    _commit_per_doc_actions_only(repo, actions)
    new_records, history_paths = _generate_and_apply_history(repo, new_records, target_doc_ids)
    manifest = Manifest(generated_at=now, documents=new_records)
    write_manifest(manifest, manifest_path)
    index_paths = [generate_index(repo, dataset, manifest) for dataset in _TRACKED_DATASETS]
    git_add(repo, [manifest_path, *index_paths, *history_paths])
    if has_staged_changes(repo):
        git_commit_msg(repo, "sync: update manifest, index, and history")


def _commit_history_followup(
    repo: Path,
    manifest_path: Path,
    new_records: dict[str, ManifestRecord],
    target_doc_ids: list[str],
    now: datetime,
) -> None:
    """Generate history for ``target_doc_ids`` after the docs+meta
    commit landed, rewrite the manifest with ``total_changes`` /
    ``last_changed``, and commit the result. Called only by single /
    Sprint-4-migration paths where history cannot ride along in the
    primary commit."""
    new_records, history_paths = _generate_and_apply_history(repo, new_records, target_doc_ids)
    if not history_paths:
        return
    manifest = Manifest(generated_at=now, documents=new_records)
    write_manifest(manifest, manifest_path)
    git_add(repo, [manifest_path, *history_paths])
    if has_staged_changes(repo):
        git_commit_msg(
            repo,
            f"sync: update history for {len(history_paths) // 2} documents",
        )


def _commit_per_doc_actions_only(
    repo: Path,
    actions: list[_DocAction],
) -> None:
    """Run per-document commits without the final manifest commit. The
    caller does the final commit after generating history (which needs
    these per-doc commits to exist before ``git log`` can see them)."""
    for action in actions:
        git_add(repo, list(action.paths))
        if has_staged_changes(repo):
            git_commit_msg(repo, action.commit_message)


def _commit_bulk(
    repo: Path,
    actions: list[_DocAction],
    extra_paths: list[Path],
    message: str,
) -> None:
    all_paths = [p for action in actions for p in action.paths]
    all_paths.extend(extra_paths)
    git_add(repo, all_paths)
    if not has_staged_changes(repo):  # pragma: no cover - defensive
        return
    git_commit_msg(repo, message)


def _generate_and_apply_history(
    repo: Path,
    records: dict[str, ManifestRecord],
    target_doc_ids: list[str],
) -> tuple[dict[str, ManifestRecord], list[Path]]:
    """For each target ``doc_id``, extract history from git, write the
    history files, and return updated records with ``total_changes`` /
    ``last_changed`` populated plus all written paths for staging.

    Tombstones (``status='removed'``) and slug-less records (legacy
    Sprint 3 format) are skipped — see decisions.md §12d for the
    intentional Sprint 5 scope cut on tombstone history.
    """
    updated = dict(records)
    written_paths: list[Path] = []
    for doc_id in target_doc_ids:
        if doc_id not in updated:
            continue
        record = updated[doc_id]
        if record.status == "removed" or record.slug is None:
            continue
        history = extract_history(
            repo_path=repo,
            current_path=record.markdown_path,
            doc_id=doc_id,
            slug=record.slug,
        )
        history_target_dir = dataset_dir(repo, record.source_dataset)
        json_path, md_path = write_history(history, history_target_dir)
        written_paths.extend([json_path, md_path])
        updated[doc_id] = _record_with_history(record, history)
    return updated, written_paths


def _record_with_history(
    record: ManifestRecord,
    history: HistoryRecord,
) -> ManifestRecord:
    """Apply ``total_changes`` and ``last_changed`` to a manifest
    record from a freshly extracted history. Pure: returns a new frozen
    record, never mutates."""
    last_changed = history.events[0].date.isoformat() if history.events else None
    return record.model_copy(
        update={
            "total_changes": len(history.events),
            "last_changed": last_changed,
        },
    )


def _needs_sprint5_history_migration(repo: Path, prior: Manifest) -> bool:
    """True if the corpus has any current docs whose dataset's
    ``history/`` directory is missing on disk. Triggers once (first
    sync after PR-B ships); subsequent syncs see populated dirs and
    skip the migration branch.

    Risk-mitigation note: a partial-failure migration leaves the dir
    present but only some docs populated. Re-run is not automatic in
    this iteration — flagged in decisions.md §12d.
    """
    if not prior.documents:
        return False
    datasets_in_use = {
        record.source_dataset for record in prior.documents.values() if record.status == "current"
    }
    return any(not (dataset_dir(repo, dataset) / "history").exists() for dataset in datasets_in_use)


def _run_sprint5_history_migration(
    repo: Path,
    manifest_path: Path,
    prior: Manifest,
    now: datetime,
) -> Manifest:
    """Bulk-generate history for every current doc in ``prior``. One
    commit. Returns the updated manifest (which the caller uses going
    forward instead of the original prior)."""
    current_doc_ids = [
        doc_id
        for doc_id, record in prior.documents.items()
        if record.status == "current" and record.slug is not None
    ]
    new_records, history_paths = _generate_and_apply_history(repo, prior.documents, current_doc_ids)
    if not history_paths:
        return prior
    new_manifest = Manifest(generated_at=now, documents=new_records)
    write_manifest(new_manifest, manifest_path)
    git_add(repo, [manifest_path, *history_paths])
    if has_staged_changes(repo):
        git_commit_msg(
            repo,
            f"migration: generate history for {len(current_doc_ids)} documents",
        )
    return new_manifest


def _migration_message(actions: list[_DocAction]) -> str:
    return f"migration: rename {len(actions)} documents to slug-based filenames"


def _single_message(actions: list[_DocAction]) -> str:
    counts: dict[str, int] = {"add": 0, "update": 0, "rename": 0, "remove": 0}
    for action in actions:
        counts[action.action] += 1
    return (
        f"sync: {counts['add']} new, {counts['update']} changed, "
        f"{counts['rename']} renamed, {counts['remove']} removed"
    )
