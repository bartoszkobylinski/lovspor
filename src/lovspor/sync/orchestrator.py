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

Commit strategy for this PR is ``single``: one commit per run covering
all changed documents plus the manifest. ``per-document`` mode is
deferred to a follow-up commit so this orchestrator stays reviewable.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.errors import ConfigError
from lovspor.extraction.tarball import iter_tarball_xml
from lovspor.parsing.xml_normalizer import hash_normalized_xml
from lovspor.rendering.document import FrontmatterContext
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

    if not (changes.new or changes.changed or changes.removed):
        return SyncReport(
            new_count=0,
            changed_count=0,
            removed_count=0,
            unchanged_count=len(changes.unchanged),
        )

    now = datetime.now(UTC)
    new_records = _carry_unchanged(prior, changes.unchanged)
    staged: list[Path] = []

    for doc_id in changes.new + changes.changed:
        upstream_doc = upstream[doc_id]
        record, path = _write_one(settings, upstream_doc, now)
        new_records[doc_id] = record
        staged.append(path)

    for doc_id in changes.removed:
        path = _delete_one(settings, prior, doc_id)
        staged.append(path)
        new_records[doc_id] = _tombstone(prior.documents[doc_id])

    manifest = Manifest(generated_at=now, documents=new_records)
    write_manifest(manifest, manifest_path)
    staged.append(manifest_path)

    _commit_staged(settings.lovverk_repo_path, staged, changes)

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
    client = LovdataClient(
        timeout_seconds=settings.http_timeout_seconds,
        user_agent=settings.http_user_agent,
    )
    with client:
        catalogue = {a.filename: a for a in client.list_datasets()}
        upstream: dict[str, _UpstreamDoc] = {}
        for dataset in _TRACKED_DATASETS:
            filename = f"{dataset}.tar.bz2"
            archive = _pick_archive(catalogue, filename)
            tar_path = client.download(archive, cache_dir).path
            upstream.update(_index_tarball(tar_path, dataset))
    return upstream


def _pick_archive(
    catalogue: dict[str, LovdataArchive],
    filename: str,
) -> LovdataArchive:
    if filename not in catalogue:
        raise ConfigError(
            f"upstream catalogue is missing expected archive {filename!r}",
        )
    return catalogue[filename]


def _index_tarball(tar_path: Path, dataset: str) -> dict[str, _UpstreamDoc]:
    docs: dict[str, _UpstreamDoc] = {}
    for member in iter_tarball_xml(tar_path):
        doc_id = Path(member.name).stem
        docs[doc_id] = _UpstreamDoc(
            doc_id=doc_id,
            source_dataset=dataset,
            xml_bytes=member.content,
            xml_hash=hash_normalized_xml(member.content),
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
        upstream.doc_id,
    )
    doc_type = doc_type_for_dataset(upstream.source_dataset)
    context = FrontmatterContext(
        doc_id=upstream.doc_id,
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
    """Mark a record as removed. Preserves original fields so audit
    trail remains: same xml_hash, same markdown_path, same last_seen
    (when the content was last observed), only status flips."""
    return ManifestRecord(
        doc_type=old.doc_type,
        xml_hash=old.xml_hash,
        markdown_path=old.markdown_path,
        source_dataset=old.source_dataset,
        last_seen=old.last_seen,
        status="removed",
    )


def _commit_staged(
    repo: Path,
    paths: list[Path],
    changes: object,
) -> None:
    if not paths:  # pragma: no cover - manifest is always staged
        return
    git_add(repo, paths)
    # Defense in depth: run_sync always writes the manifest (with a fresh
    # last_seen timestamp), so in practice this guard never triggers.
    if not has_staged_changes(repo):  # pragma: no cover
        return
    git_commit_msg(repo, _format_commit_message(changes))


def _format_commit_message(changes: object) -> str:
    # Typed loosely to avoid a circular annotation loop back to ChangeSet;
    # attribute access is the contract.
    new = len(getattr(changes, "new", []))
    changed = len(getattr(changes, "changed", []))
    removed = len(getattr(changes, "removed", []))
    return f"sync: {new} new, {changed} changed, {removed} removed"
