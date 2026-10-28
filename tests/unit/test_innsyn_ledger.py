"""The request ledger, written only through its commands (#509 point 4).

The operator's question: can every ledger field be reached using only the
CLI? The pilot register (names removed, personopplysningsloven) seeds it the
way the owner will, through ``import-pilot``; each transition then has its
own command.
"""

import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from lovspor.errors import InnsynRecordError, InnsynRefusedError
from lovspor.innsyn.ledger import (
    CaseNumber,
    RequestState,
    Sent,
    fold,
    ledger_journal,
    record_events,
    requests,
)
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.innsyn_support import FIXTURES, innsyn

PILOT = FIXTURES / "pilot"
OSLO_REQUEST = "0301-20261005T074343Z"
ROROS = "5025-20261005T074355Z"
SULDAL = "1134-20261005T074406Z"
FJALER = "4646-20261005T074425Z"


def _held(workspace: Path) -> dict[str, RequestState]:
    return requests(InnsynWorkspace(workspace, []))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "innsyn"
    result = innsyn(target, "import-pilot", "--register", str(PILOT / "sent.csv"))
    assert result.exit_code == 0, result.output
    assert "5 requests imported, 0 held" in result.stdout
    return target


class TestPilotImport:
    def test_every_register_field_lands_in_the_ledger(self, workspace: Path) -> None:
        held = _held(workspace)
        oslo = held[OSLO_REQUEST]
        assert oslo.sent.recipient == "postmottak@oslobystyre.no"
        assert oslo.sent.sent_at == datetime(2026, 10, 5, 7, 43, 43, tzinfo=UTC)
        assert len(oslo.sent.lf_ids) == 51
        assert not oslo.sent.general_clause
        assert oslo.sent.source == "pilot-import"
        assert oslo.acknowledged_at == datetime(2026, 10, 5, 7, 44, tzinfo=UTC)
        assert oslo.outcome == "referred_to_lovdata"
        assert oslo.answered_at == datetime(2026, 10, 6, 12, 28, 20, tzinfo=UTC)
        assert held[SULDAL].saksnummer == "2026/2348"
        assert held[SULDAL].outcome == "interim_reply_no_date"
        assert held[SULDAL].answered_at is None
        assert held[FJALER].outcome == "partial"
        assert held[FJALER].sent.lf_ids == (
            "2009-02-16-306",
            "2022-12-15-2677",
            "2024-09-19-2196",
        )
        assert held[ROROS].outcome is None

    def test_a_rerun_imports_nothing_twice(self, workspace: Path) -> None:
        before = len(ledger_journal(InnsynWorkspace(workspace, [])).records())
        again = innsyn(workspace, "import-pilot", "--register", str(PILOT / "sent.csv"))
        assert again.exit_code == 0, again.output
        assert "0 requests imported, 5 held" in again.stdout
        assert len(ledger_journal(InnsynWorkspace(workspace, [])).records()) == before

    def test_a_letter_that_disagrees_with_its_row_refuses_the_whole_import(
        self, tmp_path: Path
    ) -> None:
        copy = tmp_path / "pilot"
        shutil.copytree(PILOT, copy)
        letter = copy / "innsyn-4646.txt"
        letter.write_text(letter.read_text().replace("- FOR-2009-02-16-306", "- removed"))
        result = innsyn(tmp_path / "innsyn", "import-pilot", "--register", str(copy / "sent.csv"))
        assert result.exit_code == 1
        assert "lists 2 ids, the register says 3" in result.stderr
        assert not (tmp_path / "innsyn" / "requests.jsonl").exists()

    @pytest.mark.parametrize("longer", ["2009-02-16-306-7", "2009-02-16-306.7", "2009-02-16-306x"])
    def test_an_id_line_naming_a_longer_number_is_not_read_as_its_prefix(
        self, tmp_path: Path, longer: str
    ) -> None:
        copy = tmp_path / "pilot"
        shutil.copytree(PILOT, copy)
        letter = copy / "innsyn-4646.txt"
        letter.write_text(letter.read_text().replace("- FOR-2009-02-16-306 ", f"- FOR-{longer} "))
        result = innsyn(tmp_path / "innsyn", "import-pilot", "--register", str(copy / "sent.csv"))
        assert result.exit_code == 1
        assert "lists 2 ids, the register says 3" in result.stderr

    def test_a_missing_letter_or_register_is_refused(self, tmp_path: Path) -> None:
        copy = tmp_path / "pilot"
        shutil.copytree(PILOT, copy)
        (copy / "innsyn-0301.txt").unlink()
        missing_letter = innsyn(
            tmp_path / "a", "import-pilot", "--register", str(copy / "sent.csv")
        )
        assert "the letter as sent is missing" in missing_letter.stderr
        missing = innsyn(tmp_path / "b", "import-pilot", "--register", str(copy / "none.csv"))
        assert missing.exit_code == 1
        assert "no such register" in missing.stderr

    def test_an_unknown_outcome_class_is_refused(self, tmp_path: Path) -> None:
        copy = tmp_path / "pilot"
        shutil.copytree(PILOT, copy)
        register = copy / "sent.csv"
        register.write_text(register.read_text().replace("interim_reply_no_date", "waiting"))
        result = innsyn(tmp_path / "innsyn", "import-pilot", "--register", str(register))
        assert result.exit_code == 1
        assert "sent.csv:4" in result.stderr

    def test_the_letters_directory_can_be_named(self, tmp_path: Path) -> None:
        register = tmp_path / "register.csv"
        shutil.copy(PILOT / "sent.csv", register)
        result = innsyn(
            tmp_path / "innsyn",
            "import-pilot",
            "--register",
            str(register),
            "--letters",
            str(PILOT),
        )
        assert result.exit_code == 0, result.output


