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

Commit strategy:

- ``settings.git_commit_mode == "per-document"`` (default): one commit
  per add/update/rename/remove plus a final ``sync: update manifest``
  commit. This makes ``git log <file>.md`` per-act and ``git blame``
  attribute meaningful.
- ``settings.git_commit_mode == "single"``: one bulk commit per sync
  with a summary message. Useful for syncs that touch hundreds of
  documents and don't need per-act history (e.g., daily refresh
  imports).
- **Migration override**: when any rename has ``prior.slug is None``
  (Sprint 3 manifest with no slug field), the orchestrator forces a
  single bulk commit ``migration: rename N documents to slug-based
  filenames`` regardless of ``git_commit_mode``. This keeps the
  Sprint 3 -> Sprint 4 transition as one auditable event in history
  rather than thousands of individual renames.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.errors import ConfigError
from lovspor.extraction.tarball import iter_tarball_xml
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
    """One sync-action against the corpus, used for per-document commits."""

    action: str  # "add" | "update" | "rename" | "remove"
    doc_type: str  # "lov" | "forskrift"
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
                slug=prior_record.slug or doc_id,
                paths=(path,),
            ),
        )

    manifest = Manifest(generated_at=now, documents=new_records)
    write_manifest(manifest, manifest_path)
    index_paths = [
        generate_index(settings.lovverk_repo_path, dataset, manifest)
        for dataset in _TRACKED_DATASETS
    ]
    extra_paths = [manifest_path, *index_paths]

    _commit_actions(
        settings.lovverk_repo_path,
        actions,
        extra_paths,
        settings,
        is_migration=_is_migration(prior, renamed),
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


def _commit_actions(
    repo: Path,
    actions: list[_DocAction],
    extra_paths: list[Path],
    settings: Settings,
    *,
    is_migration: bool,
) -> None:
    """Stage and commit per the configured policy.

    ``extra_paths`` are the manifest + INDEX files: they get bundled
    into the bulk commit (single / migration mode) or into the final
    'sync: update manifest' commit (per-document mode).

    - Migration scenario (Sprint 3 manifest -> Sprint 4 slug filenames):
      one bulk commit so the corpus history shows a single 'migration'
      event rather than thousands of individual renames.
    - settings.git_commit_mode == 'single': one bulk commit per sync.
    - settings.git_commit_mode == 'per-document': one commit per
      ``_DocAction`` (add/update/rename/remove), then a final commit
      bundling manifest + INDEX updates.
    """
    if not actions:
        return  # pragma: no cover - run_sync early-returns when nothing changed
    if is_migration:
        _commit_bulk(repo, actions, extra_paths, _migration_message(actions))
        return
    if settings.git_commit_mode == "single":
        _commit_bulk(repo, actions, extra_paths, _single_message(actions))
        return
    _commit_per_document(repo, actions, extra_paths)


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


def _commit_per_document(
    repo: Path,
    actions: list[_DocAction],
    extra_paths: list[Path],
) -> None:
    for action in actions:
        git_add(repo, list(action.paths))
        if has_staged_changes(repo):
            git_commit_msg(repo, action.commit_message)
    git_add(repo, extra_paths)
    if has_staged_changes(repo):
        git_commit_msg(repo, "sync: update manifest and index")


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
