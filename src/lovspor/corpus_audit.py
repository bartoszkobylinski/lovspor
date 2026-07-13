"""Reconcile the corpus on disk against what the manifest claims.

Every change-detection mechanism in the engine compares *upstream* against the
*manifest*. Nothing compares the *manifest* against *disk* — so a file that has
fallen out of the manifest is invisible to all of them and can never self-heal.

That gap let 48 documents accumulate in `lovverk` between 2026-05-19 and
2026-07-03: repealed regulations whose files were committed, never deleted, and
whose manifest records were later dropped. They surfaced only because a section
count failed to add up. This module is the check that would have caught them on
day one.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from lovspor.storage.manifest import Manifest, ManifestRecord
from lovspor.sync.document_io import dataset_dir

_EMBEDDINGS_SUBDIR = "embeddings"
_DATASET_SUBDIRS = ("lover", "forskrifter")


class AuditFinding(BaseModel):
    """One reconciliation failure between the manifest and disk."""

    model_config = {"frozen": True}

    kind: str
    path: str
    doc_id: str | None = None
    detail: str = ""


class AuditReport(BaseModel):
    """Result of one audit run. Ordered, so two runs on the same corpus are
    byte-identical and the output can be diffed or gated in CI."""

    model_config = {"frozen": True}

    corpus_root: str
    documents_checked: int
    findings: tuple[AuditFinding, ...]

    @property
    def clean(self) -> bool:
        return not self.findings


def _relative(corpus_root: Path, path: Path) -> str:
    return path.relative_to(corpus_root).as_posix()


def _markdown_on_disk(corpus_root: Path) -> set[str]:
    """Every rendered act on disk, as a repo-relative path. INDEX.md is
    generated metadata, not an act, so it never participates."""
    found: set[str] = set()
    for subdir in _DATASET_SUBDIRS:
        directory = corpus_root / subdir
        if not directory.is_dir():
            continue
        for path in directory.glob("*.md"):
            if path.name != "INDEX.md":
                found.add(_relative(corpus_root, path))
    return found


def _embeddings_on_disk(corpus_root: Path) -> set[str]:
    found: set[str] = set()
    for subdir in _DATASET_SUBDIRS:
        directory = corpus_root / subdir / _EMBEDDINGS_SUBDIR
        if not directory.is_dir():
            continue
        for path in directory.glob("*.bin"):
            found.add(_relative(corpus_root, path))
    return found


def _expected_embedding(corpus_root: Path, record: ManifestRecord) -> str | None:
    """The sidecar path a current record owns, or None when it cannot own one."""
    if record.slug is None:
        return None
    try:
        directory = dataset_dir(corpus_root, record.source_dataset)
    except ValueError:
        return None
    return _relative(corpus_root, directory / _EMBEDDINGS_SUBDIR / f"{record.slug}.bin")


def _document_findings(
    manifest: Manifest,
    on_disk: set[str],
    renderer_version: int | None,
) -> list[AuditFinding]:
    """Drift in both directions between manifest records and rendered files."""
    findings: list[AuditFinding] = []
    for doc_id, record in manifest.documents.items():
        path = record.markdown_path
        present = path in on_disk
        if record.status == "removed" and present:
            findings.append(
                AuditFinding(
                    kind="tombstoned_but_present",
                    path=path,
                    doc_id=doc_id,
                    detail="record is tombstoned but its file was never deleted",
                ),
            )
        elif record.status == "current" and not present:
            findings.append(
                AuditFinding(
                    kind="missing_document",
                    path=path,
                    doc_id=doc_id,
                    detail="manifest lists this act as current but the file is absent",
                ),
            )
        elif record.status == "current" and _is_stale_render(record, renderer_version):
            findings.append(
                AuditFinding(
                    kind="stale_render",
                    path=path,
                    doc_id=doc_id,
                    detail=(
                        f"rendered by renderer v{record.renderer_version}, "
                        f"current is v{renderer_version}"
                    ),
                ),
            )
    return findings


def _is_stale_render(record: ManifestRecord, renderer_version: int | None) -> bool:
    if renderer_version is None or record.renderer_version is None:
        return False
    return record.renderer_version < renderer_version


def _orphan_findings(
    corpus_root: Path, manifest: Manifest, on_disk: set[str]
) -> list[AuditFinding]:
    """Files on disk that no manifest record — of any status — accounts for."""
    claimed = {record.markdown_path for record in manifest.documents.values()}
    orphan_docs = [
        AuditFinding(
            kind="orphan_document",
            path=path,
            detail="file is in the corpus but no manifest record refers to it",
        )
        for path in sorted(on_disk - claimed)
    ]
    owned = {
        expected
        for record in manifest.documents.values()
        if record.status == "current"
        and (expected := _expected_embedding(corpus_root, record)) is not None
    }
    orphan_bins = [
        AuditFinding(
            kind="orphan_embedding",
            path=path,
            detail="embedding sidecar with no current manifest record",
        )
        for path in sorted(_embeddings_on_disk(corpus_root) - owned)
    ]
    return orphan_docs + orphan_bins


def audit_corpus(
    corpus_root: Path,
    manifest: Manifest,
    renderer_version: int | None = None,
) -> AuditReport:
    """Reconcile ``corpus_root`` against ``manifest``.

    ``renderer_version`` enables the stale-render check; omit it to skip that
    dimension (the other checks do not need to know about rendering).

    Findings are sorted by (kind, path) so a run is reproducible and diffable.
    """
    on_disk = _markdown_on_disk(corpus_root)
    findings = _document_findings(manifest, on_disk, renderer_version)
    findings += _orphan_findings(corpus_root, manifest, on_disk)
    findings.sort(key=lambda f: (f.kind, f.path))
    return AuditReport(
        corpus_root=str(corpus_root),
        documents_checked=sum(1 for r in manifest.documents.values() if r.status == "current"),
        findings=tuple(findings),
    )
