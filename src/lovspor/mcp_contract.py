"""The served tool contract, enforced on every call (issue #570).

Owner decision 2026-10-08: every tool call made in a way the server does not
support is an error, without exception — there is no unknown parameter that
passes. FastMCP (mcp 1.28.1) does not hold that line on its own: its argument
models ignore undeclared keys, its low-level JSON-Schema validation is
switched off (``validate_input=False``), and its pydantic validation is lax
(``"5"`` becomes ``5``, ``1`` becomes ``True``, and for a parameter that is
not a plain ``str`` the string ``"null"`` is re-read as JSON ``null``). So
``get_section(..., observed_at=...)`` answered with today's text.

``ContractServer`` closes it in one place, through two public ``FastMCP``
methods: ``list_tools`` advertises every input schema with
``additionalProperties: false``, and ``call_tool`` validates the raw
arguments against exactly that advertised schema before FastMCP sees them.
JSON-Schema types are strict: a string is never an integer, a number never a
boolean, and ``null`` only where the schema allows it. The one coercion left
is JSON's own: ``5.0`` is an integer (JSON Schema defines it so; FastMCP
hands the tool ``5``). A refusal is a ``ToolError`` raised from an
``UnsupportedToolCallError``, worded like every other tool error
("Error executing tool <name>: …"), so the client gets ``isError: true``.

A sibling module rather than a section of ``mcp.py``: mutation testing of
``mcp.py`` does not fit a PR budget (issue #102).
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ContentBlock, Tool

from lovspor.errors import UnsupportedToolCallError

TIME_AXES = frozenset(
    {
        "as_at",
        "as_of",
        "at",
        "date",
        "date_a",
        "date_b",
        "effective_date",
        "in_force_at",
        "observed_at",
        "point_in_time",
        "recorded_at",
        "since",
        "target_date",
        "timestamp",
        "valid_at",
        "valid_from",
        "version_date",
    }
)
"""Argument names a client may send meaning "as of when"; a refusal of one
names the tools that serve that axis, or the axes served when none does."""

_JSON_TYPES: tuple[tuple[type, str], ...] = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
)
_SHOWN_MAX = 60


class ContractServer(FastMCP):
    """A ``FastMCP`` that refuses every call its listed schemas do not admit."""

    async def list_tools(self) -> list[Tool]:
        return [_closed(tool) for tool in await super().list_tools()]

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        tools = {tool.name: tool for tool in await self.list_tools()}
        problems = refusals(name, arguments, tools)
        if problems:
            error = UnsupportedToolCallError("; ".join(problems))
            raise ToolError(f"Error executing tool {name}: {error}") from error
        return await super().call_tool(name, arguments)


def _closed(tool: Tool) -> Tool:
    return tool.model_copy(
        update={"inputSchema": {**tool.inputSchema, "additionalProperties": False}}
    )


def refusals(name: str, arguments: Mapping[str, Any], tools: Mapping[str, Tool]) -> list[str]:
    """Every reason the call ``name(**arguments)`` is not one the tools admit."""
    tool = tools.get(name)
    if tool is None:
        return [f"unknown tool {name!r}; this server serves: {', '.join(sorted(tools))}"]
    properties: dict[str, Any] = tool.inputSchema.get("properties", {})
    declared = {key: value for key, value in arguments.items() if key in properties}
    undeclared = sorted(set(arguments) - set(properties))
    return [
        *(_undeclared(name, argument, tools) for argument in undeclared),
        *_invalid(tool.inputSchema, declared),
        *_reread(properties, declared),
    ]


def _undeclared(name: str, argument: str, tools: Mapping[str, Tool]) -> str:
    accepted = ", ".join(_declared(tools[name])) or "no arguments"
    text = f"unsupported argument {argument!r}; {name} accepts: {accepted}"
    takers = sorted(other for other, tool in tools.items() if argument in _declared(tool))
    if takers:
        kind = "a time axis" if argument in TIME_AXES else "an argument"
        return f"{text}. {argument!r} is {kind}; tools that accept it: {', '.join(takers)}"
    if argument in TIME_AXES:
        return f"{text}. {argument!r} is a time axis no tool here accepts; {_served_axes(tools)}"
    return text


def _declared(tool: Tool) -> list[str]:
    return list(tool.inputSchema.get("properties", {}))


def _served_axes(tools: Mapping[str, Tool]) -> str:
    axes: dict[str, list[str]] = {}
    for name in sorted(tools):
        for argument in _declared(tools[name]):
            if argument in TIME_AXES:
                axes.setdefault(argument, []).append(name)
    served = (f"{axis} ({', '.join(names)})" for axis, names in sorted(axes.items()))
    return f"the time axes served are: {', '.join(served)}"


def _invalid(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    properties: dict[str, Any] = schema.get("properties", {})
    missing = [
        f"missing required argument {argument!r}"
        for argument in schema.get("required", [])
        if argument not in arguments
    ]
    errors = Draft202012Validator(schema).iter_errors(arguments)
    wrong = sorted(
        _wrong(error, properties, arguments) for error in errors if error.validator != "required"
    )
    return [*missing, *wrong]


def _wrong(error: ValidationError, properties: dict[str, Any], arguments: dict[str, Any]) -> str:
    if not error.absolute_path:
        return f"arguments: {error.message}"
    argument = str(error.absolute_path[0])
    if error.validator not in ("type", "anyOf"):
        return f"argument {argument!r}: {error.message}"
    value = arguments[argument]
    expected = " or ".join(_types(properties[argument]))
    got = "null" if value is None else f"{_json_type(value)} {_shown(value)}"
    return f"argument {argument!r} must be {expected}, got {got}"


def _types(prop: dict[str, Any]) -> list[str]:
    if "type" in prop:
        return [str(prop["type"])]
    return [str(branch.get("type")) for branch in prop.get("anyOf", [])]


def _json_type(value: object) -> str:
    return next(name for python_type, name in _JSON_TYPES if isinstance(value, python_type))


def _shown(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= _SHOWN_MAX else f"{text[: _SHOWN_MAX - 3]}..."


def _reread(properties: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """FastMCP re-reads a string as JSON for any parameter that is not a plain
    ``str`` and keeps a ``null``, array or object it finds: ``"null"`` sent for
    ``recorded_at`` would arrive as ``None`` and answer for today."""
    return [
        f"argument {argument!r} is the string {_shown(value)}, which would be re-read as "
        "JSON; send the value itself, or omit the argument"
        for argument, value in sorted(arguments.items())
        if isinstance(value, str)
        and _rereadable(properties[argument])
        and _json_container_or_null(value)
    ]


def _rereadable(prop: dict[str, Any]) -> bool:
    """A parameter that admits a string but is not only a string (``str | None``)."""
    types = _types(prop)
    return "string" in types and types != ["string"]


def _json_container_or_null(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    return parsed is None or isinstance(parsed, list | dict)
