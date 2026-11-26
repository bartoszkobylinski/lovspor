"""The Git capability of AR4b-1 path B (#738): what may run, and how it runs.

A push target is an absolute, normalized local path ending in ``.git``; a
refused vector, a misplaced index and a Git that cannot start or hangs are
each a ``GitPublishError`` that names what failed and nothing more; a command
given no input reads none, never the caller's standard input.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from lovspor import git_publish
from lovspor.git_publish import GitContext, GitPublishError, allowed, run_git

_COMMIT_OBJECT = (
    b"tree 4b825dc642cb6eb9a060e54bf8d69288fbee4904\n"
    b"author A <a@lovspor.invalid> 0 +0000\n"
    b"committer A <a@lovspor.invalid> 0 +0000\n"
    b"\nsynthetic\n"
)
_HASH_OBJECT = ("hash-object", "-t", "commit", "-w", "--stdin")


@pytest.mark.parametrize(
    ("target", "fits"),
    [
        ("/srv/remotes/forarbeider.git", True),
        ("forarbeider.git", False),  # relative
        ("remotes/../forarbeider.git", False),  # relative, not normalized
        ("https://example.invalid/forarbeider.git", False),  # a URL
        ("/srv/remotes/forarbeider", False),  # not a .git path
    ],
)
def test_a_push_target_is_an_absolute_local_git_path(target: str, fits: bool) -> None:
    assert allowed(("ls-remote", target, "refs/heads/main")) is fits


def test_a_refused_vector_is_reported_by_its_command_alone(tmp_path: Path) -> None:
    with pytest.raises(GitPublishError) as refused:
        run_git(tmp_path, ("push", "--force", "/srv/r.git", "main"))
    assert str(refused.value) == "git argument vector not allowed: ('push',)"


def test_an_index_outside_the_clones_git_directory_is_refused(tmp_path: Path) -> None:
    context = GitContext(index_file=tmp_path / "elsewhere.index")
    with pytest.raises(GitPublishError, match="index file"):
        run_git(tmp_path, ("write-tree",), context=context)


def _bare(tmp_path: Path) -> Path:
    bare = tmp_path / "remote.git"
    init = ("init", "--bare", "--quiet", "--template=", "--initial-branch=main", str(bare))
    assert run_git(tmp_path, init).code == 0
    return bare


def test_a_git_that_cannot_start_names_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "no-git-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(GitPublishError) as failed:
        run_git(tmp_path, ("write-tree",))
    assert str(failed.value) == "git write-tree could not run"


def test_a_git_that_hangs_is_stopped_at_the_time_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hanging = bin_dir / "git"
    hanging.write_text("#!/bin/sh\nexec /bin/sleep 5\n", encoding="utf-8")
    hanging.chmod(0o700)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(git_publish, "TIMEOUT_SECONDS", 0.5)
    with pytest.raises(GitPublishError, match="git write-tree could not run"):
        run_git(tmp_path, ("write-tree",))


@contextmanager
def _standard_input(content: bytes) -> Iterator[None]:
    """File descriptor 0 reads ``content`` for the duration of the block."""
    read_end, write_end = os.pipe()
    os.write(write_end, content)
    os.close(write_end)
    saved = os.dup(0)
    os.dup2(read_end, 0)
    os.close(read_end)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        os.close(saved)


def test_a_command_given_no_input_never_reads_the_callers_standard_input(
    tmp_path: Path,
) -> None:
    bare = _bare(tmp_path)
    given = run_git(bare, _HASH_OBJECT, _COMMIT_OBJECT)
    assert given.code == 0, given.err

    with _standard_input(_COMMIT_OBJECT):
        answer = run_git(bare, _HASH_OBJECT)

    assert answer.code != 0
    assert answer.out == b""
