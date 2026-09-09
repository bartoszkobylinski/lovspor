"""The release probe: the observation layer of ``deployment-capabilities.json``.

The real-world twin of ``lovspor.site.fixture``: the same closed schema,
the same ``derive_state``, but the two records are observed, not
synthesised (ADR-0014 Decision 4). ``probe`` observes two subjects, each
independently, and never records a guess:

* **process** — ``GET`` the loopback ``/readyz``. A ``200`` or ``503``
  whose payload is the runtime attestation (``lovspor.attestation``) is
  ``observed`` with the attestation verbatim and ``ready`` from it; any
  other payload is ``unobserved, schema_invalid``; no answer is
  ``network`` or ``timeout``; any other status is ``http_<code>``.
* **transport** — the path a client dials. Step (a) posts an
  unauthenticated ``initialize`` to the public ``/mcp`` and records the
  status and ``WWW-Authenticate`` challenge verbatim (ADR:826-846). Step
  (b) runs only after (a) answered the documented Bearer ``401``: MCP
  ``initialize`` + ``notifications/initialized`` + ``tools/list`` over
  Streamable HTTP with the probe credential, the listed surface hashed by
  ``describe_listed_tools`` — the descriptor's own hashing, so
  ``transport_surface_match`` compares one canonical form with itself.
  The RFC 9728 document is read in the same pass and validated
  semantically (ADR:947-993): ``resource`` equal to the canonical URL,
  ``authorization_servers`` non-empty, each entry's RFC 8414 metadata
  fetchable with ``issuer`` naming the entry.

**The rule of the observation layer** (ADR:737-739): a failure
attributable to the observer — its credential, its network, its tooling
— is ``unobserved`` with its reason; only a failure attributable to the
subject is an observed failure. A missing credential is
``probe_credential_missing``; a ``401``/``403`` on (b) after (a) observed
the Bearer ``401`` is ``probe_credential_rejected``; (a) not having
answered the Bearer ``401`` leaves (b) ``not_attempted``.

Two boundaries, both injected: the HTTP client, and the clock — read
**once per run**, before the first request, so both records carry the
same ``observed_at`` (a run is seconds long; one instant names it). The
module calls no clock of its own; the package's clock scan
(``tests/unit/test_site_fingerprint.py``) keeps it that way, which is
why the injected callable is named ``clock`` and not ``now``. The secret is used exactly once, as
the Bearer of step (b); it is never logged, never raised and never
written — only outcomes are recorded (ADR:884-892).

Step (b) speaks the wire protocol directly over the injected client
rather than through the SDK's async client: the transport contract the
server enforces (``mcp/server/streamable_http.py``) is small — both
media types in ``Accept``, JSON ``Content-Type``, the ``Mcp-Session-Id``
minted on ``initialize`` and required after it, ``MCP-Protocol-Version``
once negotiated, ``202`` for a notification, one ``message`` event per
answer on an SSE body — and a synchronous, bounded exchange over one
client is what a probe with a timeout budget can reason about.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from mcp.types import (
    LATEST_PROTOCOL_VERSION,
    InitializeResult,
    JSONRPCMessage,
    JSONRPCResponse,
    ListToolsResult,
)
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_validator

from lovspor import __version__
from lovspor.attestation import ProcessAttestation
from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.capabilities import (
    AuthenticatedReason,
    AuthenticatedStep,
    CapabilityDocument,
    Checkout,
    InvalidReason,
    OAuthDiscovery,
    Observation,
    Observer,
    ProcessRecord,
    RuntimeIdentity,
    TransportRecord,
    UnauthenticatedStep,
    bearer_401,
    derive_state,
)
from lovspor.site.errors import ProbeError
from lovspor.tool_surface import describe_listed_tools

CANONICAL_MCP_URL = f"{SITE_ORIGIN}/mcp"
"""The one constant the probe targets and the discovery validator expects (ADR:955-958)."""

DEFAULT_READINESS_URL = "http://127.0.0.1:8000/readyz"
"""Loopback readiness: ``lovspor-mcp.service`` binds ``127.0.0.1:8000``."""

PROTECTED_RESOURCE_WELL_KNOWN = "/.well-known/oauth-protected-resource"
AUTHORIZATION_SERVER_WELL_KNOWN = "/.well-known/oauth-authorization-server"
OPENID_CONFIGURATION_WELL_KNOWN = "/.well-known/openid-configuration"

_OK, _ACCEPTED, _UNAUTHORIZED, _FORBIDDEN, _NOT_FOUND, _UNAVAILABLE = 200, 202, 401, 403, 404, 503
_ACCEPT = "application/json, text/event-stream"
_JSON = "application/json"
_SSE = "text/event-stream"
# A stream's lines end in CRLF, LF or CR; the SDK server writes CRLF
# (sse-starlette's default), so an event boundary is not two bare LFs.
_LINE_ENDING = re.compile(r"\r\n?")

NoAnswer = Literal["network", "timeout"]


class ProbeSettings(BaseModel):
    """Where the probe looks, with what, for how long, and as whom."""

    model_config = ConfigDict(frozen=True)

    readiness_url: str = DEFAULT_READINESS_URL
    public_mcp_url: str = CANONICAL_MCP_URL
    probe_token: SecretStr | None = None
    timeout_seconds: float = 10.0
    observer: Observer = "release-probe"

    @field_validator("readiness_url", "public_mcp_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"not an http(s) URL: {value!r}")
        return value


@dataclass(frozen=True)
class _Run:
    settings: ProbeSettings
    client: httpx.Client
    observed_at: str

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
        return self.client.build_request(
            method, url, timeout=self.settings.timeout_seconds, **kwargs
        )


def _rfc3339(moment: datetime) -> str:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ProbeError("the probe clock must be timezone-aware")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _send(client: httpx.Client, request: httpx.Request) -> httpx.Response | NoAnswer:
    """One answer, or the observer's reason for having none."""
    try:
        return client.send(request)
    except httpx.TimeoutException:
        return "timeout"
    except httpx.RequestError:
        return "network"


