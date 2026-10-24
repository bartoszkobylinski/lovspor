"""The release probe waits out the MCP's warm window (#584).

Every HTTP answer is a ``pytest-httpx`` transport mock and the probe is the
real one; only the wait's two boundaries — its sleep and its monotonic
clock — are injected, so the bound is exact and no test sleeps.
"""

from datetime import UTC, datetime

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.release.warm import WarmWindow, settled, warm_observer
from lovspor.site.capabilities import CapabilityDocument, Checkout
from lovspor.site.probe import ProbeSettings, probe
from tests.unit.probe_fixtures import MCP_URL, READINESS_URL, FakeMcp, absent_discovery, ready
from tests.unit.site_fixtures import checkout_expectations

MOMENT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


class FakeTime:
    """A monotonic clock that only moves when the wait sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []
        self.said: list[str] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now

    def window(self, budget: float = 60.0, interval: float = 5.0) -> WarmWindow:
        return WarmWindow(
            budget_seconds=budget,
            interval_seconds=interval,
            sleep=self.sleep,
            clock=self.monotonic,
            say=self.said.append,
        )


def _observe(checkout: Checkout) -> CapabilityDocument:
    settings = ProbeSettings(readiness_url=READINESS_URL, public_mcp_url=MCP_URL)
    with httpx.Client() as client:
        return probe(settings, client=client, checkout=checkout, clock=lambda: MOMENT)


def _checkout() -> Checkout:
    return Checkout.model_validate(checkout_expectations())


def _warming(httpx_mock: HTTPXMock) -> None:
    """Loopback refused, Caddy 502: the window after ``systemctl restart lovspor-mcp``."""
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=READINESS_URL)
    httpx_mock.add_response(url=MCP_URL, status_code=502)
    absent_discovery(httpx_mock)


def _warm(httpx_mock: HTTPXMock) -> None:
    ready(httpx_mock)
    httpx_mock.add_callback(FakeMcp(), url=MCP_URL)
    absent_discovery(httpx_mock)


def test_a_warm_process_is_observed_once_and_never_waited_for(httpx_mock: HTTPXMock) -> None:
    time = FakeTime()
    _warm(httpx_mock)

    document = settled(_observe, _checkout(), time.window())

    assert document.observation.process.status == "observed"
    assert time.slept == []
    assert time.said == []


def test_a_publish_inside_the_warm_window_records_the_warmed_state(
    httpx_mock: HTTPXMock,
) -> None:
    time = FakeTime()
    _warming(httpx_mock)
    _warming(httpx_mock)
    _warm(httpx_mock)

    document = warm_observer(_observe, time.window())(_checkout())

    assert document.observation.process.status == "observed"
    assert document.state.hosted_state != "unavailable"
    assert time.slept == [5.0, 5.0]
    assert len(time.said) == 2
    assert "not answering" in time.said[0]


def test_a_process_that_never_answers_is_recorded_as_unobserved_within_the_bound(
    httpx_mock: HTTPXMock,
) -> None:
    time = FakeTime()
    for _ in range(4):
        _warming(httpx_mock)

    document = settled(_observe, _checkout(), time.window(budget=15.0, interval=5.0))

    assert document.observation.process.status == "unobserved"
    assert document.observation.process.reason == "network"
    assert document.state.hosted_state == "unavailable"
    assert time.slept == [5.0, 5.0, 5.0]


def test_the_wait_never_sleeps_past_its_budget(httpx_mock: HTTPXMock) -> None:
    time = FakeTime()
    for _ in range(2):
        _warming(httpx_mock)

    settled(_observe, _checkout(), time.window(budget=9.0, interval=5.0))

    assert time.slept == [5.0]


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_without_an_upstream_is_waited_out(httpx_mock: HTTPXMock, status: int) -> None:
    """Loopback answers but Caddy has not reached it yet: still the warm window."""
    time = FakeTime()
    ready(httpx_mock)
    httpx_mock.add_response(url=MCP_URL, status_code=status)
    absent_discovery(httpx_mock)
    _warm(httpx_mock)

    document = settled(_observe, _checkout(), time.window())

    assert document.observation.transport.unauthenticated is not None
    assert document.observation.transport.unauthenticated.status_code == 401
    assert time.slept == [5.0]


def test_an_unreachable_public_path_is_waited_out(httpx_mock: HTTPXMock) -> None:
    time = FakeTime()
    ready(httpx_mock)
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=MCP_URL)
    _warm(httpx_mock)

    document = settled(_observe, _checkout(), time.window())

    assert document.observation.transport.status == "observed"
    assert time.slept == [5.0]


def test_a_process_that_answers_not_ready_is_recorded_not_waited_for(
    httpx_mock: HTTPXMock,
) -> None:
    """A ``503`` from ``/readyz`` is the subject's own answer, not the warm window."""
    time = FakeTime()
    ready(httpx_mock, status=503)
    httpx_mock.add_callback(FakeMcp(), url=MCP_URL)
    absent_discovery(httpx_mock)

    document = settled(_observe, _checkout(), time.window())

    assert document.observation.process.status == "observed"
    assert document.observation.process.ready is False
    assert time.slept == []


