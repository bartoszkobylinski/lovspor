"""Reminders, follow-up requests and answers in parts, with supported commands (#625).

The operator's question: can each of the three things the pilot did be
recorded, and seen in ``requests`` and ``due``, using only the CLI?
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.ledger import (
    Outcome,
    RequestState,
    Sent,
    ledger_journal,
    record_events,
    requests,
)
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.innsyn_support import FIXTURES, innsyn

PILOT = FIXTURES / "pilot"
OSLO = "0301-20261005T074343Z"
ROROS = "5025-20261005T074355Z"
SULDAL = "1134-20261005T074406Z"
FOLLOW_UP = (
    "Til: postmottak@byr.oslo.kommune.no\nEmne: Innsynskrav\n\nViser til svaret 6. oktober.\n"
)
BYRADET = "0301-20261015T065934Z"
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


def _follow_up(
    workspace: Path, letter: Path, parent: str = OSLO, at: str = "2026-10-15T06:59:34Z"
) -> tuple[int, str]:
    args = ("--parent", parent, "--letter", str(letter), "--sent-at", at)
    result = innsyn(workspace, "record-follow-up", *args, "--message-id", "<f.1@lovspor.no>")
    return result.exit_code, result.output


@pytest.fixture
def follow_up(tmp_path: Path) -> Path:
    path = tmp_path / "follow-up.txt"
    path.write_text(FOLLOW_UP, encoding="utf-8")
    return path


class TestFollowUp:
    def test_a_follow_up_is_a_request_of_its_own_linked_to_the_first(
        self, workspace: Path, follow_up: Path
    ) -> None:
        code, output = _follow_up(workspace, follow_up)
        assert code == 0, output
        assert (
            f"{BYRADET}: follow-up of {OSLO} to postmottak@byr.oslo.kommune.no recorded" in output
        )
        held = _held(workspace)
        first, second = held[OSLO].sent, held[BYRADET].sent
        assert second.follows == OSLO
        assert second.recipient == "postmottak@byr.oslo.kommune.no"
        assert (second.lf_ids, second.general_clause) == (first.lf_ids, first.general_clause)
        assert second.letter_sha256 == hashlib.sha256(FOLLOW_UP.encode()).hexdigest()
        assert second.message_id == "<f.1@lovspor.no>"
        assert held[BYRADET].klage_mark.isoformat() == "2026-10-22"
        rows = _rows(workspace, "requests")
        assert rows[BYRADET][15] == OSLO
        assert rows[OSLO][15] == ""

    def test_without_a_message_id_the_follow_up_is_still_recorded(
        self, workspace: Path, follow_up: Path
    ) -> None:
        args = ("--parent", OSLO, "--letter", str(follow_up), "--sent-at", "2026-10-15T06:59:34Z")
        result = innsyn(workspace, "record-follow-up", *args)
        assert result.exit_code == 0, result.output
        assert _held(workspace)[BYRADET].sent.message_id is None

    @pytest.mark.parametrize(
        ("parent", "at", "reason"),
        [
            ("0301-20991005T074343Z", "2026-10-15T06:59:34Z", "no request 0301-20991005T074343Z"),
            (OSLO, "2026-10-05T07:43:43Z", "already in the ledger"),
            (OSLO, "2026-10-04T07:00:00Z", "not sent after"),
        ],
    )
    def test_a_follow_up_the_ledger_cannot_take_is_refused(
        self, workspace: Path, follow_up: Path, parent: str, at: str, reason: str
    ) -> None:
        before = _lines(workspace)
        code, output = _follow_up(workspace, follow_up, parent, at)
        assert code == 1
        assert reason in output
        assert _lines(workspace) == before

    @pytest.mark.parametrize(
        "text", ["Emne: x\n\nbody\n", "Til: Byrådet <post@byr.no>\n\nbody\n", ""]
    )
    def test_a_letter_that_does_not_name_a_mailbox_is_refused(
        self, workspace: Path, tmp_path: Path, text: str
    ) -> None:
        letter = tmp_path / "bad.txt"
        letter.write_text(text, encoding="utf-8")
        before = _lines(workspace)
        assert _follow_up(workspace, letter)[0] == 1
        assert _lines(workspace) == before

    def test_a_follow_up_naming_another_authoritys_request_is_refused(
        self, workspace: Path, follow_up: Path
    ) -> None:
        event = _held(workspace)[OSLO].sent.model_copy(
            update={
                "follows": ROROS,
                "request_id": BYRADET,
                "sent_at": datetime(2026, 10, 15, 6, 59, 34, tzinfo=UTC),
            }
        )
        with pytest.raises(InnsynRefusedError, match="another authority"):
            record_events(InnsynWorkspace(workspace, []), [event])

    def test_a_ledger_written_before_follow_ups_still_loads(self, workspace: Path) -> None:
        path = workspace / "requests.jsonl"
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for line in lines:
            line.pop("follows", None)
        path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        assert all(
            isinstance(s.sent, Sent) and s.sent.follows is None for s in _held(workspace).values()
        )


class TestAnswerInParts:
    def test_each_part_is_an_outcome_of_its_own_and_the_first_is_the_answer(
        self, workspace: Path
    ) -> None:
        for at in ("2026-10-16T08:09:26Z", "2026-10-20T10:00:00Z"):
            args = ("--request", ROROS, "--outcome", "partial", "--at", at)
            result = innsyn(workspace, "record-outcome", *args)
            assert result.exit_code == 0, result.output
        events = ledger_journal(InnsynWorkspace(workspace, [])).records()
        parts = [e.at for e in events if isinstance(e, Outcome) and e.request_id == ROROS]
        assert parts == [
            datetime(2026, 10, 16, 8, 9, 26, tzinfo=UTC),
            datetime(2026, 10, 20, 10, tzinfo=UTC),
        ]
        assert _held(workspace)[ROROS].answered_at == parts[0]
