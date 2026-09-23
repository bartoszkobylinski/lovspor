"""Issue #369: a git hook exports GIT_DIR, and a fixture that inherits it runs its
git commands against the caller's repository instead of its own temp one."""

from pathlib import Path

import pytest

from tests.unit.site_fixtures import commit_all, git_env, throwaway_checkout


def test_git_env_drops_every_git_variable_and_keeps_the_stamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_DIR", "/somewhere/.git/worktrees/x")
    monkeypatch.setenv("GIT_WORK_TREE", "/somewhere")
    monkeypatch.setenv("GIT_INDEX_FILE", "/somewhere/index")
    monkeypatch.setenv("GIT_ARBITRARY_FUTURE_VARIABLE", "must also be removed")
    monkeypatch.setenv("GIT_PREFIX", "nested/")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "/somewhere/objects")
    monkeypatch.setenv("GIT_FUTURE_EXPORTED_VARIABLE", "must-not-survive")
    monkeypatch.setenv("PATH_KEPT", "yes")

    env = git_env(GIT_AUTHOR_NAME="t")

    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert "GIT_INDEX_FILE" not in env
    assert "GIT_ARBITRARY_FUTURE_VARIABLE" not in env
    assert "GIT_PREFIX" not in env
    assert "GIT_OBJECT_DIRECTORY" not in env
    assert "GIT_FUTURE_EXPORTED_VARIABLE" not in env
    assert env["PATH_KEPT"] == "yes"
    assert env["GIT_AUTHOR_NAME"] == "t"


def test_a_throwaway_checkout_works_under_a_hook_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shape of the 712 errors: GIT_DIR names another repository."""
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere" / ".git"))

    root, sha = throwaway_checkout(tmp_path / "repo")
    (root / "more.txt").write_text("x\n", encoding="utf-8")

    assert len(sha) == 40
    assert commit_all(root, "two") != sha
