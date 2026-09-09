"""The readiness attestation: what the hosted process runs (ADR-0014 Decision 4).

``/readyz`` on the hosted MCP server answers with more than "ready": it
attests the runtime the process is actually executing, so the release
probe can capture the payload verbatim as the ``process`` observation of
``deployment-capabilities.json`` (``lovspor.site.capabilities.ProcessRecord``
carries the same field names, unrenamed). The fields are the ADR's and
nothing else:

* ``schema_version`` — ``"1"``; any further field is a schema bump.
* ``ready`` — the existing readiness rule, the corpus manifest present.
* ``runtime_identity`` — ``tree_sha256`` over the installed package tree
  (``lovspor/site/`` excluded), ``environment_sha256`` over the
  distributions this interpreter imports, and the ``interpreter`` — the
  three functions of ``lovspor.runtime_identity`` the site build also
  calls, so both sides of every comparison are one canonical form.
* ``tool_surface_sha256`` / ``tool_count`` — the descriptor of
  ``lovspor.tool_surface`` computed against the server that serves,
  through the same ``server_factory`` seam.
* ``credential_modes`` / ``oauth_configured`` — ``["token"]`` or
  ``["token", "oauth"]``, derived from the one fact the process knows at
  startup: whether the AuthKit pair was configured.

Computed **once at startup**, never per request, and never from
``uv.lock`` or the checkout: a lockfile is not the installed environment
(ADR:778-790). No secret, URL, hostname or timestamp is in the payload —
the route is unauthenticated, so everything here is public.

A sibling of ``mcp.py`` rather than a section of it: mutation testing of
``mcp.py`` does not fit a PR budget (issue #102), so nothing that can
live beside it is added to it.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

import lovspor
from lovspor.runtime_identity import installed_environment_sha256, interpreter, tree_sha256

CredentialMode = Literal["token", "oauth"]


class RuntimeIdentity(BaseModel):
    """What this process runs: source tree, installed environment, interpreter."""

    model_config = ConfigDict(frozen=True)

    tree_sha256: str
    environment_sha256: str
    interpreter: str


class ProcessAttestation(BaseModel):
    """The ``/readyz`` payload beside ``status`` (ADR-0014 Decision 4)."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    ready: bool
    runtime_identity: RuntimeIdentity
    tool_surface_sha256: str
    tool_count: int
    credential_modes: tuple[CredentialMode, ...]
    oauth_configured: bool

    def payload(self, *, ready: bool) -> dict[str, Any]:
        """The served fields, with readiness as observed at this request.

        Everything but ``ready`` is fixed for the life of the process; the
        corpus can still vanish underneath it, and a 503 then carries the
        same attestation with ``ready: false``.
        """
        return self.model_copy(update={"ready": ready}).model_dump(mode="json")


def corpus_present(corpus_path: Path) -> bool:
    """The readiness rule ``/readyz`` has always applied: the manifest is a file.

    A stat, not a parse: a probe hammering the route must not stall the loop.
    """
    return (corpus_path / "manifest.json").is_file()


def credential_modes_for(oauth_configured: bool) -> tuple[CredentialMode, ...]:
    """Opaque tokens are always accepted; OAuth only when the AuthKit pair is set."""
    return ("token", "oauth") if oauth_configured else ("token",)


def _installed_package_dir() -> Path:
    return Path(lovspor.__file__).resolve().parent


def compute_attestation(
    corpus_path: Path,
    *,
    oauth_configured: bool,
    package_dir: Path | None = None,
    server_factory: Callable[[Path], FastMCP] | None = None,
) -> ProcessAttestation:
    """Attest the runtime once, from the same functions the site build uses.

    ``server_factory`` is the descriptor's seam: the hosted server passes
    a factory returning itself, so the surface hashed is the one it serves
    and no second server is built just to hash it. ``None`` takes the
    descriptor's default, the stdio server ``lovspor mcp`` builds.
    ``package_dir`` is the installed ``lovspor`` package unless a test
    points at another tree.
    """
    # Local import: ``tool_surface`` imports ``mcp`` for its default factory
    # and ``mcp`` imports this module for ``/readyz``. Deferring this edge
    # keeps both importable in either order.
    from lovspor.tool_surface import describe_tool_surface  # noqa: PLC0415

    descriptor = (
        describe_tool_surface(corpus_path)
        if server_factory is None
        else describe_tool_surface(corpus_path, server_factory=server_factory)
    )
    identity = RuntimeIdentity(
        tree_sha256=tree_sha256(package_dir or _installed_package_dir()),
        environment_sha256=installed_environment_sha256(),
        interpreter=interpreter().label,
    )
    return ProcessAttestation(
        ready=corpus_present(corpus_path),
        runtime_identity=identity,
        tool_surface_sha256=descriptor.schema_sha256,
        tool_count=descriptor.tool_count,
        credential_modes=credential_modes_for(oauth_configured),
        oauth_configured=oauth_configured,
    )