# --- process ---------------------------------------------------------------


def _unobserved_process(reason: str, run: _Run) -> ProcessRecord:
    return ProcessRecord.model_validate(
        {
            "status": "unobserved",
            "reason": reason,
            "observed_at": run.observed_at,
            "observer": run.settings.observer,
            "ready": None,
            "runtime_identity": None,
            "tool_surface_sha256": None,
            "tool_count": None,
            "credential_modes": None,
            "oauth_configured": None,
        }
    )


def _attestation(response: httpx.Response) -> ProcessAttestation | None:
    try:
        return ProcessAttestation.model_validate(response.json())
    except (ValueError, ValidationError):
        return None


def _observed_process(attestation: ProcessAttestation, run: _Run) -> ProcessRecord:
    """The attestation verbatim, under the record's stricter field shapes."""
    return ProcessRecord(
        status="observed",
        reason=None,
        observed_at=run.observed_at,
        observer=run.settings.observer,
        ready=attestation.ready,
        runtime_identity=RuntimeIdentity.model_validate(attestation.runtime_identity.model_dump()),
        tool_surface_sha256=attestation.tool_surface_sha256,
        tool_count=attestation.tool_count,
        credential_modes=attestation.credential_modes,
        oauth_configured=attestation.oauth_configured,
    )


def observe_process(run: _Run) -> ProcessRecord:
    """The loopback ``/readyz`` and the attestation it carries, verbatim.

    ``ready`` is the payload's and must agree with the status it came on:
    a ``200`` saying ``ready: false`` is not the schema the process
    promised, and the record never guesses which of the two to believe.
    """
    answer = _send(run.client, run.request("GET", run.settings.readiness_url))
    if isinstance(answer, str):
        return _unobserved_process(answer, run)
    if answer.status_code not in (_OK, _UNAVAILABLE):
        return _unobserved_process(f"http_{answer.status_code}", run)
    attestation = _attestation(answer)
    if attestation is None or attestation.ready != (answer.status_code == _OK):
        return _unobserved_process("schema_invalid", run)
    try:
        return _observed_process(attestation, run)
    except ValidationError:
        return _unobserved_process("schema_invalid", run)


# --- transport: the MCP exchange of step (b) --------------------------------


def _step_unobserved(reason: AuthenticatedReason) -> AuthenticatedStep:
    return AuthenticatedStep(
        status="unobserved",
        reason=reason,
        outcome=None,
        served_tool_surface_sha256=None,
        served_tool_count=None,
    )


def _step_failed(outcome: str) -> AuthenticatedStep:
    return AuthenticatedStep.model_validate(
        {
            "status": "observed",
            "reason": None,
            "outcome": outcome,
            "served_tool_surface_sha256": None,
            "served_tool_count": None,
        }
    )


def _sse_data(body: str) -> list[str]:
    """The ``data`` of every ``message`` event on one event stream.

    A field value loses the one space that may follow its colon, as the
    SSE specification says, and no other character: the payload is the
    server's bytes, joined across ``data:`` lines with LF.
    """
    payloads: list[str] = []
    for event in _LINE_ENDING.sub("\n", body).split("\n\n"):
        lines = [line for line in event.splitlines() if line]
        kind = next((line[6:].strip() for line in lines if line.startswith("event:")), "message")
        data = "\n".join(line[5:].removeprefix(" ") for line in lines if line.startswith("data:"))
        if kind == "message" and data:
            payloads.append(data)
    return payloads


