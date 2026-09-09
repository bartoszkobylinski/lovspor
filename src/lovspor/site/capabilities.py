"""The capability document: closed schema and pure state derivation.

``deployment-capabilities.json`` is emitted at release time by the release
probe and is the one artifact a hosted claim may trace to (ADR-0014
Decision 4, ADR:735-743). It has two parts under a strict allowlisted
schema: ``observation`` — one raw record per subject, the *process* over
loopback and the *transport* through the public path — and ``state``, the
semantic projection of the observation plus the build side's ``checkout``
expectations, the five comparisons and the derived ``hosted_state``.

Fixed rules implemented here, each cited to the ADR:

* Unknown keys fail validation; every v1 field is listed exhaustively
  (ADR:736). Models are closed with ``extra="forbid"``.
* An observed record carries no ``reason``; an unobserved record names
  one and records no value — the document never records a guess
  (ADR:737-743).
* ``state`` is the observation **without** ``observed_at``, ``observer``
  and the unobserved ``reason`` (ADR:997-1002), a pure function of the
  observation and the checkout; a document whose ``state`` is not that
  function of its own ``observation`` is invalid (ADR:739).
* The five comparisons are ``true | false | unknown`` and nothing else,
  ``unknown`` exactly when an operand was not observed (ADR:802-823);
  ``environment_match`` needs both hashes and both interpreters equal;
  ``transport_surface_match`` is ``unknown`` whenever the authenticated
  step is unobserved; ``oauth_discovery_consistent`` is the conjunction
  of a ``valid`` verdict and ``oauth_configured``.
* ``hosted_state`` is derived, not stored (ADR:911-931): ``available``
  iff every clause holds — both subjects observed, the process ready,
  step (a) a Bearer ``401``, step (b) observed with outcome ``ok`` and
  ``transport_surface_match == true``; else ``unavailable`` iff an
  observed subject failed; else ``unknown``. Loopback readiness alone can
  never yield ``available``, and observer failure — a rejected or missing
  probe credential — is never published as service failure.
* ``state_sha256`` is the SHA-256 of the state's canonical JSON form
  (sorted keys, no whitespace, non-ASCII kept), the ``release_key``
  component (ADR:1003-1006).

Nothing here calls a clock: ``observed_at`` is the probe's value, kept as
the string it arrived as.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from lovspor.site.errors import CapabilityDocumentError

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
ObservedAt = Annotated[
    str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
]
HttpReason = Annotated[str, StringConstraints(pattern=r"^http_\d{3}$")]
Count = Annotated[int, Field(ge=0)]

RecordStatus = Literal["observed", "unobserved"]
Observer = Literal["release-probe", "drift-timer"]
ProcessReason = Literal["timeout", "network", "tool_missing", "schema_invalid"] | HttpReason
TransportReason = Literal["network", "timeout", "tool_missing"]
AuthenticatedReason = Literal[
    "probe_credential_missing", "probe_credential_rejected", "not_attempted", "network", "timeout"
]
Outcome = Literal["ok", "protocol_error"] | HttpReason
Verdict = Literal["valid", "invalid", "absent", "unobserved"]
InvalidReason = Literal[
    "resource_mismatch",
    "no_authorization_server",
    "issuer_unreachable",
    "issuer_mismatch",
    "malformed",
]
CredentialMode = Literal["token", "oauth"]
Comparison = Literal["true", "false", "unknown"]
HostedState = Literal["available", "unavailable", "unknown"]

_PROCESS_VALUES = (
    "ready",
    "runtime_identity",
    "tool_surface_sha256",
    "tool_count",
    "credential_modes",
    "oauth_configured",
)
_SERVED_VALUES = ("served_tool_surface_sha256", "served_tool_count")
# RFC 7235 §4.1: one header may carry several challenges, comma-separated, in any
# order; the scheme token is case-insensitive and ends at whitespace, a comma or
# the end of the value. So "Basic realm=\"x\", Bearer realm=\"y\"" advertises Bearer.
_BEARER = re.compile(r"(?:^|,)\s*Bearer(?=\s|,|$)", re.IGNORECASE)
# RFC 7230 §3.2.6 quoted-string: a comma or the word Bearer inside a parameter's
# quoted value (realm="legacy, Bearer") is data, not a challenge boundary.
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
_UNAUTHORIZED = 401
"""Step (a)'s documented answer with a Bearer challenge (RFC 6750 §3)."""


class _Closed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_reason(status: RecordStatus, reason: str | None) -> None:
    if status == "observed" and reason is not None:
        raise ValueError("an observed record carries no reason")
    if status == "unobserved" and reason is None:
        raise ValueError("an unobserved record names its reason")


def _require_absent(model: BaseModel, names: tuple[str, ...], why: str) -> None:
    present = [name for name in names if getattr(model, name) is not None]
    if present:
        raise ValueError(f"{why}: {', '.join(present)}")


def _require_present(model: BaseModel, names: tuple[str, ...], why: str) -> None:
    missing = [name for name in names if getattr(model, name) is None]
    if missing:
        raise ValueError(f"{why}: {', '.join(missing)}")


