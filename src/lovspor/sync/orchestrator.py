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

import logging
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.embeddings.model import EmbeddingModel, OpenAIEmbedder, split_to_token_chunks
from lovspor.embeddings.quantize import quantize_int8
from lovspor.embeddings.sections import iter_sections, strip_frontmatter
from lovspor.embeddings.store import read_embeddings, write_embeddings
from lovspor.errors import (
    ConfigError,
    CorpusStateError,
    MassRemovalError,
    ParseError,
    RenderError,
)
from lovspor.extraction.tarball import iter_tarball_xml
from lovspor.history import HistoryRecord, extract_history, write_history
from lovspor.parsing.xml_normalizer import hash_normalized_xml
from lovspor.rendering.document import (
    FrontmatterContext,
    extract_xml_metadata,
)
from lovspor.rendering.markdown_renderer import RENDERER_VERSION
from lovspor.rendering.slug import derive_slug, resolve_collisions
from lovspor.settings import Settings
from lovspor.sources.lovdata import LovdataArchive, LovdataClient, unknown_archive_fields
from lovspor.storage.manifest import (
    Manifest,
    ManifestRecord,
    read_manifest,
    write_manifest,
)
from lovspor.sync.change_detector import (
    detect_changes,
    force_rerender_changeset,
    stale_render_changeset,
)
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
from lovspor.sync.git_commit import has_staged_changes, has_uncommitted_changes

logger = logging.getLogger(__name__)

_TRACKED_DATASETS = (
    "gjeldende-lover",
    "gjeldende-sentrale-forskrifter",
)
_MANIFEST_FILENAME = "manifest.json"
_EMBEDDINGS_SUBDIR = "embeddings"
"""Per-dataset directory holding ``<slug>.bin`` embedding files.

Lives under each ``<dataset>/`` so the existing INDEX, history, and
Markdown structure is untouched: e.g. ``lover/embeddings/skatteloven-
sktl.bin`` sits next to ``lover/skatteloven-sktl.md`` and
``lover/history/skatteloven-sktl.json``."""


class SyncReport(BaseModel):
    """Outcome of a ``run_sync`` invocation."""

    model_config = ConfigDict(frozen=True)

    new_count: int
    changed_count: int
    removed_count: int
    unchanged_count: int
    # Documents re-rendered from unchanged XML because their renderer stamp was
    # stale (or force_rerender promoted them) AND the output actually differed.
    # Distinct from changed_count, which counts real upstream content changes.
    rerendered_count: int = 0
    # Unknown fields Lovdata's /list response carried that the schema does not
    # model (additive upstream drift, tolerated). Non-empty means the workflow
    # should notify so LovdataArchive can be updated.
    unknown_archive_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class _UpstreamDoc:
    doc_id: str
    source_dataset: str
    xml_bytes: bytes
    xml_hash: str
    slug: str
    title: str
    eu_basis: tuple[str, ...]
    retrieved_at: datetime | None = None


@dataclass(frozen=True)
class _DocAction:
    """One sync-action against the corpus, used for per-document commits.

    ``doc_id`` lets the post-commit history phase look up the matching
    manifest record without having to re-scan all upstream metadata.

    ``paths`` carries the canonical Markdown paths involved in this
    action's "move" (length 1 for add / update-no-slug-change /
    remove; length 2 for rename / update-with-slug-change as
    ``(old_md, new_md)``). ``_has_rename_path_overlap`` uses this
    convention to detect cycles.

    ``sidecar_paths`` carries the embedding ``.bin`` files that
    parallel ``paths``: same length, same order. ``git_add`` stages
    both lists together so a doc's Markdown and embeddings land in
    the same commit. Empty when no embedder is configured (sync
    works without an OpenAI key).
    """

    action: str  # "add" | "update" | "rename" | "remove"
    doc_type: str  # "lov" | "forskrift"
    doc_id: str
    slug: str
    paths: tuple[Path, ...]
    sidecar_paths: tuple[Path, ...] = ()

    @property
    def commit_message(self) -> str:
        return f"{self.action}({self.doc_type}): {self.slug}"

    @property
    def all_paths_to_stage(self) -> tuple[Path, ...]:
        """All paths to pass to ``git_add`` for this action."""
        return (*self.paths, *self.sidecar_paths)


