"""Stage 6 treatment-arm tool surface (METHODOLOGY §5, ruling #25).

Describes the one thing that separates the treatment condition from the
control condition: a local stdio ``lovspor mcp`` server bound to the
pinned lovverk checkout. Everything here is derived from the server the
run will actually launch — the tool list and the schema hash come from
``build_server`` itself, so a recorded surface cannot drift away from
the served one.

The backend is deliberately local: METHODOLOGY §5 forbids pointing the
treatment arm at the hosted production endpoint, whose corpus moves.
"""

import asyncio
import hashlib
import json
import sysconfig
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from lovspor.errors import LovsporError
from lovspor.mcp import build_server

SERVER_NAME = "lovverk"
TRANSPORT = "native-mcp"


class ToolSurfaceError(LovsporError):
    """The treatment tool surface cannot be described from the given inputs."""


class ToolSurface(BaseModel, frozen=True):
    """The tool names an MCP server exposes, plus a hash of their schemas."""

    names: tuple[str, ...]
    schema_sha256: str


def tool_surface(corpus_path: Path) -> ToolSurface:
    """The surface the pinned corpus actually serves, as a client sees it.

    ``list_tools`` is the client-facing view (name, description, input
    and output schema), so the hash covers exactly the material the
    model is shown — not our idea of it.
    """
    tools = asyncio.run(build_server(corpus_path).list_tools())
    documents = sorted(
        (tool.model_dump(mode="json", exclude_none=True) for tool in tools),
        key=lambda document: str(document["name"]),
    )
    if not documents:
        raise ToolSurfaceError(f"the MCP server for {corpus_path} exposes no tools")
    canonical = json.dumps(documents, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return ToolSurface(
        names=tuple(str(document["name"]) for document in documents),
        schema_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def verify_server_command(server_command: Path) -> None:
    """Fail closed unless that command serves the code this process read.

    The recorded surface is built in-process from ``build_server``, so
    the executable the CLI launches has to be the entry point of this
    same environment. A ``lovspor`` from somewhere else may expose a
    different tool set, and the metadata would then describe a server
    the run never spoke to.
    """
    command = server_command.expanduser().absolute()
    if not command.is_file():
        raise ToolSurfaceError(f"no lovspor executable at {server_command}")
    # The scripts directory, not sys.executable: a venv's `python` is a
    # symlink, so resolving the interpreter walks straight out of the
    # environment whose entry points we are trying to identify.
    environment_bin = Path(sysconfig.get_path("scripts")).resolve()
    if command.parent.resolve() != environment_bin:
        raise ToolSurfaceError(
            f"server command {command} lives outside {environment_bin}, the environment "
            "whose lovspor produced the recorded tool surface; run the driver with that "
            "environment's interpreter, or point --server-command into it"
        )


def allowed_tools(surface: ToolSurface) -> list[str]:
    """The namespaced tool names the CLI must be told to allow."""
    return [f"mcp__{SERVER_NAME}__{name}" for name in surface.names]


def server_config(server_command: Path, corpus_path: Path) -> dict[str, Any]:
    """The ``--mcp-config`` document binding the CLI to the pinned corpus.

    Both paths must be absolute: the benchmark spawns the CLI with its
    working directory inside a throwaway sandbox, where a relative path
    would resolve somewhere else or not at all.
    """
    for label, path in (("server command", server_command), ("corpus path", corpus_path)):
        if not path.is_absolute():
            raise ToolSurfaceError(f"{label} {path} must be absolute; the CLI runs from a sandbox")
    return {
        "mcpServers": {
            SERVER_NAME: {
                "command": str(server_command),
                "args": ["mcp", "--corpus-path", str(corpus_path)],
            }
        }
    }


def server_config_json(server_command: Path, corpus_path: Path) -> str:
    """``server_config`` as the byte-stable JSON string passed on the argv."""
    document = server_config(server_command, corpus_path)
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def tool_config(surface: ToolSurface, corpus_path: Path, lovverk_commit: str) -> dict[str, Any]:
    """The run-metadata ``tool_config`` block for the treatment condition."""
    return {
        "transport": TRANSPORT,
        "tools": allowed_tools(surface),
        "tool_schema_sha256": surface.schema_sha256,
        "backend": f"local stdio `lovspor mcp` on lovverk {lovverk_commit} at {corpus_path}",
    }
