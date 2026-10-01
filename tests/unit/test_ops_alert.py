"""Unit-failure alerts for the droplet's systemd units (issue #478).

``lovspor ops alert --unit X`` is what ``lovspor-alert@.service`` runs when a
unit carrying ``OnFailure=lovspor-alert@%n.service`` fails. These tests pin the
operator's contract: an unset webhook is a logged no-op, the message carries
unit, host, UTC time, result and the journal tail trimmed to a safe size, the
body works as plain text (ntfy) or JSON (Slack/Discord), and the webhook URL
never reaches a log line in full. HTTP is faked with pytest-httpx only.
"""

import json
import locale
import os
import socket
import sys
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import typer
from pydantic import SecretStr, ValidationError
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.ops import unit_facts
from lovspor.ops.alert import (
    LINE_CHARS,
    MESSAGE_CHARS,
    Alert,
    AlertConfig,
    AlertFormat,
    load_alert_config,
    redact_url,
    render_text,
    send_alert,
)
from lovspor.ops.commands import AlertDeps, _deliver, alert_impl
from lovspor.ops.errors import AlertConfigError, AlertDeliveryError, UnitFactsError
from lovspor.ops.unit_facts import JOURNAL_LINES, UnitName, read_unit_facts, run_command
from lovspor.release.caddy import environment_file_value

WEBHOOK = "https://ntfy.example.org/lovspor-secret-topic-1234"
UNIT = "lovspor-fetch-corpus.service"
AT = datetime(2026, 9, 30, 5, 30, 12, tzinfo=UTC)


def _alert(journal: Sequence[str] = ("line one", "line two"), *, test: bool = False) -> Alert:
    return Alert(
        unit=UNIT,
        host="lovspor-droplet",
        at=AT,
        result="exit-code",
        exit_status=1,
        journal=tuple(journal),
        test=test,
    )


def _config(fmt: AlertFormat = AlertFormat.TEXT) -> AlertConfig:
    return AlertConfig(webhook=SecretStr(WEBHOOK), format=fmt)


class FakeSystemd:
    """Answers `systemctl show` and `journalctl` the way systemd prints them."""

    def __init__(self, show: str, journal: str, *, fail: str | None = None) -> None:
        self.show = show
        self.journal = journal
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str:
        self.calls.append(list(argv))
        if self.fail is not None and argv[0] == self.fail:
            raise UnitFactsError(f"{argv[0]} exited 1")
        return self.show if argv[0] == "systemctl" else self.journal


_SHOW = "Result=exit-code\nExecMainStatus=1\n"


def _deps(runner: FakeSystemd, environ: dict[str, str] | None = None) -> AlertDeps:
    return AlertDeps(
        runner=runner,
        client=httpx.Client(),
        clock=lambda: AT,
        environ=environ if environ is not None else {},
        hostname="lovspor-droplet",
    )


