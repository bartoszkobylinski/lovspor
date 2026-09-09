"""Capability-document fixtures shared by the site tests (ADR-0014 Decision 4).

Builders, not files: each returns a fresh dict so a test can mutate one
field and see exactly that field move. ``available_observation()`` is the document
in which every clause of ``available`` holds (``available_observation``); the others are the
unobserved shapes the ADR enumerates.
"""

import json
import os
import subprocess
from pathlib import Path
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


# --- Build inputs: throwaway repositories and the documents that name them ---

_GIT_STAMP = {
    "GIT_AUTHOR_DATE": "2026-02-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-02-01T00:00:00Z",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}

CORPUS_DOC = """---
title: "Testloven"
language: "nb"
ref_id: "lov/2020-01-01-1"
retrieved_at: "2026-01-01T00:00:00+00:00"
---

# Testloven

### § 1. Formål

Loven gjelder.
"""


def run_git(repo: Path, *args: str) -> str:
    """Run one git command in ``repo`` with pinned identity and dates."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_STAMP},
    )
    return result.stdout.strip()


def commit_all(repo: Path, message: str = "one") -> str:
    """Stage everything, commit, return the full HEAD sha."""
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", message)
    return run_git(repo, "rev-parse", "HEAD")


def throwaway_checkout(root: Path) -> tuple[Path, str]:
    """A clean git work tree shaped like a lovspor checkout: ``uv.lock`` and ``src/lovspor``.

    The library API takes the checkout explicitly (``build_site`` never
    discovers it), so a build in tests reads its ``lovspor_commit`` and its
    ``uv.lock`` from here — never from the developer's own repository.
    """
    root.mkdir(parents=True, exist_ok=True)
    run_git(root, "init", "-q")
    (root / "uv.lock").write_text('version = 1\nrequires-python = ">=3.12"\n', encoding="utf-8")
    (root / "src" / "lovspor").mkdir(parents=True)
    (root / "src" / "lovspor" / "__init__.py").write_text("", encoding="utf-8")
    return root, commit_all(root)


def throwaway_corpus(root: Path) -> tuple[Path, str]:
    """A one-document lovverk repository both ``emit_site`` and ``build_server`` accept."""
    (root / "lover").mkdir(parents=True)
    run_git(root, "init", "-q")
    (root / "lover" / "testloven.md").write_text(CORPUS_DOC, encoding="utf-8")
    manifest = {
        "version": 1,
        "generated_at": "2026-01-01T00:00:00Z",
        "documents": {
            "doc-1": {
                "doc_type": "lov",
                "xml_hash": "a" * 64,
                "markdown_path": "lover/testloven.md",
                "source_dataset": "gjeldende-lover",
                "status": "current",
                "slug": "testloven",
                "title": "Testloven",
                "renderer_version": 8,
                "last_seen": "2026-01-01T00:00:00Z",
            }
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, commit_all(root)


def checkout_for(lovspor_commit: str, tool_surface_sha256: str) -> dict[str, Any]:
    """The document's ``state.checkout`` naming a real commit and a real descriptor."""
    return {
        "lovspor_commit": lovspor_commit,
        "expected_runtime_identity": {
            "tree_sha256": TREE,
            "environment_sha256": ENVIRONMENT,
            "interpreter": INTERPRETER,
        },
        "expected_tool_surface_sha256": tool_surface_sha256,
    }


def with_surface(observation: dict[str, Any], tool_surface_sha256: str) -> dict[str, Any]:
    """Point every surface hash the observation carries at ``tool_surface_sha256``."""
    process = observation["process"]
    if process.get("tool_surface_sha256") is not None:
        process["tool_surface_sha256"] = tool_surface_sha256
    authenticated = observation["transport"]["authenticated"]
    if authenticated.get("served_tool_surface_sha256") is not None:
        authenticated["served_tool_surface_sha256"] = tool_surface_sha256
    return observation


def document_for(observation: dict[str, Any], checkout: dict[str, Any]) -> dict[str, Any]:
    """A valid capability document: ``state`` derived from ``observation`` and ``checkout``."""
    state = derive_state(Observation.model_validate(observation), Checkout.model_validate(checkout))
    return {
        "schema_version": "1",
        "observation": observation,
        "state": state.model_dump(mode="json"),
    }
