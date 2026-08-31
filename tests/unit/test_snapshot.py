"""Tests for lovspor.snapshot — global corpus-state resolution (ADR-0011)."""

import json
import os
import subprocess
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from lovspor.snapshot import (
    CorpusSnapshot,
    CorpusStateRef,
    HistoryBoundaryError,
    resolve_corpus_state,
)
from lovspor.timetravel import ShallowHistoryError

# ---------- CorpusStateRef ----------


def test_corpus_state_ref_is_frozen() -> None:
    ref = CorpusStateRef("sha", datetime(2026, 5, 1, tzinfo=UTC))

    with pytest.raises(FrozenInstanceError):
        ref.sha = "other"


# ---------- resolve_corpus_state (log walk, monkeypatched) ----------


def _refs(*pairs: tuple[str, datetime]) -> list[CorpusStateRef]:
    return [CorpusStateRef(sha, dt) for sha, dt in pairs]


def test_resolve_picks_newest_commit_at_or_before_end_of_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cutoff_day = date(2026, 4, 27)
    edge = datetime.combine(cutoff_day, time.max).replace(tzinfo=UTC)
    entries = _refs(
        ("newer", datetime(2026, 4, 28, tzinfo=UTC)),
        ("edge", edge),
        ("older", datetime(2026, 4, 26, tzinfo=UTC)),
    )
    monkeypatch.setattr("lovspor.snapshot._iter_state_log", lambda *_: entries)

    ref = resolve_corpus_state(tmp_path, cutoff_day)

    assert ref.sha == "edge"


def test_resolve_pre_history_date_raises_boundary_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries = _refs(("start", datetime(2026, 4, 22, tzinfo=UTC)))
    monkeypatch.setattr("lovspor.snapshot._iter_state_log", lambda *_: entries)
    monkeypatch.setattr("lovspor.snapshot._is_shallow_repository", lambda *_: False)

    with pytest.raises(HistoryBoundaryError) as excinfo:
        resolve_corpus_state(tmp_path, date(2026, 4, 21))

    # The boundary outcome names the corpus start so a caller can relay it.
    assert "2026-04-22" in str(excinfo.value)


def test_resolve_on_shallow_clone_raises_shallow_not_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries = _refs(("clone-edge", datetime(2026, 6, 1, tzinfo=UTC)))
    monkeypatch.setattr("lovspor.snapshot._iter_state_log", lambda *_: entries)
    monkeypatch.setattr("lovspor.snapshot._is_shallow_repository", lambda *_: True)

    with pytest.raises(ShallowHistoryError):
        resolve_corpus_state(tmp_path, date(2026, 5, 1))


# ---------- real-git fixture ----------


def _run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )


def _commit_all(repo: Path, message: str, iso_date: str) -> str:
    _run_git(repo, "add", "-A")
    stamp = {"GIT_AUTHOR_DATE": iso_date, "GIT_COMMITTER_DATE": iso_date}
    _run_git(repo, "commit", "-m", message, env=stamp)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _manifest_payload(body_rel: str, *, slug: str = "testloven") -> dict[str, object]:
    return {
        "version": 1,
        "generated_at": "2026-04-22T04:00:00Z",
        "documents": {
            "doc-1": {
                "doc_type": "lov",
                "xml_hash": "hash-1",
                "markdown_path": body_rel,
                "source_dataset": "gjeldende-lover",
                "last_seen": "2026-04-22T04:00:00Z",
                "status": "current",
                "slug": slug,
                "title": "Testloven",
            },
        },
    }


@pytest.fixture
def corpus_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A two-commit corpus: v1 body on 2026-05-01, v2 body on 2026-05-10."""
    repo = tmp_path / "corpus"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "lover").mkdir()
    (repo / "lover" / "testloven.md").write_text("---\nx: 1\n---\n# T\n\nv1 body\n")
    (repo / "manifest.json").write_text(json.dumps(_manifest_payload("lover/testloven.md")))
    sha1 = _commit_all(repo, "sync 1", "2026-05-01T12:00:00Z")
    (repo / "lover" / "testloven.md").write_text("---\nx: 1\n---\n# T\n\nv2 body\n")
    sha2 = _commit_all(repo, "sync 2", "2026-05-10T12:00:00Z")
    return repo, sha1, sha2


def test_resolve_against_real_history(corpus_repo: tuple[Path, str, str]) -> None:
    repo, sha1, sha2 = corpus_repo

    assert resolve_corpus_state(repo, date(2026, 5, 5)).sha == sha1
    assert resolve_corpus_state(repo, date(2026, 5, 10)).sha == sha2


def test_snapshot_reads_blob_as_of_its_commit(corpus_repo: tuple[Path, str, str]) -> None:
    repo, sha1, sha2 = corpus_repo

    assert "v1 body" in (CorpusSnapshot(repo, sha1).read_text("lover/testloven.md") or "")
    assert "v2 body" in (CorpusSnapshot(repo, sha2).read_text("lover/testloven.md") or "")


def test_snapshot_missing_path_reads_none(corpus_repo: tuple[Path, str, str]) -> None:
    repo, sha1, _ = corpus_repo

    assert CorpusSnapshot(repo, sha1).read_text("lover/absent.md") is None


def test_snapshot_manifest_and_slug_index(corpus_repo: tuple[Path, str, str]) -> None:
    repo, sha1, _ = corpus_repo
    snapshot = CorpusSnapshot(repo, sha1)

    assert "doc-1" in snapshot.manifest.documents
    doc_id, record = snapshot.slug_index["testloven"]
    assert doc_id == "doc-1"
    assert record.markdown_path == "lover/testloven.md"


def test_slug_index_first_entry_wins_and_skips_non_current(tmp_path: Path) -> None:
    repo = tmp_path / "corpus"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    payload = _manifest_payload("lover/a.md")
    payload["documents"]["doc-2"] = {
        **payload["documents"]["doc-1"],  # type: ignore[dict-item]
        "markdown_path": "lover/b.md",
    }
    payload["documents"]["doc-3"] = {
        **payload["documents"]["doc-1"],  # type: ignore[dict-item]
        "status": "removed",
        "slug": "borteloven",
    }
    (repo / "manifest.json").write_text(json.dumps(payload))
    sha = _commit_all(repo, "sync", "2026-05-01T12:00:00Z")

    index = CorpusSnapshot(repo, sha).slug_index

    assert index["testloven"][0] == "doc-1"
    assert "borteloven" not in index
