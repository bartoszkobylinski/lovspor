"""ADR-0005 Stage 2: the one coordinated LSPE version-2 cutover.

Rewrites every current sidecar from format version 1 to version 2, embedding
each record's manifest ESI in the file header so the sidecar carries its own
space identity. Vectors, section ids, scale and dimension are preserved
bit-for-bit and verified by re-reading every written file; no provider is
involved and no credential is required.

This is a derived-artifact migration (ADR-0005 §6): legal Markdown, corpus
membership, every ``xml_hash``, the manifest, and the legal temporal history
are all untouched. One commit rewrites all sidecars — the corpus is never
published in a mixed-version state (§3, binding ordering) — and the run is
idempotent: on an already-cut-over corpus it rewrites nothing and commits
nothing.

Abort conditions, all fail-closed with nothing committed: dirty worktree,
corpus HEAD drift, a current record with no recorded ESI (a header identity
nobody recorded would be fabricated provenance), a missing or unreadable
sidecar, an existing version-2 sidecar whose header disagrees with the
manifest (tamper signal), or a post-write verification mismatch.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from lovspor import __version__
from lovspor.embeddings.store import EmbeddingFile, read_embeddings, write_embeddings
from lovspor.errors import CorpusStateError
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest
from lovspor.sync.git_commit import add as git_add
from lovspor.sync.git_commit import commit as git_commit_msg
from lovspor.sync.git_commit import has_staged_changes
from lovspor.sync.input_annotation import _git_head, _require_clean_worktree

_MANIFEST_FILENAME = "manifest.json"
_COMMIT_SUBJECT = "migration: rewrite embedding sidecars as LSPE version 2"


@dataclass(frozen=True)
class CutoverReport:
    """What the cutover did, for the operator and the evidence record."""

    engine_version: str
    corpus_head: str
    rewritten: int
    already_v2: int
    tombstones_skipped: int
    header_only: int


@dataclass(frozen=True)
class _CutoverItem:
    """One version-1 sidecar selected for rewriting, with its target ESI."""

    path: Path
    esi: str
    parsed: EmbeddingFile


def _sidecar_path(repo: Path, record: ManifestRecord) -> Path:
    dataset_dir = "lover" if record.source_dataset.startswith("gjeldende-lov") else "forskrifter"
    return repo / dataset_dir / "embeddings" / f"{record.slug}.bin"


def _read_basis(repo: Path, record: ManifestRecord) -> EmbeddingFile:
    """Parse one record's sidecar, or abort the whole cutover.

    The cutover claims to preserve stored vectors exactly; a file it cannot
    read has no vectors to preserve, so it aborts and points at regeneration
    rather than skipping — a skipped file would silently stay version 1 and
    republish the mixed state the ordering forbids.
    """
    path = _sidecar_path(repo, record)
    if not path.exists():
        raise CorpusStateError(
            f"cutover aborted: {record.slug!r} has no embedding sidecar; "
            f"regenerate it before the cutover",
        )
    try:
        return read_embeddings(path)
    except (ValueError, OSError) as exc:
        raise CorpusStateError(
            f"cutover aborted: {record.slug!r} has an unreadable embedding "
            f"sidecar ({exc}); regenerate it before the cutover",
        ) from exc


def _select_items(repo: Path, manifest: Manifest) -> tuple[list[_CutoverItem], int]:
    """All version-1 sidecars with their target identities, plus the v2 count.

    Aborts on a record with no manifest ESI — writing a header identity that
    nobody recorded would fabricate provenance — and on an existing
    version-2 file whose header disagrees with the manifest, which means the
    corpus already contains a file the manifest does not describe.
    """
    items: list[_CutoverItem] = []
    already_v2 = 0
    for record in manifest.documents.values():
        if record.status != "current" or record.slug is None:
            continue
        if record.embedding_space_id is None:
            raise CorpusStateError(
                f"cutover aborted: {record.slug!r} has no recorded "
                f"embedding_space_id in the manifest; a version-2 header "
                f"cannot carry an identity nobody recorded",
            )
        parsed = _read_basis(repo, record)
        if parsed.embedding_space_id is not None:
            if parsed.embedding_space_id != record.embedding_space_id:
                raise CorpusStateError(
                    f"cutover aborted: {record.slug!r} is already version 2 "
                    f"but its header identity {parsed.embedding_space_id} "
                    f"disagrees with the manifest's "
                    f"{record.embedding_space_id}",
                )
            already_v2 += 1
            continue
        items.append(
            _CutoverItem(
                path=_sidecar_path(repo, record),
                esi=record.embedding_space_id,
                parsed=parsed,
            ),
        )
    return items, already_v2


def _verify_rewrite(item: _CutoverItem) -> None:
    """Re-read a written file and prove the payload survived unchanged."""
    reread = read_embeddings(item.path)
    payload_equal = (
        reread.version == 2  # noqa: PLR2004 - the format version literal
        and reread.embedding_space_id == item.esi
        and reread.dim == item.parsed.dim
        and reread.scale == item.parsed.scale
        and len(reread.sections) == len(item.parsed.sections)
        and all(
            a_id == b_id and np.array_equal(a_vec, b_vec)
            for (a_id, a_vec), (b_id, b_vec) in zip(
                reread.sections,
                item.parsed.sections,
                strict=True,
            )
        )
    )
    if not payload_equal:
        raise CorpusStateError(
            f"cutover aborted: {item.path.name} did not verify after the "
            f"version-2 rewrite; the worktree is partially rewritten and "
            f"must be discarded (nothing was committed)",
        )


def migrate_lspe_v2(settings: Settings) -> CutoverReport:
    """Run the coordinated LSPE version-2 cutover against the configured corpus.

    Requires a clean corpus worktree and no provider credential. Produces one
    forward-only migration commit touching only ``embeddings/*.bin`` files
    (never pushes). Idempotent: a second run rewrites nothing and creates no
    commit.
    """
    repo = settings.lovverk_repo_path
    _require_clean_worktree(repo)
    head_before = _git_head(repo)
    manifest = read_manifest(repo / _MANIFEST_FILENAME)
    items, already_v2 = _select_items(repo, manifest)
    tombstones = sum(1 for r in manifest.documents.values() if r.status != "current")
    header_only = sum(1 for i in items if not i.parsed.sections)
    if items:
        _apply_cutover(repo, items, head_before)
    return CutoverReport(
        engine_version=__version__,
        corpus_head=head_before,
        rewritten=len(items),
        already_v2=already_v2,
        tombstones_skipped=tombstones,
        header_only=header_only,
    )


def _apply_cutover(repo: Path, items: list[_CutoverItem], head_before: str) -> None:
    """Rewrite, verify, and commit — with the drift invariant enforced.

    The HEAD captured before selection is re-verified immediately before the
    first byte is written and again before the commit, so the rewrite can
    never publish against a corpus that moved under it.
    """
    _require_head_unmoved(repo, head_before)
    for item in items:
        write_embeddings(
            item.path,
            item.parsed.sections,
            item.parsed.scale,
            dim=item.parsed.dim,
            embedding_space_id=item.esi,
        )
        _verify_rewrite(item)
    _require_head_unmoved(repo, head_before)
    git_add(repo, sorted({item.path.parent for item in items}))
    if has_staged_changes(repo):
        git_commit_msg(repo, _COMMIT_SUBJECT)


def _require_head_unmoved(repo: Path, head_before: str) -> None:
    head_now = _git_head(repo)
    if head_now != head_before:
        raise CorpusStateError(
            f"cutover aborted: corpus HEAD moved during the migration "
            f"({head_before[:12]} -> {head_now[:12]}); re-run against the "
            f"new basis",
        )
