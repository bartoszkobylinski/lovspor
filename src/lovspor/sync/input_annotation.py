"""ADR-0006 metadata-only migration: annotate embedding input identity.

Stamps ``embedding_input_hash`` on every current manifest record by
reconstructing the document's embedding inputs through the ONE canonical
pipeline path and digesting them. Touches the manifest only — no Markdown,
no history, no sidecar, no ESI field, no provider call.

The metadata-only form is truthful only while its provenance argument holds:
the stored vectors must have been produced from exactly the inputs being
digested. The tool therefore verifies each record's sidecar basis before
annotating, and enforces the ADR-0006 §6 drift invariant — the corpus HEAD
is captured before computation and re-checked immediately before the
manifest is written, and every digest is recomputed and compared in a second
pass. Any drift aborts the migration with nothing written; recompute against
the new basis or regenerate, never annotate from stale assumptions.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from subprocess import run

from lovspor import __version__
from lovspor.embeddings.inputs import build_embedding_inputs, hash_embedding_inputs
from lovspor.errors import CorpusStateError
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest
from lovspor.sync.git_commit import add as git_add
from lovspor.sync.git_commit import commit as git_commit_msg
from lovspor.sync.git_commit import has_staged_changes

_MANIFEST_FILENAME = "manifest.json"
_COMMIT_SUBJECT = "migration: annotate embedding input identity in manifest"


@dataclass(frozen=True)
class AnnotationReport:
    """What the migration did, for the operator and the evidence record."""

    engine_version: str
    corpus_head: str
    annotated: int
    already_annotated: int
    tombstones_skipped: int
    empty_input_documents: int


def _git_head(repo: Path) -> str:
    """Current corpus commit — the basis the drift invariant re-checks."""
    # S603/S607 suppressed for the same reason as sync.git_commit._run: git
    # is a trusted system command invoked with list literals, never a shell.
    result = run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _record_input_hash(repo: Path, record: ManifestRecord) -> str:
    """Digest of the inputs the current pipeline derives for one record.

    Raises rather than skips: the migration must not silently annotate a
    subset. A record whose Markdown or sidecar basis is missing means the
    corpus contradicts its own manifest, which is an abort, not a gap.
    """
    markdown_path = repo / record.markdown_path
    if not markdown_path.exists():
        raise CorpusStateError(
            f"annotation aborted: {record.markdown_path} is referenced by the "
            f"manifest but missing on disk",
        )
    dataset_dir = "lover" if record.source_dataset.startswith("gjeldende-lov") else "forskrifter"
    sidecar = repo / dataset_dir / "embeddings" / f"{record.slug}.bin"
    if not sidecar.exists():
        raise CorpusStateError(
            f"annotation aborted: {record.slug!r} has no embedding sidecar — "
            f"the metadata-only proof does not cover it; regenerate instead",
        )
    rendered = markdown_path.read_text(encoding="utf-8")
    return hash_embedding_inputs(build_embedding_inputs(rendered))


def _compute_all(repo: Path, manifest: Manifest) -> dict[str, str]:
    """Input hash per current slugged doc_id; deterministic, provider-free."""
    return {
        doc_id: _record_input_hash(repo, record)
        for doc_id, record in manifest.documents.items()
        if record.status == "current" and record.slug is not None
    }


def _verify_basis_unchanged(repo: Path, head_before: str, hashes: dict[str, str]) -> None:
    """ADR-0006 §6 drift invariant, enforced immediately before publication.

    The corpus HEAD must be the one the digests were computed against, and a
    full recomputation must reproduce every digest byte-identically (which
    also proves the engine's input pipeline did not change under us — within
    one process it cannot, but the recompute makes the invariant checkable
    rather than assumed, and catches concurrent on-disk edits).
    """
    head_now = _git_head(repo)
    if head_now != head_before:
        raise CorpusStateError(
            f"annotation aborted: corpus HEAD moved during the migration "
            f"({head_before[:12]} -> {head_now[:12]}); recompute against the "
            f"new basis instead of annotating from stale assumptions",
        )
    recomputed = _compute_all(repo, read_manifest(repo / _MANIFEST_FILENAME))
    if recomputed != hashes:
        raise CorpusStateError(
            "annotation aborted: recomputed input hashes differ from the "
            "first pass — the corpus or pipeline basis drifted; nothing "
            "was written",
        )


def annotate_embedding_input_identity(settings: Settings) -> AnnotationReport:
    """Run the ADR-0006 metadata-only migration against the configured corpus.

    Requires no provider credential. Produces one forward-only migration
    commit (never pushes). Idempotent: a second run annotates nothing and
    creates no commit.
    """
    repo = settings.lovverk_repo_path
    manifest_path = repo / _MANIFEST_FILENAME
    head_before = _git_head(repo)
    manifest = read_manifest(manifest_path)
    hashes = _compute_all(repo, manifest)
    counts = _apply_annotation(repo, manifest, hashes, head_before)
    return AnnotationReport(
        engine_version=__version__,
        corpus_head=head_before,
        annotated=counts[0],
        already_annotated=counts[1],
        tombstones_skipped=sum(1 for r in manifest.documents.values() if r.status != "current"),
        empty_input_documents=sum(
            1 for doc_id, digest in hashes.items() if digest == hash_embedding_inputs([])
        ),
    )


def _apply_annotation(
    repo: Path,
    manifest: Manifest,
    hashes: dict[str, str],
    head_before: str,
) -> tuple[int, int]:
    """Stamp, drift-check, write, commit. Returns (annotated, already)."""
    annotated = already = 0
    new_records = dict(manifest.documents)
    for doc_id, digest in hashes.items():
        record = manifest.documents[doc_id]
        if record.embedding_input_hash == digest:
            already += 1
            continue
        new_records[doc_id] = record.model_copy(update={"embedding_input_hash": digest})
        annotated += 1
    if annotated == 0:
        return 0, already
    _verify_basis_unchanged(repo, head_before, hashes)
    manifest_path = repo / _MANIFEST_FILENAME
    write_manifest(Manifest(generated_at=datetime.now(UTC), documents=new_records), manifest_path)
    git_add(repo, [manifest_path])
    if has_staged_changes(repo):
        git_commit_msg(repo, _COMMIT_SUBJECT)
    return annotated, already