class TestLoadAlertConfig:
    def test_a_missing_file_and_no_variable_is_unset(self, tmp_path: Path) -> None:
        config = load_alert_config({}, tmp_path / "absent.env")

        assert config.webhook is None
        assert config.format is AlertFormat.TEXT

    def test_reads_the_webhook_and_format_from_the_env_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / "alert.env"
        env_file.write_text(
            f'# comment\nLOVSPOR_ALERT_WEBHOOK="{WEBHOOK}"\nLOVSPOR_ALERT_FORMAT=json\n',
            encoding="utf-8",
        )

        config = load_alert_config({}, env_file)

        assert config.webhook is not None
        assert config.webhook.get_secret_value() == WEBHOOK
        assert config.format is AlertFormat.JSON

    def test_a_present_but_empty_webhook_is_unset(self, tmp_path: Path) -> None:
        env_file = tmp_path / "alert.env"
        env_file.write_text("LOVSPOR_ALERT_WEBHOOK=\n", encoding="utf-8")

        assert load_alert_config({}, env_file).webhook is None

    def test_the_process_environment_wins_over_the_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / "alert.env"
        env_file.write_text("LOVSPOR_ALERT_WEBHOOK=https://file.example/t\n", encoding="utf-8")

        config = load_alert_config(
            {"LOVSPOR_ALERT_WEBHOOK": WEBHOOK, "LOVSPOR_ALERT_FORMAT": "JSON"}, env_file
        )

        assert config.webhook is not None
        assert config.webhook.get_secret_value() == WEBHOOK
        assert config.format is AlertFormat.JSON

    def test_an_empty_process_variable_falls_back_to_the_file(self, tmp_path: Path) -> None:
        env_file = tmp_path / "alert.env"
        env_file.write_text(f"LOVSPOR_ALERT_WEBHOOK={WEBHOOK}\n", encoding="utf-8")

        config = load_alert_config({"LOVSPOR_ALERT_WEBHOOK": "  "}, env_file)

        assert config.webhook is not None

    def test_an_unknown_format_is_refused_by_name(self, tmp_path: Path) -> None:
        with pytest.raises(AlertConfigError, match="LOVSPOR_ALERT_FORMAT"):
            load_alert_config({"LOVSPOR_ALERT_FORMAT": "xml"}, tmp_path / "absent.env")

    def test_a_webhook_that_is_not_http_is_refused_without_echoing_it(self, tmp_path: Path) -> None:
        with pytest.raises(AlertConfigError) as info:
            load_alert_config({"LOVSPOR_ALERT_WEBHOOK": "ftp://secret-host/x"}, tmp_path / "a")

        assert "secret-host" not in str(info.value)
        assert "LOVSPOR_ALERT_WEBHOOK" in str(info.value)

    def test_an_unreadable_file_is_an_error_not_a_silent_no_op(self, tmp_path: Path) -> None:
        with pytest.raises(AlertConfigError, match="cannot read"):
            load_alert_config({}, tmp_path)


class TestRedactUrl:
    def test_keeps_scheme_and_host_and_hides_the_path(self) -> None:
        assert redact_url(WEBHOOK) == "https://ntfy.example.org/…"

    def test_hides_credentials_in_the_authority(self) -> None:
        redacted = redact_url("https://user:pw@hooks.example.com/services/T/B/X")

        assert redacted == "https://hooks.example.com/…"

    def test_keeps_a_port(self) -> None:
        assert redact_url("http://127.0.0.1:8080/topic") == "http://127.0.0.1:8080/…"

    def test_an_unparseable_url_reveals_nothing(self) -> None:
        assert redact_url("not a url") == "<redacted>"


class TestRenderText:
    def test_names_unit_host_utc_time_result_and_status(self) -> None:
        text = render_text(_alert())

        assert UNIT in text
        assert "lovspor-droplet" in text
        assert "2026-09-30T05:30:12Z" in text
        assert "result: exit-code" in text
        assert "exit status: 1" in text
        assert text.endswith("line one\nline two")

    def test_a_test_alert_says_so_first(self) -> None:
        assert render_text(_alert(test=True)).startswith("[TEST] ")
        assert not render_text(_alert()).startswith("[TEST]")

    def test_an_unknown_exit_status_is_said_not_invented(self) -> None:
        alert = _alert().model_copy(update={"exit_status": None})

        assert "exit status: unknown" in render_text(alert)

    def test_an_empty_journal_is_said(self) -> None:
        assert "(no journal lines)" in render_text(_alert(journal=()))

    def test_a_long_line_is_cut_with_a_marker(self) -> None:
        text = render_text(_alert(journal=("x" * (LINE_CHARS + 50),)))
        (last,) = text.splitlines()[-1:]

        assert len(last) == LINE_CHARS
        assert last.endswith("…")

    def test_the_message_is_bounded_and_keeps_the_newest_lines(self) -> None:
        journal = [f"{n:03d} " + "y" * (LINE_CHARS - 10) for n in range(JOURNAL_LINES)]

        text = render_text(_alert(journal=journal))

        assert len(text) <= MESSAGE_CHARS
        assert text.endswith(journal[-1])
        assert journal[0][:4] not in text
        assert "older journal lines omitted" in text


