"""The public MCP path and the readiness payload, as ``pytest-httpx`` answers them.

Shared by the release-probe and CLI tests. ``FakeMcp`` is a transport
callback speaking the Streamable HTTP wire contract the SDK's server
enforces (``mcp/server/streamable_http.py``): both media types in
``Accept``, JSON ``Content-Type``, the session id minted on ``initialize``
and required after it, ``202`` for a notification, an SSE body carrying
one ``message`` event per answer. Only transport is faked here; the
probe's own logic is never mocked.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
from pytest_httpx import HTTPXMock

from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.probe import CANONICAL_MCP_URL
from tests.unit.site_fixtures import ENVIRONMENT, INTERPRETER, SURFACE, TREE

READINESS_URL = "http://127.0.0.1:8000/readyz"
MCP_URL = CANONICAL_MCP_URL
DISCOVERY_URL = f"{SITE_ORIGIN}/.well-known/oauth-protected-resource/mcp"
AUTHKIT = "https://vigilant-beacon-78.authkit.app"
AUTHKIT_METADATA_URL = f"{AUTHKIT}/.well-known/oauth-authorization-server"
AUTHKIT_OPENID_URL = f"{AUTHKIT}/.well-known/openid-configuration"
TOKEN = "lsp_site_probe_test_token"
SESSION_ID = "3f2a9c1e-session"
CHALLENGE = 'Bearer error="invalid_token", error_description="Authentication required"'


def attestation(*, ready: bool = True, **overrides: Any) -> dict[str, Any]:
    """The ``/readyz`` payload as ``lovspor.attestation`` serves it."""
    payload: dict[str, Any] = {
        "status": "ready" if ready else "unavailable",
        "schema_version": "1",
        "ready": ready,
        "runtime_identity": {
            "tree_sha256": TREE,
            "environment_sha256": ENVIRONMENT,
            "interpreter": INTERPRETER,
        },
        "tool_surface_sha256": SURFACE,
        "tool_count": 2,
        "credential_modes": ["token"],
        "oauth_configured": False,
    }
    payload.update(overrides)
    return payload


def sse(message: dict[str, Any]) -> bytes:
    return f"event: message\ndata: {json.dumps(message)}\n\n".encode()


def tools_listing(names: tuple[str, ...] = ("a", "b")) -> dict[str, Any]:
    return {
        "tools": [
            {"name": name, "description": "æ", "inputSchema": {"type": "object"}} for name in names
        ]
    }


class FakeMcp:
    """The public ``/mcp`` path as the SDK server answers it, behind Caddy."""

    def __init__(
        self,
        listing: dict[str, Any] | None = None,
        *,
        unauthenticated: tuple[int, str | None] = (401, CHALLENGE),
        rejection: tuple[int, str | None] = (401, CHALLENGE),
        accepted_token: str = TOKEN,
        json_response: bool = False,
    ) -> None:
        self.listing = listing if listing is not None else tools_listing()
        self.unauthenticated = unauthenticated
        self.rejection = rejection
        self.accepted_token = accepted_token
        self.json_response = json_response
        self.requests: list[httpx.Request] = []
        self.list_answer: Callable[[httpx.Request], httpx.Response] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        authorization = request.headers.get("authorization")
        if authorization is None:
            return self._refuse(self.unauthenticated)
        if authorization != f"Bearer {self.accepted_token}":
            return self._refuse(self.rejection)
        if request.method == "DELETE":
            return httpx.Response(200)
        return self._authenticated(request)

    @staticmethod
    def _refuse(answer: tuple[int, str | None]) -> httpx.Response:
        status, challenge = answer
        headers = {} if challenge is None else {"WWW-Authenticate": challenge}
        return httpx.Response(status, headers=headers, json={"error": "invalid_token"})

    @staticmethod
    def _transport_refusal(request: httpx.Request) -> httpx.Response | None:
        """What the SDK transport refuses before reading the message."""
        accept = request.headers.get("accept", "")
        if "application/json" not in accept or "text/event-stream" not in accept:
            return httpx.Response(406, json={"error": "Not Acceptable"})
        if not request.headers.get("content-type", "").startswith("application/json"):
            return httpx.Response(415, json={"error": "Unsupported Media Type"})
        return None

    def _authenticated(self, request: httpx.Request) -> httpx.Response:
        refusal = self._transport_refusal(request)
        if refusal is not None:
            return refusal
        message = json.loads(request.content)
        if message["method"] == "initialize":
            return self._reply(message, {"protocolVersion": "2025-06-18", "capabilities": {}})
        if request.headers.get("mcp-session-id") != SESSION_ID:
            return httpx.Response(400, json={"error": "Bad Request: Missing session ID"})
        return self._in_session(request, message)

    def _in_session(self, request: httpx.Request, message: dict[str, Any]) -> httpx.Response:
        if message["method"] == "notifications/initialized":
            return httpx.Response(202, headers={"Mcp-Session-Id": SESSION_ID})
        if message["method"] != "tools/list":
            return httpx.Response(400, json={"error": f"unexpected method {message['method']}"})
        if self.list_answer is not None:
            return self.list_answer(request)
        return self._reply(message, self.listing)

    def _reply(self, message: dict[str, Any], result: dict[str, Any]) -> httpx.Response:
        body = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        headers = {"Mcp-Session-Id": SESSION_ID}
        if self.json_response:
            return httpx.Response(200, headers=headers, json=body)
        headers["Content-Type"] = "text/event-stream"
        return httpx.Response(200, headers=headers, content=sse(body))

    def methods(self) -> list[str]:
        return [
            json.loads(request.content)["method"]
            for request in self.requests
            if request.method == "POST"
        ]


def install(httpx_mock: HTTPXMock, fake: FakeMcp) -> FakeMcp:
    httpx_mock.add_callback(fake, url=MCP_URL, is_reusable=True)
    return fake


def ready(
    httpx_mock: HTTPXMock, *, status: int = 200, payload: dict[str, Any] | None = None
) -> None:
    body = attestation(ready=status == 200) if payload is None else payload
    httpx_mock.add_response(url=READINESS_URL, status_code=status, json=body)


def absent_discovery(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=DISCOVERY_URL, status_code=404)