def run_sync(settings: Settings, *, force_rerender: bool = False) -> SyncReport:  # noqa: PLR0912, PLR0915
    """Execute a full sync cycle against the configured lovverk repo.

    If upstream has no new / changed / removed documents, the manifest
    on disk is not rewritten and no commit is created — the sync is a
    true no-op at the filesystem and git layers. This is the contract
    the scheduled workflow relies on to detect 'nothing to do' runs.

    ``force_rerender`` re-renders every current document even though its XML
    hash is unchanged, so a renderer fix reaches files the change detector
    would otherwise skip forever. Documents whose re-render is byte-identical
    are skipped entirely, so the run stays self-limiting: only genuinely
    different output is written, embedded, and committed. It is a parameter
    rather than a ``Settings`` field on purpose — a stray env var must never be
    able to trigger a corpus-wide rewrite from the scheduled workflow.
    """
    _ensure_corpus_git_repo(settings.lovverk_repo_path)
    _ensure_clean_corpus(settings.lovverk_repo_path)
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
    upstream, drift_fields = _collect_upstream(settings, cache_dir)

    # Sprint 8 PR-D eu_basis backfill: triggers once when the prior
    # manifest has any current record with eu_basis=None (Sprint 7-or-
    # earlier schema). Re-renders every current doc to populate the
    # new frontmatter field + manifest field, single bulk commit
    # BEFORE the regular sync flow runs. Subsequent syncs see all
    # records carrying eu_basis (possibly empty list) and skip this
    # branch. Runs after Sprint 5 history migration because the
    # backfill commit produces fresh history events that next sync
    # can pick up via the normal per-doc history regeneration path.
    if _needs_sprint8_eu_basis_migration(prior):
        prior = _run_sprint8_eu_basis_migration(
            settings,
            manifest_path,
            prior,
            upstream,
            datetime.now(UTC),
        )

    # Sprint 9 PR-B2 embeddings backfill: triggers once when any
    # current record lacks an embedding ``.bin`` sidecar on disk.
    # Reads existing Markdown from disk (no re-render — embeddings
    # are derived from the rendered output, not the raw XML), runs
    # them through the configured embedder, writes one binary file
    # per doc under ``<dataset>/embeddings/<slug>.bin``, and emits a
    # single bulk ``migration: backfill embeddings for N documents``
    # commit BEFORE the regular sync flow runs. Skipped entirely
    # when no OpenAI key is configured.
    sprint9_embedder = _load_embedder(settings)
    if sprint9_embedder is not None and _needs_sprint9_embeddings_migration(
        prior,
        settings.lovverk_repo_path,
    ):
        prior = _run_sprint9_embeddings_migration(
            settings,
            prior,
            sprint9_embedder,
            datetime.now(UTC),
        )

    upstream_hashes = {doc_id: u.xml_hash for doc_id, u in upstream.items()}
    changes = detect_changes(upstream_hashes, prior)

    renamed = _identify_renames(changes.unchanged, prior, upstream)
    # Renames are detected from the *unchanged* partition, so promote only
    # after they are known — and never promote a renamed doc, which the rename
    # loop already re-renders at its new path.
    real_changed = set(changes.changed)
    if force_rerender:
        changes = force_rerender_changeset(changes, exclude=set(renamed))
    else:
        # Auto-heal: a renderer bump leaves every doc's XML — and its
        # change-detection hash — untouched, so promote the ones still carrying
        # an old renderer_version stamp. Skipped under force_rerender, which
        # already promotes everything.
        changes = stale_render_changeset(
            changes,
            prior,
            current_version=RENDERER_VERSION,
            exclude=set(renamed),
        )
    # Docs promoted purely to re-render (stamp/force), not for a real content
    # change: their commit must be the history-exempt migration subject.
    rerender_ids = set(changes.changed) - real_changed
    if not (changes.new or changes.changed or changes.removed or renamed):
        return SyncReport(
            new_count=0,
            changed_count=0,
            removed_count=0,
            unchanged_count=len(changes.unchanged),
            unknown_archive_fields=drift_fields,
        )

    _guard_mass_removal(changes.removed, prior, settings.max_removal_ratio)

    now = datetime.now(UTC)
    new_records = _carry_unchanged(prior, changes.unchanged)
    new_records.update(_carry_tombstones(prior, set(upstream)))
    actions: list[_DocAction] = []
    embedder = _load_embedder(settings)

    # All paths written by ANY action this sync. The two delete sites
    # below — changed-with-slug-change and the renamed phase 2 —
    # consult this set before deleting, so an old_path that another
    # action has just written to (or will overwrite) is preserved.
    # This prevents path-cascade data corruption across action types,
    # not just within the renamed loop. Codex PR #43 round 1 caught
    # the update+rename variant of the same class as the production
    # crash 2026-04-30 (rename+rename). Both variants now handled.
    written_paths: set[Path] = set()
    # Prior records for changed/renamed docs whose render failed this sync.
    # Their carry-forward is deferred until every write is done, then resolved
    # against ``written_paths`` below — a failed doc keeps its prior record only
    # if no successful action took over its old path.
    deferred_carries: list[tuple[str, ManifestRecord]] = []

    for doc_id in changes.new:
        upstream_doc = upstream[doc_id]
        written = _try_write_one(settings, upstream_doc, now, embedder)
        if written is None:
            continue  # unrenderable new doc: skip; re-detected as new next sync
        record, paths_written = written
        md_path = paths_written[0]
        sidecar = tuple(paths_written[1:])
        new_records[doc_id] = record
        written_paths.add(md_path)
        written_paths.update(sidecar)
        actions.append(
            _DocAction(
                action="add",
                doc_type=record.doc_type,
                doc_id=doc_id,
                slug=upstream_doc.slug,
                paths=(md_path,),
                sidecar_paths=sidecar,
            ),
        )

    # The changed and renamed loops both write a new_path then need
    # to delete an old_path. Per-loop two-phase splits are not enough
    # — Codex PR #44 round 2 caught a cross-loop variant where a
    # changed action's old-sidecar delete fires BEFORE a renamed
    # action's phase 1 writes to the same slot. The fix is to
    # interleave: do PHASE 1 for both groups (all writes), then
    # PHASE 2 for both groups (all delete-with-protection + action
    # build) so ``written_paths`` is fully populated before any
    # delete-protection check runs.
    changed_plan: list[tuple[str, Path, Path, tuple[Path, ...], ManifestRecord]] = []
    rename_plan: list[tuple[str, Path, Path, tuple[Path, ...], ManifestRecord]] = []

    # Phase 1a: write all changed docs.
    rerender_noop_ids: list[str] = []
    for doc_id in changes.changed:
        prior_record = prior.documents[doc_id]
        upstream_doc = _rerender_upstream(upstream[doc_id], prior_record)
        if _rerender_is_noop(settings, upstream_doc, now, prior_record):
            # Re-render that reproduces the file byte-for-byte: keep the prior
            # record so last_seen and embedding_hash are not disturbed. But
            # refresh the renderer stamp when it is stale — otherwise a
            # stamp-driven promotion would re-render this doc every sync forever
            # (a renderer bump that did not touch THIS file). Only when it
            # differs, so force_rerender of an already-current doc adds no
            # manifest churn.
            if prior_record.renderer_version != RENDERER_VERSION:
                new_records[doc_id] = prior_record.model_copy(
                    update={"renderer_version": RENDERER_VERSION},
                )
            else:
                new_records[doc_id] = prior_record
            rerender_noop_ids.append(doc_id)
            continue
        old_path = settings.lovverk_repo_path / prior_record.markdown_path
        written = _try_write_one(settings, upstream_doc, now, embedder)
        if written is None:
            # Render failed: defer the carry-forward until all writes are done,
            # so we can keep the prior record (old xml_hash → retried next sync)
            # only if no successful action takes over its old path.
            deferred_carries.append((doc_id, prior_record))
            continue
        record, paths_written = written
        new_path = paths_written[0]
        new_sidecar = tuple(paths_written[1:])
        new_records[doc_id] = record
        written_paths.add(new_path)
        written_paths.update(new_sidecar)
        changed_plan.append((doc_id, old_path, new_path, new_sidecar, record))

    # Phase 1b: write all renamed docs. Done BEFORE phase 2 of the
    # changed loop so changed actions' sidecar-delete checks see
    # the renamed writes.
    for doc_id in renamed:
        prior_record = prior.documents[doc_id]
        upstream_doc = _with_retrieved_at(upstream[doc_id], prior_record.last_seen)
        old_path = settings.lovverk_repo_path / prior_record.markdown_path
        written = _try_write_one(settings, upstream_doc, now, embedder)
        if written is None:
            # Same deferral as the changed loop: keep the prior path/slug only
            # if no successful action claims it (reconciled below).
            deferred_carries.append((doc_id, prior_record))
            continue
        record, paths_written = written
        new_path = paths_written[0]
        new_sidecar = tuple(paths_written[1:])
        new_records[doc_id] = record
        written_paths.add(new_path)
        written_paths.update(new_sidecar)
        rename_plan.append((doc_id, old_path, new_path, new_sidecar, record))

    # Reconcile deferred carries now that ``written_paths`` reflects every
    # write. A failed changed/renamed doc keeps its prior record ONLY if its old
    # Markdown path was not written by a successful action this sync. If it was
    # (e.g. a rename moved into the failed doc's slug), carrying it would leave
    # two current records at one file — and that file now holds the OTHER doc's
    # content. Drop it instead: it re-appears as ``new`` and is re-added once it
    # renders on a future sync. Same path-ownership invariant the phase-2 delete
    # guards enforce (Codex #104).
    for doc_id, prior_record in deferred_carries:
        carried_path = settings.lovverk_repo_path / prior_record.markdown_path
        if carried_path in written_paths:
            logger.warning(
                "dropping %s this sync: its file %s was taken over by another "
                "document; it will be re-added once it renders on a future sync",
                doc_id,
                prior_record.markdown_path,
            )
            continue
        new_records[doc_id] = prior_record

    # Phase 2a: changed-action deletes + action build. ``written_paths``
    # now reflects every write from new + changed + rename loops, so
    # any old_path that another action took over is correctly skipped.
    for doc_id, old_path, new_path, new_sidecar, record in changed_plan:
        upstream_doc = upstream[doc_id]
        prior_record = prior.documents[doc_id]
        paths: tuple[Path, ...]
        sidecar_paths: tuple[Path, ...]
        if old_path != new_path:
            if old_path not in written_paths:
                delete_document(old_path)
            paths = (old_path, new_path)
            old_sidecar = _maybe_delete_old_embeddings(
                settings.lovverk_repo_path,
                prior_record.source_dataset,
                prior_record.slug or "",
                written_paths,
            )
            sidecar_paths = (
                *((old_sidecar,) if old_sidecar is not None else ()),
                *new_sidecar,
            )
        else:
            paths = (new_path,)
            sidecar_paths = new_sidecar
        actions.append(
            _DocAction(
                action="update",
                doc_type=record.doc_type,
                doc_id=doc_id,
                slug=upstream_doc.slug,
                paths=paths,
                sidecar_paths=sidecar_paths,
            ),
        )

    # Phase 2b: rename old-Markdown deletes (skip-if-reused) + action
    # build with protected sidecar deletes. Mirrors the changed
    # branch, but kept distinct because rename has no "slug-same"
    # subcase — every rename has paths=(old, new).
    for _, old_path, _, _, _ in rename_plan:
        if old_path not in written_paths:
            delete_document(old_path)

    for doc_id, old_path, new_path, new_sidecar, record in rename_plan:
        prior_record = prior.documents[doc_id]
        old_sidecar = _maybe_delete_old_embeddings(
            settings.lovverk_repo_path,
            prior_record.source_dataset,
            prior_record.slug or "",
            written_paths,
        )
        sidecar_paths = (
            *((old_sidecar,) if old_sidecar is not None else ()),
            *new_sidecar,
        )
        actions.append(
            _DocAction(
                action="rename",
                doc_type=record.doc_type,
                doc_id=doc_id,
                slug=upstream[doc_id].slug,
                paths=(old_path, new_path),
                sidecar_paths=sidecar_paths,
            ),
        )

    for doc_id in changes.removed:
        prior_record = prior.documents[doc_id]
        prior_path = settings.lovverk_repo_path / prior_record.markdown_path
        new_records[doc_id] = _tombstone(prior_record)
        # Production crash 2026-05-05 reproducer: a doc is removed
        # while another action (rename/update/add) has just written
        # to the SAME path (collision-resolution slug shuffle moves
        # someone else into the slot). Without this protection, an
        # unconditional delete would wipe the new owner's content AND
        # the resulting per-doc commit would crash with
        # ``pathspec did not match any files``. When the slot is
        # already taken, the tombstone is a manifest-only update —
        # no git op for the path from this action.
        remove_paths: tuple[Path, ...]
        if prior_path in written_paths:
            remove_paths = ()
        else:
            delete_document(prior_path)
            remove_paths = (prior_path,)
        # Same protection for the embedding sidecar — if another
        # action wrote a sidecar at this slug's slot, preserve it.
        remove_sidecar: tuple[Path, ...]
        if prior_record.slug is not None:
            sidecar_path = _embeddings_path(
                settings.lovverk_repo_path,
                prior_record.source_dataset,
                prior_record.slug,
            )
            if sidecar_path in written_paths:
                remove_sidecar = ()
            elif sidecar_path.exists():
                delete_document(sidecar_path)
                remove_sidecar = (sidecar_path,)
            else:
                remove_sidecar = ()
        else:
            remove_sidecar = ()
        actions.append(
            _DocAction(
                action="remove",
                doc_type=prior_record.doc_type,
                doc_id=doc_id,
                slug=prior_record.slug or doc_id,
                paths=remove_paths,
                sidecar_paths=remove_sidecar,
            ),
        )

    # Commit only when something actually lands: a document action, or a
    # manifest change (a stale-stamp refresh on a byte-identical re-render has
    # no action but does mutate a record). Without this, a force-rerender whose
    # output is unchanged everywhere would still rewrite the manifest — its
    # generated_at ticks every run — and commit an empty no-op.
    if actions or new_records != prior.documents:
        _commit_with_history(
            settings,
            repo=settings.lovverk_repo_path,
            manifest_path=manifest_path,
            actions=actions,
            new_records=new_records,
            now=now,
            is_sprint4_migration=_is_migration(prior, renamed),
            force_bulk_commit=_has_rename_path_overlap(actions),
            rerender_ids=rerender_ids,
        )

    # A byte-identical re-render wrote nothing, so report it as unchanged. Of the
    # docs that DID write, separate real upstream content changes (changed_count)
    # from stamp/force re-renders (rerendered_count) so the scheduled workflow can
    # tell a legal update from a corpus-heal. The workflow reads these counts to
    # decide whether anything happened.
    noop = set(rerender_noop_ids)
    wrote_ids = [doc_id for doc_id in changes.changed if doc_id not in noop]
    rerendered = sum(1 for doc_id in wrote_ids if doc_id in rerender_ids)
    return SyncReport(
        new_count=len(changes.new),
        changed_count=len(wrote_ids) - rerendered,
        removed_count=len(changes.removed),
        unchanged_count=len(changes.unchanged) + len(rerender_noop_ids),
        rerendered_count=rerendered,
        unknown_archive_fields=drift_fields,
    )


