"""Tests for the attestation registry (ADR-0012 points 2a/2c).

The evidence channel has exactly three answers — entry / absent /
broken — and no pair may share a shape; entries are immutable.
"""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import lovspor.sync.orchestrator as orchestrator_module
import lovspor.temporal_attestation as attestation_module
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


def test_reconcile_accumulates_totals_across_documents(repo: tuple[Path, str]) -> None:
    path, _ = repo
    (path / "one.md").write_text(_NOTE_MD, encoding="utf-8")
    (path / "two.md").write_text(_NOTE_MD, encoding="utf-8")

    totals = reconcile_corpus(
        path,
        [
            ("doc-1", "one.md", _xml_with_notes(1)),
            ("doc-2", "two.md", _xml_with_notes(1)),
        ],
    )

    assert totals == (2, 2, 2)


def test_reconcile_passes_document_identity_and_non_strict_policy(
    repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = repo
    (path / "doc.md").write_text("body", encoding="utf-8")
    calls: list[dict[str, object]] = []

    class Layer:
        notes_seen = 0
        events: tuple[()] = ()

    def derive(markdown: str, **kwargs: object) -> Layer:
        assert markdown == "body"
        calls.append(kwargs)
        return Layer()

    monkeypatch.setattr(attestation_module, "derive_temporal_layer", derive)
    monkeypatch.setattr(attestation_module, "count_source_amendment_notes", lambda _xml: 0)

    reconcile_corpus(path, [("doc-identity", "doc.md", b"<root/>")])

    assert calls == [
        {
            "document_ref": "doc-identity",
            "expected_note_count": 0,
            "strict": False,
        },
    ]


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
        f"[{json.dumps(foreign)}]",
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
    _run_git(clone, "config", "user.email", "test@example.com")
    _run_git(clone, "config", "user.name", "Test")
    _run_git(clone, "config", "commit.gpgsign", "false")
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


def test_fetch_attestations_fails_closed_when_remote_probe_fails(
    repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = repo
    monkeypatch.setattr(
        attestation_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            128,
            stdout="",
            stderr="authentication failed\n",
        ),
    )

    with pytest.raises(AttestationError, match="cannot reach origin.*authentication failed"):
        fetch_attestations(path)


def test_fetch_attestations_fails_closed_when_fetch_fails(
    repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = repo
    results = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="ref exists\n", stderr=""),
            subprocess.CompletedProcess([], 1, stdout="", stderr="non-fast-forward\n"),
        ],
    )
    monkeypatch.setattr(
        attestation_module.subprocess,
        "run",
        lambda *args, **kwargs: next(results),
    )

    with pytest.raises(AttestationError, match="failed to fetch.*non-fast-forward"):
        fetch_attestations(path)


