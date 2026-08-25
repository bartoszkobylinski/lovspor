"""The outbound dead-man switch (issue #167, part 3).

Only the transport is mocked, through `pytest-httpx`. What is under test is
which endpoint a run reports to and what happens when the report cannot be
delivered — both are decisions, not plumbing.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.observatory.heartbeat import (
    ENV_HEARTBEAT_URL,
    FAIL_SUFFIX,
    HEARTBEAT_TIMEOUT_SECONDS,
    heartbeat_url,
    ping_url,
    send_heartbeat,
)
from lovspor.observatory.sweeps import SweepRun, sweep_status

BASE = "https://hc.example.invalid/abc123"
START = datetime(2026, 8, 25, 3, 0, tzinfo=UTC)


def _run(*, refused: int = 0, failed: bool = False) -> SweepRun:
    if failed:
        return SweepRun(
            run_id=START.isoformat(),
            started_at=START,
            finished_at=START,
            active_sources=0,
            sources_completed=0,
            sources_refused=0,
            captured=0,
            failed_fetches=0,
            unchanged=0,
            status="failed",
            failure_reason="storage_unavailable",
        )
    return SweepRun(
        run_id=START.isoformat(),
        started_at=START,
        finished_at=START + timedelta(minutes=76),
        active_sources=2,
        sources_completed=2 - refused,
        sources_refused=refused,
        captured=47,
        failed_fetches=1,
        unchanged=4218,
        status=sweep_status(active=2, refused=refused),
    )


class TestArming:
    def test_no_variable_means_no_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_HEARTBEAT_URL, raising=False)

        assert heartbeat_url() is None

    def test_a_blank_variable_is_not_an_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty value is a half-finished setup, not a configured one, and
        must not read as armed."""
        monkeypatch.setenv(ENV_HEARTBEAT_URL, "   ")

        assert heartbeat_url() is None

    def test_surrounding_whitespace_is_not_part_of_the_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_HEARTBEAT_URL, f"  {BASE}\n")

        assert heartbeat_url() == BASE


class TestWhichEndpointARunReportsTo:
    def test_a_clean_sweep_reports_success(self) -> None:
        assert ping_url(BASE, _run()) == BASE

    def test_a_degraded_sweep_still_reports_success(self) -> None:
        """It ran, and liveness is what this switch guards. Ten sources already
        refuse on a normal night; alarming on that would fire nightly and the
        alert would be muted, taking the liveness signal with it."""
        assert _run(refused=1).status == "degraded"
        assert ping_url(BASE, _run(refused=1)) == BASE

    def test_only_a_sweep_that_could_not_run_reports_failure(self) -> None:
        assert ping_url(BASE, _run(failed=True)) == BASE + FAIL_SUFFIX


class TestDelivery:
    def test_the_run_travels_in_the_body(self, httpx_mock: HTTPXMock) -> None:
        """The service's history is where "what kind of night was it" gets
        answered, so degradation has to be visible even though it pings the
        success endpoint."""
        httpx_mock.add_response(url=BASE)

        with httpx.Client() as client:
            assert send_heartbeat(BASE, _run(refused=1), client) is True

        sent = httpx_mock.get_requests()[0]
        assert b'"status":"degraded"' in sent.content
        assert b'"sources_refused":1' in sent.content

    def test_a_refused_ping_is_reported_not_raised(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=BASE, status_code=500)

        with httpx.Client() as client:
            assert send_heartbeat(BASE, _run(), client) is False

    def test_a_transport_failure_is_reported_not_raised(self, httpx_mock: HTTPXMock) -> None:
        """A monitoring endpoint being unreachable must never turn a completed
        sweep into a failed command: the sweep is the point."""
        httpx_mock.add_exception(httpx.ConnectError("no route"))

        with httpx.Client() as client:
            assert send_heartbeat(BASE, _run(), client) is False

    def test_a_timeout_is_reported_not_raised(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ReadTimeout("slow"))

        with httpx.Client() as client:
            assert send_heartbeat(BASE, _run(), client) is False

    def test_the_wait_is_bounded(self) -> None:
        """The sweep already spends hours waiting out per-host rate limits; it
        must not also hang on a monitor."""
        assert 0 < HEARTBEAT_TIMEOUT_SECONDS <= 30
