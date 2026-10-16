"""Send regressions for timeout handling and checks against changing workspace data."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from lovspor.innsyn.dispatch import SendPlan, Wire, prepare, send_letters
from lovspor.innsyn.ledger import requests
from lovspor.innsyn.mailer import SmtpTransport, smtp_settings
from lovspor.innsyn.sender import read_sender
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.innsyn_support import innsyn
from tests.unit.test_innsyn_send import SMTP_ENV, FakeTransport, manifest, workspace

__all__ = ["manifest", "workspace"]
AT = datetime(2026, 10, 13, 8, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lovspor.cli.load_env", lambda: None)
    for name in SMTP_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("stage", ["connect", "starttls", "login", "send_message"])
def test_smtp_timeout_stops_without_recording_or_trying_later_letters(
    workspace: Path, manifest: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    # operations.md: the first unaccepted letter stops the run and is not recorded.
    server = Mock()
    factory = Mock(return_value=server)
    timeout = TimeoutError("submission timed out")
    if stage == "connect":
        factory.side_effect = timeout
    else:
        getattr(server, stage).side_effect = timeout
    monkeypatch.setattr("lovspor.innsyn.mailer.smtplib.SMTP", factory)
    target = InnsynWorkspace(workspace, [])

    report = send_letters(
        target,
        prepare(target, manifest),
        SendPlan(sender=read_sender(target), limit=3, pause_seconds=0),
        Wire(
            transport=SmtpTransport(smtp_settings(SMTP_ENV)),
            clock=lambda: AT,
            pause=Mock(side_effect=AssertionError("must not pause after a failed send")),
        ),
    )

    factory.assert_called_once_with("smtp.invalid", 587, timeout=60.0)
    assert report.sent == ()
    assert report.remaining == 3
    assert report.failure is not None
    assert report.failure.authority_id == "0301"
    assert not report.failure.accepted
    assert "TimeoutError: submission timed out" in report.failure.reason
    assert requests(target) == {}
    if stage in {"connect", "starttls"}:
        server.login.assert_not_called()
    if stage != "send_message":
        server.send_message.assert_not_called()
    if stage == "connect":
        server.quit.assert_not_called()
    else:
        server.quit.assert_called_once()


def test_already_sent_letter_is_skipped_despite_missing_file_and_changed_recipient(
    workspace: Path, manifest: Path
) -> None:
    # dispatch's contract gives an existing authority + SHA-256 sent event precedence.
    result = innsyn(
        workspace,
        "record-sent",
        "--manifest",
        str(manifest),
        "--id",
        "0301",
        "--sent-at",
        AT.isoformat(),
    )
    assert result.exit_code == 0, result.output
    (manifest.parent / "0301.txt").unlink()
    result = innsyn(
        workspace,
        "set-recipient",
        "--id",
        "0301",
        "--address",
        "new@example.no",
        "--evidence-url",
        "https://www.example.kommune.no/forskrifter",
        "--read-at",
        AT.isoformat(),
    )
    assert result.exit_code == 0, result.output

    prepared = prepare(InnsynWorkspace(workspace, []), manifest)

    assert prepared.skipped == ("0301",)
    assert prepared.refused == ()
    assert [ready.entry.authority_id for ready in prepared.ready] == ["4646", "46"]


def test_directory_corrupted_during_pause_stops_before_next_delivery(
    workspace: Path, manifest: Path
) -> None:
    # operations.md: reread the directory right before every letter, after the pause.
    target = InnsynWorkspace(workspace, [])
    prepared = prepare(target, manifest)
    transport = FakeTransport()
    pauses = []

    def corrupt_directory(seconds: float) -> None:
        pauses.append(seconds)
        with target.directory_log.open("ab") as handle:
            handle.write(b"{torn")

    report = send_letters(
        target,
        prepared,
        SendPlan(sender=read_sender(target), limit=3, pause_seconds=2),
        Wire(transport=transport, clock=lambda: AT, pause=corrupt_directory),
    )

    assert transport.recipients() == ["postmottak@oslobystyre.no"]
    assert pauses == [2]
    assert len(report.sent) == 1
    assert report.failure is not None
    assert report.failure.authority_id == "4646"
    assert not report.failure.accepted
    assert "could not be judged again before sending" in report.failure.reason
    assert report.remaining == 2
    assert report.skipped == ()
    assert report.refused == ()
    assert [state.sent for state in requests(target).values()] == list(report.sent)