def _ensure_corpus_git_repo(path: Path) -> None:
    if not (path / ".git").exists():
        raise ConfigError(
            f"{path} is not a git repository (expected .git/ inside it)",
        )


def _ensure_clean_corpus(path: Path) -> None:
    """Abort the sync if the corpus worktree is dirty at start.

    A dirty tree means a prior sync wrote the manifest / docs / index but
    crashed before committing them. Because change-detection state is read
    from the on-disk manifest, continuing would classify everything
    unchanged and silently drop the work forever (see ``_commit_with_history``
    — the manifest is written before its commit lands). Failing loudly turns
    that silent permanent loss into a visible error. The scheduled workflow
    clones lovverk fresh each run, so this only fires on a persistent
    checkout carrying crash residue; the operator inspects ``git status``
    and, if it is residue, ``git reset --hard`` + ``git clean`` and re-runs.
    """
    if has_uncommitted_changes(path):
        raise CorpusStateError(
            f"corpus worktree {path} has uncommitted changes at sync start — a "
            "prior sync likely crashed after writing the manifest but before "
            f"committing. Inspect `git -C {path} status`; if this is crash "
            "residue, `git reset --hard` and `git clean -fd`, then re-run.",
        )


_REMOVAL_GUARD_MIN_DOCS = 20
"""Datasets with fewer current docs than this skip the mass-removal guard:
an early-stage corpus legitimately churns a large fraction, and the absolute
risk (a handful of docs) is low."""


