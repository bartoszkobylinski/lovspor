"""Every tool call the server does not support is refused, never answered (#570).

Owner decision 2026-10-08: an undeclared argument, a wrong type, a missing
required argument, a null where none is allowed and an unknown tool name are
each an error. FastMCP on its own drops an undeclared argument and coerces
lax types, so ``get_section(..., observed_at=...)`` used to answer with
today's text. The cases below are generated from the served schemas, so a
tool added later is covered without being listed here.
"""

import asyncio
import json
import tempfile
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent, Tool

from lovspor.errors import UnsupportedToolCallError
from lovspor.mcp import _hosted, build_server
from lovspor.mcp_contract import TIME_AXES, ContractServer, refusals
from lovspor.quota import QuotaEnforcer
from lovspor.usage_metrics import UsageRecorder
from tests.unit.local_dataset_fixtures import build_central, wire

_DUMMY: dict[str, Any] = {"string": "x", "integer": 1, "number": 0.5, "boolean": False}
_WRONG: dict[str, Any] = {"string": 123, "integer": "5", "number": "0.5", "boolean": "true"}


def _listed() -> list[Tool]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "central"
        build_central(root)
        return asyncio.run(build_server(root).list_tools())


LISTED = {tool.name: tool for tool in _listed()}


def _types(prop: dict[str, Any]) -> list[str]:
    if "type" in prop:
        return [prop["type"]]
    return [branch["type"] for branch in prop["anyOf"]]


def _properties(name: str) -> dict[str, dict[str, Any]]:
    properties: dict[str, dict[str, Any]] = LISTED[name].inputSchema.get("properties", {})
    return properties


def _required(name: str) -> list[str]:
    required: list[str] = LISTED[name].inputSchema.get("required", [])
    return required


def _valid(name: str) -> dict[str, Any]:
    """A call that satisfies the schema: every required argument, a value of its type."""
    props = _properties(name)
    return {arg: _DUMMY[_types(props[arg])[0]] for arg in _required(name)}


def _pairs(*, nullable: bool | None = None) -> list[tuple[str, str]]:
    return [
        (name, arg)
        for name in sorted(LISTED)
        for arg, prop in _properties(name).items()
        if nullable is None or ("null" in _types(prop)) is nullable
    ]


REQUIRED_PAIRS = [(name, arg) for name in sorted(LISTED) for arg in _required(name)]


@pytest.fixture(scope="module")
def server() -> Iterator[FastMCP]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "central"
        build_central(root)
        yield build_server(root)


def _refusal(server: FastMCP, name: str, arguments: dict[str, Any]) -> str:
    result = wire(server, name, arguments)
    assert set(result) == {"error"}, result
    text: str = result["error"]
    assert text.startswith(f"Error executing tool {name}: "), text
    assert "validation error" not in text, "refused by pydantic, not by the contract"
    return text


def test_the_server_is_the_contract_server(server: FastMCP) -> None:
    assert isinstance(server, ContractServer)
    assert len(LISTED) >= 18


@pytest.mark.parametrize("name", sorted(LISTED))
def test_every_listed_input_schema_forbids_undeclared_arguments(name: str) -> None:
    assert LISTED[name].inputSchema["additionalProperties"] is False


@pytest.mark.parametrize("name", sorted(LISTED))
def test_the_listing_differs_from_fastmcp_only_by_additional_properties(
    server: FastMCP, name: str
) -> None:
    plain = {tool.name: tool for tool in asyncio.run(FastMCP.list_tools(server))}[name]
    closed = LISTED[name].model_dump(mode="json")
    closed["inputSchema"].pop("additionalProperties")

    assert closed == plain.model_dump(mode="json")


@pytest.mark.parametrize("name", sorted(LISTED))
def test_an_undeclared_argument_is_refused_and_named(server: FastMCP, name: str) -> None:
    text = _refusal(server, name, {**_valid(name), "no_such_argument": 1})

    assert "'no_such_argument'" in text
    for declared in _properties(name):
        assert declared in text


