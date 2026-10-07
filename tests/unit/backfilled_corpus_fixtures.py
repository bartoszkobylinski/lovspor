"""A ``lovverk`` checkout with one local regulation backfilled as v1, v2, v3 (ADR-0016 S6).

Reached the way an operator reaches it: captures appended by the
observatory's writer, decisions by ``promote approve``, versions by
``promote backfill``, one ``git`` commit per version under the subject the
command prints. The commits are dated weeks after the observations, so the
observation axis and the corpus axis cannot agree by accident.

Observations (A→B→A): v1 on 2026-08-19 and 08-20, v2 on 08-21, v3 on 08-22.
Commits: the skeleton on 2026-09-01, v1 on 09-10, v2 on 09-20, v3 on 09-30.
"""

from __future__ import annotations

import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from tests.unit.promotion_cli_fixtures import (
    FIRST_SEEN,
    Decision,
    approve,
    make_corpus,
    promote,
    register,
    store,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

CHANGED = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
DAY = timedelta(days=1)
LOCAL = "lokale-forskrifter"
COMMIT_DATES = ("2026-09-10T12:00:00Z", "2026-09-20T12:00:00Z", "2026-09-30T12:00:00Z")
INTERVALS = [
    ("2026-08-19T15:17:23Z", "2026-08-20T15:17:23Z", 2),
    ("2026-08-21T15:17:23Z", "2026-08-21T15:17:23Z", 1),
    ("2026-08-22T15:17:23Z", "2026-08-22T15:17:23Z", 1),
]


def backfilled_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The checkout after three backfill runs, each committed on its date."""
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    register(observatory)
    a = store(observatory, html_page(), observed_at=FIRST_SEEN)
    store(observatory, html_page(), observed_at=FIRST_SEEN + DAY)
    b = store(observatory, html_page(CHANGED), observed_at=FIRST_SEEN + 2 * DAY)
    store(observatory, html_page(), observed_at=FIRST_SEEN + 3 * DAY)
    for sha256 in (a, b):
        assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
    checkout = make_corpus(tmp_path)
    dated_git(
        checkout, "2026-09-01T12:00:00Z", "commit", "-q", "--amend", "--no-edit", "--reset-author"
    )
    for date in COMMIT_DATES:
        result = promote("backfill", a, checkout)
        assert result.exit_code == 0, result.output
        dated_git(checkout, date, "add", "--", LOCAL)
        dated_git(checkout, date, "commit", "-q", "-m", _printed_subject(result.output))
    return checkout


def local_address(corpus: Path) -> str:
    """``<authority_id>/<slug>`` of the one local regulation in ``corpus``."""
    [authority_dir] = [p for p in (corpus / LOCAL).iterdir() if p.is_dir()]
    [markdown] = authority_dir.glob("*.md")
    return f"{authority_dir.name}/{markdown.stem}"


def dated_git(corpus: Path, date: str, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    done = subprocess.run(
        # No auto gc / maintenance: a detached writer would race a test that
        # removes .git afterwards (#582).
        [
            "git",
            *("-c", "user.name=Operator", "-c", "user.email=op@example.invalid"),
            *("-c", "gc.auto=0", "-c", "maintenance.auto=false"),
            *args,
        ],
        cwd=corpus,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return done.stdout


def _printed_subject(output: str) -> str:
    line = next(
        line
        for line in output.splitlines()
        if line.startswith("  git -C") and " commit -m " in line
    )
    return line.split(" commit -m ", 1)[1].strip("'")