def _guard_mass_removal(
    removed: list[str],
    prior: Manifest,
    max_ratio: float,
) -> None:
    """Abort if any single dataset would lose more than ``max_ratio`` of its
    current documents in one sync.

    A valid-but-empty or truncated upstream tarball makes every document in
    that dataset look removed; unguarded, the sync deletes them all and the
    scheduled workflow auto-pushes the wipe. The check is per-dataset so one
    empty tarball cannot hide behind a healthy sibling dataset. Legitimate
    mass removals are rare — when one is real, re-run with a higher
    ``LOVSPOR_MAX_REMOVAL_RATIO``.
    """
    if not removed:
        return
    current_per_dataset: Counter[str] = Counter(
        rec.source_dataset for rec in prior.documents.values() if rec.status == "current"
    )
    removed_per_dataset: Counter[str] = Counter(
        prior.documents[doc_id].source_dataset for doc_id in removed
    )
    for dataset, removed_count in sorted(removed_per_dataset.items()):
        total = current_per_dataset.get(dataset, 0)
        if total < _REMOVAL_GUARD_MIN_DOCS:
            continue
        ratio = removed_count / total
        if ratio > max_ratio:
            raise MassRemovalError(
                f"sync would remove {removed_count}/{total} ({ratio:.0%}) of "
                f"dataset {dataset!r}, over the {max_ratio:.0%} safety threshold — "
                "likely an empty or truncated upstream tarball. If the removal is "
                f"real, re-run with LOVSPOR_MAX_REMOVAL_RATIO above {ratio:.2f}.",
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
) -> tuple[dict[str, _UpstreamDoc], tuple[str, ...]]:
    """Download both tarballs and return (doc_id -> _UpstreamDoc, schema drift).

    The second element is any unknown fields Lovdata's /list response carried
    (additive upstream drift, tolerated rather than fatal) so ``run_sync`` can
    surface them for notification.

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
        archives = client.list_datasets()
        drift = tuple(unknown_archive_fields(archives))
        catalogue = {a.filename: a for a in archives}
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
    return upstream, drift


def _with_slug(doc: _UpstreamDoc, slug: str) -> _UpstreamDoc:
    return _UpstreamDoc(
        doc_id=doc.doc_id,
        source_dataset=doc.source_dataset,
        xml_bytes=doc.xml_bytes,
        xml_hash=doc.xml_hash,
        slug=slug,
        title=doc.title,
        eu_basis=doc.eu_basis,
        retrieved_at=doc.retrieved_at,
    )


def _with_retrieved_at(doc: _UpstreamDoc, retrieved_at: datetime) -> _UpstreamDoc:
    return _UpstreamDoc(
        doc_id=doc.doc_id,
        source_dataset=doc.source_dataset,
        xml_bytes=doc.xml_bytes,
        xml_hash=doc.xml_hash,
        slug=doc.slug,
        title=doc.title,
        eu_basis=doc.eu_basis,
        retrieved_at=retrieved_at,
    )


def _frontmatter_context(upstream: _UpstreamDoc, now: datetime) -> FrontmatterContext:
    return FrontmatterContext(
        doc_id=upstream.doc_id,
        slug=upstream.slug,
        doc_type=doc_type_for_dataset(upstream.source_dataset),
        xml_hash=upstream.xml_hash,
        source_dataset=upstream.source_dataset,
        retrieved_at=upstream.retrieved_at or now,
    )


def _rerender_upstream(doc: _UpstreamDoc, prior: ManifestRecord) -> _UpstreamDoc:
    """Preserve ``retrieved_at`` when the upstream XML has not changed.

    Frontmatter ``retrieved_at`` and manifest ``last_seen`` are stamped from the
    same ``now`` inside ``_write_one``, so reusing the prior ``last_seen``
    reproduces the timestamp already on disk. Without this a forced re-render
    restamps every file and the whole corpus churns instead of only the
    documents the renderer fix actually affects.

    A genuinely changed doc (different hash) keeps ``now``, unchanged behaviour.
    """
    if doc.xml_hash != prior.xml_hash:
        return doc
    return _with_retrieved_at(doc, prior.last_seen)


def _rerender_is_noop(
    settings: Settings,
    upstream: _UpstreamDoc,
    now: datetime,
    prior: ManifestRecord,
) -> bool:
    """True when re-rendering ``upstream`` reproduces the file already on disk.

    Only reachable on a forced re-render, where the upstream hash still matches
    the manifest. Skipping such a document is what keeps the backfill
    self-limiting: no write, no embedding call, no commit, and no per-doc
    history walk for a document the renderer fix does not touch.

    An unrenderable document is not a no-op — it falls through so
    ``_try_write_one`` logs the skip and leaves the prior version in place.
    """
    if upstream.xml_hash != prior.xml_hash:
        return False
    path = document_path(settings.lovverk_repo_path, upstream.source_dataset, upstream.slug)
    if not path.exists():
        return False
    try:
        rendered = render_full_document(upstream.xml_bytes, _frontmatter_context(upstream, now))
    except (RenderError, ParseError):
        return False
    return path.read_text(encoding="utf-8") == rendered


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
                eu_basis=tuple(metadata["eu_basis"]),
            ),
        )
    return docs


def _write_one(
    settings: Settings,
    upstream: _UpstreamDoc,
    now: datetime,
    embedder: EmbeddingModel | None = None,
) -> tuple[ManifestRecord, list[Path]]:
    """Render the Markdown for a doc and (when ``embedder`` is provided)
    write its sidecar embeddings binary.

    Returns ``(record, paths)`` where ``paths`` is the list of files
    created on disk: always the Markdown file, plus the embeddings
    file when ``embedder`` is non-None. Callers stage every entry of
    ``paths`` with ``git_add`` so the embedded sidecar lands in the
    same commit as the rendered Markdown.

    Sprint 5 / Sprint 8 migration paths call without an embedder
    (their re-render purpose is unrelated to embeddings); the Sprint 9
    backfill migration has its own dedicated helper that reads
    Markdown back from disk and computes embeddings without
    re-rendering.
    """
    path = document_path(
        settings.lovverk_repo_path,
        upstream.source_dataset,
        upstream.slug,
    )
    doc_type = doc_type_for_dataset(upstream.source_dataset)
    rendered = render_full_document(upstream.xml_bytes, _frontmatter_context(upstream, now))
    write_document(path, rendered)

    written_paths: list[Path] = [path]
    if embedder is not None:
        embed_path = _write_embeddings_for_doc(
            settings.lovverk_repo_path,
            upstream.source_dataset,
            upstream.slug,
            rendered,
            embedder,
        )
        written_paths.append(embed_path)

    record = ManifestRecord(
        doc_type=doc_type,
        xml_hash=upstream.xml_hash,
        markdown_path=str(path.relative_to(settings.lovverk_repo_path)),
        source_dataset=upstream.source_dataset,
        last_seen=now,
        status="current",
        slug=upstream.slug,
        title=upstream.title,
        eu_basis=list(upstream.eu_basis),
        # A sidecar was written iff an embedder ran; record which content
        # it was built from. No embedder (keyless sync) -> None -> the doc
        # reads stale until the next keyed sync re-embeds it.
        embedding_hash=upstream.xml_hash if embedder is not None else None,
        # Stamp the renderer that produced this Markdown, so a later renderer
        # bump makes the doc detectably stale even though its XML is unchanged.
        renderer_version=RENDERER_VERSION,
    )
    return record, written_paths


def _try_write_one(
    settings: Settings,
    upstream: _UpstreamDoc,
    now: datetime,
    embedder: EmbeddingModel | None,
) -> tuple[ManifestRecord, list[Path]] | None:
    """Render+write a doc, or log and return ``None`` if it cannot render.

    A single unrenderable doc — an unhandled Lovdata structure such as a
    ``<div>`` carrying block-level text, or malformed XML — must not abort the
    whole sync.
    Returning ``None`` lets the caller skip it, leaving any prior version and
    its manifest record untouched so the change detector retries it next run.
    """
    try:
        return _write_one(settings, upstream, now, embedder)
    except (RenderError, ParseError) as exc:
        logger.warning(
            "skipping %s (%s): could not render — %s",
            upstream.slug,
            upstream.doc_id,
            exc,
        )
        return None


def _load_embedder(settings: Settings) -> EmbeddingModel | None:
    """Return an ``OpenAIEmbedder`` when an API key is configured, else ``None``.

    ``None`` means embeddings are skipped for this sync — the engine
    still produces Markdown and runs the rest of the pipeline normally.
    Documents added or changed in this run get their manifest
    ``embedding_hash`` set to ``None`` (added) or left at the old hash
    (changed), so it no longer matches their ``xml_hash``; the next sync
    with a key set re-embeds exactly those via the backfill migration.
    """
    if not settings.openai_api_key:
        return None
    return OpenAIEmbedder(api_key=settings.openai_api_key)


def _embeddings_path(corpus_root: Path, dataset: str, slug: str) -> Path:
    """Resolve the on-disk ``.bin`` path for a doc's embeddings."""
    return dataset_dir(corpus_root, dataset) / _EMBEDDINGS_SUBDIR / f"{slug}.bin"