@pytest.mark.parametrize(("name", "arg"), _pairs())
def test_a_wrong_type_is_refused_and_named(server: FastMCP, name: str, arg: str) -> None:
    wrong = _WRONG[_types(_properties(name)[arg])[0]]

    text = _refusal(server, name, {**_valid(name), arg: wrong})

    assert f"argument {arg!r} must be" in text


@pytest.mark.parametrize(("name", "arg"), _pairs(nullable=False))
def test_null_is_refused_where_the_schema_does_not_allow_it(
    server: FastMCP, name: str, arg: str
) -> None:
    text = _refusal(server, name, {**_valid(name), arg: None})

    assert f"argument {arg!r} must be" in text
    assert "got null" in text


@pytest.mark.parametrize(("name", "arg"), REQUIRED_PAIRS)
def test_a_missing_required_argument_is_refused_and_named(
    server: FastMCP, name: str, arg: str
) -> None:
    arguments = {key: value for key, value in _valid(name).items() if key != arg}

    text = _refusal(server, name, arguments)

    assert f"missing required argument {arg!r}" in text


@pytest.mark.parametrize("name", sorted(LISTED))
def test_a_valid_call_answers_exactly_as_fastmcp_would(server: FastMCP, name: str) -> None:
    """The contract is transparent to a supported call: same bytes, or the same error."""
    try:
        plain: Any = asyncio.run(FastMCP.call_tool(server, name, _valid(name)))
    except ToolError as error:
        plain = {"error": str(error)}
    else:
        plain = wire_shape(plain)

    assert wire(server, name, _valid(name)) == plain


def wire_shape(result: Any) -> dict[str, Any]:
    if isinstance(result, tuple):
        blocks, structured = result
        return {"content": _dump(blocks), "structured": structured}
    return {"content": _dump(result)}


def _dump(blocks: Any) -> list[Any]:
    return [block.model_dump(mode="json", exclude_none=True) for block in blocks]


def test_an_unknown_tool_is_refused_and_the_served_tools_named(server: FastMCP) -> None:
    text = _refusal(server, "get_lov", {"slug": "proveloven"})

    assert "unknown tool 'get_lov'" in text
    for name in LISTED:
        assert name in text


class TestTimeAxes:
    def test_observed_at_on_get_section_names_the_tool_that_takes_it(self, server: FastMCP) -> None:
        arguments = {"slug": "proveloven", "section_id": "1", "observed_at": "2026-08-20T00:00:00Z"}

        text = _refusal(server, "get_section", arguments)

        assert "unsupported argument 'observed_at'" in text
        assert "get_section accepts: slug, section_id, occurrence, recorded_at" in text
        assert "'observed_at' is a time axis; tools that accept it: get_observation_history" in text

    def test_recorded_at_on_get_observation_history_names_every_tool_that_takes_it(
        self, server: FastMCP
    ) -> None:
        arguments = {"document": "4601/x", "recorded_at": "2026-08-20"}

        text = _refusal(server, "get_observation_history", arguments)

        takers = sorted(name for name in LISTED if "recorded_at" in _properties(name))
        assert f"tools that accept it: {', '.join(takers)}" in text

    def test_an_axis_no_tool_takes_lists_the_axes_that_are_served(self, server: FastMCP) -> None:
        text = _refusal(server, "get_law", {"slug": "proveloven", "as_of": "2020-01-01"})

        assert "'as_of' is a time axis no tool here accepts" in text
        assert "observed_at (get_observation_history)" in text
        assert "target_date (get_law_at)" in text

    def test_every_served_time_parameter_is_a_known_axis(self) -> None:
        served = {arg for name in LISTED for arg in _properties(name)}

        assert {"observed_at", "recorded_at", "valid_at", "target_date"} <= served & TIME_AXES