class TestTransitions:
    @pytest.mark.parametrize(
        ("outcome", "answered", "is_open"),
        [
            ("referred_to_lovdata", True, True),
            ("interim_reply_no_date", False, True),
            ("partial", True, True),
            ("full", True, False),
            ("refused", True, False),
            ("no_answer", False, True),
        ],
    )
    def test_each_outcome_controls_answer_open_and_due_state(
        self, workspace: Path, outcome: str, answered: bool, is_open: bool
    ) -> None:
        # docs/operations.md: only the first four outcome classes are answers;
        # requests remain open until full or refused.
        result = innsyn(
            workspace,
            "record-outcome",
            "--request",
            ROROS,
            "--outcome",
            outcome,
            "--at",
            "2026-10-08T10:00:00Z",
        )
        assert result.exit_code == 0, result.output
        state = _held(workspace)[ROROS]
        assert state.outcome == outcome
        assert state.answered is answered
        assert state.is_open is is_open
        expected_at = datetime(2026, 10, 8, 10, tzinfo=UTC) if answered else None
        assert state.answered_at == expected_at
        assert not state.is_due(date(2026, 10, 12))
        assert state.is_due(date(2026, 10, 13)) is (not answered)

    @pytest.mark.parametrize("command", ["record-ack", "record-outcome", "record-klage"])
    def test_transition_times_refuse_before_send_but_accept_exact_send_time(
        self, workspace: Path, command: str
    ) -> None:
        path = workspace / "requests.jsonl"
        before = path.read_bytes()
        args = ("--outcome", "no_answer") if command == "record-outcome" else ()
        refused = innsyn(
            workspace, command, "--request", ROROS, *args, "--at", "2026-10-05T07:43:54Z"
        )
        assert refused.exit_code == 1
        assert "before it was sent" in refused.stderr
        assert path.read_bytes() == before
        accepted = innsyn(
            workspace, command, "--request", ROROS, *args, "--at", "2026-10-05T09:43:55+02:00"
        )
        assert accepted.exit_code == 0, accepted.output
        events = ledger_journal(InnsynWorkspace(workspace, [])).records()
        assert len(events) == len(before.splitlines()) + 1
        assert events[-1].request_id == ROROS

    def test_an_invalid_batch_appends_none_of_its_valid_prefix(self, workspace: Path) -> None:
        target = InnsynWorkspace(workspace, [])
        before = target.requests_log.read_bytes()
        events = [
            CaseNumber(
                request_id=request,
                recorded_at=datetime(2026, 10, 13, tzinfo=UTC),
                saksnummer="2026/999",
            )
            for request in (ROROS, "5025-20991005T074355Z")
        ]
        with pytest.raises(InnsynRefusedError, match="no request"):
            record_events(target, events)
        assert target.requests_log.read_bytes() == before
        assert _held(workspace)[ROROS].saksnummer is None

    def test_each_transition_has_its_command(self, workspace: Path) -> None:
        steps = [
            ("record-ack", "--at", "2026-10-05T08:00:00Z"),
            ("record-case-number", "--number", " 2026/1234 "),
            ("record-klage", "--at", "2026-10-13T09:00:00Z"),
        ]
        for command, option, value in steps:
            result = innsyn(workspace, command, "--request", ROROS, option, value)
            assert result.exit_code == 0, result.output
        roros = _held(workspace)[ROROS]
        assert roros.acknowledged_at == datetime(2026, 10, 5, 8, tzinfo=UTC)
        assert roros.saksnummer == "2026/1234"
        assert roros.klage_sent_at == datetime(2026, 10, 13, 9, tzinfo=UTC)

    def test_a_refusal_is_recorded_with_its_basis_and_closes_the_request(
        self, workspace: Path
    ) -> None:
        result = innsyn(
            workspace,
            "record-outcome",
            "--request",
            ROROS,
            "--outcome",
            "refused",
            "--at",
            "2026-10-09T10:00:00Z",
            "--refusal-basis",
            "offentleglova § 28 andre ledd",
        )
        assert result.exit_code == 0, result.output
        roros = _held(workspace)[ROROS]
        assert (roros.outcome, roros.refusal_basis) == ("refused", "offentleglova § 28 andre ledd")
        assert roros.answered_at == datetime(2026, 10, 9, 10, tzinfo=UTC)
        assert not roros.is_open

    def test_answered_at_is_the_first_answer_not_a_later_one(self, workspace: Path) -> None:
        for outcome, at in (("partial", "2026-10-08T10:00:00Z"), ("full", "2026-10-20T10:00:00Z")):
            result = innsyn(
                workspace, "record-outcome", "--request", ROROS, "--outcome", outcome, "--at", at
            )
            assert result.exit_code == 0, result.output
        roros = _held(workspace)[ROROS]
        assert roros.outcome == "full"
        assert roros.answered_at == datetime(2026, 10, 8, 10, tzinfo=UTC)

    def test_an_interim_reply_is_not_an_answer(self, workspace: Path) -> None:
        innsyn(
            workspace,
            "record-outcome",
            "--request",
            ROROS,
            "--outcome",
            "interim_reply_no_date",
            "--at",
            "2026-10-08T10:00:00Z",
        )
        roros = _held(workspace)[ROROS]
        assert roros.answered_at is None
        assert roros.is_open

    @pytest.mark.parametrize(
        "args",
        [
            ("record-ack", "--request", "5025-20991005T074355Z", "--at", "2026-10-05T08:00:00Z"),
            ("record-ack", "--request", ROROS, "--at", "2026-10-04T08:00:00Z"),
            (
                "record-outcome",
                "--request",
                ROROS,
                "--outcome",
                "maybe",
                "--at",
                "2026-10-06T08:00Z",
            ),
            (
                "record-outcome",
                "--request",
                ROROS,
                "--outcome",
                "full",
                "--at",
                "2026-10-06T08:00Z",
                "--refusal-basis",
                "§ 13",
            ),
            ("record-case-number", "--request", ROROS, "--number", "  "),
        ],
    )
    def test_a_transition_the_ledger_cannot_take_is_refused(
        self, workspace: Path, args: tuple[str, ...]
    ) -> None:
        before = len(ledger_journal(InnsynWorkspace(workspace, [])).records())
        result = innsyn(workspace, *args)
        assert result.exit_code == 1
        assert len(ledger_journal(InnsynWorkspace(workspace, [])).records()) == before

    def test_a_transition_before_any_ledger_exists_is_refused(self, tmp_path: Path) -> None:
        result = innsyn(tmp_path, "record-ack", "--request", ROROS, "--at", "2026-10-05T08:00Z")
        assert result.exit_code == 1
        assert "no request" in result.stderr


