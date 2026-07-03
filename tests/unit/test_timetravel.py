"""Tests for lovspor.timetravel."""

import os
import subprocess
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from lovspor.timetravel import (
    RevisionNotFoundError,
    _find_revision,
    _iter_follow_log,
    _read_blob,
    _RevisionEntry,
    get_law_at_revision,
)


def test_revision_entry_is_frozen() -> None:
    entry = _RevisionEntry("sha", datetime(2026, 4, 27, tzinfo=UTC), "lover/x.md")

    with pytest.raises(FrozenInstanceError):
        entry.sha = "other"


def test_find_revision_uses_end_of_day_cutoff_inclusively(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cutoff = datetime.combine(date(2026, 4, 27), time.max).replace(tzinfo=UTC)
    entries = [
        _RevisionEntry("new", datetime(2026, 4, 28, tzinfo=UTC), "lover/x.md"),
        _RevisionEntry("edge", cutoff, "lover/x.md"),
        _RevisionEntry("old", datetime(2026, 4, 26, tzinfo=UTC), "lover/x.md"),
    ]
    monkeypatch.setattr("lovspor.timetravel._iter_follow_log", lambda *_args: entries)

    entry = _find_revision(tmp_path, "lover/x.md", cutoff)

    assert entry.sha == "edge"


def test_find_revision_raises_when_no_entry_is_on_or_before_cutoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "lovspor.timetravel._iter_follow_log",
        lambda *_args: [
            _RevisionEntry(
                "new",
                datetime(2026, 4, 28, tzinfo=UTC),
                "lover/x.md",
            ),
        ],
    )

    with pytest.raises(
        RevisionNotFoundError,
        match=r"^no commit on or before 2026-04-27 touches 'lover/x\.md'$",
    ):
        _find_revision(
            tmp_path,
            "lover/x.md",
            datetime(2026, 4, 27, 23, 59, 59, tzinfo=UTC),
        )


def test_iter_follow_log_parses_commit_blocks_and_skips_malformed_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert args == [
            "git",
            "-c",
            "core.quotePath=false",
            "log",
            "--follow",
            "--format=__COMMIT__%n%H%n%aI",
            "--name-only",
            "--",
            "lover/current.md",
        ]
        assert kwargs["cwd"] == tmp_path
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is True
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                "__COMMIT__\n"
                "sha-new\n"
                "2026-04-27T09:00:00+00:00\n"
                "lover/current.md\n"
                "\n"
                "__COMMIT__\n"
                "sha-missing-path\n"
                "2026-04-26T09:00:00+00:00\n"
                "\n"
                "__COMMIT__\n"
                "sha-old\n"
                "2026-04-25T09:00:00+00:00\n"
                "lover/old.md\n"
            ),
        )

    monkeypatch.setattr("lovspor.timetravel.subprocess.run", fake_run)

    entries = _iter_follow_log(tmp_path, "lover/current.md")

    assert entries == [
        _RevisionEntry(
            "sha-new",
            datetime(2026, 4, 27, 9, tzinfo=UTC),
            "lover/current.md",
        ),
        _RevisionEntry(
            "sha-old",
            datetime(2026, 4, 25, 9, tzinfo=UTC),
            "lover/old.md",
        ),
    ]


def test_read_blob_uses_resolved_path_at_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert args == ["git", "show", "abc123:lover/old.md"]
        assert kwargs["cwd"] == tmp_path
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is True
        return subprocess.CompletedProcess(args, 0, stdout="old content\n")

    monkeypatch.setattr("lovspor.timetravel.subprocess.run", fake_run)

    assert _read_blob(tmp_path, "abc123", "lover/old.md") == "old content\n"


def test_get_law_at_revision_reads_latest_matching_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    def fake_find_revision(
        repo_path: Path,
        current_path: str,
        cutoff: datetime,
    ) -> _RevisionEntry:
        seen["find"] = (repo_path, current_path, cutoff)
        return _RevisionEntry("sha-old", cutoff, "lover/old.md")

    def fake_read_blob(repo_path: Path, sha: str, path: str) -> str:
        seen["read"] = (repo_path, sha, path)
        return "historical markdown"

    monkeypatch.setattr("lovspor.timetravel._find_revision", fake_find_revision)
    monkeypatch.setattr("lovspor.timetravel._read_blob", fake_read_blob)

    assert get_law_at_revision(tmp_path, "lover/current.md", date(2026, 4, 27)) == (
        "historical markdown"
    )
    assert seen["find"] == (
        tmp_path,
        "lover/current.md",
        datetime.combine(date(2026, 4, 27), time.max).replace(tzinfo=UTC),
    )
    assert seen["read"] == (tmp_path, "sha-old", "lover/old.md")


# ---------- real-git regression: non-ASCII slug paths ----------


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _commit_all(repo: Path, message: str, when: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
        env=env,
    )


def test_get_law_at_revision_handles_non_ascii_slug_paths(tmp_path: Path) -> None:
    """Regression: git C-quotes non-ASCII paths in ``--name-only`` output by
    default (``core.quotePath=true``); the quoted, octal-escaped string then
    breaks ``git show``. ~2,000 corpus files carry æøå slugs
    (arbeidsmiljøloven, opplæringslova, ...), so this is the common case."""
    repo = tmp_path / "lovverk"
    _init_repo(repo)
    (repo / "lover").mkdir()
    doc = repo / "lover" / "arbeidsmiljøloven-aml.md"

    doc.write_text("versjon 1\n", encoding="utf-8")
    _commit_all(repo, "add(lov): arbeidsmiljoloven", "2026-01-01T12:00:00+00:00")

    doc.write_text("versjon 2\n", encoding="utf-8")
    _commit_all(repo, "update(lov): arbeidsmiljoloven", "2026-06-01T12:00:00+00:00")

    rel = "lover/arbeidsmiljøloven-aml.md"
    assert get_law_at_revision(repo, rel, date(2026, 3, 1)) == "versjon 1\n"
    assert get_law_at_revision(repo, rel, date(2026, 7, 1)) == "versjon 2\n"