class RuntimeIdentity(_Closed):
    """What a process runs: source tree, installed environment, interpreter."""

    tree_sha256: Sha256
    environment_sha256: Sha256
    interpreter: str


class ProcessRecord(_Closed):
    """The loopback observation of ``/readyz`` and its attestation."""

    status: RecordStatus
    reason: ProcessReason | None
    observed_at: ObservedAt
    observer: Observer
    ready: bool | None
    runtime_identity: RuntimeIdentity | None
    tool_surface_sha256: Sha256 | None
    tool_count: Count | None
    credential_modes: tuple[CredentialMode, ...] | None
    oauth_configured: bool | None

    @model_validator(mode="after")
    def _values_follow_status(self) -> Self:
        _check_reason(self.status, self.reason)
        if self.status == "observed":
            _require_present(self, ("ready",), "an observed process says whether it is ready")
        else:
            _require_absent(self, _PROCESS_VALUES, "an unobserved process records no value")
        if self.credential_modes is not None and len(set(self.credential_modes)) != len(
            self.credential_modes
        ):
            raise ValueError("credential_modes repeats a mode")
        return self


class UnauthenticatedStep(_Closed):
    """Step (a): ``POST /mcp`` without credentials, answer recorded verbatim."""

    status_code: Annotated[int, Field(ge=100, le=599)]
    challenge: str | None


class AuthenticatedStep(_Closed):
    """Step (b): ``initialize`` + ``tools/list`` with the probe credential."""

    status: RecordStatus
    reason: AuthenticatedReason | None
    outcome: Outcome | None
    served_tool_surface_sha256: Sha256 | None
    served_tool_count: Count | None

    @model_validator(mode="after")
    def _values_follow_status(self) -> Self:
        _check_reason(self.status, self.reason)
        if self.status == "observed":
            _require_present(self, ("outcome",), "an observed step records its outcome")
        else:
            _require_absent(
                self, ("outcome", *_SERVED_VALUES), "an unobserved step records no value"
            )
        if self.outcome == "ok":
            _require_present(self, _SERVED_VALUES, "a listed surface records what it listed")
        else:
            _require_absent(self, _SERVED_VALUES, "no surface was listed")
        return self


class OAuthDiscovery(_Closed):
    """The RFC 9728 document, validated semantically, never by presence."""

    verdict: Verdict
    invalid_reason: InvalidReason | None
    document_sha256: Sha256 | None

    @model_validator(mode="after")
    def _fields_follow_verdict(self) -> Self:
        if (self.verdict == "invalid") != (self.invalid_reason is not None):
            raise ValueError("invalid_reason accompanies exactly the invalid verdict")
        if (self.verdict in {"valid", "invalid"}) != (self.document_sha256 is not None):
            raise ValueError("document_sha256 accompanies exactly a document that was read")
        return self


class TransportRecord(_Closed):
    """The public-path observation: steps (a), (b) and OAuth discovery."""

    status: RecordStatus
    reason: TransportReason | None
    observed_at: ObservedAt
    observer: Observer
    unauthenticated: UnauthenticatedStep | None
    authenticated: AuthenticatedStep
    oauth_discovery: OAuthDiscovery

    @model_validator(mode="after")
    def _values_follow_status(self) -> Self:
        _check_reason(self.status, self.reason)
        if self.status == "observed":
            _require_present(self, ("unauthenticated",), "an observed transport records step (a)")
            return self
        _require_absent(self, ("unauthenticated",), "an unobserved transport records no value")
        if (
            self.authenticated.status != "unobserved"
            or self.oauth_discovery.verdict != "unobserved"
        ):
            raise ValueError("an unobserved transport observed neither step (b) nor discovery")
        return self


class Observation(_Closed):
    process: ProcessRecord
    transport: TransportRecord


class ProcessState(_Closed):
    status: RecordStatus
    ready: bool | None
    runtime_identity: RuntimeIdentity | None
    tool_surface_sha256: Sha256 | None
    tool_count: Count | None
    credential_modes: tuple[CredentialMode, ...] | None
    oauth_configured: bool | None


class AuthenticatedState(_Closed):
    status: RecordStatus
    outcome: Outcome | None
    served_tool_surface_sha256: Sha256 | None
    served_tool_count: Count | None


class TransportState(_Closed):
    status: RecordStatus
    unauthenticated: UnauthenticatedStep | None
    authenticated: AuthenticatedState
    oauth_discovery: OAuthDiscovery


class Checkout(_Closed):
    """The build side: what the checkout at ``lovspor_commit`` expects."""

    lovspor_commit: CommitSha
    expected_runtime_identity: RuntimeIdentity
    expected_tool_surface_sha256: Sha256


class Comparisons(_Closed):
    runtime_tree_match: Comparison
    environment_match: Comparison
    tool_surface_match: Comparison
    transport_surface_match: Comparison
    oauth_discovery_consistent: Comparison


class State(_Closed):
    process: ProcessState
    transport: TransportState
    checkout: Checkout
    comparisons: Comparisons
    hosted_state: HostedState


