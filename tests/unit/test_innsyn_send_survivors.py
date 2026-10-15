"""Behavioral regressions for the listed SMTP and dispatch survivors."""

import hashlib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from lovspor.errors import ConfigError, InnsynSendError
from lovspor.innsyn import send_commands
from lovspor.innsyn.dispatch import Failure, SendPlan, Wire, prepare, send_letters
from lovspor.innsyn.mailer import MailMessage, SmtpTransport, message_id, redact, smtp_settings
from lovspor.innsyn.sender import Sender, read_sender
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.innsyn_support import innsyn
from tests.unit.test_innsyn_send import (
    SMTP_ENV,
    FakeTransport,
    _rewrite_manifest,
    manifest,
    workspace,
)

# Reuse the real workspace/manifest fixture builders, without their module's
# autouse fixture or transport patches.
__all__ = ["manifest", "workspace"]
AT = datetime(2026, 10, 13, 8, tzinfo=UTC)


@pytest.mark.parametrize("body", ["ASCII only", "Blåbær og innsyn", "你好 👋\nNeste linje"])
def test_message_body_round_trips_as_utf8(body: str) -> None:
    message = MailMessage(
        sender=Sender(name="Åse", email="ase@example.no", recorded_at=AT),
        to="post@example.no",
        subject="Innsyn",
        body=body,
        date=AT,
        message_id="<test@example.no>",
    ).email()
    assert message.get_content_charset() == "utf-8"
    assert message.get_payload(decode=True) == (body + "\n").encode("utf-8")


@pytest.mark.parametrize("address", ["a@b@example.no", '"a@b"@example.no', "a.example.no"])
def test_sender_rejects_addresses_without_one_unambiguous_domain(address: str) -> None:
    with pytest.raises(ValidationError):
        Sender(name="Åse", email=address, recorded_at=AT)


@pytest.mark.parametrize("address", ["a+b@sub.example.no", " A.B@EXAMPLE.NO "])
def test_message_id_uses_validated_sender_domain(address: str) -> None:
    sender = Sender(name="Åse", email=address, recorded_at=AT)
    assert message_id(sender).endswith(".innsyn@" + sender.email.split("@")[1] + ">")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in SMTP_ENV:
        monkeypatch.setenv(key, "")


@pytest.mark.parametrize("port", [1, 65535])
def test_inclusive_port_boundaries(port: int) -> None:
    assert smtp_settings({**SMTP_ENV, "LOVSPOR_SMTP_PORT": str(port)}).port == port


def test_port_above_tcp_range_is_configuration_error() -> None:
    with pytest.raises(ConfigError, match="must be a port number"):
        smtp_settings({**SMTP_ENV, "LOVSPOR_SMTP_PORT": "65536"})


def test_serialized_message_headers_encoding_and_line_endings() -> None:
    sender = Sender(name="Åse", email="ase@example.no", recorded_at=AT)
    message = MailMessage(
        sender=sender,
        to="post@example.no",
        subject="Innsyn Åse",
        body="Hei Åse\n",
        date=AT,
        message_id="<sample.innsyn@example.no>",
    ).email()
    data = message.as_bytes()
    assert b"\r\n\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert [line.split(b":", 1)[0] for line in data.split(b"\r\n") if b":" in line][:5] == [
        b"From",
        b"To",
        b"Subject",
        b"Date",
        b"Message-ID",
    ]
    assert message["Content-Transfer-Encoding"] == "quoted-printable"
    assert message.get_content_charset() == "utf-8"
    assert b'Content-Type: text/plain; charset="utf-8"\r\n' in data
    assert message.get_content() == "Hei Åse\n"
    ids = [message_id(sender), message_id(sender)]
    assert ids[0] != ids[1]
    assert all(value.endswith(".innsyn@example.no>") for value in ids)


@pytest.mark.parametrize("secret", ["Å\nend", "é\t'last", ""])
def test_redaction_of_python_string_escape_and_empty_password(secret: str) -> None:
    settings = smtp_settings(SMTP_ENV).model_copy(update={"password": SecretStr(secret)})
    text = f"server says {repr(secret)[1:-1]}!"
    expected = "server says [redacted]!" if secret else text
    assert redact(text, settings) == expected


def test_connection_failure_preserves_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args, **kwargs):
        raise ConnectionRefusedError("test endpoint refused connection")

    monkeypatch.setattr("smtplib.SMTP", unavailable)
    with pytest.raises(
        InnsynSendError, match="ConnectionRefusedError: test endpoint refused connection"
    ):
        SmtpTransport(smtp_settings(SMTP_ENV)).deliver(EmailMessage())


def test_default_transport_supplies_verified_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    delivered = []

    class Server:
        def __init__(self, *args, **kwargs):
            pass

        def starttls(self, *, context):
            # The SMTP boundary refuses to negotiate unless verification is enabled.
            if (
                context is None
                or not context.check_hostname
                or context.verify_mode != ssl.CERT_REQUIRED
            ):
                raise ssl.SSLError("unverified TLS")

        def login(self, *args):
            pass

        def send_message(self, message):
            delivered.append(message)

        def quit(self):
            pass

    monkeypatch.setattr("smtplib.SMTP", Server)
    message = EmailMessage()
    SmtpTransport(smtp_settings(SMTP_ENV)).deliver(message)
    assert delivered == [message]


@pytest.mark.parametrize("directory", [b"", b'{"event":"placeholder"}\n'])
def test_directory_without_recipient_refuses(
    workspace: Path, manifest: Path, directory: bytes
) -> None:
    target = InnsynWorkspace(workspace, [])
    # Keep a valid contact with only maalform, or remove all contact evidence.
    if directory:
        records = target.directory_log.read_bytes().splitlines(keepends=True)
        directory = b"".join(line for line in records if b'"maalform"' in line)
        assert directory
    target.directory_log.write_bytes(directory)
    judged = prepare(target, manifest)
    assert not judged.ready
    assert len(judged.refused) == 3
    assert all("recipient is now None" in item.reason for item in judged.refused)