def _rpc_result(response: httpx.Response, request_id: int) -> dict[str, Any] | None:
    """The ``result`` answering ``request_id``, or ``None`` for anything else."""
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == _JSON:
        payloads = [response.text]
    elif content_type == _SSE:
        payloads = _sse_data(response.text)
    else:
        return None
    for payload in payloads:
        try:
            message = JSONRPCMessage.model_validate_json(payload).root
        except ValidationError:
            return None
        if isinstance(message, JSONRPCResponse) and message.id == request_id:
            return message.result
    return None


@dataclass
class _McpSession:
    """One client session over the injected client: headers, ids, the session id."""

    run: _Run
    token: SecretStr
    session_id: str | None = None
    protocol_version: str | None = None
    next_id: int = field(default=0)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": _ACCEPT,
            "Content-Type": _JSON,
            "Authorization": f"Bearer {self.token.get_secret_value()}",
        }
        if self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version is not None:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _post(self, body: dict[str, Any]) -> httpx.Response | AuthenticatedStep:
        answer = _send(
            self.run.client,
            self.run.request(
                "POST", self.run.settings.public_mcp_url, json=body, headers=self._headers()
            ),
        )
        if isinstance(answer, str):
            return _step_unobserved(answer)
        if answer.status_code in (_UNAUTHORIZED, _FORBIDDEN):
            return _step_unobserved("probe_credential_rejected")
        if answer.status_code not in (_OK, _ACCEPTED):
            return _step_failed(f"http_{answer.status_code}")
        self.session_id = answer.headers.get("mcp-session-id", self.session_id)
        return answer

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | AuthenticatedStep:
        self.next_id += 1
        body = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        answer = self._post(body)
        if isinstance(answer, AuthenticatedStep):
            return answer
        result = _rpc_result(answer, self.next_id)
        return _step_failed("protocol_error") if result is None else result

    def notify(self, method: str) -> AuthenticatedStep | None:
        answer = self._post({"jsonrpc": "2.0", "method": method})
        return answer if isinstance(answer, AuthenticatedStep) else None

    def terminate(self) -> None:
        """Best effort: the server keeps a session until it is told to drop it."""
        if self.session_id is None:
            return
        _send(
            self.run.client,
            self.run.request("DELETE", self.run.settings.public_mcp_url, headers=self._headers()),
        )


def _initialize_params() -> dict[str, Any]:
    return {
        "protocolVersion": LATEST_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "lovspor-site-probe", "version": __version__},
    }


def _served(listing: dict[str, Any]) -> AuthenticatedStep:
    try:
        tools = ListToolsResult.model_validate(listing).tools
    except ValidationError:
        return _step_failed("protocol_error")
    descriptor = describe_listed_tools(tools)
    return AuthenticatedStep(
        status="observed",
        reason=None,
        outcome="ok",
        served_tool_surface_sha256=descriptor.schema_sha256,
        served_tool_count=descriptor.tool_count,
    )


def _list_with_credential(run: _Run, token: SecretStr) -> AuthenticatedStep:
    session = _McpSession(run, token)
    initialized = session.request("initialize", _initialize_params())
    if isinstance(initialized, AuthenticatedStep):
        return initialized
    try:
        # Step (b) promises an MCP initialize, not merely a JSON-RPC result.
        handshake = InitializeResult.model_validate(initialized)
    except ValidationError:
        return _step_failed("protocol_error")
    session.protocol_version = str(handshake.protocolVersion)
    refused = session.notify("notifications/initialized")
    if refused is not None:
        return refused
    listing = session.request("tools/list", {})
    if isinstance(listing, AuthenticatedStep):
        return listing
    session.terminate()
    return _served(listing)


def observe_authenticated(run: _Run, unauthenticated: UnauthenticatedStep) -> AuthenticatedStep:
    """Step (b), only after (a) observed the Bearer ``401`` (ADR:846-905)."""
    if not bearer_401(unauthenticated):
        return _step_unobserved("not_attempted")
    if run.settings.probe_token is None:
        return _step_unobserved("probe_credential_missing")
    return _list_with_credential(run, run.settings.probe_token)


# --- transport: OAuth discovery ---------------------------------------------


def protected_resource_metadata_url(public_mcp_url: str) -> str:
    """RFC 9728 §3.1: the well-known prefix inserted before the resource's path."""
    parts = urlsplit(public_mcp_url)
    path = PROTECTED_RESOURCE_WELL_KNOWN + parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def authorization_server_metadata_urls(issuer: str) -> tuple[str, str]:
    """RFC 8414 §3.1 (prefix inserted), then OpenID Discovery (suffix appended)."""
    parts = urlsplit(issuer)
    path = parts.path.rstrip("/")
    inserted = urlunsplit(
        (parts.scheme, parts.netloc, AUTHORIZATION_SERVER_WELL_KNOWN + path, "", "")
    )
    appended = urlunsplit(
        (parts.scheme, parts.netloc, path + OPENID_CONFIGURATION_WELL_KNOWN, "", "")
    )
    return inserted, appended


