"""Tests for lovspor.observatory.engine — which engine is running, and is it pinned.

Issue #219: the nightly worktree was found on a feature branch within an hour
of being installed, and no run record could say which commit produced its
observations. The lane worker refuses a checkout that is on a branch or dirty;
the scheduled job had no equivalent.
"""

import os
import subprocess
from pathlib import Path

import pytest

import lovspor
from lovspor.observatory import engine
from lovspor.observatory.engine import EngineCheckout, describe_engine, engine_root


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    (tmp_path / "engine.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


class TestPinnedCheckout:
    def test_a_detached_clean_checkout_is_pinned_and_names_its_commit(self, tmp_path: Path) -> None:
        repo, sha = _repo(tmp_path)
        _git(repo, "checkout", "-q", "--detach", sha)

        assert describe_engine(repo) == EngineCheckout(commit=sha, pinned=True, reason=None)

    def test_a_checkout_on_a_branch_is_not_pinned(self, tmp_path: Path) -> None:
        """A branch is what a stray `git checkout -b` lands on — the #219 incident."""
        repo, sha = _repo(tmp_path)

        checkout = describe_engine(repo)

        assert checkout.commit == sha
        assert checkout.pinned is False
        assert checkout.reason == "the engine checkout is on a branch"

    def test_a_dirty_checkout_is_not_pinned(self, tmp_path: Path) -> None:
        repo, sha = _repo(tmp_path)
        _git(repo, "checkout", "-q", "--detach", sha)
        (repo / "engine.py").write_text("x = 2\n", encoding="utf-8")

        checkout = describe_engine(repo)

        assert checkout.commit == sha
        assert checkout.pinned is False
        assert checkout.reason == "the engine checkout has local changes"

    def test_an_untracked_file_counts_as_local_changes(self, tmp_path: Path) -> None:
        repo, sha = _repo(tmp_path)
        _git(repo, "checkout", "-q", "--detach", sha)
        (repo / "stray.py").write_text("", encoding="utf-8")

        assert describe_engine(repo).pinned is False

    def test_the_callers_git_environment_cannot_redirect_the_query(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hook exports GIT_DIR for the caller's checkout, but the engine
        verdict is about the explicitly supplied checkout (issue #369)."""
        engine_root = tmp_path / "engine"
        engine_root.mkdir()
        repo, sha = _repo(engine_root)
        _git(repo, "checkout", "-q", "--detach", sha)
        other_root = tmp_path / "other"
        other_root.mkdir()
        other, _ = _repo(other_root)
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(other))

        assert describe_engine(repo) == EngineCheckout(commit=sha, pinned=True, reason=None)


class TestOutsideAGitCheckout:
    def test_an_installed_package_has_no_commit_and_no_verdict(self, tmp_path: Path) -> None:
        """A wheel install is not a checkout: nothing to pin, nothing to refuse."""
        assert describe_engine(tmp_path) == EngineCheckout(commit=None, pinned=None, reason=None)


class TestWhichCheckoutIsAsked:
    """The question is about the engine's own checkout, never about the caller's cwd."""

    def test_the_engine_root_is_where_the_package_was_imported_from(self) -> None:
        root = engine_root()

        assert (root / "src" / "lovspor" / "__init__.py") == Path(lovspor.__file__).resolve()

    def test_the_default_root_is_the_engine_not_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cron job's cwd is wherever launchd put it — outside any checkout."""
        monkeypatch.chdir(tmp_path)

        assert describe_engine().commit is not None
        assert describe_engine().commit == describe_engine(engine_root()).commit

    def test_a_git_answer_is_text(self, tmp_path: Path) -> None:
        """Pydantic's lax mode would decode bytes into `commit` unnoticed, so the
        helper's own type is pinned here rather than through the model."""
        repo, sha = _repo(tmp_path)

        answer = engine._git(repo, "rev-parse", "HEAD")

        assert answer == sha
        assert isinstance(answer, str)
