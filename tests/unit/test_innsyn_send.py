"""``innsyn send`` (#509 point 6): a wave delivered over SMTP, each letter recorded as accepted.

The SMTP server is the only thing replaced: a fake transport that keeps the
messages it is handed, or a local in-process socket that speaks just enough
SMTP to show what the real transport does before it would send a password.
No test connects to a real server or reads a real credential: ``.env``
loading is switched off and every SMTP variable is set by the test.
"""

import email
import hashlib
import json
import socket
import ssl
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.policy import default
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import ConfigError, InnsynSendError
from lovspor.innsyn import send_commands
from lovspor.innsyn.dispatch import SendPlan, Wire, prepare, send_letters
from lovspor.innsyn.ledger import Outcome, Sent, record_events, requests
from lovspor.innsyn.mailer import (
    SmtpSettings,
    SmtpTransport,
    smtp_settings,
    tls_context,
)
from lovspor.innsyn.sender import read_sender
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.lf_ledger import LedgerEntry
from tests.unit.innsyn_support import fetched, innsyn

PAGE = "https://www.example.kommune.no/forskrifter"
SENDER_NAME = "Bjørn Ærlig Åsen"
SENDER_EMAIL = "kontakt@example.no"
PASSWORD = "app-pw-Æ9ø-never-shown"
SMTP_ENV = {
    "LOVSPOR_SMTP_HOST": "smtp.invalid",
    "LOVSPOR_SMTP_PORT": "587",
    "LOVSPOR_SMTP_USER": "owner@example.no",
    "LOVSPOR_SMTP_PASSWORD": PASSWORD,
}
ORDER = ["0301", "4646", "46"]


class FakeTransport:
    """Accepts every message, except the ``fail_at``-th (1-based), which it refuses."""

    def __init__(self, fail_at: int | None = None) -> None:
        self.delivered: list[EmailMessage] = []
        self.calls = 0
        self._fail_at = fail_at

    def deliver(self, message: EmailMessage) -> None:
        self.calls += 1
        if self.calls == self._fail_at:
            raise InnsynSendError("smtp.invalid:587: SMTPDataError: (550, b'rejected')")
        self.delivered.append(message)

    def recipients(self) -> list[str]:
        return [str(message["To"]) for message in self.delivered]


def _ok(workspace: Path, *args: str) -> None:
    result = innsyn(workspace, *args)
    assert result.exit_code == 0, result.output


def _fact(workspace: Path, command: str, authority_id: str, flag: str, value: str) -> None:
    evidence = ("--evidence-url", PAGE, "--read-at", "2026-10-03T10:00:00Z")
    _ok(workspace, command, "--id", authority_id, flag, value, *evidence)


def _lf_line(authority_id: str, lf_id: str) -> str:
    return LedgerEntry(
        authority_id=authority_id,
        lf_id=lf_id,
        kind="LF",
        first_seen=datetime(2026, 10, 1, tzinfo=UTC),
        url=PAGE,
        sha256="a" * 64,
    ).model_dump_json()


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lovspor.cli.load_env", lambda: None)
    for name in SMTP_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in SMTP_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    target = fetched(tmp_path / "innsyn", httpx_mock)
    _ok(target, "set-sender", "--name", SENDER_NAME, "--email", SENDER_EMAIL)
    _fact(target, "set-recipient", "4646", "--address", "postmottak@fjaler.kommune.no")
    _fact(target, "set-recipient", "0301", "--address", "postmottak@oslobystyre.no")
    _fact(target, "set-recipient", "46", "--address", "post@vlfk.no")
    _fact(target, "set-maalform", "4646", "--form", "nn")
    return target


@pytest.fixture
def manifest(workspace: Path, tmp_path: Path) -> Path:
    ledger = tmp_path / "lf-ledger.jsonl"
    lines = [_lf_line("4646", "2024-09-19-2196"), _lf_line("0301", "2019-03-20-400")]
    ledger.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    out = tmp_path / "wave"
    _ok(workspace, "letters", "--out", str(out), "--lf-ledger", str(ledger))
    return out / "manifest.json"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    transport = FakeTransport()
    monkeypatch.setattr(send_commands, "transport_for", lambda settings: transport)
    return transport


