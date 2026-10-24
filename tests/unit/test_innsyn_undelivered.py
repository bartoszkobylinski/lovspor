"""A letter that never reached the authority, recorded with supported commands (#644).

The operator's question: can a bounced letter, or one a mailbox closed to
e-mail turned away, be recorded, kept off ``due``, and sent again once
``set-recipient`` names a working mailbox, using only the CLI?
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from lovspor.innsyn.ledger import RequestState, ledger_journal, requests
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.lf_ledger import LedgerEntry
from tests.unit.innsyn_support import FIXTURES, fetched, innsyn

PILOT = FIXTURES / "pilot"
ROROS = "5025-20261005T074355Z"
SULDAL = "1134-20261005T074406Z"
DSN = b"Status: 5.4.1\nDiagnostic-Code: smtp; 550 5.4.1 Recipient address rejected\n"
PAGE = "https://www.fjaler.kommune.no/"
_AT_RESEND = ("--sent-at", "2026-10-21T08:00:00Z")


def _held(workspace: Path) -> dict[str, RequestState]:
    return requests(InnsynWorkspace(workspace, []))


def _lines(workspace: Path) -> int:
    return len(ledger_journal(InnsynWorkspace(workspace, [])).records())


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "innsyn"
    result = innsyn(target, "import-pilot", "--register", str(PILOT / "sent.csv"))
    assert result.exit_code == 0, result.output
    return target


@pytest.fixture
def evidence(tmp_path: Path) -> Path:
    path = tmp_path / "dsn.eml"
    path.write_bytes(DSN)
    return path


def _bounce(workspace: Path, evidence: Path, status: str = "5.4.1") -> tuple[int, str]:
    result = innsyn(
        workspace,
        "record-bounce",
        "--request",
        ROROS,
        "--at",
        "2026-10-05T07:48:56Z",
        "--evidence",
        str(evidence),
        "--status",
        status,
    )
    return result.exit_code, result.output


class TestRecording:
    def test_a_bounce_is_recorded_with_its_status_and_evidence_digest(
        self, workspace: Path, evidence: Path
    ) -> None:
        code, output = _bounce(workspace, evidence)
        assert code == 0, output
        assert f"{ROROS}: undelivered recorded" in output
        roros = _held(workspace)[ROROS]
        assert roros.undelivered == "bounced"
        assert not roros.delivered
        last = ledger_journal(InnsynWorkspace(workspace, [])).records()[-1]
        assert last.model_dump(include={"dsn_status", "evidence_sha256", "at"}) == {
            "dsn_status": "5.4.1",
            "evidence_sha256": hashlib.sha256(DSN).hexdigest(),
            "at": datetime(2026, 10, 5, 7, 48, 56, tzinfo=UTC),
        }

    def test_a_mailbox_closed_to_e_mail_is_recorded_without_a_status(
        self, workspace: Path, evidence: Path
    ) -> None:
        result = innsyn(
            workspace,
            "record-mailbox-closed",
            "--request",
            SULDAL,
            "--at",
            "2026-10-05T07:52:00Z",
            "--evidence",
            str(evidence),
        )
        assert result.exit_code == 0, result.output
        suldal = _held(workspace)[SULDAL]
        assert suldal.undelivered == "mailbox_closed"
        assert not suldal.is_open

    def test_an_undelivered_request_is_not_due_and_the_listing_says_why(
        self, workspace: Path, evidence: Path
    ) -> None:
        assert _bounce(workspace, evidence)[0] == 0
        due = innsyn(workspace, "due", "--today", "2026-10-13")
        assert ROROS not in due.stdout
        assert SULDAL in due.stdout
        listing = innsyn(workspace, "requests")
        rows = {line.split("\t")[0]: line.split("\t") for line in listing.stdout.splitlines()}
        assert rows[ROROS][13] == "bounced"
        assert rows[SULDAL][13] == ""

    @pytest.mark.parametrize("status", ["4.4.1", "550", "5.4", "5.4.1 x", ""])
    def test_a_status_that_is_not_a_permanent_failure_is_refused(
        self, workspace: Path, evidence: Path, status: str
    ) -> None:
        before = _lines(workspace)
        code, output = _bounce(workspace, evidence, status)
        assert code == 1
        assert "Refused" in output
        assert _lines(workspace) == before

    def test_missing_or_empty_evidence_is_refused(self, workspace: Path, tmp_path: Path) -> None:
        before = _lines(workspace)
        assert _bounce(workspace, tmp_path / "none.eml")[0] == 1
        empty = tmp_path / "empty.eml"
        empty.write_bytes(b"")
        assert _bounce(workspace, empty)[0] == 1
        assert _lines(workspace) == before

    def test_a_second_undelivered_and_one_before_the_send_are_refused(
        self, workspace: Path, evidence: Path
    ) -> None:
        assert _bounce(workspace, evidence)[0] == 0
        code, output = _bounce(workspace, evidence)
        assert code == 1
        assert "already recorded undelivered" in output
        early = innsyn(
            workspace,
            "record-mailbox-closed",
            "--request",
            SULDAL,
            "--at",
            "2026-10-05T07:44:05Z",
            "--evidence",
            str(evidence),
        )
        assert early.exit_code == 1
        assert "before it was sent" in early.stderr


def _ok(workspace: Path, *args: str) -> str:
    result = innsyn(workspace, *args)
    assert result.exit_code == 0, result.output
    return result.stdout


def _recipient(workspace: Path, address: str) -> None:
    evidence = ("--evidence-url", PAGE, "--read-at", "2026-10-03T10:00:00Z")
    _ok(workspace, "set-recipient", "--id", "4646", "--address", address, *evidence)


def _wave(workspace: Path, lf_ledger: Path, out: Path) -> dict[str, Any]:
    _ok(workspace, "letters", "--out", str(out), "--lf-ledger", str(lf_ledger))
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


class TestResend:
    @pytest.fixture
    def lf_ledger(self, tmp_path: Path) -> Path:
        entry = LedgerEntry(
            authority_id="4646",
            lf_id="2024-09-19-2196",
            kind="LF",
            first_seen=datetime(2026, 10, 1, tzinfo=UTC),
            url=PAGE,
            sha256="a" * 64,
        )
        path = tmp_path / "lf-ledger.jsonl"
        path.write_text(entry.model_dump_json() + "\n", encoding="utf-8")
        return path

    def test_a_bounced_authority_waits_for_a_new_recipient_then_gets_a_new_letter(
        self, tmp_path: Path, httpx_mock: HTTPXMock, lf_ledger: Path, evidence: Path
    ) -> None:
        target = fetched(tmp_path / "innsyn", httpx_mock)
        _ok(target, "set-sender", "--name", "Ola Nordmann", "--email", "kontakt@example.no")
        _recipient(target, "postmottak@fjaler.kommune.no")
        first = tmp_path / "wave-01" / "manifest.json"
        _wave(target, lf_ledger, first.parent)
        sent = ("record-sent", "--manifest", str(first), "--id", "4646")
        _ok(target, *sent, "--sent-at", "2026-10-19T23:46:00Z")
        request = next(iter(_held(target)))
        bounce = ("--at", "2026-10-19T23:48:56Z", "--evidence", str(evidence))
        _ok(target, "record-bounce", "--request", request, *bounce, "--status", "5.4.1")
        dead = _wave(target, lf_ledger, tmp_path / "wave-02")
        assert "4646" in dead["unresolved"]
        assert all(letter["authority_id"] != "4646" for letter in dead["letters"])
        _recipient(target, "post@fjaler.kommune.no")
        second = tmp_path / "wave-03" / "manifest.json"
        wave = _wave(target, lf_ledger, second.parent)
        letter = next(e for e in wave["letters"] if e["authority_id"] == "4646")
        assert letter["recipient"] == "post@fjaler.kommune.no"
        assert letter["lf_ids"] == ["2024-09-19-2196"]
        _ok(target, "record-sent", "--manifest", str(second), "--id", "4646", *_AT_RESEND)
        resent = [s for s in _held(target).values() if s.delivered]
        assert [s.sent.recipient for s in resent] == ["post@fjaler.kommune.no"]


@pytest.mark.parametrize("command", ["record-bounce", "record-mailbox-closed"])
def test_delivery_evidence_keeps_only_digest_not_personal_text(
    workspace: Path, tmp_path: Path, command: str
) -> None:
    payload = b"From: case-officer@example.no\r\n\r\nPrivate officer name\xff\r\n"
    evidence = tmp_path / "private-officer-name.eml"
    evidence.write_bytes(payload)
    args = [
        command,
        "--request",
        ROROS,
        "--at",
        "2026-10-05T07:48:56Z",
        "--evidence",
        str(evidence),
    ]
    if command == "record-bounce":
        args.extend(["--status", "5.4.1"])
    result = innsyn(workspace, *args)
    assert result.exit_code == 0, result.output
    journal = ledger_journal(InnsynWorkspace(workspace, []))
    event = journal.records()[-1].model_dump(mode="json")
    assert event["evidence_sha256"] == hashlib.sha256(payload).hexdigest()
    assert event["source"] == "cli"
    assert set(event) == {
        "event",
        "request_id",
        "recorded_at",
        "reason",
        "at",
        "evidence_sha256",
        "dsn_status",
        "source",
    }
    raw = journal.path.read_bytes()
    assert b"case-officer" not in raw
    assert b"Private officer name" not in raw
    assert evidence.name.encode() not in raw