class TestSendAlert:
    def test_text_format_posts_plain_text_with_ntfy_headers(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=200)

        status = send_alert(_alert(), _config(), httpx.Client())

        request = httpx_mock.get_request()
        assert request is not None
        assert status == 200
        assert request.headers["content-type"] == "text/plain; charset=utf-8"
        assert request.headers["title"] == f"lovspor: {UNIT} failed on lovspor-droplet"
        assert request.headers["priority"] == "high"
        assert request.content.decode() == render_text(_alert())

    def test_json_format_carries_slack_and_discord_keys_and_the_facts(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=204)

        send_alert(_alert(), _config(AlertFormat.JSON), httpx.Client())

        request = httpx_mock.get_request()
        assert request is not None
        body = json.loads(request.content)
        assert body["text"] == body["content"] == render_text(_alert())
        assert body["unit"] == UNIT
        assert body["host"] == "lovspor-droplet"
        assert body["time"] == "2026-09-30T05:30:12Z"
        assert body["result"] == "exit-code"
        assert body["exit_status"] == 1
        assert body["journal"] == ["line one", "line two"]
        assert "title" not in request.headers

    def test_a_refusing_endpoint_is_an_error_naming_the_status_not_the_url(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=403)

        with pytest.raises(AlertDeliveryError) as info:
            send_alert(_alert(), _config(), httpx.Client())

        assert "403" in str(info.value)
        assert "secret-topic" not in str(info.value)

    def test_a_transport_failure_is_an_error_without_the_url(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ConnectError(f"cannot reach {WEBHOOK}"))

        with pytest.raises(AlertDeliveryError) as info:
            send_alert(_alert(), _config(), httpx.Client())

        assert "ConnectError" in str(info.value)
        assert "secret-topic" not in str(info.value)

    def test_an_unset_webhook_is_a_programming_error_not_a_post(self) -> None:
        with pytest.raises(AlertConfigError):
            send_alert(_alert(), AlertConfig(), httpx.Client())


class TestUnitName:
    @pytest.mark.parametrize(
        "name", [UNIT, "lovspor-publish.service", "lovspor-alert@x.service", "a.timer"]
    )
    def test_accepts_systemd_unit_names(self, name: str) -> None:
        assert UnitName.model_validate({"unit": name}).unit == name

    @pytest.mark.parametrize(
        "name", ["", "-n.service", "--help", "a b.service", "lovspor", "x.service;rm"]
    )
    def test_refuses_anything_else(self, name: str) -> None:
        with pytest.raises(ValidationError):
            UnitName.model_validate({"unit": name})


class TestReadUnitFacts:
    def test_reads_result_status_and_the_journal_tail(self) -> None:
        runner = FakeSystemd(_SHOW, "05:30 fetch failed\n05:30 exit 1\n")

        facts = read_unit_facts(runner, UNIT)

        assert facts.result == "exit-code"
        assert facts.exit_status == 1
        assert facts.journal == ("05:30 fetch failed", "05:30 exit 1")
        show, journal = runner.calls
        assert show[:3] == ["systemctl", "show", UNIT]
        assert journal[:3] == ["journalctl", "--unit", UNIT]
        assert f"--lines={JOURNAL_LINES}" in journal

    def test_keeps_only_the_last_lines_even_if_more_arrive(self) -> None:
        runner = FakeSystemd(_SHOW, "\n".join(str(n) for n in range(50)))

        facts = read_unit_facts(runner, UNIT)

        assert facts.journal == tuple(str(n) for n in range(50 - JOURNAL_LINES, 50))

    def test_a_status_systemd_did_not_record_is_none(self) -> None:
        facts = read_unit_facts(FakeSystemd("Result=\nExecMainStatus=\n", ""), UNIT)

        assert facts.result == "unknown"
        assert facts.exit_status is None
        assert facts.journal == ()

    def test_an_unreadable_journal_degrades_to_a_note_not_a_lost_alert(self) -> None:
        facts = read_unit_facts(FakeSystemd(_SHOW, "", fail="journalctl"), UNIT)

        assert facts.result == "exit-code"
        assert facts.journal == ("(journal unavailable: journalctl exited 1)",)

    def test_an_unreadable_state_degrades_to_unknown(self) -> None:
        facts = read_unit_facts(FakeSystemd(_SHOW, "a\n", fail="systemctl"), UNIT)

        assert facts.result == "unknown"
        assert facts.exit_status is None
        assert facts.journal == ("a",)