def _write_embeddings_for_doc(
    corpus_root: Path,
    dataset: str,
    slug: str,
    rendered_markdown: str,
    embedder: EmbeddingModel,
) -> Path:
    """Compute and write embeddings for one document.

    Parses sections out of the rendered Markdown (frontmatter +
    leading H1 stripped, ``### §`` headings split into units), encodes
    them with the model, int8-quantizes, and writes one binary file.
    Returns the written path.

    A doc with zero embedding-eligible sections (very short act with
    only chapter headers, or a malformed render) gets an empty file
    with a valid header. Empty is intentional — the file exists and its
    manifest ``embedding_hash`` matches ``xml_hash``, so the staleness
    check in ``_needs_sprint9_embeddings_migration`` stops re-triggering.

    Sections longer than the model's input window are split into
    token-bounded chunks, each embedded under the same section_id —
    previously the tail of such sections was silently truncated and
    invisible to semantic search. The search side dedupes by
    ``(slug, section_id)`` keeping the best chunk score.
    """
    body = strip_frontmatter(rendered_markdown)
    sections = iter_sections(body)
    path = _embeddings_path(corpus_root, dataset, slug)
    if not sections:
        write_embeddings(path, [], scale=1.0)
        return path
    texts: list[str] = []
    section_ids: list[str] = []
    for section in sections:
        for chunk in split_to_token_chunks(section.text):
            texts.append(chunk)
            section_ids.append(section.section_id)
    matrix = embedder.encode(texts)
    quantized, scale = quantize_int8(matrix)
    pairs = [(section_ids[i], quantized[i]) for i in range(len(section_ids))]
    write_embeddings(path, pairs, scale)
    return path


