"""Tests for the attestation registry (ADR-0012 points 2a/2c).

The evidence channel has exactly three answers — entry / absent /
broken — and no pair may share a shape; entries are immutable.
"""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.errors import TemporalDerivationError
from lovspor.storage.manifest import ManifestRecord
from lovspor.sync.orchestrator import _attest_temporal_conformance, _UpstreamDoc
from lovspor.temporal import TEMPORAL_PARSER_VERSION
from lovspor.temporal_attestation import (
    ATTESTATION_NOTES_REF,
    AttestationError,
    TemporalAttestation,
    read_attestation,
    reconcile_corpus,
    write_attestation,
)


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ},
    )


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "corpus"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "a.md").write_text("x\n")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", "sync")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, sha


def _attestation(sha: str, version: int = 1, documents: int = 3) -> TemporalAttestation:
    return TemporalAttestation(
        corpus_commit=sha,
        parser_version=version,
        documents_reconciled=documents,
        notes_total=7,
        events_total=9,
        attested_at=datetime(2026, 9, 2, 4, 0, tzinfo=UTC),
    )


def test_absent_attestation_reads_none(repo: tuple[Path, str]) -> None:
    path, sha = repo

    assert read_attestation(path, sha, 1) is None


def test_write_then_read_roundtrip(repo: tuple[Path, str]) -> None:
    path, sha = repo
    entry = _attestation(sha)

    write_attestation(path, entry)

    assert read_attestation(path, sha, 1) == entry
    # A different parser version on the same commit is a different key.
    assert read_attestation(path, sha, 2) is None


def test_identical_rewrite_is_idempotent(repo: tuple[Path, str]) -> None:
    path, sha = repo
    entry = _attestation(sha)
    write_attestation(path, entry)

    write_attestation(path, entry)

    assert read_attestation(path, sha, 1) == entry


def test_conflicting_rewrite_is_refused(repo: tuple[Path, str]) -> None:
    path, sha = repo
    write_attestation(path, _attestation(sha, documents=3))

    with pytest.raises(AttestationError, match="immutable"):
        write_attestation(path, _attestation(sha, documents=4))


def test_two_parser_versions_coexist_on_one_commit(repo: tuple[Path, str]) -> None:
    path, sha = repo
    write_attestation(path, _attestation(sha, version=1))
    write_attestation(path, _attestation(sha, version=2))

    assert read_attestation(path, sha, 1) is not None
    assert read_attestation(path, sha, 2) is not None


def test_corrupt_note_is_a_channel_failure_not_absence(repo: tuple[Path, str]) -> None:
    # ADR-0012 point 2c: a broken evidence channel must never impersonate
    # absence of evidence.
    path, sha = repo
    _run_git(path, "notes", f"--ref={ATTESTATION_NOTES_REF}", "add", "-f", "-m", "not json", sha)

    with pytest.raises(AttestationError, match="unparseable"):
        read_attestation(path, sha, 1)


def test_unresolvable_commit_is_a_channel_failure(repo: tuple[Path, str]) -> None:
    path, _ = repo

    with pytest.raises(AttestationError, match="unreadable"):
        read_attestation(path, "0" * 40, 1)


# ---------- reconcile_corpus (the gate's proof) ----------

_NOTE_MD = (
    "# Testloven\n\n### § 1. Regel\n\nTekst.\n\n"
    "> Endret ved lov [21 juni 2024 nr. 46](lov/2024-06-21-46) (i kraft 1 juli 2024).\n"
)


def _xml_with_notes(count: int) -> bytes:
    divs = "".join('<div class="changesToParent">x</div>' for _ in range(count))
    return f"<root>{divs}</root>".encode()


def test_reconcile_passes_when_counts_match(repo: tuple[Path, str]) -> None:
    path, _ = repo
    (path / "doc.md").write_text(_NOTE_MD, encoding="utf-8")

    totals = reconcile_corpus(path, [("doc-1", "doc.md", _xml_with_notes(1))])

    assert totals.documents == 1
    assert totals.notes == 1
    assert totals.events == 1


def test_reconcile_mismatch_raises_naming_the_document(repo: tuple[Path, str]) -> None:
    path, _ = repo
    (path / "doc.md").write_text(_NOTE_MD, encoding="utf-8")

    with pytest.raises(TemporalDerivationError, match="doc-1"):
        reconcile_corpus(path, [("doc-1", "doc.md", _xml_with_notes(2))])


def test_reconcile_tolerates_unrecognised_markers(repo: tuple[Path, str]) -> None:
    # The attestation proves COUNTS; marker recognisability stays the
    # serving path's strict contract (ADR-0012 point 2 vs point 3).
    path, _ = repo
    body = (
        "# Testloven\n\n### § 1. Regel\n\nTekst.\n\n"
        "> Endret ved lov [21 juni 2024 nr. 46](lov/2024-06-21-46) "
        "(i kraft når departementet bestemmer).\n"
    )
    (path / "doc.md").write_text(body, encoding="utf-8")

    totals = reconcile_corpus(path, [("doc-1", "doc.md", _xml_with_notes(1))])

    assert totals.notes == 1


# ---------- the sync hook ----------


def test_attest_hook_records_head_and_refuses_missing_xml(repo: tuple[Path, str]) -> None:
    path, sha = repo
    (path / "doc.md").write_text(_NOTE_MD, encoding="utf-8")
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="h1",
        markdown_path="doc.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 9, 2, tzinfo=UTC),
        status="current",
        slug="testloven",
    )
    upstream_doc = _UpstreamDoc(
        doc_id="doc-1",
        source_dataset="gjeldende-lover",
        xml_bytes=_xml_with_notes(1),
        xml_hash="h1",
        slug="testloven",
        title="Testloven",
        eu_basis=(),
    )
    now = datetime(2026, 9, 2, 4, 0, tzinfo=UTC)

    _attest_temporal_conformance(path, {"doc-1": upstream_doc}, {"doc-1": record}, now)

    recorded = read_attestation(path, sha, TEMPORAL_PARSER_VERSION)
    assert recorded is not None
    assert recorded.documents_reconciled == 1
    assert recorded.attested_at == now

    with pytest.raises(AttestationError, match="no upstream XML"):
        _attest_temporal_conformance(path, {}, {"doc-1": record}, now)