class TestRunCommand:
    def test_returns_stdout_of_a_real_process(self) -> None:
        assert run_command([sys.executable, "-c", "print('ok')"]) == "ok\n"

    def test_a_non_zero_exit_is_a_typed_error(self) -> None:
        with pytest.raises(UnitFactsError, match="exited 3"):
            run_command([sys.executable, "-c", "import sys; sys.exit(3)"])

    def test_a_missing_program_is_a_typed_error(self) -> None:
        with pytest.raises(UnitFactsError, match="cannot run"):
            run_command(["/nonexistent/lovspor-no-such-program"])


class TestAlertImpl:
    def test_an_unset_webhook_logs_and_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runner = FakeSystemd(_SHOW, "x\n")

        alert_impl(UNIT, tmp_path / "absent.env", _deps(runner), test=False)

        assert "alert webhook not configured" in capsys.readouterr().err
        assert runner.calls == []

    def test_sends_the_units_facts_and_logs_only_the_redacted_target(
        self, tmp_path: Path, httpx_mock: HTTPXMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=200)
        environ = {"LOVSPOR_ALERT_WEBHOOK": WEBHOOK}

        alert_impl(UNIT, tmp_path / "absent.env", _deps(FakeSystemd(_SHOW, "boom\n"), environ))

        request = httpx_mock.get_request()
        assert request is not None
        assert request.content.decode().endswith("boom")
        out = capsys.readouterr()
        assert "alert sent for lovspor-fetch-corpus.service" in out.out
        assert "https://ntfy.example.org/…" in out.out
        assert "secret-topic" not in out.out + out.err

    def test_a_test_alert_reads_no_unit_state(self, tmp_path: Path, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=200)
        runner = FakeSystemd(_SHOW, "x\n")
        environ = {"LOVSPOR_ALERT_WEBHOOK": WEBHOOK}

        alert_impl(UNIT, tmp_path / "absent.env", _deps(runner, environ), test=True)

        request = httpx_mock.get_request()
        assert request is not None
        assert request.content.decode().startswith("[TEST] ")
        assert runner.calls == []

    def test_a_delivery_failure_exits_one_without_the_url(
        self, tmp_path: Path, httpx_mock: HTTPXMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=500)
        environ = {"LOVSPOR_ALERT_WEBHOOK": WEBHOOK}

        with pytest.raises(typer.Exit) as info:
            alert_impl(UNIT, tmp_path / "a.env", _deps(FakeSystemd(_SHOW, ""), environ))

        assert info.value.exit_code == 1
        err = capsys.readouterr().err
        assert "500" in err
        assert "secret-topic" not in err

    def test_a_bad_config_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(typer.Exit) as info:
            alert_impl(UNIT, tmp_path, _deps(FakeSystemd(_SHOW, "")))

        assert info.value.exit_code == 1
        assert "cannot read" in capsys.readouterr().err

    def test_a_malformed_unit_name_exits_two_before_anything_runs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runner = FakeSystemd(_SHOW, "")

        with pytest.raises(typer.Exit) as info:
            alert_impl(
                "--help", tmp_path / "a.env", _deps(runner, {"LOVSPOR_ALERT_WEBHOOK": WEBHOOK})
            )

        assert info.value.exit_code == 2
        assert runner.calls == []
        assert "--unit" in capsys.readouterr().err


