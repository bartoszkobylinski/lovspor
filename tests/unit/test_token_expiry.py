"""Tests for scripts/ci/token_expiry.py and its workflow (issue #270).

On 2026-09-10 `LOVSPOR_CI_PUSH_TOKEN` expired and every PR blocked at checkout
under a message that blamed the pull request. The check must say so a week or
two before, and must never read "cannot tell" as "fine".
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pytest
import yaml  # type: ignore[import-untyped]

_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = _ROOT / "scripts" / "ci" / "token_expiry.py"
WORKFLOW = _ROOT / ".github" / "workflows" / "token-expiry.yml"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
NAME = "LOVSPOR_CI_PUSH_TOKEN"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("token_expiry", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


token_expiry = _load()


def _judge(status: int, expiry: str | None, warn_days: int = 14) -> Any:
    return token_expiry.judge(token_expiry.Answer(status, expiry), NOW, warn_days, NAME)


class TestParseExpiry:
    def test_reads_the_utc_suffixed_form(self) -> None:
        parsed = token_expiry.parse_expiry("2026-10-10 08:28:30 UTC")
        assert parsed == datetime(2026, 10, 10, 8, 28, 30, tzinfo=UTC)

    def test_reads_a_numeric_offset_and_normalises_the_instant(self) -> None:
        parsed = token_expiry.parse_expiry("2026-10-10 10:28:30 +0200")
        assert parsed == datetime(2026, 10, 10, 8, 28, 30, tzinfo=UTC)
        assert parsed.tzinfo is UTC

    def test_refuses_a_shape_it_does_not_know(self) -> None:
        with pytest.raises(ValueError, match="unrecognised"):
            token_expiry.parse_expiry("next Tuesday")

    def test_reads_a_negative_offset_as_a_later_utc_instant(self) -> None:
        parsed = token_expiry.parse_expiry("2026-10-09 23:28:30 -0900")
        assert parsed == datetime(2026, 10, 10, 8, 28, 30, tzinfo=UTC)
        assert parsed.tzinfo is UTC

    def test_a_zero_offset_is_already_utc(self) -> None:
        parsed = token_expiry.parse_expiry("2026-10-10 08:28:30 +0000")
        assert parsed == datetime(2026, 10, 10, 8, 28, 30, tzinfo=UTC)
        assert parsed.tzinfo is UTC

    def test_the_utc_suffixed_form_carries_the_utc_singleton(self) -> None:
        assert token_expiry.parse_expiry("2026-10-10 08:28:30 UTC").tzinfo is UTC

    @pytest.mark.parametrize(
        "value",
        [
            "2026-10-10 08:28:30",
            "2026-10-10 08:28:30 +02",
            "2026-10-10 08:28:30 CEST",
            "2026-10-10T08:28:30Z",
            "",
        ],
    )
    def test_refuses_a_missing_or_malformed_zone(self, value: str) -> None:
        with pytest.raises(ValueError, match="unrecognised"):
            token_expiry.parse_expiry(value)


class TestJudge:
    def test_far_expiry_passes_and_names_the_date(self) -> None:
        verdict = _judge(200, "2026-11-09 19:32:00 UTC")
        assert verdict.ok
        assert "2026-11-09 19:32 UTC" in verdict.message
        assert "45.3 days" in verdict.message

    def test_numeric_offset_is_reported_as_the_equivalent_utc_time(self) -> None:
        verdict = _judge(200, "2026-11-09 21:32:00 +0200")
        assert verdict.ok
        assert "2026-11-09 19:32 UTC" in verdict.message
        assert "45.3 days" in verdict.message

    def test_an_offset_moving_the_instant_into_the_window_fails(self) -> None:
        # 2026-10-09 13:00 +0200 is 11:00 UTC: 13.96 days, inside the 14-day window.
        verdict = _judge(200, "2026-10-09 13:00:00 +0200")
        assert not verdict.ok
        assert "2026-10-09 11:00 UTC" in verdict.message

    def test_a_negative_offset_moving_the_instant_out_of_the_window_passes(self) -> None:
        # 2026-10-09 08:00 -0500 is 13:00 UTC: 14.04 days, outside the window.
        verdict = _judge(200, "2026-10-09 08:00:00 -0500")
        assert verdict.ok
        assert "2026-10-09 13:00 UTC" in verdict.message

    def test_expiry_inside_the_warning_window_fails(self) -> None:
        verdict = _judge(200, "2026-10-01 12:00:00 UTC")
        assert not verdict.ok
        assert "6.0 days" in verdict.message
        assert "renew it" in verdict.message

    def test_the_window_edge_counts_as_inside(self) -> None:
        assert not _judge(200, "2026-10-09 12:00:00 UTC").ok
        assert _judge(200, "2026-10-09 12:01:00 UTC").ok

    def test_an_already_passed_date_fails(self) -> None:
        verdict = _judge(200, "2026-09-10 08:28:30 UTC")
        assert not verdict.ok
        assert "-15.1 days" in verdict.message

    def test_a_token_with_no_expiry_header_passes_and_says_so(self) -> None:
        verdict = _judge(200, None)
        assert verdict.ok
        assert "reports no expiry date" in verdict.message

    def test_a_rejected_token_fails_as_expired_or_revoked(self) -> None:
        verdict = _judge(401, None)
        assert not verdict.ok
        assert "expired or revoked" in verdict.message

    @pytest.mark.parametrize("status", [403, 404, 500])
    def test_any_other_status_fails_closed(self, status: int) -> None:
        verdict = _judge(status, "2027-01-01 00:00:00 UTC")
        assert not verdict.ok
        assert f"HTTP {status}" in verdict.message

    def test_an_unreachable_api_fails_closed(self) -> None:
        verdict = _judge(0, None)
        assert not verdict.ok
        assert "unreachable" in verdict.message

    def test_an_unreadable_header_fails_closed(self) -> None:
        verdict = _judge(200, "soon")
        assert not verdict.ok
        assert "unrecognised" in verdict.message


class _Api(BaseHTTPRequestHandler):
    status = 200
    expiry: str | None = None
    seen: ClassVar[list[dict[str, str]]] = []

    def do_GET(self) -> None:
        type(self).seen.append({"path": self.path, **dict(self.headers.items())})
        self.send_response(type(self).status)
        if type(self).expiry is not None:
            self.send_header(token_expiry.EXPIRY_HEADER, type(self).expiry)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def api() -> Iterator[str]:
    _Api.status, _Api.expiry, _Api.seen = 200, None, []
    server = HTTPServer(("127.0.0.1", 0), _Api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _run(api: str, monkeypatch: pytest.MonkeyPatch, value: str = "tok-123") -> int:
    monkeypatch.setenv(NAME, value)
    argv = ["--secret-env", NAME, "--repo", "o/r", "--api-url", api]
    code: int = token_expiry.main(argv)
    return code


class TestAgainstALiveServer:
    def test_sends_the_token_as_a_bearer_header_to_the_repo(
        self, api: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _Api.expiry = "2027-01-01 00:00:00 UTC"
        assert _run(api, monkeypatch) == 0
        (request,) = _Api.seen
        assert request["path"] == "/repos/o/r"
        assert request["Authorization"] == "Bearer tok-123"

    def test_near_expiry_exits_nonzero_with_an_error_annotation(
        self, api: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _Api.expiry = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        assert _run(api, monkeypatch) == 1
        assert capsys.readouterr().out.startswith(f"::error::{NAME} expires ")

    def test_a_401_exits_nonzero(self, api: str, monkeypatch: pytest.MonkeyPatch) -> None:
        _Api.status = 401
        assert _run(api, monkeypatch) == 1

    def test_the_token_never_reaches_the_output(
        self, api: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _Api.status = 401
        _run(api, monkeypatch, value="secret-value-xyz")
        assert "secret-value-xyz" not in capsys.readouterr().out

    def test_writes_the_verdict_to_the_step_summary(
        self, api: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        assert _run(api, monkeypatch) == 0
        assert "reports no expiry date" in summary.read_text(encoding="utf-8")


def test_an_empty_secret_fails_without_calling_the_api(
    api: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(api, monkeypatch, value="  ") == 1
    assert _Api.seen == []
    assert "is empty or not set" in capsys.readouterr().out


def test_a_closed_port_reads_as_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    server = HTTPServer(("127.0.0.1", 0), _Api)
    port = server.server_address[1]
    server.server_close()
    assert _run(f"http://127.0.0.1:{port}", monkeypatch) == 1


class TestWorkflow:
    def _workflow(self) -> dict[str, Any]:
        loaded: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        return loaded

    def test_runs_weekly_and_on_demand(self) -> None:
        triggers = self._workflow()[True]
        assert set(triggers) == {"schedule", "workflow_dispatch"}
        assert len(triggers["schedule"]) == 1

    def test_holds_read_only_permissions(self) -> None:
        assert self._workflow()["permissions"] == {"contents": "read"}

    def test_checks_the_push_token_the_pipeline_checks_out_with(self) -> None:
        (job,) = self._workflow()["jobs"].values()
        step = next(s for s in job["steps"] if "token_expiry.py" in s.get("run", ""))
        assert step["env"] == {NAME: "${{ secrets.LOVSPOR_CI_PUSH_TOKEN }}"}
        assert f"--secret-env {NAME}" in step["run"]
        pipeline = (_ROOT / ".github" / "workflows" / "pr-pipeline.yml").read_text("utf-8")
        assert "secrets.LOVSPOR_CI_PUSH_TOKEN" in pipeline

    def test_checkout_does_not_persist_credentials(self) -> None:
        (job,) = self._workflow()["jobs"].values()
        checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout"))
        assert checkout["with"]["persist-credentials"] is False
        assert "@" in checkout["uses"] and len(checkout["uses"].split("@")[1].split()[0]) == 40


def test_refuses_a_non_http_api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match="must be an http"):
        _run("file:///etc/passwd", monkeypatch)
