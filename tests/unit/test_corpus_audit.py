"""Corpus audit — reconcile what is on disk against what the manifest claims.

Nothing in the engine did this. The 48 orphaned documents found on 2026-07-12
(files committed to `lovverk` with no manifest record at all) accumulated for
seven weeks and surfaced only because a section count failed to add up. Every
mechanism in the sync compares *upstream* against the *manifest*; a file that
is in neither is invisible to all of them and can never self-heal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from lovspor.corpus_audit import AuditFinding, audit_corpus
from lovspor.storage.manifest import Manifest, ManifestRecord


def _record(
    slug: str,
    *,
    status: str = "current",
    dataset: str = "gjeldende-lover",
    renderer_version: int | None = 3,
) -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path=f"lover/{slug}.md",
        source_dataset=dataset,
        last_seen=datetime(2026, 7, 12, tzinfo=UTC),
        status=status,  # type: ignore[arg-type]
        slug=slug,
        title=slug.title(),
        renderer_version=renderer_version,
    )


def _manifest(**records: ManifestRecord) -> Manifest:
    return Manifest(
        generated_at=datetime(2026, 7, 12, tzinfo=UTC),
        documents=dict(records),
    )


def _seed(root: Path, *slugs: str) -> None:
    (root / "lover").mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        (root / "lover" / f"{slug}.md").write_text(f"# {slug}\n", encoding="utf-8")


def _kinds(findings: tuple[AuditFinding, ...]) -> list[tuple[str, str]]:
    return [(f.kind, f.path) for f in findings]


def test_clean_corpus_reports_no_findings(tmp_path: Path) -> None:
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert report.findings == ()
    assert report.clean is True
    assert report.documents_checked == 1


def test_detects_an_orphan_document_on_disk(tmp_path: Path) -> None:
    """The 2026-07-12 bug: a file committed to the corpus with NO manifest
    record. Invisible to change detection, so it never self-heals."""
    _seed(tmp_path, "skatteloven", "endr-i-kjøretøyforskriften")
    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert _kinds(report.findings) == [
        ("orphan_document", "lover/endr-i-kjøretøyforskriften.md"),
    ]
    assert report.clean is False


def test_detects_a_tombstoned_document_whose_file_was_never_deleted(tmp_path: Path) -> None:
    """The canary for the *other* half of the 2026-07-12 bug: a removal that
    wrote its manifest tombstone but skipped deleting the file. Before PR #89
    the tombstone was then dropped and the file became an untraceable orphan."""
    _seed(tmp_path, "skatteloven", "opphevet-lov")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("opphevet-lov", status="removed")),
    )

    assert _kinds(report.findings) == [
        ("tombstoned_but_present", "lover/opphevet-lov.md"),
    ]


def test_detects_a_current_document_missing_from_disk(tmp_path: Path) -> None:
    """The inverse drift: the manifest promises a law the corpus does not have.
    get_law would raise on a slug that INDEX.md advertises."""
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("forsvunnet-lov")),
    )

    assert _kinds(report.findings) == [("missing_document", "lover/forsvunnet-lov.md")]


def test_detects_an_orphan_embedding_sidecar(tmp_path: Path) -> None:
    """A crashed embeddings backfill leaves .bin files with no owning record."""
    _seed(tmp_path, "skatteloven")
    embeddings = tmp_path / "lover" / "embeddings"
    embeddings.mkdir(parents=True)
    (embeddings / "skatteloven.bin").write_bytes(b"\x00")
    (embeddings / "spøkelse.bin").write_bytes(b"\x00")

    report = audit_corpus(tmp_path, _manifest(nl1=_record("skatteloven")))

    assert _kinds(report.findings) == [("orphan_embedding", "lover/embeddings/spøkelse.bin")]


def test_detects_a_stale_render(tmp_path: Path) -> None:
    """Renderer-version self-healing re-renders these, but a doc stuck below the
    current version across many syncs means the backfill is not converging."""
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven", renderer_version=1)),
        renderer_version=3,
    )

    assert _kinds(report.findings) == [("stale_render", "lover/skatteloven.md")]


def test_a_tombstone_whose_file_is_gone_is_not_a_finding(tmp_path: Path) -> None:
    """A correctly-executed removal: tombstone retained, file deleted. Clean."""
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("opphevet-lov", status="removed")),
    )

    assert report.findings == ()
    assert report.clean is True


def test_findings_are_sorted_so_the_report_is_stable(tmp_path: Path) -> None:
    """Byte-stable output: the audit is meant to be diffable across runs and
    gateable in CI, so two runs on the same corpus must agree exactly."""
    _seed(tmp_path, "b-lov", "a-lov", "c-lov")
    manifest = _manifest(nl1=_record("a-lov"))

    first = audit_corpus(tmp_path, manifest)
    second = audit_corpus(tmp_path, manifest)

    assert first == second
    assert _kinds(first.findings) == [
        ("orphan_document", "lover/b-lov.md"),
        ("orphan_document", "lover/c-lov.md"),
    ]


def test_documents_checked_counts_current_records_only(tmp_path: Path) -> None:
    _seed(tmp_path, "skatteloven")
    report = audit_corpus(
        tmp_path,
        _manifest(nl1=_record("skatteloven"), nl2=_record("opphevet", status="removed")),
    )

    assert report.documents_checked == 1