class TestCommandWiring:
    def test_lovspor_ops_alert_is_a_no_op_without_a_webhook(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LOVSPOR_ALERT_WEBHOOK", raising=False)
        result = CliRunner().invoke(
            app, ["ops", "alert", "--unit", UNIT, "--env-file", str(tmp_path / "absent.env")]
        )

        assert result.exit_code == 0, result.output
        assert "alert webhook not configured" in result.output


# Gaps the PR #482 mutation run found: each test below pins what the operator
# or the alert receiver sees, at a boundary the earlier tests only skirted.

_EXPECTED_MESSAGE = "\n".join(
    [
        f"lovspor unit failed: {UNIT}",
        "host: lovspor-droplet",
        "time: 2026-09-30T05:30:12Z",
        "result: exit-code",
        "exit status: 1",
        "last journal lines:",
        "line one",
        "line two",
    ]
)


def _header_chars(host: str) -> int:
    """The length of the message header alone, for building journals around the bound."""
    return len(render_text(_alert(journal=("",)).model_copy(update={"host": host}))) - 1


def _journal_lines_kept(text: str, journal: Sequence[str]) -> int:
    return sum(1 for line in journal if line in text)


@pytest.fixture
def tokyo_local_time() -> Iterator[None]:
    """Run with the process's local zone far from UTC, then restore it."""
    saved = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


class TestAlertConfigEdges:
    def test_a_plain_http_webhook_is_accepted(self) -> None:
        url = "http://127.0.0.1:8080/topic"

        config = load_alert_config({"LOVSPOR_ALERT_WEBHOOK": url}, Path("/nonexistent/a.env"))

        assert config.webhook is not None
        assert config.webhook.get_secret_value() == url

    def test_an_unknown_format_error_lists_the_accepted_formats(self, tmp_path: Path) -> None:
        with pytest.raises(AlertConfigError) as info:
            load_alert_config({"LOVSPOR_ALERT_FORMAT": "xml"}, tmp_path / "absent.env")

        assert str(info.value) == "LOVSPOR_ALERT_FORMAT must be one of text, json; got 'xml'"

    def test_the_env_file_is_utf8_whatever_the_locale(self, tmp_path: Path) -> None:
        # In-process on purpose: a subprocess imports the unmutated package, so
        # under mutmut it could never see a locale-default read.
        env_file = tmp_path / "alert.env"
        env_file.write_text(f"# varsling — drift\nLOVSPOR_ALERT_WEBHOOK={WEBHOOK}\n", "utf-8")
        before = locale.setlocale(locale.LC_CTYPE)
        locale.setlocale(locale.LC_CTYPE, "C")
        try:
            config = load_alert_config({}, env_file)
        finally:
            locale.setlocale(locale.LC_CTYPE, before)

        assert config.webhook is not None
        assert config.webhook.get_secret_value() == WEBHOOK

    def test_assumption_a_text_without_an_assignment_sets_nothing(self) -> None:
        """Pins the equivalence registered for `_read_env_file`'s missing-file value."""
        for name in ("LOVSPOR_ALERT_WEBHOOK", "LOVSPOR_ALERT_FORMAT"):
            assert environment_file_value("", name) is None
            assert environment_file_value("XXXX", name) is None


class TestRedactUrlEdges:
    def test_an_out_of_range_port_reveals_nothing(self) -> None:
        assert redact_url("https://hooks.example.com:99999/secret") == "<redacted>"

    def test_a_scheme_without_a_host_reveals_nothing(self) -> None:
        assert redact_url("https:///secret-path") == "<redacted>"


class TestRenderTextExact:
    def test_the_whole_message_reads_as_the_operator_sees_it(self) -> None:
        assert render_text(_alert()) == _EXPECTED_MESSAGE

    def test_a_test_alert_differs_only_by_its_marker(self) -> None:
        assert render_text(_alert(test=True)) == "[TEST] " + _EXPECTED_MESSAGE

    def test_an_empty_journal_says_so_as_its_only_line(self) -> None:
        assert render_text(_alert(journal=())).splitlines()[-1] == "(no journal lines)"

    def test_a_line_of_exactly_the_limit_is_kept_whole(self) -> None:
        line = "z" * LINE_CHARS

        assert render_text(_alert(journal=(line,))).splitlines()[-1] == line

    def test_a_message_of_exactly_the_bound_keeps_every_line(self) -> None:
        budget = MESSAGE_CHARS - _header_chars("lovspor-droplet")
        journal = [f"{n:02d}" + "w" * 198 for n in range(budget // 201)]
        journal.append("last" + "v" * (budget - 201 * len(journal) - 5))

        text = render_text(_alert(journal=journal))

        assert len(text) == MESSAGE_CHARS
        assert _journal_lines_kept(text, journal) == len(journal)
        assert "omitted" not in text

    def test_the_omitted_count_and_the_kept_lines_add_up_to_the_journal(self) -> None:
        journal = [f"{n:03d} " + "y" * (LINE_CHARS - 10) for n in range(JOURNAL_LINES)]

        text = render_text(_alert(journal=journal))

        omitted = JOURNAL_LINES - _journal_lines_kept(text, journal)
        assert omitted > 1
        assert f"({omitted} older journal lines omitted)" in text.splitlines()


class TestRenderTextOversizedHeader:
    def test_the_newest_line_is_never_dropped_only_cut(self) -> None:
        host = "h" * 1600
        alert = _alert(journal=("newest " + "n" * 100,)).model_copy(update={"host": host})

        text = render_text(alert)

        assert len(text) == MESSAGE_CHARS
        assert "newest" in text
        assert "omitted" not in text

    def test_older_lines_still_go_first(self) -> None:
        host = "h" * 1400
        journal = ("FIRST" + "f" * 195, "LAST" + "l" * 196)
        alert = _alert(journal=journal).model_copy(update={"host": host})

        text = render_text(alert)

        assert "FIRST" not in text
        assert text.endswith(journal[-1])
        assert "(1 older journal lines omitted)" in text.splitlines()


class TestTimeIsUtc:
    def test_an_offset_time_is_reported_in_utc_whatever_the_local_zone(
        self, tokyo_local_time: None
    ) -> None:
        at = datetime(2026, 9, 30, 7, 30, 12, tzinfo=timezone(timedelta(hours=2)))
        alert = _alert().model_copy(update={"at": at})

        assert "time: 2026-09-30T05:30:12Z" in render_text(alert).splitlines()


class TestSendAlertWire:
    def test_an_unset_webhook_error_names_the_variable(self) -> None:
        with pytest.raises(AlertConfigError, match="^LOVSPOR_ALERT_WEBHOOK is not set$"):
            send_alert(_alert(), AlertConfig(), httpx.Client())

    @pytest.mark.parametrize("fmt", list(AlertFormat))
    def test_the_post_is_bounded_by_the_alert_timeout(
        self, httpx_mock: HTTPXMock, fmt: AlertFormat
    ) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=200)

        send_alert(_alert(), _config(fmt), httpx.Client())

        request = httpx_mock.get_request()
        assert request is not None
        assert request.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), 15.0
        )

    def test_ntfy_headers_go_out_as_ntfy_documents_them(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=200)

        send_alert(_alert(test=True), _config(), httpx.Client())

        request = httpx_mock.get_request()
        assert request is not None
        sent = {key.decode(): value.decode() for key, value in request.headers.raw}
        assert sent["Content-Type"] == "text/plain; charset=utf-8"
        assert sent["Title"] == f"[TEST] lovspor: {UNIT} failed on lovspor-droplet"
        assert sent["Priority"] == "high"
        assert sent["Tags"] == "warning"

    def test_a_non_ascii_title_is_sent_with_replacement_marks(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=200)
        alert = _alert().model_copy(update={"host": "dråpe"})

        send_alert(alert, _config(), httpx.Client())

        request = httpx_mock.get_request()
        assert request is not None
        assert request.headers["title"] == f"lovspor: {UNIT} failed on dr?pe"

    @pytest.mark.parametrize("test", [False, True])
    def test_the_json_body_says_whether_it_is_a_test(
        self, httpx_mock: HTTPXMock, test: bool
    ) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=200)

        send_alert(_alert(test=test), _config(AlertFormat.JSON), httpx.Client())

        request = httpx_mock.get_request()
        assert request is not None
        assert json.loads(request.content)["test"] is test


