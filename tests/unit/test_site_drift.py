"""The drift check: a fresh observation against the served document's state.

ADR-0014 Decision 4 (ADR:1054-1090): ``state`` is compared, never
``observation`` — ``observed_at``, ``observer`` and an unobserved
``reason`` are diagnostic — and the expectations are the served
document's own ``checkout``, so a moved work tree without a release is
not drift of the hosted state, while a restart that changed what the
process runs or serves is.

``Host`` is the droplet as ``pytest-httpx`` answers for it: a readiness
payload, the fake MCP path and no discovery document. ``released()`` is
the document the release probe wrote for that host — what the site
serves — so a drift test starts from a served document the probe itself
produced, never a hand-written one.
"""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from mcp.types import ListToolsResult
from pydantic import SecretStr
from pytest_httpx import HTTPXMock

from lovspor.site.capabilities import Checkout, State, parse_capabilities
from lovspor.site.drift import (
    DriftReport,
    drift_check,
    fetch_served_document,
    state_differences,
)
from lovspor.site.errors import CapabilityDocumentError, ServedDocumentError
from lovspor.site.fixture import document_bytes
from lovspor.site.probe import ProbeSettings, probe
from lovspor.tool_surface import describe_listed_tools
from tests.unit.probe_fixtures import (
    DISCOVERY_URL,
    MCP_URL,
    READINESS_URL,
    TOKEN,
    FakeMcp,
    attestation,
    install,
    tools_listing,
)
from tests.unit.site_fixtures import (
    available_observation,
    capability_document,
    checkout_expectations,
    unobserved_authenticated,
)

SERVED_URL = "https://lovspor.no/deployment-capabilities.json"


def clock() -> datetime:
    return datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)


def settings(observer: str = "drift-timer") -> ProbeSettings:
    return ProbeSettings(
        readiness_url=READINESS_URL,
        public_mcp_url=MCP_URL,
        probe_token=SecretStr(TOKEN),
        timeout_seconds=1.0,
        observer=observer,  # type: ignore[arg-type]
    )


def listing_hash(listing: dict[str, Any]) -> str:
    return describe_listed_tools(ListToolsResult.model_validate(listing).tools).schema_sha256


class Host:
    """The droplet behind the mocks: restartable, re-listable, re-keyed."""

    def __init__(self, httpx_mock: HTTPXMock, listing: dict[str, Any] | None = None) -> None:
        self.fake = FakeMcp(listing or tools_listing())
        self.payload: dict[str, Any] = {}
        self.restart_with(self.fake.listing)
        httpx_mock.add_callback(
            lambda _request: httpx.Response(200, json=self.payload),
            url=READINESS_URL,
            is_reusable=True,
        )
        install(httpx_mock, self.fake)
        httpx_mock.add_response(url=DISCOVERY_URL, status_code=404, is_reusable=True)

    def restart_with(self, listing: dict[str, Any]) -> None:
        self.fake.listing = listing
        self.payload = attestation(
            tool_surface_sha256=listing_hash(listing), tool_count=len(listing["tools"])
        )

    def checkout(self) -> Checkout:
        expectations = checkout_expectations()
        expectations["expected_tool_surface_sha256"] = self.payload["tool_surface_sha256"]
        return Checkout.model_validate(expectations)

    def released(self) -> dict[str, Any]:
        """What the release probe wrote for this host, as the site serves it."""
        with httpx.Client() as client:
            document = probe(
                settings("release-probe"), client=client, checkout=self.checkout(), clock=clock
            )
        assert document.state.hosted_state == "available"
        return json.loads(document_bytes(document))


def served_state(observation: dict[str, Any] | None = None) -> State:
    return parse_capabilities(json.dumps(capability_document(observation)).encode()).state


def serve(httpx_mock: HTTPXMock, document: dict[str, Any]) -> None:
    httpx_mock.add_response(url=SERVED_URL, json=document)


class TestStateDifferences:
    def test_equal_states_differ_nowhere(self) -> None:
        assert state_differences(served_state(), served_state()) == ()

    def test_names_every_differing_leaf_by_its_dotted_path_sorted(self) -> None:
        moved = available_observation()
        moved["process"]["tool_count"] = 18
        moved["process"]["runtime_identity"]["tree_sha256"] = "f" * 64
        moved["transport"]["authenticated"] = unobserved_authenticated("probe_credential_rejected")[
            "transport"
        ]["authenticated"]

        differences = state_differences(served_state(), served_state(moved))

        assert differences == (
            "comparisons.runtime_tree_match",
            "comparisons.transport_surface_match",
            "hosted_state",
            "process.runtime_identity.tree_sha256",
            "process.tool_count",
            "transport.authenticated.outcome",
            "transport.authenticated.served_tool_count",
            "transport.authenticated.served_tool_surface_sha256",
            "transport.authenticated.status",
        )

    def test_a_record_that_vanished_is_named_at_its_root(self) -> None:
        unobserved = available_observation()
        unobserved["process"] = {
            **unobserved["process"],
            "status": "unobserved",
            "reason": "timeout",
            "ready": None,
            "runtime_identity": None,
            "tool_surface_sha256": None,
            "tool_count": None,
            "credential_modes": None,
            "oauth_configured": None,
        }

        differences = state_differences(served_state(), served_state(unobserved))

        assert "process.runtime_identity" in differences
        assert "process.status" in differences
        assert not any(name.startswith("process.runtime_identity.") for name in differences)

    def test_observation_time_observer_and_reason_are_not_state(self) -> None:
        """Two documents of one meaning: the comparison reads none of them."""
        later = available_observation()
        later["process"]["observed_at"] = "2026-02-02T00:00:00Z"
        later["transport"]["observed_at"] = "2026-02-02T00:00:00Z"
        later["process"]["observer"] = "drift-timer"
        later["transport"]["observer"] = "drift-timer"

        assert state_differences(served_state(), served_state(later)) == ()


