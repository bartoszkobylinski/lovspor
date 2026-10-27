"""An interim reply that names a date (#689): recorded with its day, not due until it passes."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.innsyn.ledger import Outcome, RequestState, requests
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.cli_output import said
from tests.unit.innsyn_support import FIXTURES, innsyn

PILOT = FIXTURES / "pilot"
ROROS = "5025-20261005T074355Z"  # sent 2026-10-05, § 32 mark 2026-10-12
REPLY_AT = "2026-10-08T10:00:00Z"


def _held(workspace: Path) -> dict[str, RequestState]:
    return requests(InnsynWorkspace(workspace, []))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "innsyn"
    result = innsyn(target, "import-pilot", "--register", str(PILOT / "sent.csv"))
    assert result.exit_code == 0, result.output
    return target


def _interim(workspace: Path, promised_by: str, at: str = REPLY_AT) -> None:
    args = ("--request", ROROS, "--at", at, "--promised-by", promised_by)
    result = innsyn(workspace, "record-interim", *args)
    assert result.exit_code == 0, result.output
    assert f"{ROROS}: outcome recorded" in result.stdout


class TestPromisedDay:
    def test_the_promise_is_held_and_is_not_an_answer(self, workspace: Path) -> None:
        _interim(workspace, "2026-11-22")
        roros = _held(workspace)[ROROS]
        assert roros.outcome == "interim_reply_with_date"
        assert roros.promised_by == date(2026, 11, 22)
        assert roros.answered_at is None
        assert not roros.answered
        assert roros.is_open

    def test_due_waits_for_the_promised_day_then_flags_the_missed_date(
        self, workspace: Path
    ) -> None:
        _interim(workspace, "2026-11-22")
        roros = _held(workspace)[ROROS]
        assert roros.klage_mark == date(2026, 10, 12)
        assert roros.due_from == date(2026, 11, 22)
        assert not roros.is_due(date(2026, 10, 13))
        assert not roros.is_due(date(2026, 11, 22))
        assert roros.is_due(date(2026, 11, 23))

    def test_a_promise_before_the_mark_still_waits_for_the_mark(self, workspace: Path) -> None:
        _interim(workspace, "2026-10-09")
        roros = _held(workspace)[ROROS]
        assert roros.due_from == date(2026, 10, 12)
        assert not roros.is_due(date(2026, 10, 12))
        assert roros.is_due(date(2026, 10, 13))

    def test_due_lists_it_only_after_the_promised_day(self, workspace: Path) -> None:
        _interim(workspace, "2026-11-22")
        before = innsyn(workspace, "due", "--today", "2026-11-22")
        assert ROROS not in before.stdout
        after = innsyn(workspace, "due", "--today", "2026-11-23")
        assert ROROS in after.stdout

    def test_requests_shows_the_promised_day_as_the_last_column(self, workspace: Path) -> None:
        _interim(workspace, "2026-11-22")
        listing = innsyn(workspace, "requests").stdout.splitlines()
        rows = {r.split("\t")[0]: r.split("\t") for r in listing}
        assert rows[ROROS][9] == "interim_reply_with_date"
        assert rows[ROROS][-1] == "2026-11-22"
        assert rows["1134-20261005T074406Z"][-1] == ""

    def test_a_later_promise_replaces_the_earlier_one(self, workspace: Path) -> None:
        _interim(workspace, "2026-11-01")
        _interim(workspace, "2026-11-22", at="2026-10-20T10:00:00Z")
        assert _held(workspace)[ROROS].promised_by == date(2026, 11, 22)

    def test_an_answer_after_the_promise_drops_it(self, workspace: Path) -> None:
        _interim(workspace, "2026-11-22")
        closing = ("--request", ROROS, "--outcome", "full", "--at", "2026-11-10T10:00:00Z")
        assert innsyn(workspace, "record-outcome", *closing).exit_code == 0
        roros = _held(workspace)[ROROS]
        assert roros.promised_by is None
        assert roros.answered_at == datetime(2026, 11, 10, 10, tzinfo=UTC)
        assert not roros.is_open

    def test_an_undated_interim_after_a_dated_one_falls_back_to_the_mark(
        self, workspace: Path
    ) -> None:
        _interim(workspace, "2026-11-22")
        undated = ("--outcome", "interim_reply_no_date", "--at", "2026-10-20T10:00:00Z")
        assert innsyn(workspace, "record-outcome", "--request", ROROS, *undated).exit_code == 0
        roros = _held(workspace)[ROROS]
        assert roros.promised_by is None
        assert roros.is_due(date(2026, 10, 21))


class TestRefusals:
    def test_record_outcome_cannot_record_the_dated_class_without_its_day(
        self, workspace: Path
    ) -> None:
        args = ("--request", ROROS, "--outcome", "interim_reply_with_date", "--at", REPLY_AT)
        result = innsyn(workspace, "record-outcome", *args)
        assert result.exit_code == 1
        assert "an interim reply with a date says the day it promised" in result.stderr
        assert _held(workspace)[ROROS].outcome is None

    def test_a_day_before_the_reply_is_refused(self, workspace: Path) -> None:
        args = ("--request", ROROS, "--at", REPLY_AT, "--promised-by", "2026-10-07")
        result = innsyn(workspace, "record-interim", *args)
        assert result.exit_code == 1
        assert "the promised day is before the reply that promised it" in result.stderr
        assert _held(workspace)[ROROS].outcome is None

    def test_the_reply_day_itself_is_accepted(self, workspace: Path) -> None:
        _interim(workspace, "2026-10-08")
        assert _held(workspace)[ROROS].promised_by == date(2026, 10, 8)

    def test_record_interim_without_the_day_is_a_usage_error_that_writes_nothing(
        self, workspace: Path
    ) -> None:
        journal = InnsynWorkspace(workspace, []).requests_log
        before = journal.read_bytes()
        result = innsyn(workspace, "record-interim", "--request", ROROS, "--at", REPLY_AT)
        assert result.exit_code == 2, result.output
        assert said("Missing option '--promised-by'", result.output)
        assert journal.read_bytes() == before

    def test_a_malformed_day_is_a_usage_error(self, workspace: Path) -> None:
        args = ("--request", ROROS, "--at", REPLY_AT, "--promised-by", "22.11.2026")
        result = innsyn(workspace, "record-interim", *args)
        assert result.exit_code == 2
        assert said("--promised-by: not an ISO date (YYYY-MM-DD): 22.11.2026", result.output)
        assert _held(workspace)[ROROS].outcome is None

    def test_a_dated_reply_before_the_send_is_refused(self, workspace: Path) -> None:
        args = ("--request", ROROS, "--at", "2026-10-04T10:00:00Z", "--promised-by", "2026-11-22")
        result = innsyn(workspace, "record-interim", *args)
        assert result.exit_code == 1
        assert "is before it was sent" in result.stderr

    @pytest.mark.parametrize("outcome", ["interim_reply_no_date", "full", "no_answer"])
    def test_only_the_dated_class_may_promise_a_day(self, outcome: str) -> None:
        data = {
            "request_id": ROROS,
            "recorded_at": datetime(2026, 10, 8, 10, tzinfo=UTC),
            "outcome": outcome,
            "at": datetime(2026, 10, 8, 10, tzinfo=UTC),
            "promised_by": date(2026, 11, 22),
        }
        with pytest.raises(ValidationError, match="only an interim reply with a date"):
            Outcome.model_validate(data)

    def test_a_pilot_row_of_the_dated_class_needs_its_day_too(self) -> None:
        data = {
            "request_id": ROROS,
            "recorded_at": datetime(2026, 10, 8, 10, tzinfo=UTC),
            "outcome": "interim_reply_with_date",
            "at": None,
            "source": "pilot-import",
        }
        with pytest.raises(ValidationError, match="says the day it promised"):
            Outcome.model_validate(data)
