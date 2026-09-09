"""The release probe: the observation layer of ``deployment-capabilities.json``.

ADR-0014 Decision 4 (ADR:735-743, 826-931, 947-993). Every HTTP answer
here is a ``pytest-httpx`` transport mock; the MCP handshake is answered
by ``FakeMcp``, a callback that speaks the Streamable HTTP wire contract
the SDK's server enforces (``mcp/server/streamable_http.py``): both
media types in ``Accept``, JSON ``Content-Type``, the session id minted
on ``initialize`` and required after it, ``202`` for a notification, an
SSE body carrying one ``message`` event per answer. Nothing of the probe's
own logic is mocked.
"""

import ast
import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.types import LATEST_PROTOCOL_VERSION, InitializeRequest, ListToolsResult
from pydantic import SecretStr, ValidationError
from pytest_httpx import HTTPXMock

import lovspor.site.probe as probe_module
from lovspor import __version__
from lovspor.mcp import HttpConfig, build_server
from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.capabilities import (
    CapabilityDocument,
    Checkout,
    derive_state,
    parse_capabilities,
)
from lovspor.site.errors import ProbeError
from lovspor.site.fixture import document_bytes
from lovspor.site.probe import (
    CANONICAL_MCP_URL,
    ProbeSettings,
    authorization_server_metadata_urls,
    probe,
    protected_resource_metadata_url,
)
from lovspor.tool_surface import describe_tool_surface
from tests.unit.llhb_fixtures import build_corpus
from tests.unit.probe_fixtures import (
    AUTHKIT,
    AUTHKIT_METADATA_URL,
    AUTHKIT_OPENID_URL,
    CHALLENGE,
    DISCOVERY_URL,
    LISTING_SURFACE,
    MCP_URL,
    READINESS_URL,
    SESSION_ID,
    TOKEN,
    FakeMcp,
    absent_discovery,
    attestation,
    install,
    ready,
    sse,
    tools_listing,
)
from tests.unit.site_fixtures import (
    ENVIRONMENT,
    INTERPRETER,
    TREE,
    checkout_expectations,
)

MOMENT = datetime(2026, 9, 9, 10, 11, 12, 345678, tzinfo=UTC)
OBSERVED_AT = "2026-09-09T10:11:12Z"
CORPUS_DOCS = {"testloven": ("Testloven", "### § 1. Formål\n\nLoven gjelder.\n")}
SSE_TYPE = "text/event-stream"
INITIALIZE_PARAMS = {
    "protocolVersion": LATEST_PROTOCOL_VERSION,
    "capabilities": {},
    "clientInfo": {"name": "lovspor-site-probe", "version": __version__},
}


