"""The one tool-surface registry both the benchmark and publication read.

``describe_tool_surface`` builds the MCP server the way ``lovspor mcp``
does, lists its tools as a client sees them (name, description, input and
output schema) and hashes that list in one canonical form. The result is
the descriptor every consumer of "which tools does lovverk serve" reads:
the LLHB Stage 6 treatment arm (``lovspor.llhb.mcp_surface``) records it
in run metadata, and the public product surface — the site build and the
``/readyz`` attestation — publishes it.

It lives in the core layer, beside ``build_server``, on purpose. The
benchmark module describes itself as the Stage 6 treatment-arm surface,
and a public product surface must not import benchmark methodology to
learn what the server serves. Both read this neutral value instead, and
LLHB's frozen-surface test (``tests/unit/test_llhb_mcp_surface.py``)
keeps guarding it from drift. ADR-0014 (lovspor-notebook, Follow-Up Work)
names this module as that registry.

A sibling module rather than a section of ``mcp.py``: mutation testing of
``mcp.py`` does not fit a PR budget (issue #102), so nothing that can live
beside it is added to it.
"""

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Self

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, model_validator

from lovspor.mcp import build_server


class ToolSurfaceDescriptor(BaseModel, frozen=True):
    """What one built server serves, as a client sees it."""

    names: tuple[str, ...]
    schema_sha256: str
    tool_count: int

    @model_validator(mode="after")
    def _count_matches_names(self) -> Self:
        if self.tool_count != len(self.names):
            raise ValueError(f"tool_count {self.tool_count} does not match {len(self.names)} names")
        return self


def describe_tool_surface(
    corpus_path: Path, *, server_factory: Callable[[Path], FastMCP] = build_server
) -> ToolSurfaceDescriptor:
    """Describe the surface the server bound to ``corpus_path`` serves.

    ``list_tools`` is the client-facing view (name, description, input and
    output schema), so the hash covers exactly the material a model is
    shown — not our idea of it. Name order, key order, separators,
    non-ASCII escaping and the omission of empty fields are all part of
    what the hash means.

    ``server_factory`` is the constructor the description is derived from.
    A consumer whose promise is "this surface comes from the server I
    launch" names its own, so the binding is visible where the promise is
    made rather than hidden in a default.
    """
    tools = asyncio.run(server_factory(corpus_path).list_tools())
    documents = sorted(
        (tool.model_dump(mode="json", exclude_none=True) for tool in tools),
        key=lambda document: str(document["name"]),
    )
    canonical = json.dumps(documents, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    names = tuple(str(document["name"]) for document in documents)
    return ToolSurfaceDescriptor(
        names=names,
        schema_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        tool_count=len(names),
    )
