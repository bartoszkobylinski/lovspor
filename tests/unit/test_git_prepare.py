"""The Git capability of the AR4a release clone (#738): what may run, and with what.

``git_environment`` is the whole environment Git gets, nothing inherited but
``PATH``. Argument vectors must fit a shape exactly: clone paths are absolute
and normalized, a verified revision is ``HEAD``, ``main`` or a 40-hex commit
peeled with ``^{commit}``. A refused vector names its command alone, and a
Git that answers with a failing exit code is an answer, not an error.
"""

import os
from pathlib import Path

import pytest

from lovspor.git_prepare import GitPrepareError, allowed, git_environment, run_git

_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_CLONE = ("clone", "--no-local", "--no-hardlinks", "--no-checkout", "--template=", "--quiet", "--")


def test_the_environment_is_built_here_and_inherits_only_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/opt/git/bin")
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("HOME", "/home/someone")

    assert git_environment(tmp_path / "clone") == {
        "PATH": "/opt/git/bin",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_GRAFT_FILE": os.devnull,
    }


def test_without_a_path_git_gets_the_default_search_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PATH", raising=False)

    assert git_environment(tmp_path / "clone")["PATH"] == os.defpath


@pytest.mark.parametrize(
    ("source", "fits"),
    [
        ("/srv/corpus", True),
        ("corpus", False),  # relative, though normalized
        ("/srv/../corpus", False),  # absolute, not normalized
    ],
)
def test_clone_paths_are_absolute_and_normalized(source: str, fits: bool) -> None:
    assert allowed((*_CLONE, source, "/srv/release-clone")) is fits


@pytest.mark.parametrize(
    ("revision", "fits"),
    [
        ("HEAD^{commit}", True),
        ("refs/heads/main^{commit}", True),
        (f"{_COMMIT}^{{commit}}", True),
        (_COMMIT, False),  # not peeled
        (f"{_COMMIT[:39]}^{{commit}}", False),  # not 40 hex
        (f"{_COMMIT}^{{tree}}", False),  # peeled to something else
    ],
)
def test_a_verified_revision_is_head_main_or_a_peeled_commit(revision: str, fits: bool) -> None:
    assert allowed(("rev-parse", "--verify", "--quiet", revision)) is fits


def test_a_refused_vector_is_reported_by_its_command_alone(tmp_path: Path) -> None:
    with pytest.raises(GitPrepareError) as refused:
        run_git(tmp_path, ("push", "--force", "origin", "main"))
    assert str(refused.value) == "git argument vector not allowed: ('push',)"


def test_a_failing_git_is_an_answer_with_its_exit_code(tmp_path: Path) -> None:
    not_a_clone = tmp_path / "empty"
    not_a_clone.mkdir()

    answer = run_git(not_a_clone, ("rev-parse", "--verify", "--quiet", "HEAD^{commit}"))

    assert answer.code != 0
    assert answer.out == b""
