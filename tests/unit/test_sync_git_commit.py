"""Tests for lovspor.sync.git_commit.

Each test uses a fresh real git repo created in tmp_path. We invoke
the real `git` binary so behavior matches what the orchestrator will
see in CI and in production.
"""

import subprocess
from pathlib import Path

import pytest

from lovspor.sync.git_commit import (
    GitCommandError,
    add,
    commit,
    has_staged_changes,
    push,
    status_porcelain,
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Initialize a fresh git repo with a test user identity."""
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.name", "test"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "commit.gpgsign", "false"], tmp_path)
    return tmp_path


def test_add_does_nothing_for_empty_paths(repo: Path) -> None:
    add(repo, [])
    assert not has_staged_changes(repo)


def test_add_stages_a_relative_string_path(repo: Path) -> None:
    (repo / "a.txt").write_text("hi")
    add(repo, ["a.txt"])
    assert has_staged_changes(repo)


def test_add_stages_a_relative_pathlib_path(repo: Path) -> None:
    (repo / "a.txt").write_text("hi")
    add(repo, [Path("a.txt")])
    assert has_staged_changes(repo)


def test_add_stages_an_absolute_path(repo: Path) -> None:
    file_path = repo / "a.txt"
    file_path.write_text("hi")
    add(repo, [file_path])
    assert has_staged_changes(repo)


def test_add_handles_relative_repo_with_absolute_file(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MEDIUM regression guard: when ``repo`` is a relative path and the
    file argument is absolute, path.relative_to(repo) previously raised
    raw ValueError because Path comparison requires both to be the same
    flavor. Codex PR #14 reproducer: add(Path('repo'), [absolute_path])."""
    monkeypatch.chdir(repo.parent)
    relative_repo = Path(repo.name)
    file_path = (repo / "a.txt").resolve()
    file_path.write_text("hi")
    add(relative_repo, [file_path])
    assert has_staged_changes(relative_repo)


def test_add_raises_git_command_error_on_unknown_path(repo: Path) -> None:
    with pytest.raises(GitCommandError, match="exited"):
        add(repo, ["does-not-exist.txt"])


def test_has_staged_changes_false_on_clean_repo(repo: Path) -> None:
    assert not has_staged_changes(repo)


def test_has_staged_changes_true_after_add(repo: Path) -> None:
    (repo / "a.txt").write_text("hi")
    add(repo, ["a.txt"])
    assert has_staged_changes(repo)


def test_commit_creates_a_commit_and_clears_staged_state(repo: Path) -> None:
    (repo / "a.txt").write_text("hi")
    add(repo, ["a.txt"])
    commit(repo, "feat: add a")
    assert not has_staged_changes(repo)
    log = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert log.stdout.strip() == "feat: add a"


def test_commit_raises_when_nothing_is_staged(repo: Path) -> None:
    # An initial commit on an empty repo with no staged changes fails
    # with "nothing to commit". Caller is expected to gate via
    # has_staged_changes.
    with pytest.raises(GitCommandError):
        commit(repo, "empty")


def test_commit_message_with_multiline_body_preserved(repo: Path) -> None:
    (repo / "a.txt").write_text("hi")
    add(repo, ["a.txt"])
    message = "feat: subject\n\nLonger body explaining\nthe change."
    commit(repo, message)
    log = subprocess.run(
        ["git", "log", "--format=%B", "-1"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Longer body explaining" in log.stdout


def test_status_porcelain_empty_on_clean_repo(repo: Path) -> None:
    assert status_porcelain(repo) == ""


def test_status_porcelain_lists_untracked_files(repo: Path) -> None:
    (repo / "untracked.txt").write_text("x")
    out = status_porcelain(repo)
    assert "untracked.txt" in out
    assert out.startswith("??")


def test_push_to_local_bare_repo(tmp_path: Path) -> None:
    bare = tmp_path / "bare.git"
    _git(["init", "--bare", "-q", str(bare)], tmp_path)

    src = tmp_path / "src"
    src.mkdir()
    _git(["init", "-q"], src)
    _git(["config", "user.name", "test"], src)
    _git(["config", "user.email", "test@example.com"], src)
    _git(["config", "commit.gpgsign", "false"], src)
    _git(["remote", "add", "origin", str(bare)], src)
    _git(["checkout", "-q", "-b", "main"], src)
    (src / "a.txt").write_text("hi")
    _git(["add", "a.txt"], src)
    _git(["commit", "-m", "init"], src)

    push(src, branch="main")

    # Verify the bare repo received the push by inspecting refs directly
    # (default 'git log' on a bare repo without HEAD set is fragile).
    refs = subprocess.run(
        ["git", "show-ref", "refs/heads/main"],
        cwd=bare,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "refs/heads/main" in refs.stdout


def test_push_defaults_to_main_branch(tmp_path: Path) -> None:
    """Pin push() default branch='main'. Mutation hardening: the previous
    push test passed branch='main' explicitly, leaving the default
    parameter unexercised."""
    bare = tmp_path / "bare.git"
    _git(["init", "--bare", "-q", str(bare)], tmp_path)

    src = tmp_path / "src"
    src.mkdir()
    _git(["init", "-q"], src)
    _git(["config", "user.name", "test"], src)
    _git(["config", "user.email", "test@example.com"], src)
    _git(["config", "commit.gpgsign", "false"], src)
    _git(["remote", "add", "origin", str(bare)], src)
    _git(["checkout", "-q", "-b", "main"], src)
    (src / "a.txt").write_text("hi")
    _git(["add", "a.txt"], src)
    _git(["commit", "-m", "init"], src)

    push(src)  # no branch kwarg — relies on default

    refs = subprocess.run(
        ["git", "show-ref", "refs/heads/main"],
        cwd=bare,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "refs/heads/main" in refs.stdout


def test_push_raises_on_unknown_remote(repo: Path) -> None:
    (repo / "a.txt").write_text("hi")
    add(repo, ["a.txt"])
    commit(repo, "init")
    with pytest.raises(GitCommandError, match="exited"):
        push(repo, remote="missing-remote", branch="main")


def test_command_error_includes_stderr_in_message(repo: Path) -> None:
    """Surface git's stderr so the operator can see why the command failed."""
    with pytest.raises(GitCommandError) as exc_info:
        add(repo, ["completely-missing.txt"])
    assert "completely-missing.txt" in str(exc_info.value)
