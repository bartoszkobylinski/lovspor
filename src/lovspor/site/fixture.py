"""Synthetic capability documents for CI and tests (ADR-0014 Decision 4).

The release probe that captures ``deployment-capabilities.json`` from
the running server is later work; until it exists — and beside it, for
tests — this generator writes a document of the same closed schema in
which the **checkout part is real** and the **observations are
synthesised**: ``state.checkout`` is read from the work tree the
generator runs in (its ``HEAD``, the runtime identity of its
``src/lovspor`` and installed environment, the tool-surface descriptor
computed against the corpus), while the two records are the shapes the
ADR's validation enumerates (ADR:2431-2450): every clause of
``available`` met, each subject failing or unobserved, each comparison
false on its own. ``available`` reflects today's host — token only,
OAuth off, no discovery document; ``oauth_discovery_valid`` is the
same host with the pair configured and a valid discovery document.

``observed_at`` is an explicit input with a fixed default — the
generator calls no clock (Decision 3), so two runs from the same
checkout give the same bytes. The observer is ``release-probe``.

``state`` is derived by the one function the builder validates with
(``derive_state``), so a fixture is a valid document by construction.
"""

import hashlib
from enum import StrEnum
from pathlib import Path

from lovspor.publish.companion import companion_json_bytes
from lovspor.runtime_identity import installed_environment_sha256, interpreter, tree_sha256
from lovspor.site.build import require_clean_work_tree
from lovspor.site.capabilities import (
    AuthenticatedReason,
    AuthenticatedStep,
    CapabilityDocument,
    Checkout,
    OAuthDiscovery,
    Observation,
    Observer,
    ProcessRecord,
    RuntimeIdentity,
    TransportRecord,
    UnauthenticatedStep,
    derive_state,
)
from lovspor.tool_surface import ToolSurfaceDescriptor, describe_tool_surface

FIXED_OBSERVED_AT = "2026-01-01T00:00:00Z"
_OBSERVER: Observer = "release-probe"
_UNAUTHORIZED = 401
_MISDIRECTED = 421


class FixtureCase(StrEnum):
    """The observation shapes a fixture can take; one per validation clause."""

    available = "available"
    process_not_ready = "process_not_ready"
    process_unobserved = "process_unobserved"
    transport_unobserved = "transport_unobserved"
    transport_misdirected = "transport_misdirected"
    credential_rejected = "credential_rejected"
    credential_missing = "credential_missing"
    served_surface_differs = "served_surface_differs"
    tree_differs = "tree_differs"
    environment_differs = "environment_differs"
    surface_differs = "surface_differs"
    oauth_discovery_valid = "oauth_discovery_valid"


_STEP_UNOBSERVED: dict[FixtureCase, AuthenticatedReason] = {
    FixtureCase.credential_rejected: "probe_credential_rejected",
    FixtureCase.credential_missing: "probe_credential_missing",
    FixtureCase.transport_misdirected: "not_attempted",
    FixtureCase.transport_unobserved: "not_attempted",
}


def _other(label: str) -> str:
    """A hash that is deterministic and certainly not the checkout's."""
    return hashlib.sha256(f"fixture:{label}".encode()).hexdigest()


def _checkout_part(checkout: Path, descriptor: ToolSurfaceDescriptor) -> Checkout:
    lovspor_commit = require_clean_work_tree(checkout)
    identity = RuntimeIdentity(
        tree_sha256=tree_sha256(checkout / "src" / "lovspor"),
        environment_sha256=installed_environment_sha256(),
        interpreter=interpreter().label,
    )
    return Checkout(
        lovspor_commit=lovspor_commit,
        expected_runtime_identity=identity,
        expected_tool_surface_sha256=descriptor.schema_sha256,
    )


def expected_checkout(checkout: Path, corpus: Path) -> Checkout:
    """The real build side: ``HEAD`` of a clean tree, its runtime identity, the descriptor."""
    return _checkout_part(checkout, describe_tool_surface(corpus))


def _absent_process(case: FixtureCase, observed_at: str) -> ProcessRecord:
    """Unobserved (timeout), or observed not-ready without an attestation (a 503)."""
    unobserved = case is FixtureCase.process_unobserved
    return ProcessRecord(
        status="unobserved" if unobserved else "observed",
        reason="timeout" if unobserved else None,
        observed_at=observed_at,
        observer=_OBSERVER,
        ready=None if unobserved else False,
        runtime_identity=None,
        tool_surface_sha256=None,
        tool_count=None,
        credential_modes=None,
        oauth_configured=None,
    )


