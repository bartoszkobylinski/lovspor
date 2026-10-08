"""Observable contract diagnostics, including schemas without explicit properties."""

import asyncio
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent, Tool

from lovspor.errors import UnsupportedToolCallError
from lovspor.mcp_contract import ContractServer, refusals


def tool(name: str, **schema: Any) -> Tool:
    return Tool(name=name, inputSchema={"type": "object", **schema})


def test_call_reports_multiple_problems_with_readable_separator() -> None:
    server = ContractServer("diagnostics")

    def echo(count: int) -> str:
        return str(count)

    server.add_tool(echo)
    with pytest.raises(ToolError) as caught:
        asyncio.run(server.call_tool("echo", {"extra": True}))

    expected = (
        "unsupported argument 'extra'; echo accepts: count; missing required argument 'count'"
    )
    assert str(caught.value) == f"Error executing tool echo: {expected}"
    assert isinstance(caught.value.__cause__, UnsupportedToolCallError)
    assert str(caught.value.__cause__) == expected


def test_unknown_tool_lists_sorted_names_with_readable_separator() -> None:
    tools = {name: tool(name) for name in ("zebra", "alpha")}
    assert refusals("missing", {}, tools) == [
        "unknown tool 'missing'; this server serves: alpha, zebra"
    ]


def test_schema_without_properties_accepts_empty_call_and_names_no_arguments() -> None:
    tools = {"ping": tool("ping")}
    assert refusals("ping", {}, tools) == []
    assert refusals("ping", {"extra": 1}, tools) == [
        "unsupported argument 'extra'; ping accepts: no arguments"
    ]


def test_unserved_axis_lists_sorted_axes_and_all_tools() -> None:
    tools = {
        "zebra": tool("zebra", properties={"recorded_at": {"type": "string"}}),
        "ping": tool("ping"),
        "alpha": tool(
            "alpha", properties={"valid_at": {"type": "string"}, "recorded_at": {"type": "string"}}
        ),
    }
    assert refusals("ping", {"as_of": "2020-01-01"}, tools) == [
        "unsupported argument 'as_of'; ping accepts: no arguments. "
        "'as_of' is a time axis no tool here accepts; "
        "the time axes served are: recorded_at (alpha, zebra), valid_at (alpha)"
    ]


def test_missing_required_argument_is_reported_once() -> None:
    tools = {"echo": tool("echo", properties={"count": {"type": "integer"}}, required=["count"])}
    assert refusals("echo", {}, tools) == ["missing required argument 'count'"]


def test_union_type_diagnostic_names_alternatives() -> None:
    tools = {
        "echo": tool("echo", properties={"date": {"anyOf": [{"type": "string"}, {"type": "null"}]}})
    }
    assert refusals("echo", {"date": 123}, tools) == [
        "argument 'date' must be string or null, got integer 123"
    ]


def test_unconstrained_property_accepts_string_without_json_rereading() -> None:
    tools = {"echo": tool("echo", properties={"value": {}})}
    assert refusals("echo", {"value": "null"}, tools) == []


@pytest.mark.parametrize("value", ["blåbær", "ø" * 58, "ø" * 59])
def test_wrong_type_shows_unicode_and_truncates_by_characters(value: str) -> None:
    tools = {"echo": tool("echo", properties={"count": {"type": "integer"}})}
    shown = f'"{value}"' if len(value) <= 58 else f'"{value[:56]}...'
    assert refusals("echo", {"count": value}, tools) == [
        f"argument 'count' must be integer, got string {shown}"
    ]


def test_reread_refusal_explains_how_to_correct_the_call() -> None:
    tools = {
        "echo": tool("echo", properties={"date": {"anyOf": [{"type": "string"}, {"type": "null"}]}})
    }
    assert refusals("echo", {"date": "null"}, tools) == [
        "argument 'date' is the string \"null\", which would be re-read as "
        "JSON; send the value itself, or omit the argument"
    ]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({}, []),
        ({"required": ["count"]}, ["missing required argument 'count'"]),
        ({"minProperties": 1}, ["arguments: {} should be non-empty"]),
    ],
)
def test_missing_properties_schema_still_reports_whole_call_constraints(
    schema: dict[str, Any], expected: list[str]
) -> None:
    tools = {"ping": tool("ping", **schema)}
    assert refusals("ping", {}, tools) == expected


def test_missing_properties_filters_arguments_before_schema_validation() -> None:
    tools = {"ping": tool("ping", minProperties=1, required=["count"])}
    assert refusals("ping", {"count": 3}, tools) == [
        "unsupported argument 'count'; ping accepts: no arguments",
        "missing required argument 'count'",
        "arguments: {} should be non-empty",
    ]


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (["blåbær", "雪"], 'array ["blåbær", "雪"]'),
        ({"ø": "雪"}, 'object {"ø": "雪"}'),
    ],
)
def test_wrong_type_shows_unicode_inside_containers(value: Any, shown: str) -> None:
    tools = {"echo": tool("echo", properties={"count": {"type": "integer"}})}
    assert refusals("echo", {"count": value}, tools) == [
        f"argument 'count' must be integer, got {shown}"
    ]


@pytest.mark.parametrize("arguments", [{}, {"value": None}, {"value": "5"}, {"value": "true"}])
def test_supported_nullable_strings_reach_the_body_unchanged(arguments: dict[str, Any]) -> None:
    """docs/mcp.md keeps supported calls transparent, including actual JSON null."""
    server = ContractServer("nullable")
    received: list[str | None] = []

    def echo(value: str | None = None) -> str:
        received.append(value)
        return "ok"

    server.add_tool(echo)
    asyncio.run(server.call_tool("echo", arguments))

    assert received == [arguments.get("value")]


@pytest.mark.parametrize(
    ("name", "arguments", "diagnostic"),
    [
        ("missing", {}, "unknown tool 'missing'"),
        ("echo", {"count": 1, "extra": True}, "unsupported argument 'extra'"),
        ("echo", {"count": "1"}, "argument 'count' must be integer"),
        ("echo", {"count": None}, "got null"),
        ("echo", {}, "missing required argument 'count'"),
        ("echo", {"count": 1, "recorded_at": "null"}, "would be re-read as JSON"),
    ],
)
def test_each_refusal_category_is_a_client_error_without_executing_the_body(
    name: str, arguments: dict[str, Any], diagnostic: str
) -> None:
    """docs/mcp.md promises isError and no tool execution for every refusal."""
    server = ContractServer("wire-errors")
    received: list[tuple[int, str | None]] = []

    def echo(count: int, recorded_at: str | None = None) -> str:
        received.append((count, recorded_at))
        return "ok"

    server.add_tool(echo)

    async def call() -> None:
        async with create_connected_server_and_client_session(server) as client:
            result = await client.call_tool(name, arguments)
            assert result.isError is True
            assert result.content
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text.startswith(f"Error executing tool {name}: ")
            assert diagnostic in result.content[0].text

    asyncio.run(call())
    assert received == []