class TestFetchServedDocument:
    def test_a_valid_document_is_returned(self, httpx_mock: HTTPXMock) -> None:
        serve(httpx_mock, capability_document())
        with httpx.Client() as client:
            document = fetch_served_document(SERVED_URL, client, timeout_seconds=1.0)

        assert document.state == served_state()
        request = httpx_mock.get_request(url=SERVED_URL)
        assert request is not None
        assert request.extensions["timeout"]["read"] == 1.0
        assert "authorization" not in request.headers

    @pytest.mark.parametrize(
        ("answer", "reason"),
        [
            (httpx.ConnectError("dns"), "network"),
            (httpx.ReadTimeout("slow"), "timeout"),
            (httpx.Response(404), "http_404"),
            (httpx.Response(502), "http_502"),
            (httpx.Response(200, content=b"<html>"), "invalid"),
            (httpx.Response(200, json={"schema_version": "2"}), "invalid"),
        ],
        ids=["network", "timeout", "404", "502", "html", "wrong-schema"],
    )
    def test_anything_else_is_the_served_document_error_naming_the_reason(
        self, httpx_mock: HTTPXMock, answer: httpx.Response | Exception, reason: str
    ) -> None:
        if isinstance(answer, Exception):
            httpx_mock.add_exception(answer, url=SERVED_URL)
        else:
            httpx_mock.add_callback(lambda _request: answer, url=SERVED_URL)

        with httpx.Client() as client, pytest.raises(ServedDocumentError) as caught:
            fetch_served_document(SERVED_URL, client, timeout_seconds=1.0)

        assert caught.value.reason == reason
        assert isinstance(caught.value, CapabilityDocumentError)
        assert reason in str(caught.value)

    def test_a_document_whose_state_is_not_its_own_derivation_is_invalid(
        self, httpx_mock: HTTPXMock
    ) -> None:
        tampered = capability_document()
        tampered["state"]["hosted_state"] = "unknown"
        serve(httpx_mock, tampered)

        with httpx.Client() as client, pytest.raises(ServedDocumentError) as caught:
            fetch_served_document(SERVED_URL, client, timeout_seconds=1.0)

        assert caught.value.reason == "invalid"


class TestDriftCheck:
    def test_no_drift_when_the_host_still_matches_the_served_state(
        self, httpx_mock: HTTPXMock
    ) -> None:
        host = Host(httpx_mock)
        serve(httpx_mock, host.released())
        with httpx.Client() as client:
            report = drift_check(SERVED_URL, settings(), client=client, clock=clock)

        assert isinstance(report, DriftReport)
        assert report.differences == ()
        assert report.drifted is False
        assert report.served.state == report.observed.state
        assert report.observed.observation.process.observer == "drift-timer"
        assert report.observed.observation.process.observed_at == "2026-09-09T12:00:00Z"
        assert report.served.observation.process.observer == "release-probe"

    def test_the_expectations_are_the_served_documents_checkout(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """A moved work tree without a release is not drift (ADR:1073-1076):
        the check reads no checkout of its own."""
        host = Host(httpx_mock)
        serve(httpx_mock, host.released())
        with httpx.Client() as client:
            report = drift_check(SERVED_URL, settings(), client=client, clock=clock)

        assert report.observed.state.checkout == report.served.state.checkout
        assert report.observed.state.checkout == host.checkout()

    def test_a_restart_that_changed_the_surface_is_drift(self, httpx_mock: HTTPXMock) -> None:
        host = Host(httpx_mock)
        serve(httpx_mock, host.released())
        host.restart_with(tools_listing(("a", "b", "c")))
        with httpx.Client() as client:
            report = drift_check(SERVED_URL, settings(), client=client, clock=clock)

        assert report.drifted is True
        assert "process.tool_surface_sha256" in report.differences
        assert "process.tool_count" in report.differences
        assert "transport.authenticated.served_tool_count" in report.differences
        assert "comparisons.tool_surface_match" in report.differences
        assert "hosted_state" not in report.differences

    def test_an_expired_probe_credential_is_drift_naming_the_rejection(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Observer failure is still a state difference: ``authenticated``
        moves from observed to unobserved (ADR:1084-1089)."""
        host = Host(httpx_mock)
        serve(httpx_mock, host.released())
        host.fake.accepted_token = "lsp_rotated"
        with httpx.Client() as client:
            report = drift_check(SERVED_URL, settings(), client=client, clock=clock)

        assert report.drifted is True
        assert "transport.authenticated.status" in report.differences
        assert "hosted_state" in report.differences
        assert report.observed.observation.transport.authenticated.reason == (
            "probe_credential_rejected"
        )

    def test_a_served_document_that_cannot_be_read_is_the_error_not_drift(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=SERVED_URL, status_code=503)

        with httpx.Client() as client, pytest.raises(ServedDocumentError) as caught:
            drift_check(SERVED_URL, settings(), client=client, clock=clock)

        assert caught.value.reason == "http_503"
        assert not httpx_mock.get_requests(url=READINESS_URL)
