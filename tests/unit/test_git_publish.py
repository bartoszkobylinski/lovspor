"""The Git capability of AR4b-1 path B (#738): the environment Git gets.

``environment`` is the whole environment of every Git process this module
starts: nothing is inherited but ``PATH``, configuration outside the clone,
prompts, replace objects and grafts are off, and a simulated remote's ref
update records a fixed identity and time.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.git_publish import environment

_ISOLATION = {
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_GRAFT_FILE": os.devnull,
}


def test_the_environment_is_built_here_and_inherits_only_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/opt/git/bin")
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("HOME", "/home/someone")
    clone = tmp_path / "clone"

    assert environment(clone, None) == {
        "PATH": "/opt/git/bin",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        **_ISOLATION,
    }


def test_without_a_path_git_gets_the_default_search_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PATH", raising=False)

    assert environment(tmp_path / "clone", None)["PATH"] == os.defpath


def test_a_private_index_and_a_simulated_time_are_passed_to_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/opt/git/bin")
    clone = tmp_path / "clone"
    index = clone / ".git" / "publication.index"
    at = datetime(2026, 11, 22, 12, 30, tzinfo=UTC)

    assert environment(clone, index, at) == {
        "PATH": "/opt/git/bin",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        **_ISOLATION,
        "GIT_INDEX_FILE": str(index),
        "GIT_COMMITTER_NAME": "lovspor-simulated-remote",
        "GIT_COMMITTER_EMAIL": "remote@lovspor.invalid",
        "GIT_COMMITTER_DATE": f"{int(at.timestamp())} +0000",
    }