def _maybe_delete_old_embeddings(
    corpus_root: Path,
    dataset: str,
    slug: str,
    written_paths: set[Path],
) -> Path | None:
    """Delete an old embedding sidecar unless another action has just
    written to the same path.

    Mirrors the ``written_paths`` protection that the changed and
    renamed Markdown delete sites already use (see PR #43 hotfix).
    Without this check, a slug swap between two docs would delete
    the very sidecar that the other rename's phase 1 just wrote.
    Codex PR #44 round 1 reproducer; same root cause class as the
    production crash 2026-04-30.

    Returns the deleted path (caller stages the removal via
    ``git_add``), or ``None`` when no deletion happened — either
    the sidecar was absent or it was protected because another
    action owns the slot now.
    """
    path = _embeddings_path(corpus_root, dataset, slug)
    if not path.exists() or path in written_paths:
        return None
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


def _carry_tombstones(
    prior: Manifest,
    upstream_ids: set[str],
) -> dict[str, ManifestRecord]:
    """Preserve ``removed`` records across syncs so a removal stays a
    permanent audit record.

    ``detect_changes`` excludes tombstones from its ``current`` set, so a
    tombstoned doc lands in none of new/changed/removed/unchanged and would
    be dropped from the rewritten manifest on the next sync that commits
    anything — surviving exactly one sync instead of forever. A tombstone
    still absent upstream is carried verbatim. One whose ``doc_id`` reappears
    upstream is a ``new`` doc this sync and is deliberately NOT carried — the
    new-doc write replaces it with a fresh ``current`` record.
    """
    return {
        doc_id: rec
        for doc_id, rec in prior.documents.items()
        if rec.status == "removed" and doc_id not in upstream_ids
    }


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


def _has_rename_path_overlap(actions: list[_DocAction]) -> bool:
    """True when ANY path appears in two distinct actions.

    This is the universal collision detector. Per-doc commits cannot
    handle a shared path because the first commit removes that path
    from the index, the second commit's ``git add`` then errors with
    ``pathspec did not match any files``. Bulk commit records all
    touches in one atomic diff and sidesteps the ordering problem.

    Earlier revisions of this function only considered
    ``rename``/``update`` actions and only their (old, new) pair.
    That was a partial detector — it missed:

    - **rename+remove** where rename's new_path equals remove's
      prior_path (production crash 2026-05-05:
      ``forskrifter/endr-i-utlendingsforskriften-2.md``)
    - **update+remove** with the same shape
    - **add+remove** where the new doc takes the path of a removed
      doc

    All three slipped through and produced the same ``pathspec did
    not match any files`` failure in production CI. Function name
    kept for git-blame continuity even though the detector now
    considers every action type.
    """
    seen: dict[Path, str] = {}
    for action in actions:
        # Dedupe within an action — a single rename has paths=(old,
        # new) which are by definition different, but defensive.
        for path in set(action.paths):
            owner = seen.get(path)
            if owner is not None and owner != action.doc_id:
                return True
            seen[path] = action.doc_id
    return False


