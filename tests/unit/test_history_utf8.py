"""History derivation reads git's output as UTF-8 whatever the locale (PR #517).

Every local regulation carries "åndsverkloven § 14", and ~2,000 corpus files
have æøå slugs, so both git calls — the ``log --numstat`` walk and the
``show`` of a blob's front matter — return non-ASCII bytes. Under an ASCII
locale codec (``LC_CTYPE=C``) a decode that follows the locale crashes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lovspor.history import _frontmatter_id_at, _run_git_log

PATH = "lokale-forskrifter/0301/slamtømming.md"
DOC_ID = "lk-0301-aa8ae774921d"
SUBJECT = "promote(lokal-forskrift): 0301/slamtømming v1"
BODY = f'---\nid: "{DOC_ID}"\nsource_license: "åndsverkloven § 14"\n---\n\n# Slamtømming\n'


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=Operator", "-c", "user.email=op@example.invalid", *args],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return done.stdout.decode("utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose one commit adds an æøå-named file with an æøå subject."""
    (tmp_path / PATH).parent.mkdir(parents=True)
    (tmp_path / PATH).write_bytes(BODY.encode("utf-8"))
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", SUBJECT)
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD").strip()


def test_the_log_walk_reads_non_ascii_paths_and_subjects_under_an_ascii_locale(
    repo: tuple[Path, str], c_locale: None
) -> None:
    path, sha = repo

    log = _run_git_log(path, PATH)

    lines = log.splitlines()
    assert (lines[0], lines[1], lines[3]) == ("__COMMIT__", sha, SUBJECT)
    assert lines[-1] == f"6\t0\t{PATH}"


def test_the_front_matter_read_decodes_the_blob_under_an_ascii_locale(
    repo: tuple[Path, str], c_locale: None
) -> None:
    path, sha = repo

    assert _frontmatter_id_at(path, sha, PATH) == DOC_ID


def test_assumption_git_reads_the_quotepath_setting_case_insensitively(
    repo: tuple[Path, str],
) -> None:
    """Pins the git behaviour the history.py quotePath equivalents argue from.

    Config section and key names are case-insensitive and boolean values
    case-fold, so every spelling gives the raw UTF-8 path in ``--numstat``
    rows; the quoted default (``"\\303\\270"``) is the control that shows the
    setting is read at all.
    """
    path, _ = repo
    walk = ("log", "--follow", "--numstat", "--format=%H%n%s", "--", PATH)
    spellings = ("core.quotePath=false", "core.quotepath=false", "CORE.QUOTEPATH=FALSE")

    outputs = [_git(path, "-c", spelling, *walk) for spelling in spellings]

    assert outputs[0] == outputs[1] == outputs[2]
    assert outputs[0].endswith(f"\t{PATH}\n")
    assert "\\303\\270" in _git(path, *walk)
