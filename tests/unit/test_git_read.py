"""``lovspor.git_read``: the one read-only Git runner of the release-checkout guard (#738, B1a).

Real Git in ``tmp_path``. The runner refuses any argument vector outside its
exact allowlist before a process starts, never inherits the caller's Git
environment, and never lets ``git status`` rewrite the index.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lovspor import git_read
from lovspor.git_read import ALLOWED, GitReadError, git_environment, read_git
from tests.unit.forarbeider_checkout_support import git, release_clone

WRITES = {
    "add",
    "am",
    "apply",
    "checkout",
    "cherry-pick",
    "clean",
    "clone",
    "commit",
    "fetch",
    "gc",
    "init",
    "ls-remote",
    "merge",
    "mv",
    "notes",
    "pull",
    "push",
    "rebase",
    "remote",
    "reset",
    "restore",
    "revert",
    "rm",
    "stash",
    "switch",
    "tag",
    "update-index",
    "update-ref",
    "worktree",
}
CONFIG_WRITES = {"--add", "--unset", "--unset-all", "--replace-all", "--edit", "--rename-section"}


def test_the_allowlist_holds_no_write_network_or_notes_verb() -> None:
    assert ALLOWED
    for argv in ALLOWED:
        assert argv[0] not in WRITES, argv
        assert not set(argv) & CONFIG_WRITES, argv
        assert "notes" not in argv, argv


def test_an_argv_outside_the_allowlist_is_refused_before_any_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_process(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a process was started")

    monkeypatch.setattr(git_read.subprocess, "run", no_process)
    for argv in (("add", "-A"), ("config", "--add", "x.y", "z"), ("status",), ("notes", "list")):
        with pytest.raises(GitReadError):
            read_git(tmp_path, argv)


def test_the_environment_is_minimal_and_never_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "HOME"):
        monkeypatch.setenv(name, "/elsewhere")
    env = git_environment(tmp_path / "clone")
    assert set(env) == {
        "PATH",
        "LC_ALL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_TERMINAL_PROMPT",
        "GIT_OPTIONAL_LOCKS",
        "GIT_CEILING_DIRECTORIES",
    }
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["LC_ALL"] == "C"
    assert env["GIT_CEILING_DIRECTORIES"] == str(tmp_path)
    assert env["PATH"] == os.environ["PATH"]


def test_a_reading_returns_the_exit_code_and_the_raw_output(tmp_path: Path) -> None:
    clone = release_clone(tmp_path)
    reading = read_git(clone, ("symbolic-ref", "--quiet", "HEAD"))
    assert reading.code == 0
    assert reading.out == b"refs/heads/forarbeider/batch-2100-04-10\n"
    git(clone, "checkout", "--quiet", "--detach")
    assert read_git(clone, ("symbolic-ref", "--quiet", "HEAD")).code == 1


def test_an_inherited_git_dir_cannot_point_the_reader_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = release_clone(tmp_path / "a")
    other = release_clone(tmp_path / "b", branch="forarbeider/other")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    reading = read_git(clone, ("symbolic-ref", "--quiet", "HEAD"))
    assert reading.out == b"refs/heads/forarbeider/batch-2100-04-10\n"


def test_status_does_not_rewrite_a_stale_index(tmp_path: Path) -> None:
    clone = release_clone(tmp_path)
    tracked = clone / "lover" / "README.md"
    later = tracked.stat().st_mtime_ns + 5_000_000_000
    os.utime(tracked, ns=(later, later))
    index = clone / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    argv = ("status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignored=no")
    assert read_git(clone, argv).out == b""
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before


def test_a_timeout_or_a_missing_git_is_a_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def times_out(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired("git", 1)

    def missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("git")

    for failure in (times_out, missing):
        monkeypatch.setattr(git_read.subprocess, "run", failure)
        with pytest.raises(GitReadError):
            read_git(tmp_path, ("symbolic-ref", "--quiet", "HEAD"))


def test_the_process_is_started_with_list_argv_no_shell_and_the_fixed_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def record(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(git_read.subprocess, "run", record)
    read_git(tmp_path, ("symbolic-ref", "--quiet", "HEAD"))
    argv = seen["argv"]
    assert argv[0] == "git"
    assert argv[-3:] == ["symbolic-ref", "--quiet", "HEAD"]
    assert argv[argv.index("-C") + 1] == str(tmp_path)
    for flag in ("--no-optional-locks", "--no-pager", "--literal-pathspecs"):
        assert flag in argv
    for setting in ("core.fsmonitor=false", "core.untrackedCache=false"):
        assert argv[argv.index(setting) - 1] == "-c"
    assert seen.get("shell", False) is False
    assert seen["stdin"] == subprocess.DEVNULL
    assert seen["check"] is False
    assert seen["timeout"] == git_read.TIMEOUT_SECONDS
    assert seen["env"] == git_environment(tmp_path)


def test_the_version_is_readable_through_the_allowlist(tmp_path: Path) -> None:
    clone = release_clone(tmp_path)
    reading = read_git(clone, ("version",))
    assert reading.code == 0
    assert reading.out.startswith(b"git version ")