def _issuer_of(run: _Run, url: str) -> str | None:
    answer = _send(run.client, run.request("GET", url))
    if isinstance(answer, str) or answer.status_code != _OK:
        return None
    try:
        metadata = answer.json()
    except ValueError:
        return None
    issuer = metadata.get("issuer") if isinstance(metadata, dict) else None
    return issuer if isinstance(issuer, str) else None


def _issuer_reason(run: _Run, entry: str) -> InvalidReason | None:
    issuer = next(
        (
            found
            for url in authorization_server_metadata_urls(entry)
            if (found := _issuer_of(run, url)) is not None
        ),
        None,
    )
    if issuer is None:
        return "issuer_unreachable"
    # A bare-host issuer is serialised with a trailing slash by the SDK's
    # AnyHttpUrl and written without one by the authorization server: the
    # same identifier, so the slash is not a difference (RFC 8414 §3.3).
    if issuer.rstrip("/") != entry.rstrip("/"):
        return "issuer_mismatch"
    return None


def _discovery_reason(run: _Run, raw: bytes) -> InvalidReason | None:
    try:
        document = json.loads(raw)
    except ValueError:
        return "malformed"
    if not isinstance(document, dict):
        return "malformed"
    if document.get("resource") != run.settings.public_mcp_url:
        return "resource_mismatch"
    servers = document.get("authorization_servers")
    if servers is None or servers == []:
        return "no_authorization_server"
    if not isinstance(servers, list) or not all(isinstance(entry, str) for entry in servers):
        return "malformed"
    return next((reason for entry in servers if (reason := _issuer_reason(run, entry))), None)


def observe_discovery(run: _Run) -> OAuthDiscovery:
    """The RFC 9728 document, judged for consistency, never for presence."""
    url = protected_resource_metadata_url(run.settings.public_mcp_url)
    answer = _send(run.client, run.request("GET", url))
    if isinstance(answer, str) or answer.status_code not in (_OK, _NOT_FOUND):
        return OAuthDiscovery(verdict="unobserved", invalid_reason=None, document_sha256=None)
    if answer.status_code == _NOT_FOUND:
        return OAuthDiscovery(verdict="absent", invalid_reason=None, document_sha256=None)
    reason = _discovery_reason(run, answer.content)
    return OAuthDiscovery(
        verdict="valid" if reason is None else "invalid",
        invalid_reason=reason,
        document_sha256=hashlib.sha256(answer.content).hexdigest(),
    )


# --- transport ---------------------------------------------------------------


def _unobserved_transport(reason: NoAnswer, run: _Run) -> TransportRecord:
    return TransportRecord(
        status="unobserved",
        reason=reason,
        observed_at=run.observed_at,
        observer=run.settings.observer,
        unauthenticated=None,
        authenticated=_step_unobserved("not_attempted"),
        oauth_discovery=OAuthDiscovery(
            verdict="unobserved", invalid_reason=None, document_sha256=None
        ),
    )


def observe_transport(run: _Run) -> TransportRecord:
    """Step (a), then (b) and the discovery document in the same pass."""
    body = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": _initialize_params()}
    headers = {"Accept": _ACCEPT, "Content-Type": _JSON}
    answer = _send(
        run.client, run.request("POST", run.settings.public_mcp_url, json=body, headers=headers)
    )
    if isinstance(answer, str):
        return _unobserved_transport(answer, run)
    unauthenticated = UnauthenticatedStep(
        status_code=answer.status_code, challenge=answer.headers.get("www-authenticate")
    )
    return TransportRecord(
        status="observed",
        reason=None,
        observed_at=run.observed_at,
        observer=run.settings.observer,
        unauthenticated=unauthenticated,
        authenticated=observe_authenticated(run, unauthenticated),
        oauth_discovery=observe_discovery(run),
    )


# --- the probe ---------------------------------------------------------------


def probe(
    settings: ProbeSettings,
    *,
    client: httpx.Client,
    checkout: Checkout,
    clock: Callable[[], datetime],
) -> CapabilityDocument:
    """Observe both subjects and derive the state against ``checkout``.

    The clock is read once, before the first request; ``checkout`` is the
    build side's expectation — the release's own (``fixture.expected_checkout``)
    or, for the drift timer, the served document's.
    """
    run = _Run(settings=settings, client=client, observed_at=_rfc3339(clock()))
    observation = Observation(process=observe_process(run), transport=observe_transport(run))
    return CapabilityDocument(
        schema_version="1", observation=observation, state=derive_state(observation, checkout)
    )