class Undecided(tzinfo):
    """A tzinfo that never states its offset: Python treats the datetime as naive."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


def clock() -> datetime:
    return MOMENT


def settings(**overrides: Any) -> ProbeSettings:
    values: dict[str, Any] = {
        "readiness_url": READINESS_URL,
        "public_mcp_url": MCP_URL,
        "probe_token": SecretStr(TOKEN),
        "timeout_seconds": 2.0,
        "observer": "release-probe",
    }
    values.update(overrides)
    return ProbeSettings(**values)


def checkout() -> Checkout:
    return Checkout.model_validate(checkout_expectations())


def run(
    httpx_mock: HTTPXMock,
    *,
    probe_settings: ProbeSettings | None = None,
    fake: FakeMcp | None = None,
    discovery: bool = True,
) -> CapabilityDocument:
    """Both subjects healthy unless a caller registered its own answers first."""
    if fake is not None:
        install(httpx_mock, fake)
    if discovery:
        absent_discovery(httpx_mock)
    with httpx.Client() as client:
        return probe(probe_settings or settings(), client=client, checkout=checkout(), clock=clock)


@pytest.fixture
def healthy(httpx_mock: HTTPXMock) -> FakeMcp:
    ready(httpx_mock)
    return install(httpx_mock, FakeMcp())


@pytest.fixture
def kolkata_local_time(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A process zone five and a half hours from UTC, so a local-time reading would show."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("probe") / "corpus"
    build_corpus(root, CORPUS_DOCS)
    return root


class TestProcess:
    def test_a_ready_answer_is_observed_with_the_attestation_verbatim(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        document = run(httpx_mock)
        process = document.observation.process

        assert process.status == "observed"
        assert process.reason is None
        assert process.ready is True
        assert process.runtime_identity is not None
        assert process.runtime_identity.tree_sha256 == TREE
        assert process.runtime_identity.environment_sha256 == ENVIRONMENT
        assert process.runtime_identity.interpreter == INTERPRETER
        assert process.tool_surface_sha256 == LISTING_SURFACE
        assert process.tool_count == 2
        assert process.credential_modes == ("token",)
        assert process.oauth_configured is False

    def test_a_503_is_observed_not_ready_with_the_attestation_it_carries(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """``/readyz`` serves the same attestation on both answers (docs/mcp.md);
        the record keeps it, so the comparisons stay computable."""
        ready(httpx_mock, status=503)
        document = run(httpx_mock, fake=FakeMcp())
        process = document.observation.process

        assert process.status == "observed"
        assert process.ready is False
        assert process.runtime_identity is not None
        assert document.state.hosted_state == "unavailable"
        assert document.state.comparisons.runtime_tree_match == "true"

    @pytest.mark.parametrize(
        "payload",
        [
            {"status": "ready"},
            attestation(tool_count="many"),
            attestation(tool_surface_sha256="not-hex"),
            attestation(ready=False),
            {**attestation(), "credential_modes": ["token", "token"]},
        ],
        ids=[
            "no-attestation",
            "wrong-type",
            "not-a-hash",
            "ready-contradicts-200",
            "repeated-mode",
        ],
    )
    def test_a_payload_outside_the_schema_is_unobserved_schema_invalid(
        self, httpx_mock: HTTPXMock, payload: dict[str, Any]
    ) -> None:
        ready(httpx_mock, payload=payload)
        process = run(httpx_mock, fake=FakeMcp()).observation.process

        assert process.status == "unobserved"
        assert process.reason == "schema_invalid"
        assert process.ready is None
        assert process.runtime_identity is None

    def test_a_body_that_is_not_json_is_schema_invalid(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=READINESS_URL, content=b"<html>proxy</html>")
        process = run(httpx_mock, fake=FakeMcp()).observation.process

        assert (process.status, process.reason) == ("unobserved", "schema_invalid")

    def test_a_connection_error_is_unobserved_network(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ConnectError("refused"), url=READINESS_URL)
        process = run(httpx_mock, fake=FakeMcp()).observation.process

        assert (process.status, process.reason) == ("unobserved", "network")

    def test_a_timeout_is_unobserved_timeout(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=READINESS_URL)
        process = run(httpx_mock, fake=FakeMcp()).observation.process

        assert (process.status, process.reason) == ("unobserved", "timeout")

    @pytest.mark.parametrize("status", [404, 500, 302])
    def test_another_status_is_unobserved_http_code(
        self, httpx_mock: HTTPXMock, status: int
    ) -> None:
        httpx_mock.add_response(url=READINESS_URL, status_code=status, json={})
        process = run(httpx_mock, fake=FakeMcp()).observation.process

        assert (process.status, process.reason) == ("unobserved", f"http_{status}")

    def test_the_process_is_read_with_the_configured_timeout_and_no_credential(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        run(httpx_mock, probe_settings=settings(timeout_seconds=0.25))
        request = httpx_mock.get_request(url=READINESS_URL)

        assert request is not None
        assert request.method == "GET"
        assert "authorization" not in request.headers
        assert request.extensions["timeout"]["read"] == 0.25


class TestTransportUnauthenticated:
    def test_step_a_posts_initialize_without_a_credential_and_records_the_challenge(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        transport = run(httpx_mock).observation.transport
        first = healthy.requests[0]

        assert transport.status == "observed"
        assert transport.unauthenticated is not None
        assert transport.unauthenticated.status_code == 401
        assert transport.unauthenticated.challenge == CHALLENGE
        assert first.method == "POST"
        assert "authorization" not in first.headers
        assert json.loads(first.content)["method"] == "initialize"
        assert first.headers["accept"] == "application/json, text/event-stream"
        assert first.headers["content-type"] == "application/json"

    def test_step_a_is_the_documented_initialize_request_on_the_wire(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        """A JSON-RPC 2.0 ``initialize`` the SDK's own schema accepts, naming
        this probe, under the two headers the transport requires — each
        spelled as the client already spells it, so the wire carries no
        header the client would not send by itself."""
        run(httpx_mock)
        first = healthy.requests[0]
        body = json.loads(first.content)
        with httpx.Client() as client:
            plain = client.build_request("POST", MCP_URL, json={}).headers.raw

        assert body == {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": INITIALIZE_PARAMS,
        }
        InitializeRequest.model_validate({"method": "initialize", "params": body["params"]})
        assert {name for name, _ in first.headers.raw} == {name for name, _ in plain}

    def test_step_b_runs_when_bearer_is_not_the_first_advertised_challenge(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """The contract requires a 401 with a Bearer challenge, regardless of
        whether the server advertises another authentication scheme first."""
        challenge = f'Basic realm="legacy", {CHALLENGE}'
        ready(httpx_mock)
        fake = FakeMcp(unauthenticated=(401, challenge))

        document = run(httpx_mock, fake=fake)

        assert document.observation.transport.unauthenticated is not None
        assert document.observation.transport.unauthenticated.challenge == challenge
        assert document.observation.transport.authenticated.outcome == "ok"
        assert document.state.hosted_state == "available"

    @pytest.mark.parametrize(
        ("status", "challenge"),
        [(421, None), (404, None), (502, None), (200, None), (401, None), (401, "Basic")],
        ids=["misdirected", "not-found", "bad-gateway", "open", "401-no-challenge", "401-basic"],
    )
    def test_any_other_answer_is_recorded_verbatim_and_step_b_is_not_attempted(
        self, httpx_mock: HTTPXMock, status: int, challenge: str | None
    ) -> None:
        ready(httpx_mock)
        fake = FakeMcp(unauthenticated=(status, challenge))
        document = run(httpx_mock, fake=fake)
        transport = document.observation.transport

        assert transport.status == "observed"
        assert transport.unauthenticated is not None
        assert transport.unauthenticated.status_code == status
        assert transport.unauthenticated.challenge == challenge
        assert transport.authenticated.status == "unobserved"
        assert transport.authenticated.reason == "not_attempted"
        assert fake.methods() == ["initialize"]
        assert document.state.hosted_state == "unavailable"

    def test_a_connection_error_leaves_the_whole_transport_unobserved(
        self, httpx_mock: HTTPXMock
    ) -> None:
        ready(httpx_mock)
        httpx_mock.add_exception(httpx.ConnectError("no route"), url=MCP_URL)
        document = run(httpx_mock, discovery=False)
        transport = document.observation.transport

        assert transport.status == "unobserved"
        assert transport.reason == "network"
        assert transport.unauthenticated is None
        assert transport.authenticated.status == "unobserved"
        assert transport.authenticated.reason == "not_attempted"
        assert transport.oauth_discovery.verdict == "unobserved"
        assert document.state.hosted_state == "unknown"

    def test_a_timeout_leaves_the_whole_transport_unobserved(self, httpx_mock: HTTPXMock) -> None:
        ready(httpx_mock)
        httpx_mock.add_exception(httpx.ConnectTimeout("slow"), url=MCP_URL)
        transport = run(httpx_mock, discovery=False).observation.transport

        assert (transport.status, transport.reason) == ("unobserved", "timeout")
        assert not httpx_mock.get_requests(url=DISCOVERY_URL)


class TestTransportAuthenticated:
    def test_a_missing_credential_is_the_observers_failure(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        document = run(httpx_mock, probe_settings=settings(probe_token=None))
        step = document.observation.transport.authenticated

        assert step.status == "unobserved"
        assert step.reason == "probe_credential_missing"
        assert healthy.methods() == ["initialize"]
        assert document.state.hosted_state == "unknown"
        assert document.state.comparisons.transport_surface_match == "unknown"

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_credential_is_the_observers_failure_never_the_services(
        self, httpx_mock: HTTPXMock, status: int
    ) -> None:
        ready(httpx_mock)
        fake = FakeMcp(accepted_token="lsp_another", rejection=(status, CHALLENGE))
        document = run(httpx_mock, fake=fake)
        step = document.observation.transport.authenticated

        assert step.status == "unobserved"
        assert step.reason == "probe_credential_rejected"
        assert fake.methods() == ["initialize", "initialize"]
        assert document.state.hosted_state == "unknown"
        assert document.state.comparisons.transport_surface_match == "unknown"

    def test_a_listed_surface_is_ok_with_its_hash_and_count(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        document = run(httpx_mock)
        step = document.observation.transport.authenticated
        canonical = (
            '[{"description":"æ","inputSchema":{"type":"object"},"name":"a"},'
            '{"description":"æ","inputSchema":{"type":"object"},"name":"b"}]'
        )

        assert step.status == "observed"
        assert step.outcome == "ok"
        assert step.served_tool_count == 2
        assert step.served_tool_surface_sha256 == hashlib.sha256(canonical.encode()).hexdigest()

    def test_the_handshake_is_initialize_initialized_list_then_terminate(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        run(httpx_mock)
        posts = [r for r in healthy.requests if r.method == "POST" and "authorization" in r.headers]
        listing = posts[-1]

        assert healthy.methods() == [
            "initialize",
            "initialize",
            "notifications/initialized",
            "tools/list",
        ]
        assert healthy.requests[-1].method == "DELETE"
        assert healthy.requests[-1].headers["mcp-session-id"] == SESSION_ID
        assert listing.headers["mcp-session-id"] == SESSION_ID
        assert listing.headers["mcp-protocol-version"] == "2025-06-18"
        assert "mcp-session-id" not in posts[0].headers
        assert json.loads(posts[0].content) == {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": INITIALIZE_PARAMS,
        }
        assert json.loads(posts[1].content) == {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        assert json.loads(listing.content) == {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }

    def test_a_json_answer_is_read_like_an_sse_one(self, httpx_mock: HTTPXMock) -> None:
        ready(httpx_mock)
        step = run(httpx_mock, fake=FakeMcp(json_response=True)).observation.transport.authenticated

        assert step.outcome == "ok"
        assert step.served_tool_count == 2

    @pytest.mark.parametrize(
        "content_type",
        ["application/json; charset=utf-8", "text/event-stream; charset=utf-8"],
        ids=["json", "sse"],
    )
    def test_a_media_type_with_parameters_is_still_the_answer(
        self, httpx_mock: HTTPXMock, content_type: str
    ) -> None:
        """A charset parameter, as a server or a proxy may add one, does not
        turn the answer into something the probe cannot read."""
        ready(httpx_mock)
        answer = {"jsonrpc": "2.0", "id": 2, "result": tools_listing(("a", "b", "c"))}
        content = sse(answer) if content_type.startswith(SSE_TYPE) else json.dumps(answer).encode()
        fake = FakeMcp()
        fake.list_answer = lambda _request: httpx.Response(
            200, headers={"Content-Type": content_type}, content=content
        )
        step = run(httpx_mock, fake=fake).observation.transport.authenticated

        assert (step.outcome, step.served_tool_count) == ("ok", 3)

    def test_an_answer_without_a_content_type_is_a_protocol_error(
        self, httpx_mock: HTTPXMock
    ) -> None:
        ready(httpx_mock)
        answer = {"jsonrpc": "2.0", "id": 2, "result": tools_listing()}
        fake = FakeMcp()
        fake.list_answer = lambda _request: httpx.Response(200, content=sse(answer))
        step = run(httpx_mock, fake=fake).observation.transport.authenticated

        assert (step.status, step.outcome) == ("observed", "protocol_error")

    def test_the_answer_needs_no_event_line_and_no_space_after_the_colon(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """An event without an ``event:`` line is a ``message`` event, the
        space after ``data:`` is optional, and the stream need not end in a
        blank line (SSE: event dispatch at end of stream)."""
        ready(httpx_mock)
        answer = {"jsonrpc": "2.0", "id": 2, "result": tools_listing(("a", "b", "c"))}
        fake = FakeMcp()
        fake.list_answer = lambda _request: httpx.Response(
            200,
            headers={"Content-Type": SSE_TYPE},
            content=f"data:{json.dumps(answer)}".encode(),
        )
        step = run(httpx_mock, fake=fake).observation.transport.authenticated

        assert (step.outcome, step.served_tool_count) == ("ok", 3)

    def test_the_answer_may_follow_other_events_on_the_stream_the_sdk_server_writes(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """One POST's stream is CRLF-separated (sse-starlette's default, which the
        SDK server keeps) and may carry a priming event, a keep-alive, and a
        notification before the response: only ``message`` events are read,
        empty data is no payload, and a payload spans every ``data:`` line."""
        ready(httpx_mock)
        notification = {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}
        stream = "".join(
            [
                "id: 7\r\nretry: 1000\r\ndata: \r\n\r\n",
                "event: ping\r\ndata: keepalive\r\n\r\n",
                f"event: message\r\ndata: {json.dumps(notification)}\r\n\r\n",
                'event:message\r\ndata: {"jsonrpc": "2.0", "id": 2,\r\n',
                f'data: "result": {json.dumps(tools_listing(("a", "b", "c")))}}}\r\n\r\n',
            ]
        )
        fake = FakeMcp()
        fake.list_answer = lambda _request: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=stream.encode()
        )
        step = run(httpx_mock, fake=fake).observation.transport.authenticated

        assert (step.status, step.outcome) == ("observed", "ok")
        assert step.served_tool_count == 3

    def test_the_served_surface_hashes_as_the_descriptor_hashes_the_same_server(
        self, httpx_mock: HTTPXMock, corpus: Path
    ) -> None:
        """One hashing on both sides of ``transport_surface_match``: a real
        server's ``tools/list``, serialised as the SDK puts it on the wire,
        hashes to the descriptor computed from the server object."""
        server = build_server(corpus, http=HttpConfig(allow_insecure=True))
        listed = ListToolsResult(tools=asyncio.run(server.list_tools()))
        wire = listed.model_dump(by_alias=True, mode="json", exclude_none=True)
        descriptor = describe_tool_surface(corpus)
        ready(
            httpx_mock,
            payload=attestation(
                tool_surface_sha256=descriptor.schema_sha256, tool_count=descriptor.tool_count
            ),
        )
        document = run(httpx_mock, fake=FakeMcp(wire))
        step = document.observation.transport.authenticated

        assert step.served_tool_surface_sha256 == descriptor.schema_sha256
        assert step.served_tool_count == descriptor.tool_count
        assert document.state.comparisons.transport_surface_match == "true"
        assert document.state.hosted_state == "available"

    def test_a_surface_that_differs_from_the_attested_one_is_unavailable(
        self, httpx_mock: HTTPXMock
    ) -> None:
        ready(httpx_mock, payload=attestation(tool_count=3))
        document = run(httpx_mock, fake=FakeMcp(tools_listing(("a", "b", "c"))))

        assert document.observation.transport.authenticated.served_tool_count == 3
        assert document.state.comparisons.transport_surface_match == "false"
        assert document.state.hosted_state == "unavailable"

    @pytest.mark.parametrize(
        "answer",
        [
            httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=b""),
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "error": {"code": -32601}}),
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {"tools": "none"}}),
            httpx.Response(200, json={"jsonrpc": "2.0", "id": 99, "result": tools_listing()}),
            httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>"),
            httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"{"),
        ],
        ids=["empty-stream", "rpc-error", "not-a-listing", "other-id", "html", "truncated"],
    )
    def test_an_answer_that_is_not_a_listing_is_a_protocol_error(
        self, httpx_mock: HTTPXMock, answer: httpx.Response
    ) -> None:
        ready(httpx_mock)
        fake = FakeMcp()
        fake.list_answer = lambda _request: answer
        document = run(httpx_mock, fake=fake)
        step = document.observation.transport.authenticated

        assert step.status == "observed"
        assert step.outcome == "protocol_error"
        assert step.served_tool_count is None
        assert document.state.hosted_state == "unavailable"

    @pytest.mark.parametrize("status", [400, 404, 500, 503])
    def test_an_http_error_on_the_listing_is_an_observed_failure(
        self, httpx_mock: HTTPXMock, status: int
    ) -> None:
        ready(httpx_mock)
        fake = FakeMcp()
        fake.list_answer = lambda _request: httpx.Response(status, json={})
        step = run(httpx_mock, fake=fake).observation.transport.authenticated

        assert (step.status, step.outcome) == ("observed", f"http_{status}")

    def test_an_initialize_that_is_not_accepted_is_an_observed_failure(
        self, httpx_mock: HTTPXMock
    ) -> None:
        ready(httpx_mock)
        answers = iter([httpx.Response(401, headers={"WWW-Authenticate": CHALLENGE})])

        def gateway(_request: httpx.Request) -> httpx.Response:
            answer = next(answers, None)
            return answer if answer is not None else httpx.Response(500, content=b"upstream")

        httpx_mock.add_callback(gateway, url=MCP_URL, is_reusable=True)
        step = run(httpx_mock).observation.transport.authenticated

        assert (step.status, step.outcome) == ("observed", "http_500")

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (500, ("observed", None, "http_500")),
            (401, ("unobserved", "probe_credential_rejected", None)),
        ],
        ids=["subject-http-error", "credential-rejected"],
    )
    def test_a_refused_initialized_notification_stops_before_listing(
        self,
        httpx_mock: HTTPXMock,
        status: int,
        expected: tuple[str, str | None, str | None],
    ) -> None:
        ready(httpx_mock)

        class RefuseInitialized(FakeMcp):
            def _in_session(
                self, request: httpx.Request, message: dict[str, Any]
            ) -> httpx.Response:
                if message["method"] == "notifications/initialized":
                    return httpx.Response(status)
                return super()._in_session(request, message)

        fake = RefuseInitialized()
        step = run(httpx_mock, fake=fake).observation.transport.authenticated

        assert (step.status, step.reason, step.outcome) == expected
        assert fake.methods() == [
            "initialize",
            "initialize",
            "notifications/initialized",
        ]
        assert not any(request.method == "DELETE" for request in fake.requests)

    def test_an_initialize_result_outside_the_mcp_schema_is_a_protocol_error(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Step (b) promises an MCP initialize, not merely a JSON-RPC result."""
        ready(httpx_mock)

        class InvalidInitialize(FakeMcp):
            def _authenticated(self, request: httpx.Request) -> httpx.Response:
                message = json.loads(request.content)
                if message["method"] == "initialize":
                    return self._reply(message, {})
                return super()._authenticated(request)

        step = run(httpx_mock, fake=InvalidInitialize()).observation.transport.authenticated

        assert (step.status, step.outcome) == ("observed", "protocol_error")
        assert step.served_tool_surface_sha256 is None
        assert step.served_tool_count is None

    def test_a_network_failure_mid_handshake_is_the_observers(self, httpx_mock: HTTPXMock) -> None:
        ready(httpx_mock)
        fake = FakeMcp()
        httpx_mock.add_callback(fake, url=MCP_URL)
        httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=MCP_URL)
        absent_discovery(httpx_mock)
        with httpx.Client() as client:
            step = probe(
                settings(), client=client, checkout=checkout(), clock=clock
            ).observation.transport.authenticated

        assert (step.status, step.reason) == ("unobserved", "timeout")

    def test_a_credential_rejected_on_initialized_is_the_observers_failure(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """The observer-failure rule covers every request in authenticated step (b)."""
        ready(httpx_mock)

        class RejectInitialized(FakeMcp):
            def _in_session(
                self, request: httpx.Request, message: dict[str, Any]
            ) -> httpx.Response:
                if message["method"] == "notifications/initialized":
                    return self._refuse((401, CHALLENGE))
                return super()._in_session(request, message)

        document = run(httpx_mock, fake=RejectInitialized())
        step = document.observation.transport.authenticated

        assert (step.status, step.reason) == ("unobserved", "probe_credential_rejected")
        assert step.outcome is None
        assert document.state.hosted_state == "unknown"


class TestOAuthDiscovery:
    def _valid(self, **overrides: Any) -> dict[str, Any]:
        document: dict[str, Any] = {
            "resource": MCP_URL,
            "authorization_servers": [f"{AUTHKIT}/"],
            "bearer_methods_supported": ["header"],
        }
        document.update(overrides)
        return document

    def test_the_metadata_url_is_the_rfc_9728_path_suffixed_form(self) -> None:
        assert protected_resource_metadata_url(MCP_URL) == DISCOVERY_URL
        assert protected_resource_metadata_url("https://h.example:8443/x/mcp") == (
            "https://h.example:8443/.well-known/oauth-protected-resource/x/mcp"
        )

    @pytest.mark.parametrize(
        ("resource", "path"),
        [("https://h.example/x/mcp/", "/x/mcp"), ("https://h.example/X", "/X")],
        ids=["trailing-slash-dropped", "last-character-kept"],
    )
    def test_the_resource_path_is_inserted_verbatim_but_for_trailing_slashes(
        self, resource: str, path: str
    ) -> None:
        assert protected_resource_metadata_url(resource) == (
            f"https://h.example/.well-known/oauth-protected-resource{path}"
        )

    def test_the_authorization_server_urls_follow_rfc_8414_then_openid(self) -> None:
        assert authorization_server_metadata_urls(f"{AUTHKIT}/") == (
            AUTHKIT_METADATA_URL,
            AUTHKIT_OPENID_URL,
        )
        assert authorization_server_metadata_urls("https://as.example/issuer1") == (
            "https://as.example/.well-known/oauth-authorization-server/issuer1",
            "https://as.example/issuer1/.well-known/openid-configuration",
        )
        assert authorization_server_metadata_urls("https://as.example/X/") == (
            "https://as.example/.well-known/oauth-authorization-server/X",
            "https://as.example/X/.well-known/openid-configuration",
        )

    def test_a_404_is_absent(self, httpx_mock: HTTPXMock, healthy: FakeMcp) -> None:
        discovery = run(httpx_mock).observation.transport.oauth_discovery

        assert discovery.verdict == "absent"
        assert discovery.document_sha256 is None
        assert discovery.invalid_reason is None

    def test_a_consistent_document_is_valid_with_its_hash(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        raw = json.dumps(self._valid()).encode()
        httpx_mock.add_response(url=DISCOVERY_URL, content=raw)
        httpx_mock.add_response(url=AUTHKIT_METADATA_URL, json={"issuer": AUTHKIT})
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert discovery.verdict == "valid"
        assert discovery.invalid_reason is None
        assert discovery.document_sha256 == hashlib.sha256(raw).hexdigest()
        for url in (DISCOVERY_URL, AUTHKIT_METADATA_URL):
            request = httpx_mock.get_request(url=url)
            assert request is not None and request.method == "GET", url

    @pytest.mark.parametrize(
        ("listed", "issuer"),
        [(AUTHKIT, f"{AUTHKIT}/"), ("https://as.example/X", "https://as.example/X")],
        ids=["slash-on-the-issuer-side", "path-verbatim"],
    )
    def test_the_issuer_is_the_listed_server_with_or_without_a_trailing_slash(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp, listed: str, issuer: str
    ) -> None:
        """RFC 8414 §3.3: one identifier however the slash falls; the rest
        of the path is compared verbatim."""
        httpx_mock.add_response(url=DISCOVERY_URL, json=self._valid(authorization_servers=[listed]))
        httpx_mock.add_response(
            url=authorization_server_metadata_urls(listed)[0], json={"issuer": issuer}
        )
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert (discovery.verdict, discovery.invalid_reason) == ("valid", None)

    def test_a_valid_document_beside_an_oauth_process_is_consistent(
        self, httpx_mock: HTTPXMock
    ) -> None:
        ready(
            httpx_mock,
            payload=attestation(credential_modes=["token", "oauth"], oauth_configured=True),
        )
        httpx_mock.add_response(url=DISCOVERY_URL, json=self._valid())
        httpx_mock.add_response(url=AUTHKIT_METADATA_URL, json={"issuer": AUTHKIT})
        document = run(httpx_mock, fake=FakeMcp(), discovery=False)

        assert document.observation.process.oauth_configured is True
        assert document.state.comparisons.oauth_discovery_consistent == "true"

    def test_the_openid_configuration_is_the_fallback_for_the_issuer(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        httpx_mock.add_response(url=DISCOVERY_URL, json=self._valid())
        httpx_mock.add_response(url=AUTHKIT_METADATA_URL, status_code=404)
        httpx_mock.add_response(url=AUTHKIT_OPENID_URL, json={"issuer": AUTHKIT})
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert discovery.verdict == "valid"

    def test_a_resource_that_is_not_the_canonical_url_is_invalid(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        """The known failure: a stale LOVSPOR_PUBLIC_URL served verbatim (ADR:947-955)."""
        stale = self._valid(resource="https://lovspor.bartoszkobylinski.com/mcp")
        httpx_mock.add_response(url=DISCOVERY_URL, json=stale)
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert (discovery.verdict, discovery.invalid_reason) == ("invalid", "resource_mismatch")
        assert discovery.document_sha256 is not None
        assert not httpx_mock.get_requests(url=AUTHKIT_METADATA_URL)

    @pytest.mark.parametrize(
        "resource", [MCP_URL + "/", "http://lovspor.no/mcp", "https://LOVSPOR.no/mcp", None]
    )
    def test_the_resource_must_match_scheme_host_and_path_exactly(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp, resource: str | None
    ) -> None:
        document = self._valid()
        document["resource"] = resource
        httpx_mock.add_response(url=DISCOVERY_URL, json=document)
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert discovery.invalid_reason == "resource_mismatch"

    @pytest.mark.parametrize("servers", [[], None])
    def test_no_authorization_server_is_invalid(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp, servers: list[str] | None
    ) -> None:
        document = self._valid()
        if servers is None:
            del document["authorization_servers"]
        else:
            document["authorization_servers"] = servers
        httpx_mock.add_response(url=DISCOVERY_URL, json=document)
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert discovery.invalid_reason == "no_authorization_server"

    def test_an_issuer_whose_metadata_cannot_be_fetched_is_invalid(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        httpx_mock.add_response(url=DISCOVERY_URL, json=self._valid())
        httpx_mock.add_exception(httpx.ConnectError("dns"), url=AUTHKIT_METADATA_URL)
        httpx_mock.add_response(url=AUTHKIT_OPENID_URL, status_code=500)
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert discovery.invalid_reason == "issuer_unreachable"

    def test_metadata_without_an_issuer_is_unreachable_metadata(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        httpx_mock.add_response(url=DISCOVERY_URL, json=self._valid())
        httpx_mock.add_response(url=AUTHKIT_METADATA_URL, content=b"not json")
        httpx_mock.add_response(url=AUTHKIT_OPENID_URL, json={"token_endpoint": "x"})
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert discovery.invalid_reason == "issuer_unreachable"

    def test_an_issuer_that_names_another_server_is_invalid(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        httpx_mock.add_response(url=DISCOVERY_URL, json=self._valid())
        httpx_mock.add_response(url=AUTHKIT_METADATA_URL, json={"issuer": "https://other.example"})
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert discovery.invalid_reason == "issuer_mismatch"

    def test_every_listed_issuer_is_checked(self, httpx_mock: HTTPXMock, healthy: FakeMcp) -> None:
        second = "https://second.authkit.app"
        httpx_mock.add_response(
            url=DISCOVERY_URL, json=self._valid(authorization_servers=[AUTHKIT, second])
        )
        httpx_mock.add_response(url=AUTHKIT_METADATA_URL, json={"issuer": AUTHKIT})
        httpx_mock.add_response(
            url=f"{second}/.well-known/oauth-authorization-server", json={"issuer": AUTHKIT}
        )
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert discovery.invalid_reason == "issuer_mismatch"

    @pytest.mark.parametrize(
        "raw",
        [
            b"not json",
            b"[]",
            b'{"resource": "https://lovspor.no/mcp", "authorization_servers": "x"}',
        ],
        ids=["text", "array", "servers-not-a-list"],
    )
    def test_a_document_that_is_not_rfc_9728_metadata_is_malformed(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp, raw: bytes
    ) -> None:
        httpx_mock.add_response(url=DISCOVERY_URL, content=raw)
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert (discovery.verdict, discovery.invalid_reason) == ("invalid", "malformed")
        assert discovery.document_sha256 == hashlib.sha256(raw).hexdigest()

    def test_no_answer_is_unobserved(self, httpx_mock: HTTPXMock, healthy: FakeMcp) -> None:
        httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=DISCOVERY_URL)
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert discovery.verdict == "unobserved"
        assert discovery.document_sha256 is None

    def test_an_answer_that_is_neither_the_document_nor_404_is_unobserved(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        """A 5xx is an answer without a document: not absent (the route may
        exist), not a document to judge — so nothing was observed."""
        httpx_mock.add_response(url=DISCOVERY_URL, status_code=502)
        discovery = run(httpx_mock, discovery=False).observation.transport.oauth_discovery

        assert discovery.verdict == "unobserved"


class TestDocument:
    def test_observed_at_is_the_injected_clock_once_per_run_in_rfc_3339_utc(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        observation = run(httpx_mock).observation

        assert observation.process.observed_at == OBSERVED_AT
        assert observation.transport.observed_at == OBSERVED_AT

    def test_the_clock_is_read_in_utc_whatever_zone_it_or_the_process_reports(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp, kolkata_local_time: None
    ) -> None:
        oslo = MOMENT.astimezone(timezone(timedelta(hours=2)))
        absent_discovery(httpx_mock)
        assert datetime.now().astimezone().utcoffset() == timedelta(hours=5, minutes=30)
        with httpx.Client() as client:
            document = probe(settings(), client=client, checkout=checkout(), clock=lambda: oslo)

        assert document.observation.process.observed_at == OBSERVED_AT

    @pytest.mark.parametrize(
        "moment",
        [MOMENT.replace(tzinfo=None), MOMENT.replace(tzinfo=Undecided())],
        ids=["no-tzinfo", "tzinfo-without-an-offset"],
    )
    def test_a_clock_that_is_not_aware_is_refused(
        self, httpx_mock: HTTPXMock, moment: datetime
    ) -> None:
        """Aware, in Python's sense: a tzinfo whose utcoffset() is not None."""
        with httpx.Client() as client, pytest.raises(ProbeError) as refusal:
            probe(settings(), client=client, checkout=checkout(), clock=lambda: moment)

        assert str(refusal.value) == "the probe clock must be timezone-aware"
        assert not httpx_mock.get_requests()

    @pytest.mark.parametrize("observer", ["release-probe", "drift-timer"])
    def test_the_observer_is_the_settings(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp, observer: str
    ) -> None:
        observation = run(httpx_mock, probe_settings=settings(observer=observer)).observation

        assert observation.process.observer == observer
        assert observation.transport.observer == observer

    def test_the_state_is_the_shared_derivation_and_the_bytes_round_trip(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        document = run(httpx_mock)

        assert document.schema_version == "1"
        assert document.state == derive_state(document.observation, checkout())
        assert document.state.checkout == checkout()
        assert parse_capabilities(document_bytes(document)) == document

    def test_the_secret_is_in_no_document_log_or_error(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        document = run(httpx_mock)

        assert TOKEN not in document_bytes(document).decode()
        assert TOKEN not in caplog.text
        assert TOKEN not in repr(settings())
        assert TOKEN not in str(settings())

    def test_the_secret_is_sent_only_as_the_bearer_of_step_b(
        self, httpx_mock: HTTPXMock, healthy: FakeMcp
    ) -> None:
        run(httpx_mock)
        bearers = [
            request.headers.get("authorization")
            for request in httpx_mock.get_requests()
            if "authorization" in request.headers
        ]

        assert bearers
        assert set(bearers) == {f"Bearer {TOKEN}"}
        for request in httpx_mock.get_requests():
            assert (str(request.url) == MCP_URL) or "authorization" not in request.headers

    def test_the_canonical_url_derives_from_the_site_origin(self) -> None:
        assert f"{SITE_ORIGIN}/mcp" == CANONICAL_MCP_URL
        assert ProbeSettings().public_mcp_url == CANONICAL_MCP_URL
        assert ProbeSettings().readiness_url == READINESS_URL
        assert ProbeSettings().probe_token is None

    @pytest.mark.parametrize("url", ["lovspor.no/mcp", "ftp://lovspor.no/mcp", ""])
    def test_a_target_outside_http_is_refused_at_construction(self, url: str) -> None:
        with pytest.raises(ValidationError):
            settings(public_mcp_url=url)
        with pytest.raises(ValidationError):
            settings(readiness_url=url)


class TestNoClock:
    def test_the_probe_reads_its_clock_only_through_now(self) -> None:
        """observed_at is the injected clock's value (ADR:995-1001); the module
        never calls a clock of its own."""
        tree = ast.parse(Path(probe_module.__file__).read_text(encoding="utf-8"))
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}

        assert not calls & {"now", "utcnow", "today", "time", "monotonic"}
        assert "time" not in imported
