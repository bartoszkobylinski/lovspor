"""An authority's request to narrow (offentleglova § 28 andre ledd, #700): recorded,
waiting on the owner, off ``due`` until the owner narrows."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.innsyn.ledger import Outcome, RequestState, ledger_journal, requests
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.cli_output import said
from tests.unit.innsyn_support import FIXTURES, innsyn

PILOT = FIXTURES / "pilot"
SULDAL = "1134-20261005T074406Z"  # sent 2026-10-05, § 32 mark 2026-10-12
ROROS = "5025-20261005T074355Z"
ASKED_AT = "2026-10-08T10:00:00Z"
NARROWING = "Til: postmottak@suldal.kommune.no\nEmne: Re: Innsynskrav\n\nVi avgrensar kravet.\n"
REQUESTED_AT_COLUMN, NARROW_BY_COLUMN = 20, 21


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


def _asked(workspace: Path, *extra: str, at: str = ASKED_AT, request: str = SULDAL) -> None:
    args = ("--request", request, "--at", at, *extra)
    result = innsyn(workspace, "record-narrowing-request", *args)
    assert result.exit_code == 0, result.output
    assert f"{request}: outcome recorded" in result.stdout


def _narrowed(workspace: Path, letter: Path, at: str) -> None:
    args = ("--request", SULDAL, "--at", at, "--kind", "narrowing", "--letter", str(letter))
    result = innsyn(workspace, "record-reply", *args)
    assert result.exit_code == 0, result.output


class TestWaitingOnTheOwner:
    def test_the_request_to_narrow_is_held_and_is_not_an_answer(self, workspace: Path) -> None:
        _asked(workspace)
        suldal = _held(workspace)[SULDAL]
        assert suldal.outcome == "narrowing_requested"
        assert suldal.narrowing_requested_at == datetime(2026, 10, 8, 10, tzinfo=UTC)
        assert suldal.narrow_by is None
        assert suldal.answered_at is None
        assert not suldal.answered
        assert suldal.is_open
        assert suldal.awaits_narrowing

    def test_a_deadline_the_authority_set_is_held(self, workspace: Path) -> None:
        _asked(workspace, "--narrow-by", "2026-10-22")
        assert _held(workspace)[SULDAL].narrow_by == date(2026, 10, 22)

    def test_due_leaves_out_a_request_waiting_on_the_owner(self, workspace: Path) -> None:
        _asked(workspace)
        rows = _rows(workspace, "due", "--today", "2026-10-20")
        assert SULDAL not in rows
        assert ROROS in rows

    def test_the_owner_s_narrowing_ends_the_wait_and_restarts_the_mark(
        self, workspace: Path, letter: Path
    ) -> None:
        _asked(workspace)
        _narrowed(workspace, letter, "2026-10-15T09:39:49Z")  # Thursday; mark 2026-10-22
        suldal = _held(workspace)[SULDAL]
        assert not suldal.awaits_narrowing
        assert suldal.klage_mark == date(2026, 10, 22)
        assert SULDAL not in _rows(workspace, "due", "--today", "2026-10-22")
        assert SULDAL in _rows(workspace, "due", "--today", "2026-10-23")

    def test_a_narrowing_before_the_request_to_narrow_does_not_answer_it(
        self, workspace: Path, letter: Path
    ) -> None:
        _narrowed(workspace, letter, "2026-10-06T09:00:00Z")
        _asked(workspace)
        suldal = _held(workspace)[SULDAL]
        assert suldal.awaits_narrowing
        assert SULDAL not in _rows(workspace, "due", "--today", "2026-10-30")

    def test_the_latest_request_to_narrow_stands_whatever_the_order_recorded(
        self, workspace: Path, letter: Path
    ) -> None:
        _asked(workspace, "--narrow-by", "2026-10-30", at="2026-10-20T10:00:00Z")
        _asked(workspace, "--narrow-by", "2026-10-15", at="2026-10-08T10:00:00Z")
        suldal = _held(workspace)[SULDAL]
        assert suldal.narrowing_requested_at == datetime(2026, 10, 20, 10, tzinfo=UTC)
        assert suldal.narrow_by == date(2026, 10, 30)
        _narrowed(workspace, letter, "2026-10-15T09:39:49Z")
        assert _held(workspace)[SULDAL].awaits_narrowing

    def test_a_closing_outcome_ends_the_wait(self, workspace: Path) -> None:
        _asked(workspace)
        args = ("--request", SULDAL, "--outcome", "full", "--at", "2026-10-20T10:00:00Z")
        assert innsyn(workspace, "record-outcome", *args).exit_code == 0
        suldal = _held(workspace)[SULDAL]
        assert not suldal.awaits_narrowing
        assert not suldal.is_open

    def test_a_partial_answer_with_a_request_to_narrow_the_rest_still_waits(
        self, workspace: Path
    ) -> None:
        args = ("--request", SULDAL, "--outcome", "partial", "--at", "2026-10-07T10:00:00Z")
        assert innsyn(workspace, "record-outcome", *args).exit_code == 0
        _asked(workspace)
        suldal = _held(workspace)[SULDAL]
        assert suldal.answered
        assert suldal.awaits_narrowing


class TestListing:
    def test_requests_shows_when_it_was_asked_and_the_deadline_as_the_last_columns(
        self, workspace: Path
    ) -> None:
        _asked(workspace, "--narrow-by", "2026-10-22")
        rows = _rows(workspace, "requests")
        assert rows[SULDAL][9] == "narrowing_requested"
        assert rows[SULDAL][REQUESTED_AT_COLUMN:] == ["2026-10-08T10:00:00+00:00", "2026-10-22"]
        assert rows[ROROS][REQUESTED_AT_COLUMN:] == ["", ""]

    def test_requests_waiting_lists_only_the_requests_waiting_on_the_owner(
        self, workspace: Path, letter: Path
    ) -> None:
        _asked(workspace)
        _asked(workspace, request=ROROS)
        _narrowed(workspace, letter, "2026-10-15T09:39:49Z")
        result = innsyn(workspace, "requests", "--waiting")
        assert result.exit_code == 0, result.output
        assert [line.split("\t")[0] for line in result.stdout.splitlines()] == [ROROS]
        assert "1 requests waiting on the owner" in result.stderr

    def test_requests_waiting_lists_none_when_nothing_waits(self, workspace: Path) -> None:
        result = innsyn(workspace, "requests", "--waiting")
        assert result.exit_code == 0, result.output
        assert result.stdout == ""
        assert "0 requests waiting on the owner" in result.stderr


class TestRefusals:
    def test_record_outcome_takes_the_class_too(self, workspace: Path) -> None:
        args = ("--request", SULDAL, "--outcome", "narrowing_requested", "--at", ASKED_AT)
        result = innsyn(workspace, "record-outcome", *args)
        assert result.exit_code == 0, result.output
        assert _held(workspace)[SULDAL].awaits_narrowing

    def test_a_deadline_before_the_request_to_narrow_is_refused(self, workspace: Path) -> None:
        before = _lines(workspace)
        args = ("--request", SULDAL, "--at", ASKED_AT, "--narrow-by", "2026-10-07")
        result = innsyn(workspace, "record-narrowing-request", *args)
        assert result.exit_code == 1
        assert "before the request that set it" in result.stderr
        assert _lines(workspace) == before

    def test_a_bad_deadline_is_a_usage_error_that_writes_nothing(self, workspace: Path) -> None:
        before = _lines(workspace)
        args = ("--request", SULDAL, "--at", ASKED_AT, "--narrow-by", "22.10.2026")
        result = innsyn(workspace, "record-narrowing-request", *args)
        assert result.exit_code == 2
        assert said("--narrow-by: not an ISO date", result.output)
        assert _lines(workspace) == before

    def test_only_a_request_to_narrow_sets_a_deadline(self) -> None:
        fields = {
            "request_id": SULDAL,
            "recorded_at": datetime(2026, 10, 8, tzinfo=UTC),
            "outcome": "interim_reply_no_date",
            "at": datetime(2026, 10, 8, 10, tzinfo=UTC),
            "narrow_by": date(2026, 10, 22),
        }
        with pytest.raises(ValidationError, match="only a request to narrow sets a deadline"):
            Outcome.model_validate(fields)

    def test_a_request_to_narrow_says_when_it_arrived(self) -> None:
        fields = {
            "request_id": SULDAL,
            "recorded_at": datetime(2026, 10, 8, tzinfo=UTC),
            "outcome": "narrowing_requested",
            "at": None,
            "source": "pilot-import",
        }
        with pytest.raises(ValidationError, match="says when it arrived"):
            Outcome.model_validate(fields)

    def test_a_deadline_on_the_day_it_was_asked_is_accepted(self) -> None:
        asked = Outcome.model_validate(
            {
                "request_id": SULDAL,
                "recorded_at": datetime(2026, 10, 8, tzinfo=UTC),
                "outcome": "narrowing_requested",
                "at": datetime(2026, 10, 8, 10, tzinfo=UTC),
                "narrow_by": date(2026, 10, 8),
            }
        )
        assert asked.narrow_by == date(2026, 10, 8)


def test_a_narrowing_at_the_same_instant_as_the_request_does_not_end_the_wait(
    workspace: Path, letter: Path
) -> None:
    """The narrowing that ends the wait is later than the request, not as early."""
    _asked(workspace)
    _narrowed(workspace, letter, ASKED_AT)
    assert _held(workspace)[SULDAL].awaits_narrowing


@pytest.mark.parametrize("kind", ["answer", "other"])
def test_a_reply_that_does_not_narrow_keeps_the_request_waiting(
    workspace: Path, letter: Path, kind: str
) -> None:
    """Operations §28 flow: only a later narrowing ends the owner's wait."""
    _asked(workspace)
    result = innsyn(
        workspace,
        "record-reply",
        "--request",
        SULDAL,
        "--at",
        "2026-10-15T09:39:49Z",
        "--kind",
        kind,
        "--letter",
        str(letter),
    )
    assert result.exit_code == 0, result.output
    assert _held(workspace)[SULDAL].awaits_narrowing
    assert SULDAL in _rows(workspace, "requests", "--waiting")
    assert SULDAL not in _rows(workspace, "due", "--today", "2026-11-30")


