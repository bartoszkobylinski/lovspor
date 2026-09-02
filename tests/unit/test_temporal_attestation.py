"""Tests for the attestation registry (ADR-0012 points 2a/2c).

The evidence channel has exactly three answers — entry / absent /
broken — and no pair may share a shape; entries are immutable.
"""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.errors import TemporalDerivationError
from lovspor.storage.manifest import ManifestRecord
from lovspor.sync.orchestrator import (
    _attest_temporal_conformance,
    _ensure_head_attested,
    _UpstreamDoc,
)
from lovspor.temporal import TEMPORAL_PARSER_VERSION
from lovspor.temporal_attestation import (
    ATTESTATION_NOTES_REF,
    AttestationError,
    TemporalAttestation,
    fetch_attestations,
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


# ---------- review round 1 (PR #227): trust-boundary fixes ----------


def test_note_anchored_to_a_different_commit_is_corrupt(repo: tuple[Path, str]) -> None:
    # MAJOR: a syntactically valid entry claiming another commit is
    # semantic corruption, never evidence.
    path, sha = repo
    foreign = _attestation("f" * 40).model_dump(mode="json")
    _run_git(
        path,
        "notes",
        f"--ref={ATTESTATION_NOTES_REF}",
        "add",
        "-f",
        "-m",
        f"[{__import__('json').dumps(foreign)}]",
        sha,
    )

    with pytest.raises(AttestationError, match="corrupt"):
        read_attestation(path, sha, 1)


def test_fetch_attestations_brings_remote_notes_into_a_plain_clone(
    repo: tuple[Path, str],
    tmp_path: Path,
) -> None:
    # BLOCKER 1: a plain clone omits refs/notes/*; without the fetch a
    # remote attestation reads as a false local absence, and a write
    # from such a clone would fork the notes history.
    origin_path, sha = repo
    write_attestation(origin_path, _attestation(sha))
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(origin_path), str(clone)],
        check=True,
        capture_output=True,
    )
    assert read_attestation(clone, sha, 1) is None  # the trap the fetch closes

    fetch_attestations(clone)

    assert read_attestation(clone, sha, 1) is not None
    # And a writer starting from the fetched ref appends fast-forward:
    (clone / "b.md").write_text("y\n")
    _run_git(clone, "add", "-A")
    _run_git(clone, "commit", "-m", "sync 2")
    sha2 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=clone,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    write_attestation(clone, _attestation(sha2))
    _run_git(clone, "push", "origin", f"{ATTESTATION_NOTES_REF}:{ATTESTATION_NOTES_REF}")


def test_fetch_attestations_tolerates_a_remote_without_the_ref(
    repo: tuple[Path, str],
    tmp_path: Path,
) -> None:
    origin_path, _ = repo
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(origin_path), str(clone)],
        check=True,
        capture_output=True,
    )

    fetch_attestations(clone)  # bootstrap: no ref yet, no error


def test_attest_hook_refuses_a_carried_forward_document(repo: tuple[Path, str]) -> None:
    # BLOCKER 3: a failed render keeps the OLD Markdown while upstream
    # serves NEW XML — a coincidental count match must not attest.
    path, _ = repo
    (path / "doc.md").write_text(_NOTE_MD, encoding="utf-8")
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="old-hash",
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
        xml_hash="new-hash",
        slug="testloven",
        title="Testloven",
        eu_basis=(),
    )

    with pytest.raises(AttestationError, match="carried-forward"):
        _attest_temporal_conformance(
            path,
            {"doc-1": upstream_doc},
            {"doc-1": record},
            datetime(2026, 9, 2, 4, 0, tzinfo=UTC),
        )


def test_ensure_head_attested_is_presence_based(repo: tuple[Path, str]) -> None:
    # BLOCKER 2: the condition is a missing (HEAD, parser_version)
    # attestation — so a parser bump re-attests an unchanged head, and a
    # second run under the same version is a no-op.
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
    upstream = {"doc-1": upstream_doc}
    records = {"doc-1": record}

    _ensure_head_attested(path, upstream, records, now)
    assert read_attestation(path, sha, TEMPORAL_PARSER_VERSION) is not None

    _ensure_head_attested(path, upstream, records, now)  # idempotent

    bumped = TEMPORAL_PARSER_VERSION + 1
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(orchestrator_module, "TEMPORAL_PARSER_VERSION", bumped)
        _ensure_head_attested(path, upstream, records, now)
    assert read_attestation(path, sha, bumped) is not None