@pytest.mark.parametrize(
    ("head", "body", "reason"),
    [
        (
            "Til: postmottak@oslobystyre.no\nEmne: title\nExtra: header",
            "body",
            "expected `Til:`, `Emne:`, a blank line, then the body",
        ),
        (
            "Til: wrong@example.no\nEmne: title",
            "body",
            "expected `Til:`, `Emne:`, a blank line, then the body",
        ),
        (
            "Til: postmottak@oslobystyre.no\nEmne: title\rhidden",
            "body",
            "the second line is not a one-line `Emne:`",
        ),
        (
            "Til: postmottak@oslobystyre.no\nSubject: title",
            "body",
            "the second line is not a one-line `Emne:`",
        ),
        ("Til: postmottak@oslobystyre.no\nEmne: title", " \n", "the body is empty"),
    ],
)
def test_malformed_letter_explains_refusal(
    workspace: Path, manifest: Path, head: str, body: str, reason: str
) -> None:
    payload = (head + "\n\n" + body).encode()
    path = manifest.parent / "0301.txt"
    path.write_bytes(payload)
    _rewrite_manifest(manifest, "0301", letter_sha256=hashlib.sha256(payload).hexdigest())
    judged = prepare(InnsynWorkspace(workspace, []), manifest)
    assert judged.refused[0].reason == f"{path}: not a letter: {reason}"


def test_pause_is_consumed_even_when_next_letter_is_refused(
    workspace: Path, manifest: Path
) -> None:
    target = InnsynWorkspace(workspace, [])
    prepared = prepare(target, manifest)
    pauses = []

    def pause(seconds):
        pauses.append(seconds)
        path = manifest.parent / "4646.txt"
        path.write_bytes(path.read_bytes() + b"edited")

    transport = FakeTransport()
    report = send_letters(
        target,
        prepared,
        SendPlan(sender=read_sender(target), limit=3, pause_seconds=2),
        Wire(transport=transport, clock=lambda: AT, pause=pause),
    )
    assert len(report.sent) == 2
    assert len(report.refused) == 1
    assert pauses == [2]


def test_one_pause_before_multiple_late_refusals(workspace: Path, manifest: Path) -> None:
    target = InnsynWorkspace(workspace, [])
    prepared = prepare(target, manifest)
    pauses = []

    def pause(seconds):
        pauses.append(seconds)
        for ready in prepared.ready[1:]:
            path = manifest.parent / ready.entry.letter_file
            path.write_bytes(path.read_bytes() + b"edited during pause")

    transport = FakeTransport()
    report = send_letters(
        target,
        prepared,
        SendPlan(sender=read_sender(target), limit=3, pause_seconds=2),
        Wire(transport=transport, clock=lambda: AT, pause=pause),
    )
    assert len(report.sent) == 1
    assert tuple(item.authority_id for item in report.refused) == tuple(
        ready.entry.authority_id for ready in prepared.ready[1:]
    )
    assert report.failure is None
    assert report.remaining == 0
    assert pauses == [2]


def test_dry_run_configuration_diagnostic_is_on_stderr(workspace: Path, manifest: Path) -> None:
    result = innsyn(workspace, "send", "--manifest", str(manifest))
    assert result.exit_code == 0
    assert result.stderr.splitlines()[:2] == [
        "SMTP not configured: LOVSPOR_SMTP_HOST is not set (in the environment or .env)",
        "dry run: nothing sent; add --send to send",
    ]
    assert "SMTP" not in result.stdout


@pytest.mark.parametrize(("accepted", "at"), [(True, AT), (True, None), (False, AT), (False, None)])
def test_failure_guidance_requires_acceptance_and_timestamp(
    accepted, at, tmp_path: Path, capsys
) -> None:
    failure = Failure(
        authority_id="0301",
        reason="ledger unavailable",
        accepted=accepted,
        sent_at=at,
        message_id="<mail@example.no>",
    )
    path = tmp_path / "manifest.json"
    send_commands._failure(failure, path)
    output = capsys.readouterr()
    assert output.out == ""
    if accepted and at is not None:
        guidance = (
            "record it now: lovspor observatory innsyn record-sent "
            f"--manifest {path} --id 0301 --sent-at {AT.isoformat()} "
            "--message-id '<mail@example.no>'"
        )
    else:
        guidance = (
            "nothing recorded for it; the run stopped and later letters were not tried. "
            "Check Gmail's Sent folder for it before running again."
        )
    assert output.err == f"FAILED 0301: ledger unavailable\n{guidance}\n"


def test_cli_recovers_acceptance_when_ledger_becomes_unwritable(
    workspace: Path,
    manifest: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in SMTP_ENV.items():
        monkeypatch.setenv(key, value)

    class Accepted:
        def deliver(self, message):
            # External acceptance races with a filesystem failure of the journal.
            (workspace / "requests.jsonl").mkdir()

    monkeypatch.setattr(send_commands, "transport_for", lambda _: Accepted())
    result = innsyn(workspace, "send", "--manifest", str(manifest), "--send")
    assert result.exit_code == 1
    assert "Is a directory" in result.stderr or "is a directory" in result.stderr
    assert "accepted by the server, but not recorded: None" not in result.stderr
    assert (
        "record it now: lovspor observatory innsyn record-sent "
        f"--manifest {manifest} --id 0301 --sent-at " in result.stderr
    )
    assert "--message-id '<" in result.stderr
    assert result.stdout == ""
