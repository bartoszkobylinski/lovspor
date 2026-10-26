"""The owner's reply to an authority, recorded through the CLI (#686).

The operator's question: when a kommune asks for a nærare avgrensing under
offentleglova § 28 andre ledd and the owner narrows the request, can the
ledger hold that, and does ``due`` judge the narrowed request rather than
the first letter?
"""

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from lovspor.innsyn.ledger import RequestState, ledger_journal, requests
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.cli_output import said
from tests.unit.innsyn_support import FIXTURES, innsyn

PILOT = FIXTURES / "pilot"
SULDAL = "1134-20261005T074406Z"
ROROS = "5025-20261005T074355Z"
NARROWING = "Til: postmottak@suldal.kommune.no\nEmne: Re: Innsynskrav\n\nVi avgrensar kravet.\n"
NARROWED_AT = "2026-10-15T09:39:49Z"
REPLIED_AT_COLUMN, KIND_COLUMN = 17, 18


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
    path = tmp_path / "reply.txt"
    path.write_text(NARROWING, encoding="utf-8")
    return path


def _reply(
    workspace: Path, letter: Path, at: str, kind: str = "narrowing", request: str = SULDAL
) -> tuple[int, str]:
    args = ("--request", request, "--at", at, "--kind", kind, "--letter", str(letter))
    result = innsyn(workspace, "record-reply", *args)
    return result.exit_code, result.output


class TestNarrowing:
    def test_a_narrowing_is_recorded_with_the_letter_as_sent(
        self, workspace: Path, letter: Path
    ) -> None:
        code, output = _reply(workspace, letter, NARROWED_AT)
        assert code == 0, output
        assert f"{SULDAL}: reply recorded" in output
        state = _held(workspace)[SULDAL]
        narrowed = datetime(2026, 10, 15, 9, 39, 49, tzinfo=UTC)
        assert (state.replied_at, state.reply_kind, state.narrowed_at) == (
            narrowed,
            "narrowing",
            narrowed,
        )
        last = ledger_journal(InnsynWorkspace(workspace, [])).records()[-1]
        assert last.model_dump(include={"event", "kind", "letter_sha256"}) == {
            "event": "reply",
            "kind": "narrowing",
            "letter_sha256": hashlib.sha256(NARROWING.encode()).hexdigest(),
        }

    def test_the_32_mark_runs_from_the_narrowing(self, workspace: Path, letter: Path) -> None:
        before = _held(workspace)[SULDAL]
        assert before.klage_mark == date(2026, 10, 12)
        assert before.is_due(date(2026, 10, 17))
        assert _reply(workspace, letter, NARROWED_AT)[0] == 0
        after = _held(workspace)[SULDAL]
        assert after.klage_mark == date(2026, 10, 22)
        assert not after.is_due(date(2026, 10, 22))
        assert after.is_due(date(2026, 10, 23))
        assert SULDAL not in _rows(workspace, "due", "--today", "2026-10-17")
        due = _rows(workspace, "due", "--today", "2026-10-23")
        assert due[SULDAL][10] == "2026-10-22"
        assert due[SULDAL][REPLIED_AT_COLUMN : KIND_COLUMN + 1] == [
            "2026-10-15T09:39:49+00:00",
            "narrowing",
        ]

    def test_requests_shows_the_latest_reply_and_its_kind(
        self, workspace: Path, letter: Path
    ) -> None:
        for at, kind in (
            ("2026-10-15T09:39:49Z", "narrowing"),
            ("2026-10-20T08:00:00Z", "answer"),
            ("2026-10-14T08:00:00Z", "other"),
        ):
            assert _reply(workspace, letter, at, kind)[0] == 0
        rows = _rows(workspace, "requests")
        assert rows[SULDAL][REPLIED_AT_COLUMN:] == ["2026-10-20T08:00:00+00:00", "answer"]
        assert rows[ROROS][REPLIED_AT_COLUMN:] == ["", ""]
        state = _held(workspace)[SULDAL]
        assert state.narrowed_at == datetime(2026, 10, 15, 9, 39, 49, tzinfo=UTC)
        assert state.klage_mark == date(2026, 10, 22)

    def test_the_latest_narrowing_sets_the_mark_whatever_the_order_recorded(
        self, workspace: Path, letter: Path
    ) -> None:
        for at in ("2026-10-20T08:00:00Z", "2026-10-15T09:39:49Z"):
            assert _reply(workspace, letter, at)[0] == 0
        state = _held(workspace)[SULDAL]
        assert state.narrowed_at == datetime(2026, 10, 20, 8, tzinfo=UTC)
        assert state.klage_mark == date(2026, 10, 27)


class TestOtherReplies:
    @pytest.mark.parametrize("kind", ["answer", "other"])
    def test_an_answer_or_other_reply_moves_nothing(
        self, workspace: Path, letter: Path, kind: str
    ) -> None:
        assert _reply(workspace, letter, NARROWED_AT, kind)[0] == 0
        state = _held(workspace)[SULDAL]
        assert state.reply_kind == kind
        assert state.replied_at == datetime(2026, 10, 15, 9, 39, 49, tzinfo=UTC)
        assert state.narrowed_at is None
        assert state.klage_mark == date(2026, 10, 12)
        assert state.is_due(date(2026, 10, 17))


class TestRefusals:
    @pytest.mark.parametrize(
        ("at", "kind", "request_id", "reason"),
        [
            ("2026-10-05T07:44:05Z", "narrowing", SULDAL, "before it was sent"),
            (NARROWED_AT, "widening", SULDAL, "kind"),
            (NARROWED_AT, "narrowing", "1134-20991005T074406Z", "no request"),
        ],
    )
    def test_a_reply_the_ledger_cannot_take_is_refused(
        self, workspace: Path, letter: Path, at: str, kind: str, request_id: str, reason: str
    ) -> None:
        before = _lines(workspace)
        code, output = _reply(workspace, letter, at, kind, request_id)
        assert code == 1
        assert "Refused" in output
        assert reason in output
        assert _lines(workspace) == before

    def test_a_reply_without_its_letter_is_refused(self, workspace: Path, tmp_path: Path) -> None:
        before = _lines(workspace)
        code, output = _reply(workspace, tmp_path / "none.txt", NARROWED_AT)
        assert code == 1
        assert "no such file" in output
        assert _lines(workspace) == before


class TestRefusalsNameTheirCause:
    """Read through ``said``: the unit console is 20 columns wide (#295), so rich wraps."""

    @pytest.mark.parametrize(
        ("at", "reason"),
        [
            ("2026-10-15T09:39:49", "--at: give an offset or Z"),
            ("yesterday", "--at: not an ISO-8601 instant: yesterday"),
        ],
    )
    def test_a_bad_at_names_the_option_and_appends_nothing(
        self, workspace: Path, letter: Path, at: str, reason: str
    ) -> None:
        journal = workspace / "requests.jsonl"
        before = journal.read_bytes()
        code, output = _reply(workspace, letter, at)
        assert code == 2
        assert said(reason, output)
        assert journal.read_bytes() == before