class TestAlertImplFacts:
    def test_reads_the_failed_units_facts_into_the_message(
        self, tmp_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=200)
        runner = FakeSystemd(_SHOW, "boom\n")

        alert_impl(UNIT, tmp_path / "a.env", _deps(runner, {"LOVSPOR_ALERT_WEBHOOK": WEBHOOK}))

        request = httpx_mock.get_request()
        assert request is not None
        assert "exit status: 1" in request.content.decode().splitlines()
        assert [call[2] for call in runner.calls] == [UNIT, UNIT]

    def test_an_empty_webhook_is_reported_as_unset_not_as_a_url(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config = AlertConfig(webhook=SecretStr(""))

        with pytest.raises(typer.Exit):
            _deliver(_alert(), config, httpx.Client())

        assert "not delivered to <unset>: webhook unreachable" in capsys.readouterr().err


def _fake_program(directory: Path, name: str, output: str) -> None:
    program = directory / name
    program.write_text(f"#!{sys.executable}\nprint({output!r}, end='')\n", encoding="utf-8")
    program.chmod(0o755)


class TestCommandWiringRealPath:
    def test_the_command_reads_systemd_and_posts_from_this_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
    ) -> None:
        _fake_program(tmp_path, "systemctl", _SHOW)
        _fake_program(tmp_path, "journalctl", "05:30 fetch failed\n")
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("LOVSPOR_ALERT_WEBHOOK", WEBHOOK)
        httpx_mock.add_response(url=WEBHOOK, method="POST", status_code=200)

        args = ["ops", "alert", "--unit", UNIT, "--env-file", str(tmp_path / "absent.env")]
        result = CliRunner().invoke(app, args)

        assert result.exit_code == 0, result.output
        request = httpx_mock.get_request()
        assert request is not None
        lines = request.content.decode().splitlines()
        assert f"host: {socket.gethostname()}" in lines
        assert "result: exit-code" in lines
        assert lines[-1] == "05:30 fetch failed"
        assert lines[2].startswith("time: 20")