def _commit_with_history(
    settings: Settings,
    *,
    repo: Path,
    manifest_path: Path,
    actions: list[_DocAction],
    new_records: dict[str, ManifestRecord],
    now: datetime,
    is_sprint4_migration: bool,
    force_bulk_commit: bool = False,
    rerender_ids: Collection[str] = (),
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

    ``force_bulk_commit`` overrides per-document mode for renames whose
    paths overlap (one rename's new_path equals another rename's
    old_path). Per-doc commits cannot handle that case: the first
    commit removes a file from the index that the second commit then
    fails to stage with ``pathspec did not match any files``. Bulk
    commit avoids the issue by recording all renames as one atomic
    diff. Production crash 2026-04-30 reproducer.

    ``rerender_ids`` names the documents promoted purely to re-render on a
    stale renderer stamp (not a real content change). Their ``update`` actions
    are peeled off and committed FIRST as one ``migration: re-render N documents
    (renderer vK)`` commit, the subject the history classifier ignores — so a
    corpus-wide re-render does not stamp thousands of phantom "Content updated"
    events. They still ride the shared manifest + INDEX + history tail below, so
    their renderer_version stamp lands and their history files stay accurate
    (the exempt commit contributes no event, real prior commits still do).

    Empty ``actions`` is not short-circuited: a sync that only refreshes stale
    renderer stamps on byte-identical re-renders has no document actions, yet the
    manifest still needs to persist the new stamps. Every commit below is gated
    on ``has_staged_changes``, so a truly-empty run writes no commit anyway.
    """
    rerender_actions = [a for a in actions if a.action == "update" and a.doc_id in rerender_ids]
    if rerender_actions:
        _commit_rerender_bulk(repo, rerender_actions)
    other_actions = [a for a in actions if a not in rerender_actions]

    # History and the manifest cover EVERY document (re-rendered included); only
    # the doc-content commit above is split out. target_doc_ids therefore spans
    # all non-remove actions so re-rendered docs' history regenerates too.
    target_doc_ids = [a.doc_id for a in actions if a.action != "remove"]

    if is_sprint4_migration or force_bulk_commit or settings.git_commit_mode == "single":
        message = (
            _migration_message(other_actions)
            if is_sprint4_migration
            else _single_message(other_actions)
        )
        manifest = Manifest(generated_at=now, documents=new_records)
        write_manifest(manifest, manifest_path)
        index_paths = [generate_index(repo, dataset, manifest) for dataset in _TRACKED_DATASETS]
        _commit_bulk(repo, other_actions, [manifest_path, *index_paths], message)
        _commit_history_followup(repo, manifest_path, new_records, target_doc_ids, now)
        return

    _commit_per_doc_actions_only(repo, other_actions)
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
        git_add(repo, list(action.all_paths_to_stage))
        if has_staged_changes(repo):
            git_commit_msg(repo, action.commit_message)


def _commit_bulk(
    repo: Path,
    actions: list[_DocAction],
    extra_paths: list[Path],
    message: str,
) -> None:
    all_paths = [p for action in actions for p in action.all_paths_to_stage]
    all_paths.extend(extra_paths)
    git_add(repo, all_paths)
    if not has_staged_changes(repo):  # pragma: no cover - defensive
        return
    git_commit_msg(repo, message)


def _commit_rerender_bulk(repo: Path, actions: list[_DocAction]) -> None:
    """Commit the re-rendered documents as one history-exempt migration commit.

    The subject matches the ``^migration: re-render `` prefix the history
    classifier drops, so re-rendering a frozen-render backlog stamps no phantom
    "Content updated" event on any document. Markdown + embedding sidecars only;
    the manifest / INDEX / history tail rides the shared commit afterwards.
    """
    all_paths = [p for action in actions for p in action.all_paths_to_stage]
    git_add(repo, all_paths)
    if not has_staged_changes(repo):  # pragma: no cover - defensive
        return
    git_commit_msg(
        repo, f"migration: re-render {len(actions)} documents (renderer v{RENDERER_VERSION})"
    )


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
        if not history.events and record.total_changes is not None:
            # An empty extraction can only mean every commit touching the
            # file is history-exempt (e.g. "migration: re-render"). Never
            # let that erase previously recorded legal-history state or
            # overwrite the doc's history files with an empty record.
            continue
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


def _needs_sprint8_eu_basis_migration(prior: Manifest) -> bool:
    """True if any current record has ``eu_basis is None`` AND a
    populated ``slug``. Triggers once on the first sync after Sprint 8
    PR-D ships; once every current record carries ``eu_basis:
    list[str]`` (possibly empty), the trigger goes false and never
    fires again.

    Records with ``slug is None`` (legacy Sprint-3 manifest) are
    deliberately ignored here: those get handled by the Sprint 4
    rename migration in the normal sync flow, which calls
    ``_write_one`` and populates ``eu_basis`` along the way. Trying
    to backfill them in Sprint 8 first would orphan the legacy
    ``<doc_id>.md`` files because Sprint 8 writes to the new slug
    path without deleting the old one.
    """
    if not prior.documents:
        return False
    return any(
        record.status == "current" and record.slug is not None and record.eu_basis is None
        for record in prior.documents.values()
    )


def _run_sprint8_eu_basis_migration(
    settings: Settings,
    manifest_path: Path,
    prior: Manifest,
    upstream: dict[str, _UpstreamDoc],
    now: datetime,
) -> Manifest:
    """Re-render every current doc to populate ``eu_basis`` in the
    manifest record AND in the file's YAML frontmatter. Single bulk
    commit ``migration: backfill eu_basis for N documents``.

    Why re-render: the new ``eu_basis`` field is part of the
    LegalDocumentFrontMatter, so populating it changes the rendered
    Markdown content even when the underlying XML hash is unchanged.
    Standard sync flow only re-renders when xml_hash changes; this
    migration overrides that to do a one-time corpus-wide refresh.

    Tombstones (``status='removed'``) are skipped because their files
    don't exist on disk anymore. Slug-less records (legacy Sprint-3
    manifest) are also skipped; they get backfilled as a side effect
    of the Sprint 4 rename migration in the normal sync flow.

    Records whose upstream slug differs from the prior manifest slug
    are also skipped: writing them here would update
    ``markdown_path`` to the new slug without deleting the old file,
    and the subsequent rename detector would then see
    ``prior.slug == upstream.slug`` (because we just changed it) and
    skip the rename, orphaning the old ``<old-slug>.md`` on disk.
    The regular rename flow handles those records correctly — its
    ``_write_one`` call also populates ``eu_basis`` as a side effect,
    so they end up backfilled too, just via a different commit.
    """
    new_records: dict[str, ManifestRecord] = {}
    written_paths: list[Path] = []
    for doc_id, record in prior.documents.items():
        if record.status != "current" or record.slug is None:
            new_records[doc_id] = record
            continue
        upstream_doc = upstream.get(doc_id)
        if upstream_doc is None:
            # Doc disappeared upstream between fetch and migration; let
            # the normal sync flow handle it as a removal next.
            new_records[doc_id] = record
            continue
        if upstream_doc.slug != record.slug:
            # Slug changed upstream. Defer to the rename flow so the
            # old file gets deleted; otherwise we'd orphan it.
            new_records[doc_id] = record
            continue
        # Sprint 8 migration intentionally does NOT compute embeddings
        # here. Sprint 9 has its own dedicated backfill path that
        # reads Markdown back from disk; mixing the two would slow
        # the eu_basis re-render and would fail entirely if no
        # OpenAI key is configured.
        new_record, paths_written = _write_one(
            settings,
            _with_retrieved_at(upstream_doc, record.last_seen),
            now,
        )
        new_records[doc_id] = new_record
        written_paths.append(paths_written[0])
    if not written_paths:
        return prior
    new_manifest = Manifest(generated_at=now, documents=new_records)
    write_manifest(new_manifest, manifest_path)
    git_add(settings.lovverk_repo_path, [manifest_path, *written_paths])
    if has_staged_changes(settings.lovverk_repo_path):
        git_commit_msg(
            settings.lovverk_repo_path,
            f"migration: backfill eu_basis for {len(written_paths)} documents",
        )
    return new_manifest


def _embedding_is_stale(embedding_hash: str | None, xml_hash: str, embed_path: Path) -> bool:
    """True if an embedding sidecar is missing or built from stale content.

    Stale = the sidecar's recorded source hash (``embedding_hash``)
    differs from the doc's current ``xml_hash`` — the vectors were built
    from text that has since changed (e.g. a keyless sync updated the
    Markdown but skipped the embedder) — or the ``.bin`` is absent. The
    hash compare is in-memory (no file read); existence is checked only
    when the hash matches, to catch a deleted-but-recorded sidecar.
    """
    if embedding_hash != xml_hash:
        return True
    return not embed_path.exists()


def _needs_sprint9_embeddings_migration(prior: Manifest, repo_path: Path) -> bool:
    """True if any current record has a missing or stale ``<slug>.bin``.

    Triggers the embeddings backfill whenever a sidecar is absent OR its
    source hash no longer matches the doc's ``xml_hash``. Existence alone
    (the original check) could not catch a stale-but-present file left by
    a keyless sync or an older render, so ``semantic_search`` silently
    scored current text against outdated vectors.

    Tombstones (``status='removed'``) and slug-less records are skipped —
    their files don't exist and the legacy schema is the Sprint 4 rename
    migration's concern.
    """
    for record in prior.documents.values():
        if record.status != "current" or record.slug is None:
            continue
        embed_path = _embeddings_path(repo_path, record.source_dataset, record.slug)
        if _embedding_is_stale(record.embedding_hash, record.xml_hash, embed_path):
            return True
    return False


def _run_sprint9_embeddings_migration(
    settings: Settings,
    prior: Manifest,
    embedder: EmbeddingModel,
    now: datetime,
) -> Manifest:
    """Backfill embedding sidecars for every current doc whose sidecar is
    missing or stale, stamp each rebuilt record's ``embedding_hash``, and
    rewrite the manifest so the repair is durably recorded.

    Reads existing Markdown from disk and computes embeddings without
    re-rendering (embeddings derive from the rendered output, not the raw
    XML). One bulk commit records the newly-written sidecars plus the
    manifest update; returns the rewritten manifest so the caller uses the
    stamped records for the rest of the sync (else the final manifest
    write would clobber the ``embedding_hash`` fields).
    """
    repo = settings.lovverk_repo_path
    manifest_path = repo / _MANIFEST_FILENAME
    written: list[Path] = []
    new_records = dict(prior.documents)
    for doc_id, record in prior.documents.items():
        if record.status != "current" or record.slug is None:
            continue
        embed_path = _embeddings_path(repo, record.source_dataset, record.slug)
        if not _embedding_is_stale(record.embedding_hash, record.xml_hash, embed_path):
            continue
        markdown_path = repo / record.markdown_path
        if not markdown_path.exists():
            # Stale manifest entry — record claims a Markdown file
            # that is missing on disk. Skip rather than raise; the
            # next regular sync flow will surface the inconsistency.
            continue
        rendered = markdown_path.read_text(encoding="utf-8")
        new_path = _write_embeddings_for_doc(
            repo,
            record.source_dataset,
            record.slug,
            rendered,
            embedder,
        )
        written.append(new_path)
        new_records[doc_id] = record.model_copy(update={"embedding_hash": record.xml_hash})
    if not written:
        return prior
    new_manifest = Manifest(generated_at=now, documents=new_records)
    write_manifest(new_manifest, manifest_path)
    git_add(repo, [manifest_path, *written])
    if has_staged_changes(repo):
        git_commit_msg(
            repo,
            f"migration: backfill embeddings for {len(written)} documents",
        )
    return new_manifest


def _embedding_section_count(embed_path: Path) -> int:
    """Number of vectors stored in a ``.bin`` (0 if absent or unreadable).

    A corrupt sidecar counts as 0 so the caller rebuilds it rather than
    trusting a file the reader rejects.
    """
    if not embed_path.exists():
        return 0
    try:
        return len(read_embeddings(embed_path).sections)
    except ValueError:
        return 0


def _find_undersized_embeddings(repo: Path, prior: Manifest) -> set[str]:
    """``doc_id``s of current docs whose stored vector count differs from the
    section count the current parser finds.

    This is the staleness signal that ``embedding_hash`` cannot give: a flat
    ``## §`` act embedded before the parser fix has ``embedding_hash ==
    xml_hash`` and a present-but-empty ``.bin``, so ``_embedding_is_stale``
    reports it fresh. Comparing section counts catches exactly those.
    """
    undersized: set[str] = set()
    for doc_id, record in prior.documents.items():
        if record.status != "current" or record.slug is None:
            continue
        markdown_path = repo / record.markdown_path
        if not markdown_path.exists():
            continue
        body = strip_frontmatter(markdown_path.read_text(encoding="utf-8"))
        embed_path = _embeddings_path(repo, record.source_dataset, record.slug)
        if len(iter_sections(body)) != _embedding_section_count(embed_path):
            undersized.add(doc_id)
    return undersized


def mark_undersized_embeddings_stale(settings: Settings) -> int:
    """Flag current docs whose embeddings under-count their sections so the next
    ``sync``'s Sprint 9 backfill re-embeds them; return how many were flagged.

    One-time repair for corpora embedded before a section-parser fix (flat
    ``## §`` acts produced zero vectors). Clears each flagged record's
    ``embedding_hash`` and commits the manifest — committing keeps the tree
    clean so the follow-up ``sync``, which aborts on a dirty tree, can run.
    """
    repo = settings.lovverk_repo_path
    manifest_path = repo / _MANIFEST_FILENAME
    prior = _load_or_empty_manifest(manifest_path)
    flagged = _find_undersized_embeddings(repo, prior)
    if not flagged:
        return 0
    new_records = dict(prior.documents)
    for doc_id in flagged:
        new_records[doc_id] = new_records[doc_id].model_copy(update={"embedding_hash": None})
    write_manifest(Manifest(generated_at=datetime.now(UTC), documents=new_records), manifest_path)
    git_add(repo, [manifest_path])
    git_commit_msg(repo, f"migration: flag {len(flagged)} under-embedded documents for re-embed")
    return len(flagged)


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