def test_fetch_attestations_invokes_git_with_non_raising_text_capture(
    repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = repo
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(attestation_module.subprocess, "run", run)

    fetch_attestations(path, "upstream")

    assert len(calls) == 2
    for _, kwargs in calls:
        assert kwargs == {
            "cwd": path,
            "capture_output": True,
            "text": True,
            "check": False,
        }


def test_write_attestation_surfaces_git_notes_failure(
    repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, sha = repo
    monkeypatch.setattr(attestation_module, "_read_entries", lambda *_args: [])
    monkeypatch.setattr(
        attestation_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr="cannot lock ref\n",
        ),
    )

    with pytest.raises(AttestationError, match="failed to record.*cannot lock ref"):
        write_attestation(path, _attestation(sha))


def test_write_attestation_uses_stable_json_and_non_raising_text_capture(
    repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, sha = repo
    monkeypatch.setattr(attestation_module, "_read_entries", lambda *_args: [])
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(attestation_module.subprocess, "run", run)

    entry = _attestation(sha)
    write_attestation(path, entry)

    args, kwargs = calls[0]
    assert args[-2] == json.dumps([entry.model_dump(mode="json")], sort_keys=True)
    assert kwargs == {
        "cwd": path,
        "capture_output": True,
        "text": True,
        "check": False,
    }


def test_read_attestation_surfaces_unexpected_git_notes_failure(
    repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, sha = repo
    results = iter(
        [
            subprocess.CompletedProcess([], 0, stdout=f"{sha}\n", stderr=""),
            subprocess.CompletedProcess([], 1, stdout="", stderr="bad notes ref\n"),
        ],
    )
    monkeypatch.setattr(
        attestation_module.subprocess,
        "run",
        lambda *args, **kwargs: next(results),
    )

    with pytest.raises(AttestationError, match="registry unreadable.*bad notes ref"):
        read_attestation(path, sha, 1)


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


def test_ensure_head_attested_queries_the_current_parser_version(
    repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, sha = repo
    queries: list[tuple[Path, str, int]] = []
    monkeypatch.setattr(orchestrator_module, "head_commit_or_none", lambda _repo: sha)

    def read(repo_path: Path, commit: str, version: int) -> TemporalAttestation:
        queries.append((repo_path, commit, version))
        return _attestation(commit, version=version)

    monkeypatch.setattr(orchestrator_module, "read_attestation", read)

    _ensure_head_attested(path, {}, {}, datetime(2026, 9, 2, tzinfo=UTC))

    assert queries == [(path, sha, TEMPORAL_PARSER_VERSION)]


def test_attest_hook_skips_every_ineligible_record_and_keeps_scanning(
    repo: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = repo
    skipped = _attestation("a" * 40)
    records = {
        "removed": ManifestRecord(
            doc_type="lov",
            xml_hash="old",
            markdown_path="removed.md",
            source_dataset="gjeldende-lover",
            last_seen=datetime(2026, 9, 2, tzinfo=UTC),
            status="removed",
            slug="removed",
        ),
        "missing-slug": ManifestRecord(
            doc_type="lov",
            xml_hash="old",
            markdown_path="missing.md",
            source_dataset="gjeldende-lover",
            last_seen=datetime(2026, 9, 2, tzinfo=UTC),
            status="current",
            slug=None,
        ),
    }
    reconciled: list[list[tuple[str, str, bytes]]] = []
    monkeypatch.setattr(
        orchestrator_module, "head_commit_or_none", lambda _repo: skipped.corpus_commit
    )
    monkeypatch.setattr(
        orchestrator_module,
        "reconcile_corpus",
        lambda _repo, docs: (
            reconciled.append(list(docs)) or attestation_module.ReconciliationTotals(0, 0, 0)
        ),
    )
    monkeypatch.setattr(orchestrator_module, "write_attestation", lambda *_args: None)

    _attest_temporal_conformance(
        path,
        {
            doc_id: _UpstreamDoc(
                doc_id=doc_id,
                source_dataset="gjeldende-lover",
                xml_bytes=b"<root/>",
                xml_hash="old",
                slug=doc_id,
                title=doc_id,
                eu_basis=(),
            )
            for doc_id in records
        },
        records,
        datetime(2026, 9, 2, tzinfo=UTC),
    )

    assert reconciled == [[]]


def test_duplicate_parser_version_in_one_note_is_corrupt(repo: tuple[Path, str]) -> None:
    # Review round 2: two entries under one (commit, parser_version) key
    # cannot both be the immutable record — picking the first would
    # silently prefer one of them.
    path, sha = repo
    entry = _attestation(sha).model_dump(mode="json")
    payload = json.dumps([entry, entry])
    _run_git(path, "notes", f"--ref={ATTESTATION_NOTES_REF}", "add", "-f", "-m", payload, sha)

    with pytest.raises(AttestationError, match="duplicate"):
        read_attestation(path, sha, 1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parser_version", 0),
        ("documents_reconciled", -1),
        ("notes_total", -1),
        ("events_total", -1),
    ],
)
def test_attestation_rejects_impossible_versions_and_counts(
    repo: tuple[Path, str],
    field: str,
    value: int,
) -> None:
    # Codex round on 46df456: a note carrying an impossible value is
    # channel corruption at read time — versions start at 1, counts are
    # never negative.
    path, sha = repo
    entry = _attestation(sha).model_dump(mode="json")
    entry[field] = value
    _run_git(
        path,
        "notes",
        f"--ref={ATTESTATION_NOTES_REF}",
        "add",
        "-f",
        "-m",
        json.dumps([entry]),
        sha,
    )

    with pytest.raises(AttestationError):
        read_attestation(path, sha, 1)