class CapabilityDocument(_Closed):
    schema_version: Literal["1"]
    observation: Observation
    state: State

    @model_validator(mode="after")
    def _state_is_its_own_derivation(self) -> Self:
        if self.state != derive_state(self.observation, self.state.checkout):
            raise ValueError("state is not the derivation of its own observation")
        return self


def _compare(left: str | None, right: str | None) -> Comparison:
    if left is None or right is None:
        return "unknown"
    return "true" if left == right else "false"


def _environment_match(identity: RuntimeIdentity | None, expected: RuntimeIdentity) -> Comparison:
    if identity is None:
        return "unknown"
    same = (
        identity.environment_sha256 == expected.environment_sha256
        and identity.interpreter == expected.interpreter
    )
    return "true" if same else "false"


def _oauth_consistent(discovery: OAuthDiscovery, oauth_configured: bool | None) -> Comparison:
    if discovery.verdict == "unobserved" or oauth_configured is None:
        return "unknown"
    return "true" if discovery.verdict == "valid" and oauth_configured else "false"


def derive_comparisons(observation: Observation, checkout: Checkout) -> Comparisons:
    """The five comparisons of ADR-0014 Decision 4 (ADR:802-823)."""
    process, transport = observation.process, observation.transport
    identity = process.runtime_identity
    observed_tree = None if identity is None else identity.tree_sha256
    return Comparisons(
        runtime_tree_match=_compare(observed_tree, checkout.expected_runtime_identity.tree_sha256),
        environment_match=_environment_match(identity, checkout.expected_runtime_identity),
        tool_surface_match=_compare(
            process.tool_surface_sha256, checkout.expected_tool_surface_sha256
        ),
        transport_surface_match=_compare(
            transport.authenticated.served_tool_surface_sha256, process.tool_surface_sha256
        ),
        oauth_discovery_consistent=_oauth_consistent(
            transport.oauth_discovery, process.oauth_configured
        ),
    )


def bearer_401(step: UnauthenticatedStep | None) -> bool:
    """Step (a) answered the documented ``401`` with a Bearer challenge (RFC 6750 §3).

    The one clause both the derivation and the probe read: ``available``
    requires it, and the probe attempts step (b) only after it — a
    credential presented to a transport that did not ask for one observes
    nothing about the auth layer.
    """
    return (
        step is not None
        and step.status_code == _UNAUTHORIZED
        and step.challenge is not None
        and _BEARER.search(_QUOTED.sub('""', step.challenge)) is not None
    )


def _subject_failed(observation: Observation, comparisons: Comparisons) -> bool:
    process, transport = observation.process, observation.transport
    if process.status == "observed" and process.ready is False:
        return True
    if transport.status != "observed":
        return False
    authenticated = transport.authenticated
    return (
        not bearer_401(transport.unauthenticated)
        or (authenticated.status == "observed" and authenticated.outcome != "ok")
        or comparisons.transport_surface_match == "false"
    )


def derive_hosted_state(observation: Observation, comparisons: Comparisons) -> HostedState:
    """One pure function of the two records and one comparison (ADR:911-931)."""
    process, transport = observation.process, observation.transport
    authenticated = transport.authenticated
    if (
        process.status == "observed"
        and process.ready is True
        and transport.status == "observed"
        and bearer_401(transport.unauthenticated)
        and authenticated.status == "observed"
        and authenticated.outcome == "ok"
        and comparisons.transport_surface_match == "true"
    ):
        return "available"
    if _subject_failed(observation, comparisons):
        return "unavailable"
    return "unknown"


def _process_state(record: ProcessRecord) -> ProcessState:
    return ProcessState.model_validate(
        record.model_dump(exclude={"reason", "observed_at", "observer"})
    )


def _transport_state(record: TransportRecord) -> TransportState:
    return TransportState(
        status=record.status,
        unauthenticated=record.unauthenticated,
        authenticated=AuthenticatedState.model_validate(
            record.authenticated.model_dump(exclude={"reason"})
        ),
        oauth_discovery=record.oauth_discovery,
    )


def derive_state(observation: Observation, checkout: Checkout) -> State:
    """The semantic projection: observation minus time, observer and unobserved reason."""
    comparisons = derive_comparisons(observation, checkout)
    return State(
        process=_process_state(observation.process),
        transport=_transport_state(observation.transport),
        checkout=checkout,
        comparisons=comparisons,
        hosted_state=derive_hosted_state(observation, comparisons),
    )


def state_sha256(state: State) -> str:
    """SHA-256 of the state's canonical JSON form — the ``release_key`` component."""
    canonical = json.dumps(
        state.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_capabilities(raw: bytes) -> CapabilityDocument:
    """Validate document bytes against the closed schema and the derivation rule."""
    try:
        return CapabilityDocument.model_validate_json(raw)
    except ValidationError as error:
        raise CapabilityDocumentError(f"invalid capability document: {error}") from error


def load_capabilities(path: Path) -> CapabilityDocument:
    """Read and validate ``deployment-capabilities.json``; absent is invalid."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CapabilityDocumentError(f"cannot read capability document: {error}") from error
    return parse_capabilities(raw)