def _observed_identity(case: FixtureCase, expected: RuntimeIdentity) -> RuntimeIdentity:
    return RuntimeIdentity(
        tree_sha256=_other("tree") if case is FixtureCase.tree_differs else expected.tree_sha256,
        environment_sha256=(
            _other("environment")
            if case is FixtureCase.environment_differs
            else expected.environment_sha256
        ),
        interpreter=expected.interpreter,
    )


def _process(
    checkout: Checkout, case: FixtureCase, tool_count: int, observed_at: str
) -> ProcessRecord:
    if case in (FixtureCase.process_unobserved, FixtureCase.process_not_ready):
        return _absent_process(case, observed_at)
    oauth = case is FixtureCase.oauth_discovery_valid
    surface_differs = case is FixtureCase.surface_differs
    return ProcessRecord(
        status="observed",
        reason=None,
        observed_at=observed_at,
        observer=_OBSERVER,
        ready=True,
        runtime_identity=_observed_identity(case, checkout.expected_runtime_identity),
        tool_surface_sha256=(
            _other("surface") if surface_differs else checkout.expected_tool_surface_sha256
        ),
        tool_count=tool_count,
        credential_modes=("token", "oauth") if oauth else ("token",),
        oauth_configured=oauth,
    )


def _authenticated(case: FixtureCase, process: ProcessRecord) -> AuthenticatedStep:
    if case in _STEP_UNOBSERVED:
        return AuthenticatedStep(
            status="unobserved",
            reason=_STEP_UNOBSERVED[case],
            outcome=None,
            served_tool_surface_sha256=None,
            served_tool_count=None,
        )
    attested_surface = process.tool_surface_sha256 or _other("unattested")
    attested_count = process.tool_count or 0
    differs = case is FixtureCase.served_surface_differs
    return AuthenticatedStep(
        status="observed",
        reason=None,
        outcome="ok",
        served_tool_surface_sha256=_other("served") if differs else attested_surface,
        served_tool_count=attested_count + 1 if differs else attested_count,
    )


def _discovery(case: FixtureCase) -> OAuthDiscovery:
    if case is FixtureCase.transport_unobserved:
        return OAuthDiscovery(verdict="unobserved", invalid_reason=None, document_sha256=None)
    if case is FixtureCase.oauth_discovery_valid:
        return OAuthDiscovery(
            verdict="valid", invalid_reason=None, document_sha256=_other("discovery")
        )
    return OAuthDiscovery(verdict="absent", invalid_reason=None, document_sha256=None)


def _transport(case: FixtureCase, process: ProcessRecord, observed_at: str) -> TransportRecord:
    unobserved = case is FixtureCase.transport_unobserved
    misdirected = case is FixtureCase.transport_misdirected
    return TransportRecord(
        status="unobserved" if unobserved else "observed",
        reason="network" if unobserved else None,
        observed_at=observed_at,
        observer=_OBSERVER,
        unauthenticated=(
            None
            if unobserved
            else UnauthenticatedStep(
                status_code=_MISDIRECTED if misdirected else _UNAUTHORIZED,
                challenge=None if misdirected else "Bearer",
            )
        ),
        authenticated=_authenticated(case, process),
        oauth_discovery=_discovery(case),
    )


def synthetic_document(
    checkout: Path, corpus: Path, case: FixtureCase, observed_at: str = FIXED_OBSERVED_AT
) -> CapabilityDocument:
    """A valid document for ``case``: real checkout part, synthesised observation."""
    descriptor = describe_tool_surface(corpus)
    expected = _checkout_part(checkout, descriptor)
    process = _process(expected, case, descriptor.tool_count, observed_at)
    observation = Observation(process=process, transport=_transport(case, process, observed_at))
    return CapabilityDocument(
        schema_version="1", observation=observation, state=derive_state(observation, expected)
    )


def document_bytes(document: CapabilityDocument) -> bytes:
    """The document as the file the build reads: sorted keys, stable separators."""
    return companion_json_bytes(document.model_dump(mode="json"))