@pytest.fixture
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    taken: list[float] = []
    monkeypatch.setattr(send_commands, "pause", taken.append)
    return taken


def _send(workspace: Path, manifest: Path, *extra: str) -> tuple[int, str, str]:
    result = innsyn(workspace, "send", "--manifest", str(manifest), *extra)
    return result.exit_code, result.stdout, result.stderr


def _sent(workspace: Path) -> dict[str, Sent]:
    held = requests(InnsynWorkspace(workspace, []))
    return {state.sent.authority_id: state.sent for state in held.values()}


def _rewrite_manifest(manifest: Path, authority_id: str, **fields: object) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for letter in data["letters"]:
        if letter["authority_id"] == authority_id:
            letter.update(fields)
    manifest.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.usefixtures("smtp_env")
class TestDryRun:
    def test_prints_what_would_go_and_sends_and_records_nothing(
        self, workspace: Path, manifest: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_server(settings: SmtpSettings) -> FakeTransport:
            raise AssertionError("a dry run must not build a transport")

        monkeypatch.setattr(send_commands, "transport_for", no_server)
        code, out, err = _send(workspace, manifest)
        assert code == 0, err
        rows = [line.split("\t") for line in out.splitlines()]
        assert [row[0] for row in rows] == ORDER
        assert rows[2][1:4] == [
            "To: post@vlfk.no",
            f"From: {SENDER_NAME} <{SENDER_EMAIL}>",
            "Subject: Innsynskrav etter offentleglova \N{EN DASH} lokale forskrifter i "
            "Vestland fylkeskommune",
        ]
        assert all(row[4].endswith(" bytes") and int(row[4].split()[0]) > 500 for row in rows)
        assert "dry run: nothing sent" in err
        assert "SMTP: smtp.invalid:587 as owner@example.no" in err
        assert "send: sent 0, skipped (already sent) 0, refused 0, remaining 3" in err
        assert not (workspace / "requests.jsonl").exists()

    def test_limit_bounds_what_the_dry_run_lists(self, workspace: Path, manifest: Path) -> None:
        code, out, _ = _send(workspace, manifest, "--limit", "1")
        assert code == 0
        assert [line.split("\t")[0] for line in out.splitlines()] == ["0301"]


class TestSend:
    @pytest.mark.usefixtures("smtp_env")
    def test_sends_the_limit_in_manifest_order_and_records_each(
        self, workspace: Path, manifest: Path, fake: FakeTransport, pauses: list[float]
    ) -> None:
        code, out, err = _send(workspace, manifest, "--send", "--limit", "2")
        assert code == 0, err
        assert fake.recipients() == ["postmottak@oslobystyre.no", "postmottak@fjaler.kommune.no"]
        assert pauses == [20.0]
        held = _sent(workspace)
        assert sorted(held) == ["0301", "4646"]
        letters = json.loads(manifest.read_text(encoding="utf-8"))["letters"]
        by_id = {letter["authority_id"]: letter for letter in letters}
        for message in fake.delivered:
            event = next(s for s in held.values() if s.recipient == str(message["To"]))
            assert event.message_id == message["Message-ID"]
            assert event.letter_sha256 == by_id[event.authority_id]["letter_sha256"]
            assert tuple(by_id[event.authority_id]["lf_ids"]) == event.lf_ids
            assert f"{event.request_id}: sent to {event.recipient} {event.message_id}" in out
        assert "send: sent 2, skipped (already sent) 0, refused 0, remaining 1" in err

    @pytest.mark.usefixtures("smtp_env")
    def test_the_pause_is_the_operators_and_comes_only_between_letters(
        self, workspace: Path, manifest: Path, fake: FakeTransport, pauses: list[float]
    ) -> None:
        code, _, err = _send(workspace, manifest, "--send", "--pause-seconds", "0.5")
        assert code == 0, err
        assert len(fake.delivered) == 3
        assert pauses == [0.5, 0.5]

    @pytest.mark.usefixtures("smtp_env")
    def test_a_rerun_never_sends_a_letter_twice(
        self, workspace: Path, manifest: Path, fake: FakeTransport, pauses: list[float]
    ) -> None:
        assert _send(workspace, manifest, "--send", "--limit", "2")[0] == 0
        code, _, err = _send(workspace, manifest, "--send")
        assert code == 0, err
        assert fake.recipients() == [
            "postmottak@oslobystyre.no",
            "postmottak@fjaler.kommune.no",
            "post@vlfk.no",
        ]
        assert "send: sent 1, skipped (already sent) 2, refused 0, remaining 0" in err
        code, _, err = _send(workspace, manifest, "--send")
        assert code == 0
        assert len(fake.delivered) == 3
        assert "send: sent 0, skipped (already sent) 3, refused 0, remaining 0" in err
        assert len(requests(InnsynWorkspace(workspace, []))) == 3

    @pytest.mark.usefixtures("smtp_env")
    def test_a_letter_recorded_by_hand_is_not_sent(
        self, workspace: Path, manifest: Path, fake: FakeTransport, pauses: list[float]
    ) -> None:
        args = ("--manifest", str(manifest), "--id", "0301", "--sent-at", "2026-10-13T08:00:00Z")
        _ok(workspace, "record-sent", *args)
        code, _, err = _send(workspace, manifest, "--send")
        assert code == 0, err
        assert "postmottak@oslobystyre.no" not in fake.recipients()
        assert "skipped (already sent) 1" in err

    @pytest.mark.usefixtures("smtp_env")
    def test_a_changed_letter_is_refused_and_the_rest_go(
        self, workspace: Path, manifest: Path, fake: FakeTransport, pauses: list[float]
    ) -> None:
        letter = manifest.parent / "0301.txt"
        letter.write_bytes(letter.read_bytes() + b"P.S.\n")
        code, _, err = _send(workspace, manifest, "--send")
        assert code == 0, err
        assert fake.recipients() == ["postmottak@fjaler.kommune.no", "post@vlfk.no"]
        assert "refused 0301:" in err
        assert "is not the manifest's; the letter changed" in err
        assert "0301" not in _sent(workspace)
        assert "sent 2, skipped (already sent) 0, refused 1, remaining 0" in err

    @pytest.mark.usefixtures("smtp_env")
    def test_a_recipient_changed_since_the_wave_is_refused(
        self, workspace: Path, manifest: Path, fake: FakeTransport, pauses: list[float]
    ) -> None:
        _fact(workspace, "set-recipient", "46", "--address", "postmottak@vlfk.no")
        code, _, err = _send(workspace, manifest, "--send")
        assert code == 0, err
        assert "post@vlfk.no" not in fake.recipients()
        assert "postmottak@vlfk.no" not in fake.recipients()
        assert (
            "refused 46: the directory's recipient is now postmottak@vlfk.no, "
            "the manifest's is post@vlfk.no"
        ) in err

    @pytest.mark.usefixtures("smtp_env")
    def test_smtp_failure_stops_the_run_and_records_only_what_went(
        self, workspace: Path, manifest: Path, monkeypatch: pytest.MonkeyPatch, pauses: list[float]
    ) -> None:
        failing = FakeTransport(fail_at=2)
        monkeypatch.setattr(send_commands, "transport_for", lambda settings: failing)
        code, _, err = _send(workspace, manifest, "--send")
        assert code == 1
        assert failing.calls == 2
        assert failing.recipients() == ["postmottak@oslobystyre.no"]
        assert sorted(_sent(workspace)) == ["0301"]
        assert "FAILED 4646: smtp.invalid:587: SMTPDataError" in err
        assert "nothing recorded for it" in err
        assert "send: sent 1, skipped (already sent) 0, refused 0, remaining 2" in err


class TestSettings:
    @pytest.mark.parametrize(
        "missing", ["LOVSPOR_SMTP_HOST", "LOVSPOR_SMTP_USER", "LOVSPOR_SMTP_PASSWORD"]
    )
    def test_a_missing_setting_is_named_and_nothing_is_sent(
        self,
        workspace: Path,
        manifest: Path,
        monkeypatch: pytest.MonkeyPatch,
        missing: str,
    ) -> None:
        for name, value in SMTP_ENV.items():
            if name != missing:
                monkeypatch.setenv(name, value)

        def no_server(settings: SmtpSettings) -> FakeTransport:
            raise AssertionError("no transport without settings")

        monkeypatch.setattr(send_commands, "transport_for", no_server)
        code, _, err = _send(workspace, manifest, "--send")
        assert code == 1
        assert f"Refused: {missing} is not set" in err
        assert "Traceback" not in err
        assert not (workspace / "requests.jsonl").exists()

    def test_the_port_defaults_to_submission_and_must_be_a_port(self) -> None:
        unset = {k: v for k, v in SMTP_ENV.items() if k != "LOVSPOR_SMTP_PORT"}
        assert smtp_settings(unset).port == 587
        for bad in ("smtp", "0", "70000"):
            with pytest.raises(ConfigError, match="LOVSPOR_SMTP_PORT must be a port number"):
                smtp_settings({**SMTP_ENV, "LOVSPOR_SMTP_PORT": bad})

    def test_the_password_is_held_as_a_secret(self) -> None:
        settings = smtp_settings(SMTP_ENV)
        assert PASSWORD not in repr(settings)
        assert PASSWORD not in str(settings)
        assert settings.password.get_secret_value() == PASSWORD

    @pytest.mark.usefixtures("smtp_env")
    def test_the_password_is_never_printed(
        self, workspace: Path, manifest: Path, monkeypatch: pytest.MonkeyPatch, pauses: list[float]
    ) -> None:
        dry = _send(workspace, manifest)
        failing = FakeTransport(fail_at=1)
        monkeypatch.setattr(send_commands, "transport_for", lambda settings: failing)
        real = _send(workspace, manifest, "--send")
        for _, out, err in (dry, real):
            assert PASSWORD not in out
            assert PASSWORD not in err


class TestMessage:
    @pytest.mark.usefixtures("smtp_env")
    def test_headers_and_body_carry_norwegian_letters_intact(
        self, workspace: Path, manifest: Path, fake: FakeTransport, pauses: list[float]
    ) -> None:
        assert _send(workspace, manifest, "--send", "--limit", "1")[0] == 0
        wire_bytes = fake.delivered[0].as_bytes()
        assert wire_bytes.isascii()
        head = wire_bytes.split(b"\r\n\r\n", 1)[0].decode("ascii")
        assert "=?utf-8?" in head
        parsed = email.message_from_bytes(wire_bytes, policy=default)
        letter = (manifest.parent / "0301.txt").read_text(encoding="utf-8")
        _, subject_line, _, *_ = letter.split("\n", 3)
        assert parsed["Subject"] == subject_line.removeprefix("Emne: ")
        assert parsed["From"].addresses[0].display_name == SENDER_NAME
        assert parsed["From"].addresses[0].addr_spec == SENDER_EMAIL
        assert parsed["To"] == "postmottak@oslobystyre.no"
        assert parsed.get_content_type() == "text/plain"
        assert parsed.get_content_charset() == "utf-8"
        body = parsed.get_content().replace("\r\n", "\n")
        assert body == letter.split("\n\n", 1)[1]
        assert "avslått" in body
        assert SENDER_NAME in body
        assert parsed["Message-ID"].endswith("@example.no>")
        assert parsed["Date"].datetime.tzinfo is not None


class _Clock:
    def __init__(self, start: datetime) -> None:
        self._at = start

    def __call__(self) -> datetime:
        self._at += timedelta(seconds=1)
        return self._at


class TestLedgerRefusals:
    def test_an_open_request_from_elsewhere_is_refused(
        self, workspace: Path, manifest: Path
    ) -> None:
        target = InnsynWorkspace(workspace, [])
        record_events(target, [_foreign_sent("46", datetime(2026, 10, 5, 7, 0, tzinfo=UTC))])
        prepared = prepare(target, manifest)
        assert [r.entry.authority_id for r in prepared.ready] == ["0301", "4646"]
        assert prepared.refused[0].authority_id == "46"
        assert "has an open request 46-20261005T070000Z not from this manifest" in (
            prepared.refused[0].reason
        )

    def test_accepted_but_unrecordable_names_the_command_that_records_it(
        self, workspace: Path, manifest: Path
    ) -> None:
        target = InnsynWorkspace(workspace, [])
        start = datetime(2026, 10, 13, 8, 0, 0, tzinfo=UTC)
        clash = start + timedelta(seconds=2)
        closed = Outcome(
            request_id=f"0301-{clash:%Y%m%dT%H%M%SZ}", recorded_at=clash, outcome="full", at=clash
        )
        record_events(target, [_foreign_sent("0301", clash), closed])
        transport = FakeTransport()
        plan = SendPlan(sender=read_sender(target), limit=3, pause_seconds=0)
        wire = Wire(transport=transport, clock=_Clock(start), pause=lambda _: None)
        report = send_letters(target, prepare(target, manifest), plan, wire)
        assert len(transport.delivered) == 1
        assert report.sent == ()
        assert report.failure is not None
        assert report.failure.accepted
        assert report.failure.sent_at == clash
        assert report.failure.message_id == transport.delivered[0]["Message-ID"]
        assert report.remaining == 2


def _foreign_sent(authority_id: str, at: datetime) -> Sent:
    return Sent(
        request_id=f"{authority_id}-{at:%Y%m%dT%H%M%SZ}",
        recorded_at=at,
        authority_id=authority_id,
        recipient="post@vlfk.no" if authority_id == "46" else "postmottak@fjaler.kommune.no",
        sent_at=at,
        lf_ids=(),
        general_clause=True,
        letter_sha256=hashlib.sha256(b"another letter").hexdigest(),
    )


@pytest.fixture
def plain_server() -> Iterator[tuple[int, list[bytes]]]:
    """A local SMTP server that offers no STARTTLS; it keeps every command it is sent."""
    listener = socket.create_server(("127.0.0.1", 0))
    heard: list[bytes] = []
    thread = threading.Thread(target=_serve_once, args=(listener, heard), daemon=True)
    thread.start()
    yield listener.getsockname()[1], heard
    thread.join(timeout=5)
    listener.close()


def _serve_once(listener: socket.socket, heard: list[bytes]) -> None:
    connection, _ = listener.accept()
    with connection, connection.makefile("rwb") as stream:
        stream.write(b"220 localhost ESMTP test\r\n")
        stream.flush()
        for line in stream:
            heard.append(line)
            verb = line[:4].upper()
            reply = b"250-localhost\r\n250 8BITMIME\r\n" if verb in {b"EHLO", b"HELO"} else None
            if verb == b"QUIT":
                stream.write(b"221 bye\r\n")
                stream.flush()
                return
            stream.write(reply or b"502 not implemented\r\n")
            stream.flush()


class TestSmtpTransport:
    def test_a_server_without_starttls_never_sees_the_password(
        self, plain_server: tuple[int, list[bytes]]
    ) -> None:
        port, heard = plain_server
        settings = smtp_settings(
            {**SMTP_ENV, "LOVSPOR_SMTP_HOST": "127.0.0.1", "LOVSPOR_SMTP_PORT": str(port)}
        )
        message = EmailMessage()
        message.set_content("x")
        with pytest.raises(InnsynSendError, match="SMTPNotSupportedError") as caught:
            SmtpTransport(settings).deliver(message)
        assert PASSWORD not in str(caught.value)
        said = b"".join(heard)
        assert b"AUTH" not in said.upper()
        assert b"MAIL FROM" not in said.upper()

    def test_an_unreachable_server_is_a_send_error(self) -> None:
        with socket.create_server(("127.0.0.1", 0)) as probe:
            port = probe.getsockname()[1]
        settings = smtp_settings(
            {**SMTP_ENV, "LOVSPOR_SMTP_HOST": "127.0.0.1", "LOVSPOR_SMTP_PORT": str(port)}
        )
        with pytest.raises(InnsynSendError, match=f"127.0.0.1:{port}"):
            SmtpTransport(settings).deliver(EmailMessage())

    def test_tls_verifies_the_certificate_and_the_host_name(self) -> None:
        context = tls_context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