class TestStrictTypes:
    def test_a_numeric_string_is_not_coerced_to_an_integer(self, server: FastMCP) -> None:
        text = _refusal(server, "search_laws", {"query": "lov", "limit": "5"})

        assert "argument 'limit' must be integer, got string \"5\"" in text

    def test_a_number_is_not_coerced_to_a_boolean(self, server: FastMCP) -> None:
        text = _refusal(server, "get_observation_history", {"document": "x", "include_text": 1})

        assert "argument 'include_text' must be boolean, got integer 1" in text

    def test_the_string_null_is_not_read_as_json_null(self, server: FastMCP) -> None:
        """FastMCP re-reads a string as JSON for a non-``str`` parameter, so
        ``recorded_at="null"`` would become ``None`` and answer for today."""
        arguments = {"slug": "proveloven", "section_id": "1", "recorded_at": "null"}

        text = _refusal(server, "get_section", arguments)

        assert "argument 'recorded_at' is the string \"null\"" in text

    def test_fastmcp_alone_reads_the_string_null_as_json_null(self, server: FastMCP) -> None:
        """Assumption behind the guard above: without it the call is answered."""
        arguments = {"slug": "proveloven", "section_id": "2", "recorded_at": "null"}
        plain = asyncio.run(FastMCP.call_tool(server, "get_section", arguments))
        today = asyncio.run(
            FastMCP.call_tool(server, "get_section", {"slug": "proveloven", "section_id": "2"})
        )

        assert plain == today

    def test_a_plain_string_parameter_keeps_json_looking_text(self, server: FastMCP) -> None:
        assert "error" not in wire(server, "search_laws", {"query": "null"})


def test_fastmcp_alone_drops_an_undeclared_argument(server: FastMCP) -> None:
    """The defect this contract closes, kept as the assumption it rests on."""
    plain = asyncio.run(FastMCP.call_tool(server, "get_law", {"slug": "proveloven"}))
    extra = asyncio.run(
        FastMCP.call_tool(server, "get_law", {"slug": "proveloven", "as_of": "2020-01-01"})
    )

    assert plain == extra


def test_a_refusal_reaches_the_client_as_an_error_result(server: FastMCP) -> None:
    """tools/call is routed through the subclass, and the refusal is isError."""

    async def call() -> Any:
        async with create_connected_server_and_client_session(server) as client:
            listed = await client.list_tools()
            result = await client.call_tool("get_law", {"slug": "proveloven", "as_of": "x"})
            return listed, result

    listed, result = anyio.run(call)

    assert all(tool.inputSchema["additionalProperties"] is False for tool in listed.tools)
    assert result.isError is True
    assert isinstance(result.content[0], TextContent)
    assert "unsupported argument 'as_of'" in result.content[0].text
    assert json.dumps(sorted(t.name for t in listed.tools)) == json.dumps(sorted(LISTED))


def test_an_argument_another_tool_takes_is_named_with_that_tool(server: FastMCP) -> None:
    text = _refusal(server, "search_body", {"query": "lov", "authority": "0301"})

    assert "'authority' is an argument; tools that accept it: search_laws" in text


def test_an_integral_number_is_an_integer_as_json_schema_defines_it(server: FastMCP) -> None:
    """The one coercion kept: JSON has no integer type distinct from 5.0."""
    assert wire(server, "search_laws", {"query": "lov", "limit": 5.0}) == wire(
        server, "search_laws", {"query": "lov", "limit": 5}
    )


def _tool(name: str, schema: dict[str, Any]) -> Tool:
    return Tool(name=name, inputSchema={"type": "object", **schema})


