"""Authorities that take requests only through a web form or KS eDialog (#644, owner 2026-10-24).

The owner sends those by hand. The operator's question: can such an
authority be given its channel, get a letter, be refused by ``send``, be
recorded as sent by hand with its receipt, and then be due and reminded like
any other, using only the CLI?
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from lovspor.innsyn.directory import Contact, RecipientRecorded, contacts, directory_journal
from lovspor.innsyn.ledger import RequestState, Sent, ledger_journal, requests
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.cli_output import said
from tests.unit.innsyn_support import FIXTURES, fetched, innsyn

HAREID = "1517"
EDIALOG = "https://svarut.ks.no/edialog/mottaker/964979278"
RECEIPT = b"%PDF-1.4 kvittering\n"
READ_AT = ("--read-at", "2026-10-20T08:00:00Z")


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lovspor.cli.load_env", lambda: None)


def _ok(workspace: Path, *args: str) -> str:
    result = innsyn(workspace, *args)
    assert result.exit_code == 0, result.output
    return result.stdout


def _held(workspace: Path) -> dict[str, RequestState]:
    return requests(InnsynWorkspace(workspace, []))


def _channel(workspace: Path, channel: str = "edialog", url: str = EDIALOG) -> str:
    args = ("--id", HAREID, "--channel", channel, "--evidence-url", url, *READ_AT)
    return _ok(workspace, "set-channel", *args)


def _wave(workspace: Path, out: Path) -> dict[str, Any]:
    _ok(workspace, "letters", "--out", str(out), "--lf-ledger", str(out.parent / "lf.jsonl"))
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    target = fetched(tmp_path / "innsyn", httpx_mock)
    _ok(target, "set-sender", "--name", "Ola Nordmann", "--email", "kontakt@example.no")
    (tmp_path / "lf.jsonl").write_text("", encoding="utf-8")
    return target


@pytest.fixture
def receipt(tmp_path: Path) -> Path:
    path = tmp_path / "kvittering.pdf"
    path.write_bytes(RECEIPT)
    return path


def _manual(workspace: Path, manifest: Path, receipt: Path) -> Any:
    args = ("--manifest", str(manifest), "--id", HAREID, "--sent-at", "2026-10-21T09:00:00Z")
    return innsyn(workspace, "record-sent-manual", *args, "--evidence", str(receipt))


class TestChannel:
    def test_a_channel_resolves_the_authority_and_the_latest_route_stands(
        self, workspace: Path
    ) -> None:
        assert f"{HAREID}: edialog {EDIALOG}" in _channel(workspace)
        rows = {r.split("\t")[0]: r.split("\t") for r in _ok(workspace, "authorities").splitlines()}
        assert rows[HAREID][3:5] == [f"edialog {EDIALOG}", EDIALOG]
        page = ("--evidence-url", "https://www.hareid.kommune.no/", *READ_AT)
        _ok(
            workspace, "set-recipient", "--id", HAREID, "--address", "post@hareid.kommune.no", *page
        )
        rows = {r.split("\t")[0]: r.split("\t") for r in _ok(workspace, "authorities").splitlines()}
        assert rows[HAREID][3] == "post@hareid.kommune.no"

    def test_an_unknown_channel_or_an_authority_outside_the_population_is_refused(
        self, workspace: Path
    ) -> None:
        for args in (
            ("--id", HAREID, "--channel", "fax"),
            ("--id", "9999", "--channel", "webform"),
        ):
            result = innsyn(workspace, "set-channel", *args, "--evidence-url", EDIALOG, *READ_AT)
            assert result.exit_code == 1
        assert not (workspace / "directory.jsonl").exists()


class TestManualSend:
    def test_letter_refused_by_send_recorded_by_hand_then_due_and_reminded(
        self, workspace: Path, tmp_path: Path, receipt: Path
    ) -> None:
        _channel(workspace)
        wave = _wave(workspace, tmp_path / "wave")
        entry = next(e for e in wave["letters"] if e["authority_id"] == HAREID)
        assert (entry["channel"], entry["recipient"]) == ("edialog", EDIALOG)
        assert HAREID not in wave["unresolved"]
        letter = (tmp_path / "wave" / entry["letter_file"]).read_text(encoding="utf-8")
        assert letter.startswith(f"Til: {EDIALOG}\n")
        manifest = tmp_path / "wave" / "manifest.json"
        dry = innsyn(workspace, "send", "--manifest", str(manifest))
        assert f"refused {HAREID}: goes by edialog, not e-mail" in dry.stderr
        result = _manual(workspace, manifest, receipt)
        assert result.exit_code == 0, result.output
        request = f"{HAREID}-20261021T090000Z"
        assert f"{request}: sent by edialog to {EDIALOG} recorded" in result.stdout
        sent = _held(workspace)[request].sent
        assert (sent.channel, sent.recipient) == ("edialog", EDIALOG)
        assert sent.receipt_sha256 == hashlib.sha256(RECEIPT).hexdigest()
        assert sent.message_id is None
        due = {
            r.split("\t")[0]: r.split("\t")
            for r in _ok(workspace, "due", "--today", "2026-10-29").splitlines()
        }
        assert due[request][16] == "edialog"
        reminder = tmp_path / "reminder.txt"
        reminder.write_text("Påminning\n", encoding="utf-8")
        remind = ("--request", request, "--at", "2026-10-29T08:00:00Z", "--letter", str(reminder))
        _ok(workspace, "record-reminder", *remind)
        assert _held(workspace)[request].reminded_at == datetime(2026, 10, 29, 8, tzinfo=UTC)
        assert _held(workspace)[request].is_due(datetime(2026, 10, 30).date())

    def test_record_sent_refuses_a_manual_letter_and_manual_refuses_an_e_mail_one(
        self, workspace: Path, tmp_path: Path, receipt: Path
    ) -> None:
        _channel(workspace)
        page = ("--evidence-url", "https://www.fjaler.kommune.no/", *READ_AT)
        _ok(
            workspace, "set-recipient", "--id", "4646", "--address", "post@fjaler.kommune.no", *page
        )
        manifest = tmp_path / "wave" / "manifest.json"
        _wave(workspace, manifest.parent)
        by_mail = ("--manifest", str(manifest), "--id", HAREID, "--sent-at", "2026-10-21T09:00Z")
        assert innsyn(workspace, "record-sent", *by_mail).exit_code == 1
        args = ("--manifest", str(manifest), "--id", "4646", "--sent-at", "2026-10-21T09:00Z")
        wrong = innsyn(workspace, "record-sent-manual", *args, "--evidence", str(receipt))
        assert wrong.exit_code == 1
        assert "goes by e-mail" in wrong.stderr
        assert _manual(workspace, manifest, tmp_path / "none.pdf").exit_code == 1
        assert not (workspace / "requests.jsonl").exists()


def test_a_sent_line_written_before_channels_still_loads(tmp_path: Path) -> None:
    target = tmp_path / "innsyn"
    _ok(target, "import-pilot", "--register", str(FIXTURES / "pilot" / "sent.csv"))
    path = target / "requests.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        for key in ("channel", "receipt_sha256", "follows"):
            line.pop(key, None)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    sent = [state.sent for state in _held(target).values()]
    assert all(isinstance(s, Sent) and s.channel == "email" for s in sent)
    assert len(ledger_journal(InnsynWorkspace(target, [])).records()) == len(lines)


@pytest.mark.parametrize("channel", ["webform", "edialog"])
def test_manual_sent_schema_requires_page_receipt_and_no_message_id(channel: str) -> None:
    """The manual-delivery contract in operations.md must also hold on journal reload."""
    data = {
        "request_id": f"{HAREID}-20261021T090000Z",
        "authority_id": HAREID,
        "recorded_at": "2026-10-21T09:00:00Z",
        "sent_at": "2026-10-21T09:00:00Z",
        "recipient": EDIALOG,
        "channel": channel,
        "receipt_sha256": hashlib.sha256(RECEIPT).hexdigest(),
        "letter_sha256": "a" * 64,
        "lf_ids": [],
        "general_clause": True,
    }
    sent = Sent.model_validate(data)
    assert Sent.model_validate_json(sent.model_dump_json()) == sent
    for invalid in (
        {"receipt_sha256": None},
        {"receipt_sha256": "not-a-sha256"},
        {"message_id": "<mail@example.no>"},
        {"recipient": "post@example.no"},
    ):
        with pytest.raises(ValidationError):
            Sent.model_validate({**data, **invalid})
    email = {**data, "channel": "email", "recipient": "post@example.no"}
    with pytest.raises(ValidationError, match="receipt"):
        Sent.model_validate(email)
    assert Sent.model_validate({**email, "receipt_sha256": None}).channel == "email"


@pytest.mark.parametrize("channel", ["webform", "edialog"])
def test_manual_channel_replaces_mailbox_without_losing_maalform(
    workspace: Path, tmp_path: Path, channel: str
) -> None:
    page = ("--evidence-url", EDIALOG, *READ_AT)
    _ok(workspace, "set-maalform", "--id", HAREID, "--form", "nn", *page)
    _ok(workspace, "set-recipient", "--id", HAREID, "--address", "post@example.no", *page)
    _channel(workspace, channel)
    wave = _wave(workspace, tmp_path / "wave")
    entry = next(e for e in wave["letters"] if e["authority_id"] == HAREID)
    assert (entry["channel"], entry["recipient"]) == (channel, EDIALOG)
    assert entry["maalform"] == "nn"
    assert entry["maalform_recorded"] is True
    _ok(workspace, "set-recipient", "--id", HAREID, "--address", "new@example.no", *page)
    wave = _wave(workspace, tmp_path / "email-wave")
    entry = next(e for e in wave["letters"] if e["authority_id"] == HAREID)
    assert (entry["channel"], entry["recipient"]) == ("email", "new@example.no")
    assert entry["maalform"] == "nn"
    assert entry["maalform_recorded"] is True


@pytest.mark.parametrize("failure", ["empty-receipt", "wrong-addressee", "duplicate"])
def test_refused_manual_recording_preserves_existing_ledger(
    workspace: Path, tmp_path: Path, receipt: Path, failure: str
) -> None:
    _channel(workspace)
    manifest = tmp_path / "wave" / "manifest.json"
    wave = _wave(workspace, manifest.parent)
    assert _manual(workspace, manifest, receipt).exit_code == 0
    journal = workspace / "requests.jsonl"
    before = journal.read_bytes()
    if failure == "empty-receipt":
        receipt.write_bytes(b"")
    elif failure == "wrong-addressee":
        entry = next(e for e in wave["letters"] if e["authority_id"] == HAREID)
        (manifest.parent / entry["letter_file"]).write_text(
            "Til: https://other.example.no/\nEmne: Innsyn\n\nBody\n", encoding="utf-8"
        )
    result = _manual(workspace, manifest, receipt)
    assert result.exit_code == 1
    assert "Refused" in result.output
    reason = {
        "empty-receipt": "is empty",
        "wrong-addressee": "is not the manifest's recipient",
        "duplicate": "already in the ledger",
    }[failure]
    assert reason in result.output
    assert journal.read_bytes() == before


def test_a_recipient_after_a_channel_leaves_no_channel_behind(workspace: Path) -> None:
    _channel(workspace)
    page = ("--evidence-url", "https://www.hareid.kommune.no/", *READ_AT)
    _ok(workspace, "set-recipient", "--id", HAREID, "--address", "post@hareid.kommune.no", *page)
    records = directory_journal(InnsynWorkspace(workspace, [])).records()
    recipient = records[-1]
    assert isinstance(recipient, RecipientRecorded)
    assert contacts(records)[HAREID] == Contact(recipient=recipient)


@pytest.mark.parametrize(
    ("at", "reason"),
    [
        ("2026-10-21T09:00:00", "--sent-at: give an offset or Z"),
        ("yesterday", "--sent-at: not an ISO-8601 instant: yesterday"),
    ],
)
def test_a_bad_manual_sent_at_names_the_option(
    workspace: Path, tmp_path: Path, receipt: Path, at: str, reason: str
) -> None:
    _channel(workspace)
    manifest = tmp_path / "wave" / "manifest.json"
    _wave(workspace, manifest.parent)
    args = ("--manifest", str(manifest), "--id", HAREID, "--sent-at", at)
    result = innsyn(workspace, "record-sent-manual", *args, "--evidence", str(receipt))
    assert result.exit_code == 2
    assert said(reason, result.output)
    assert not (workspace / "requests.jsonl").exists()


@pytest.mark.parametrize("channel", ["webform", "edialog"])
def test_manual_wave_letters_are_deterministic_and_match_manifest_digests(
    workspace: Path, tmp_path: Path, channel: str
) -> None:
    _channel(workspace, channel)
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first = _wave(workspace, first_dir)
    second = _wave(workspace, second_dir)
    assert first["letters"] == second["letters"]
    for entry in first["letters"]:
        payload = (first_dir / entry["letter_file"]).read_bytes()
        assert payload == (second_dir / entry["letter_file"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == entry["letter_sha256"]


@pytest.mark.parametrize("channel", ["webform", "edialog"])
def test_manual_recording_hashes_actual_letter_and_keeps_only_receipt_digest(
    workspace: Path, tmp_path: Path, channel: str
) -> None:
    """operations.md promises the letter as sent and no receipt content in the ledger."""
    _channel(workspace, channel)
    manifest = tmp_path / "wave" / "manifest.json"
    wave = _wave(workspace, manifest.parent)
    entry = next(e for e in wave["letters"] if e["authority_id"] == HAREID)
    letter = manifest.parent / entry["letter_file"]
    payload = letter.read_bytes() + b"\nTillegg som vart sendt.\n"
    letter.write_bytes(payload)
    receipt = tmp_path / "private-receipt.png"
    receipt_payload = b"\x89PNG\r\nOfficer: Private Person\xff\x00"
    receipt.write_bytes(receipt_payload)

    result = _manual(workspace, manifest, receipt)
    assert result.exit_code == 0, result.output
    sent = _held(workspace)[f"{HAREID}-20261021T090000Z"].sent
    assert sent.letter_sha256 == hashlib.sha256(payload).hexdigest()
    assert sent.letter_sha256 != entry["letter_sha256"]
    assert sent.receipt_sha256 == hashlib.sha256(receipt_payload).hexdigest()
    raw = (workspace / "requests.jsonl").read_bytes()
    assert b"Private Person" not in raw
    assert receipt.name.encode() not in raw


@pytest.mark.parametrize("channel", ["webform", "edialog"])
def test_manual_request_supports_klage_and_outcome_without_losing_receipt(
    workspace: Path, tmp_path: Path, receipt: Path, channel: str
) -> None:
    """operations.md: manual requests use the same outcome and klage commands."""
    _channel(workspace, channel)
    manifest = tmp_path / "wave" / "manifest.json"
    _wave(workspace, manifest.parent)
    assert _manual(workspace, manifest, receipt).exit_code == 0
    request = f"{HAREID}-20261021T090000Z"
    sent = _held(workspace)[request].sent
    next_wave = _wave(workspace, tmp_path / "next")
    assert HAREID in next_wave["skipped_open"]
    assert not next_wave["letters"]

    _ok(workspace, "record-klage", "--request", request, "--at", "2026-10-30T08:00:00Z")
    state = _held(workspace)[request]
    assert state.klage_sent_at == datetime(2026, 10, 30, 8, tzinfo=UTC)
    assert not state.is_due(datetime(2026, 11, 2).date())
    assert state.is_open
    _ok(
        workspace,
        "record-outcome",
        "--request",
        request,
        "--outcome",
        "full",
        "--at",
        "2026-11-02T08:00:00Z",
    )
    state = _held(workspace)[request]
    assert state.sent == sent
    assert state.outcome == "full"
    assert state.answered_at == datetime(2026, 11, 2, 8, tzinfo=UTC)
    assert not state.is_open
    assert not state.is_due(datetime(2026, 11, 3).date())