def test_a_newer_request_without_a_deadline_replaces_the_previous_deadline(
    workspace: Path,
) -> None:
    """A second request replaces the first, including its optional deadline."""
    _asked(workspace, "--narrow-by", "2026-10-22")
    _asked(workspace, at="2026-10-20T10:00:00Z")
    state = _held(workspace)[SULDAL]
    assert state.narrowing_requested_at == datetime(2026, 10, 20, 10, tzinfo=UTC)
    assert state.narrow_by is None
    assert state.awaits_narrowing
    assert _rows(workspace, "requests")[SULDAL][REQUESTED_AT_COLUMN:] == [
        "2026-10-20T10:00:00+00:00",
        "",
    ]


@pytest.mark.parametrize("deadline", ["2026-10-30", None])
def test_the_last_recorded_request_at_the_same_instant_replaces_the_deadline(
    workspace: Path, deadline: str | None
) -> None:
    """Equal arrival times retain journal order when correcting the deadline."""
    _asked(workspace, "--narrow-by", "2026-10-22")
    extra = ("--narrow-by", deadline) if deadline is not None else ()
    _asked(workspace, *extra)

    state = _held(workspace)[SULDAL]
    assert state.narrowing_requested_at == datetime(2026, 10, 8, 10, tzinfo=UTC)
    assert state.narrow_by == (date.fromisoformat(deadline) if deadline else None)
    assert _rows(workspace, "requests")[SULDAL][REQUESTED_AT_COLUMN:] == [
        "2026-10-08T10:00:00+00:00",
        deadline or "",
    ]