_SHOW_ARGV = ["systemctl", "show", UNIT, "--property=Result", "--property=ExecMainStatus"]
_JOURNAL_ARGV = [
    "journalctl",
    "--unit",
    UNIT,
    f"--lines={JOURNAL_LINES}",
    "--no-pager",
    "--quiet",
    "--output=short-iso",
]


class TestUnitFactsEdges:
    def test_asks_systemd_exactly_what_it_needs(self) -> None:
        runner = FakeSystemd(_SHOW, "x\n")

        read_unit_facts(runner, UNIT)

        assert runner.calls == [_SHOW_ARGV, _JOURNAL_ARGV]

    def test_a_value_is_everything_after_the_first_equals_sign(self) -> None:
        facts = read_unit_facts(FakeSystemd("Result=a=b\nExecMainStatus=2\n", ""), UNIT)

        assert facts.result == "a=b"
        assert facts.exit_status == 2

    def test_a_missing_status_line_is_none(self) -> None:
        facts = read_unit_facts(FakeSystemd("Result=exit-code\n", ""), UNIT)

        assert (facts.result, facts.exit_status) == ("exit-code", None)

    def test_a_missing_result_line_is_unknown(self) -> None:
        facts = read_unit_facts(FakeSystemd("ExecMainStatus=1\n", ""), UNIT)

        assert (facts.result, facts.exit_status) == ("unknown", 1)


class TestRunCommandEdges:
    def test_a_failure_names_the_program_and_its_exit(self) -> None:
        with pytest.raises(UnitFactsError) as info:
            run_command([sys.executable, "-c", "import sys; sys.exit(3)"])

        assert str(info.value) == f"{sys.executable} exited 3"

    def test_a_missing_program_names_the_error_type(self) -> None:
        missing = "/nonexistent/lovspor-no-such-program"
        with pytest.raises(UnitFactsError) as info:
            run_command([missing])

        assert str(info.value) == f"cannot run {missing}: FileNotFoundError"

    def test_a_hung_program_is_stopped_at_the_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(unit_facts, "COMMAND_TIMEOUT_SECONDS", 0.5)

        with pytest.raises(UnitFactsError, match=": TimeoutExpired$"):
            run_command([sys.executable, "-c", "import time; time.sleep(5)"])