class TestListings:
    def test_invalid_utf8_is_a_record_error_and_a_cli_refusal(self, workspace: Path) -> None:
        # Journal.records promises InnsynRecordError when a line cannot parse;
        # app.guarded turns that into the operator's refusal, without a traceback.
        target = InnsynWorkspace(workspace, [])
        original = target.requests_log.read_bytes()
        payload = original + b"\xff\n"
        target.requests_log.write_bytes(payload)
        listing = innsyn(workspace, "requests")
        append = innsyn(workspace, "record-case-number", "--request", ROROS, "--number", "2026/999")
        assert target.requests_log.read_bytes() == payload
        for result in (listing, append):
            assert result.exit_code == 1
            assert result.stdout == ""
            assert "Refused:" in result.stderr
            assert "requests.jsonl" in result.stderr
        with pytest.raises(InnsynRecordError):
            ledger_journal(target).records()

    @pytest.mark.parametrize("damage", ["torn", "malformed_middle", "blank_middle"])
    def test_corrupt_evidence_is_refused_and_cannot_be_appended_to(
        self, workspace: Path, damage: str
    ) -> None:
        path = workspace / "requests.jsonl"
        original = path.read_bytes()
        if damage == "torn":
            payload = original.rstrip(b"\n")
            message = "incomplete (no newline)"
        else:
            lines = original.splitlines(keepends=True)
            payload = lines[0] + (b"{bad}\n" if damage == "malformed_middle" else b"\n")
            payload += b"".join(lines[1:])
            message = "requests.jsonl:2: does not parse"
        path.write_bytes(payload)
        listing = innsyn(workspace, "requests")
        assert listing.exit_code == 1
        assert message in listing.stderr
        assert listing.stdout == ""
        result = innsyn(workspace, "record-case-number", "--request", ROROS, "--number", "123")
        assert result.exit_code == 1
        assert message in result.stderr
        assert path.read_bytes() == payload

    def test_requests_lists_every_field_and_the_mark(self, workspace: Path) -> None:
        result = innsyn(workspace, "requests")
        assert result.exit_code == 0, result.output
        rows = {line.split("\t")[0]: line.split("\t") for line in result.stdout.splitlines()}
        assert rows[SULDAL] == [
            SULDAL,
            "1134",
            "postmottak@suldal.kommune.no",
            "2026-10-05T07:44:06+00:00",
            "21",
            "",
            "2026/2348",
            "2026-10-05T07:44:22+00:00",
            "",
            "interim_reply_no_date",
            "2026-10-12",
            "",
            "",
            "",
            "",
            "",
            "email",
            "",
            "",
            "",
            "",
            "",
        ]
        assert "5 requests" in result.stderr

    def test_due_lists_the_unanswered_after_the_mark(self, workspace: Path) -> None:
        on_the_mark = innsyn(workspace, "due", "--today", "2026-10-12")
        assert on_the_mark.stdout == ""
        after = innsyn(workspace, "due", "--today", "2026-10-13")
        assert after.exit_code == 0, after.output
        assert {line.split("\t")[0] for line in after.stdout.splitlines()} == {
            ROROS,
            SULDAL,
            "4627-20261005T074416Z",
        }
        assert "3 requests past their § 32 mark" in after.stderr

    def test_a_klage_takes_the_request_off_due(self, workspace: Path) -> None:
        innsyn(workspace, "record-klage", "--request", ROROS, "--at", "2026-10-13T09:00Z")
        after = innsyn(workspace, "due", "--today", "2026-10-13")
        assert ROROS not in after.stdout

    def test_due_without_a_date_judges_by_today(self, workspace: Path) -> None:
        result = innsyn(workspace, "due")
        assert result.exit_code == 0, result.output

    def test_a_bad_date_is_a_usage_error(self, workspace: Path) -> None:
        assert innsyn(workspace, "due", "--today", "13.10.2026").exit_code == 2

    def test_a_damaged_ledger_is_refused(self, workspace: Path) -> None:
        with (workspace / "requests.jsonl").open("a") as handle:
            handle.write('{"event": "nonsense"}\n')
        result = innsyn(workspace, "requests")
        assert result.exit_code == 1
        assert "does not parse" in result.stderr
        with pytest.raises(InnsynRecordError):
            ledger_journal(InnsynWorkspace(workspace, [])).records()


def test_a_sent_event_must_name_its_request_and_ask_for_something() -> None:
    base = {
        "request_id": "4646-20261005T074425Z",
        "recorded_at": datetime(2026, 10, 5, tzinfo=UTC),
        "authority_id": "4646",
        "recipient": "postmottak@fjaler.kommune.no",
        "sent_at": datetime(2026, 10, 5, 7, 44, 25, tzinfo=UTC),
        "lf_ids": ("2009-02-16-306",),
        "general_clause": False,
        "letter_sha256": "0" * 64,
    }
    assert Sent.model_validate(base).request_id == FJALER
    with pytest.raises(ValueError, match="authority and sent_at"):
        Sent.model_validate({**base, "authority_id": "1134"})
    with pytest.raises(ValueError, match="or both"):
        Sent.model_validate({**base, "lf_ids": ()})
    with pytest.raises(ValueError, match="sorted and distinct"):
        Sent.model_validate({**base, "lf_ids": ("2024-01-01-2", "2009-02-16-306")})
    state = fold([Sent.model_validate(base)])[FJALER]
    assert state.klage_mark == date(2026, 10, 12)
    assert state.is_due(date(2026, 10, 13))