class TestRefusalsOnSchemasNotYetServed:
    """Shapes no lovverk tool has today, so a future one cannot slip through."""

    def test_a_tool_without_arguments_says_so(self) -> None:
        tools = {"ping": _tool("ping", {"properties": {}})}

        assert refusals("ping", {"x": 1}, tools) == [
            "unsupported argument 'x'; ping accepts: no arguments"
        ]

    def test_a_constraint_other_than_type_is_named_with_its_argument(self) -> None:
        schema = {"properties": {"limit": {"type": "integer", "minimum": 1}}}
        tools = {"t": _tool("t", schema)}

        assert refusals("t", {"limit": 0}, tools) == [
            "argument 'limit': 0 is less than the minimum of 1"
        ]

    def test_a_constraint_on_the_whole_call_is_reported_as_such(self) -> None:
        tools = {"t": _tool("t", {"properties": {"a": {"type": "string"}}, "minProperties": 1})}

        assert refusals("t", {}, tools) == ["arguments: {} should be non-empty"]

    def test_a_long_value_is_shortened_in_the_message(self) -> None:
        tools = {"t": _tool("t", {"properties": {"n": {"type": "integer"}}})}

        (text,) = refusals("t", {"n": "9" * 100}, tools)

        assert text == f"argument 'n' must be integer, got string \"{'9' * 56}..."

    def test_a_value_at_the_length_limit_is_shown_whole(self) -> None:
        tools = {"t": _tool("t", {"properties": {"n": {"type": "integer"}}})}

        (text,) = refusals("t", {"n": "9" * 58}, tools)

        assert text == f"argument 'n' must be integer, got string \"{'9' * 58}\""

    def test_an_optional_string_that_is_not_json_is_kept(self) -> None:
        nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
        tools = {"t": _tool("t", {"properties": {"d": nullable}})}

        assert refusals("t", {"d": "2026-01-01"}, tools) == []
        assert refusals("t", {"d": "5"}, tools) == []
        assert refusals("t", {"d": "[]"}, tools) != []
        assert refusals("t", {"d": "{}"}, tools) != []

    def test_a_valid_call_has_no_refusal(self) -> None:
        tools = {"t": _tool("t", {"properties": {"n": {"type": "integer"}}, "required": ["n"]})}

        assert refusals("t", {"n": 3}, tools) == []


def test_one_refusal_reports_all_independent_problems(server: FastMCP) -> None:
    """docs/mcp.md promises every problem at once, including an omitted slug."""
    text = _refusal(
        server,
        "get_section",
        {
            "section_id": 123,
            "occurrence": None,
            "recorded_at": "null",
            "observed_at": "x",
            "unexpected": True,
        },
    )

    assert "missing required argument 'slug'" in text
    assert "argument 'section_id' must be string, got integer 123" in text
    assert "argument 'recorded_at' is the string \"null\"" in text
    assert "unsupported argument 'observed_at'" in text
    assert "get_observation_history" in text
    assert "unsupported argument 'unexpected'" in text
    assert "argument 'occurrence'" not in text  # JSON null is allowed here.


@pytest.mark.parametrize("value", [True, False, 1.5])
def test_boolean_and_fractional_numbers_are_not_integers(server: FastMCP, value: Any) -> None:
    text = _refusal(server, "search_laws", {"query": "lov", "limit": value})

    assert "argument 'limit' must be integer" in text


@pytest.mark.parametrize("value", [" null ", "[]", "{}"])
def test_optional_strings_cannot_be_reread_as_json_containers_or_null(
    server: FastMCP, value: str
) -> None:
    text = _refusal(
        server, "get_section", {"slug": "proveloven", "section_id": "2", "recorded_at": value}
    )

    assert "argument 'recorded_at' is the string" in text
    assert "would be re-read as JSON" in text


@pytest.mark.parametrize(
    "arguments",
    [{"slug": "x", "observed_at": "2026-08-20"}, {"slug": 1}, {"slug": None}, {}],
)
def test_refused_calls_never_enter_hosted_body_quota_or_usage(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any]
) -> None:
    """docs/mcp.md explicitly excludes refused calls from hosted accounting."""
    body = Mock()
    enforcer = Mock(spec=QuotaEnforcer)
    enforcer.guard.return_value = nullcontext()
    usage = Mock(spec=UsageRecorder)
    token = Mock(client_id="contract-test")
    monkeypatch.setattr("lovspor.mcp.get_access_token", lambda: token)
    server = ContractServer("contract-test")

    def get_law(slug: str) -> str:
        body(slug)
        return slug

    server.add_tool(_hosted(get_law, enforcer, usage))

    with pytest.raises(ToolError) as caught:
        asyncio.run(server.call_tool("get_law", arguments))

    assert isinstance(caught.value.__cause__, UnsupportedToolCallError)
    body.assert_not_called()
    enforcer.guard.assert_not_called()
    usage.record.assert_not_called()

    # Positive control: the same registered wrappers do run for an admitted call.
    asyncio.run(server.call_tool("get_law", {"slug": "x"}))
    body.assert_called_once_with("x")
    enforcer.guard.assert_called_once_with("contract-test", paid=False)
    usage.record.assert_called_once()
    assert usage.record.call_args.args[0].outcome == "ok"