def test_the_default_window_is_bounded() -> None:
    window = WarmWindow()

    assert 0 < window.interval_seconds < window.budget_seconds <= 120.0


@pytest.mark.parametrize(
    ("status", "payload", "reason"),
    [(500, {}, "http_500"), (200, {}, "schema_invalid")],
)
def test_a_readiness_answer_without_an_attestation_is_recorded_without_retry(
    httpx_mock: HTTPXMock, status: int, payload: dict[str, object], reason: str
) -> None:
    """An HTTP or schema failure is an answer, not a process still warming."""
    time = FakeTime()
    httpx_mock.add_response(url=READINESS_URL, status_code=status, json=payload)
    httpx_mock.add_callback(FakeMcp(), url=MCP_URL)
    absent_discovery(httpx_mock)

    document = settled(_observe, _checkout(), time.window())

    assert document.observation.process.status == "unobserved"
    assert document.observation.process.reason == reason
    assert time.slept == []
    assert time.said == []


@pytest.mark.parametrize("status", [200, 400, 401, 403, 404, 500])
def test_other_public_http_answers_are_recorded_without_retry(
    httpx_mock: HTTPXMock, status: int
) -> None:
    """docs/mcp.md: any step (a) status outside 502/503/504 settles immediately."""
    time = FakeTime()
    ready(httpx_mock)
    httpx_mock.add_response(url=MCP_URL, status_code=status)
    absent_discovery(httpx_mock)

    document = settled(_observe, _checkout(), time.window())

    step = document.observation.transport.unauthenticated
    assert step is not None
    assert step.status_code == status
    assert time.slept == []
    assert time.said == []


@pytest.mark.parametrize("url", [READINESS_URL, MCP_URL])
def test_a_timeout_is_retried_until_an_answer_arrives(httpx_mock: HTTPXMock, url: str) -> None:
    time = FakeTime()
    if url == READINESS_URL:
        httpx_mock.add_exception(httpx.ReadTimeout("warming"), url=url)
        httpx_mock.add_callback(FakeMcp(), url=MCP_URL)
        absent_discovery(httpx_mock)
    else:
        ready(httpx_mock)
        httpx_mock.add_exception(httpx.ReadTimeout("warming"), url=url)
    _warm(httpx_mock)

    document = settled(_observe, _checkout(), time.window())

    assert document.observation.process.status == "observed"
    assert document.observation.transport.status == "observed"
    assert time.slept == [5.0]


def test_probe_duration_counts_toward_budget_and_the_last_document_is_returned(
    httpx_mock: HTTPXMock,
) -> None:
    """The bounded wait includes observing, and exhaustion records the last observation."""
    time = FakeTime()
    checkout = _checkout()
    documents: list[CapabilityDocument] = []
    for _ in range(2):
        _warming(httpx_mock)

    def slow_observe(subject: Checkout) -> CapabilityDocument:
        assert subject is checkout
        document = _observe(subject)
        documents.append(document)
        time.now += 3.0
        return document

    document = settled(slow_observe, checkout, time.window(budget=12.0))

    assert len(documents) == 2
    assert document is documents[-1]
    assert time.slept == [5.0]
    assert time.said == [
        "release probe: hosted MCP not answering (warm window?); observing again in 5 s"
    ]


def test_zero_budget_still_records_one_observation(httpx_mock: HTTPXMock) -> None:
    time = FakeTime()
    _warming(httpx_mock)

    document = settled(_observe, _checkout(), time.window(budget=0.0))

    assert document.observation.process.reason == "network"
    assert time.slept == []
    assert time.said == []
