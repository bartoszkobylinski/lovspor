"""Capability-document fixtures shared by the site tests (ADR-0014 Decision 4).

Builders, not files: each returns a fresh dict so a test can mutate one
field and see exactly that field move. ``available_observation()`` is the document
in which every clause of ``available`` holds (``available_observation``); the others are the
unobserved shapes the ADR enumerates.
"""

from typing import Any

from lovspor.site.capabilities import Checkout, Observation, derive_state

TREE = "1" * 64
ENVIRONMENT = "2" * 64
SURFACE = "3" * 64
DISCOVERY = "4" * 64
COMMIT = "a" * 40
INTERPRETER = "cpython 3.12.11"
OBSERVED_AT = "2026-01-01T00:00:00Z"


def available_observation() -> dict[str, Any]:
    """Both subjects observed and every clause of ``available`` met."""
    return {
        "process": {
            "status": "observed",
            "reason": None,
            "observed_at": OBSERVED_AT,
            "observer": "release-probe",
            "ready": True,
            "runtime_identity": {
                "tree_sha256": TREE,
                "environment_sha256": ENVIRONMENT,
                "interpreter": INTERPRETER,
            },
            "tool_surface_sha256": SURFACE,
            "tool_count": 17,
            "credential_modes": ["token", "oauth"],
            "oauth_configured": True,
        },
        "transport": {
            "status": "observed",
            "reason": None,
            "observed_at": OBSERVED_AT,
            "observer": "release-probe",
            "unauthenticated": {"status_code": 401, "challenge": "Bearer"},
            "authenticated": {
                "status": "observed",
                "reason": None,
                "outcome": "ok",
                "served_tool_surface_sha256": SURFACE,
                "served_tool_count": 17,
            },
            "oauth_discovery": {
                "verdict": "valid",
                "invalid_reason": None,
                "document_sha256": DISCOVERY,
            },
        },
    }


def checkout_expectations() -> dict[str, Any]:
    return {
        "lovspor_commit": COMMIT,
        "expected_runtime_identity": {
            "tree_sha256": TREE,
            "environment_sha256": ENVIRONMENT,
            "interpreter": INTERPRETER,
        },
        "expected_tool_surface_sha256": SURFACE,
    }


def capability_document(observation: dict[str, Any] | None = None) -> dict[str, Any]:
    observation = observation if observation is not None else available_observation()
    state = derive_state(
        Observation.model_validate(observation), Checkout.model_validate(checkout_expectations())
    )
    return {
        "schema_version": "1",
        "observation": observation,
        "state": state.model_dump(mode="json"),
    }


def unobserved_process(reason: str) -> dict[str, Any]:
    observation = available_observation()
    observation["process"] = {
        "status": "unobserved",
        "reason": reason,
        "observed_at": OBSERVED_AT,
        "observer": "release-probe",
        "ready": None,
        "runtime_identity": None,
        "tool_surface_sha256": None,
        "tool_count": None,
        "credential_modes": None,
        "oauth_configured": None,
    }
    return observation


def unobserved_transport(reason: str) -> dict[str, Any]:
    observation = available_observation()
    observation["transport"] = {
        "status": "unobserved",
        "reason": reason,
        "observed_at": OBSERVED_AT,
        "observer": "release-probe",
        "unauthenticated": None,
        "authenticated": {
            "status": "unobserved",
            "reason": "not_attempted",
            "outcome": None,
            "served_tool_surface_sha256": None,
            "served_tool_count": None,
        },
        "oauth_discovery": {
            "verdict": "unobserved",
            "invalid_reason": None,
            "document_sha256": None,
        },
    }
    return observation


def unobserved_authenticated(reason: str) -> dict[str, Any]:
    observation = available_observation()
    observation["transport"]["authenticated"] = {
        "status": "unobserved",
        "reason": reason,
        "outcome": None,
        "served_tool_surface_sha256": None,
        "served_tool_count": None,
    }
    return observation


def readyz_503() -> dict[str, Any]:
    """A 503 carries no attestation: observed, not ready, every value absent."""
    observation = available_observation()
    observation["process"].update(
        ready=False,
        runtime_identity=None,
        tool_surface_sha256=None,
        tool_count=None,
        credential_modes=None,
        oauth_configured=None,
    )
    return observation
