"""Reminders, follow-up requests and answers in parts, with supported commands (#625).

The operator's question: can each of the three things the pilot did be
recorded, and seen in ``requests`` and ``due``, using only the CLI?
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.innsyn.ledger import RequestState, ledger_journal, requests
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.innsyn_support import FIXTURES, innsyn

PILOT = FIXTURES / "pilot"
OSLO = "0301-20261005T074343Z"
ROROS = "5025-20261005T074355Z"
SULDAL = "1134-20261005T074406Z"
REMINDER = "Til: postmottak@suldal.kommune.no\nEmne: Påminning\n\nViser til offentleglova § 29.\n"


def _held(workspace: Path) -> dict[str, RequestState]:
    return requests(InnsynWorkspace(workspace, []))


def _lines(workspace: Path) -> int:
    return len(ledger_journal(InnsynWorkspace(workspace, [])).records())


def _rows(workspace: Path, command: str, *args: str) -> dict[str, list[str]]:
    result = innsyn(workspace, command, *args)
    assert result.exit_code == 0, result.output
    return {line.split("\t")[0]: line.split("\t") for line in result.stdout.splitlines()}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "innsyn"
    result = innsyn(target, "import-pilot", "--register", str(PILOT / "sent.csv"))
    assert result.exit_code == 0, result.output
    return target


@pytest.fixture
def letter(tmp_path: Path) -> Path:
    path = tmp_path / "reminder.txt"
    path.write_text(REMINDER, encoding="utf-8")
    return path


def _remind(workspace: Path, letter: Path, at: str) -> tuple[int, str]:
    args = ("--request", SULDAL, "--at", at, "--letter", str(letter))
    result = innsyn(workspace, "record-reminder", *args)
    return result.exit_code, result.output


class TestReminder:
    def test_a_reminder_is_recorded_and_due_shows_the_last_one(
        self, workspace: Path, letter: Path
    ) -> None:
        for at in ("2026-10-15T09:39:49Z", "2026-10-14T08:00:00Z"):
            code, output = _remind(workspace, letter, at)
            assert code == 0, output
            assert f"{SULDAL}: reminder recorded" in output
        assert _held(workspace)[SULDAL].reminded_at == datetime(2026, 10, 15, 9, 39, 49, tzinfo=UTC)
        last = ledger_journal(InnsynWorkspace(workspace, [])).records()[-1]
        assert last.model_dump(include={"letter_sha256"}) == {
            "letter_sha256": hashlib.sha256(REMINDER.encode()).hexdigest()
        }
        due = _rows(workspace, "due", "--today", "2026-10-17")
        assert due[SULDAL][14] == "2026-10-15T09:39:49+00:00"
        assert due[ROROS][14] == ""

    def test_a_reminder_leaves_the_request_due(self, workspace: Path, letter: Path) -> None:
        assert _remind(workspace, letter, "2026-10-15T09:39:49Z")[0] == 0
        assert _held(workspace)[SULDAL].is_due(datetime(2026, 10, 17).date())

    def test_a_reminder_before_the_send_or_without_its_letter_is_refused(
        self, workspace: Path, letter: Path, tmp_path: Path
    ) -> None:
        before = _lines(workspace)
        code, output = _remind(workspace, letter, "2026-10-05T07:44:05Z")
        assert code == 1
        assert "before it was sent" in output
        assert _remind(workspace, tmp_path / "none.txt", "2026-10-15T09:39:49Z")[0] == 1
        assert _lines(workspace) == before