@pytest.mark.parametrize("outcome", ["partial", "full", "refused"])
def test_an_answer_after_the_request_only_ends_the_wait_if_it_closes(
    workspace: Path, outcome: str
) -> None:
    """Operations explicitly keeps partial answers waiting but closes full/refused."""
    _asked(workspace)
    result = innsyn(
        workspace,
        "record-outcome",
        "--request",
        SULDAL,
        "--outcome",
        outcome,
        "--at",
        "2026-10-20T10:00:00Z",
    )
    assert result.exit_code == 0, result.output
    waiting = outcome == "partial"
    assert _held(workspace)[SULDAL].awaits_narrowing is waiting
    assert (SULDAL in _rows(workspace, "requests", "--waiting")) is waiting


@pytest.mark.parametrize(("deadline", "accepted"), [("2026-10-08", False), ("2026-10-09", True)])
def test_the_deadline_is_compared_with_the_oslo_day_of_arrival(
    workspace: Path, deadline: str, accepted: bool
) -> None:
    """At 22:30 UTC in October, the authority's Oslo day is already tomorrow."""
    before = _lines(workspace)
    result = innsyn(
        workspace,
        "record-narrowing-request",
        "--request",
        SULDAL,
        "--at",
        "2026-10-08T22:30:00Z",
        "--narrow-by",
        deadline,
    )
    if accepted:
        assert result.exit_code == 0, result.output
        assert _held(workspace)[SULDAL].narrow_by == date(2026, 10, 9)
        assert _lines(workspace) == before + 1
    else:
        assert result.exit_code == 1
        assert "before the request that set it" in result.stderr
        assert _lines(workspace) == before


def test_due_keeps_the_narrowing_request_columns_after_the_owner_replies(
    workspace: Path, letter: Path
) -> None:
    """The documented due columns retain the authority's request and deadline."""
    _asked(workspace, "--narrow-by", "2026-10-22")
    _narrowed(workspace, letter, "2026-10-15T09:39:49Z")
    row = _rows(workspace, "due", "--today", "2026-10-23")[SULDAL]
    assert row[REQUESTED_AT_COLUMN:] == ["2026-10-08T10:00:00+00:00", "2026-10-22"]
