"""Claude Code CLI driver for both LLHB conditions (ruling #25).

Builds the exact ``claude -p`` invocation for one case and turns the
CLI's ``--output-format stream-json`` transcript into a schema-valid
result record. No subprocess is spawned here; executing the argv
belongs to the run orchestrator.

Both conditions share one argv shape: built-in tools are hard-disabled
with ``--tools ""`` and MCP is confined to what ``--mcp-config``
declares under ``--strict-mcp-config``, so the only difference between
the arms is whether that config is empty. The streaming format is what
makes that claim checkable instead of asserted — the transcript carries
the tool list the model was offered and every call it made, so a
control run that touched a tool records the evidence rather than a
hardcoded empty list (ruling #25 invalidates such a run).

Parsing is fail-closed on the tool trace: a transcript whose tool
blocks cannot be read becomes an error record. A partially readable
trace would silently understate tool use, which is the one thing
Stage 6 measures.
"""

import json
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

_EMPTY_MCP_CONFIG = '{"mcpServers":{}}'


class RunIdentity(BaseModel):
    """The identity fields every record of one run must share."""

    run_id: str
    provider: str
    model_id: str
    condition: str


class ToolAccess(BaseModel, frozen=True):
    """What the treatment condition adds on top of the shared argv."""

    mcp_config_json: str
    allowed_tools: tuple[str, ...]


class CaseTiming(BaseModel):
    """Wall-clock capture for one case, measured by the orchestrator."""

    started_at: str
    total_ms: int


class ToolCall(BaseModel):
    """One tool invocation, as issued, with the payload it came back with."""

    index: int
    name: str
    arguments: dict[str, Any]
    result: str | list[Any] | dict[str, Any] | None = None
    is_error: bool = False


class HarnessTrace(BaseModel):
    """What the CLI itself reported about the case's tool environment."""

    exposed_tools: tuple[str, ...]
    mcp_servers: tuple[dict[str, Any], ...]
    permission_denials: tuple[dict[str, Any], ...]


class ParsedCliResult(BaseModel):
    """Outcome of one CLI invocation, normalized across success and failure."""

    ok: bool
    final_answer: str | None = None
    turns: int | None = None
    usage: dict[str, object] | None = None
    error: str | None = None
    tool_calls: list[ToolCall] = []
    harness: HarnessTrace | None = None
    truncated: bool = False


def build_argv(
    identity: RunIdentity,
    question: str,
    system_prompt: str,
    access: ToolAccess | None = None,
) -> list[str]:
    """The full ``claude -p`` argv for one case of either condition."""
    _check_access_matches_condition(identity.condition, access)
    argv = [
        "claude",
        "-p",
        question,
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        identity.model_id,
        "--system-prompt",
        system_prompt,
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        _EMPTY_MCP_CONFIG if access is None else access.mcp_config_json,
    ]
    # Variadic flag: it stays last so it cannot swallow another option.
    return argv if access is None else [*argv, "--allowedTools", *access.allowed_tools]


def _check_access_matches_condition(condition: str, access: ToolAccess | None) -> None:
    """Fail closed when the argv would not match the condition it claims."""
    if condition == "control" and access is not None:
        raise ValueError("control argv requested with tool access")
    if condition == "lovspor" and access is None:
        raise ValueError("lovspor argv requested without tool access")
    if condition not in {"control", "lovspor"}:
        raise ValueError(f"unknown condition {condition!r}")


def parse_stream_json(stdout: str, returncode: int) -> ParsedCliResult:
    """Normalize a stream-json transcript; never raises on bad output."""
    if returncode != 0:
        return ParsedCliResult(ok=False, error=f"claude exited with exit code {returncode}")
    try:
        events = _load_events(stdout)
        final = _last_result_event(events)
        return _parsed(events, final)
    except ValueError as exc:
        # ValueError, not just JSONDecodeError: syntactically valid JSON can
        # still blow CPython's 4300-digit int-conversion limit.
        return ParsedCliResult(ok=False, error=f"unreadable stream-json transcript: {exc}")


def _parsed(events: list[dict[str, Any]], final: dict[str, Any]) -> ParsedCliResult:
    """Assemble the result, keeping tool evidence even for a failed case."""
    return _from_result_event(final).model_copy(
        update={
            "tool_calls": _tool_calls(events),
            "harness": _harness(events, final),
            "truncated": _truncated(final),
        }
    )


def _load_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"line {number} is not JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"line {number} is not a JSON object")
        events.append(loaded)
    return events


def _last_result_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    finals = [event for event in events if event.get("type") == "result"]
    if not finals:
        raise ValueError("transcript carries no result event")
    return finals[-1]


def _from_result_event(payload: dict[str, Any]) -> ParsedCliResult:
    subtype = payload.get("subtype")
    result = payload.get("result")
    if payload.get("is_error") or subtype != "success":
        return ParsedCliResult(ok=False, error=f"CLI reported failure (subtype={subtype!r})")
    if not isinstance(result, str):
        return ParsedCliResult(ok=False, error="CLI success payload lacks a string result")
    turns = payload.get("num_turns")
    usage = payload.get("usage")
    return ParsedCliResult(
        ok=True,
        final_answer=result,
        turns=turns if isinstance(turns, int) and turns >= 1 else None,
        usage=usage if isinstance(usage, dict) else None,
    )


def _tool_calls(events: list[dict[str, Any]]) -> list[ToolCall]:
    payloads = _tool_results(events)
    claimed: set[str] = set()
    calls: list[ToolCall] = []
    for index, block in enumerate(_blocks(events, "assistant", "tool_use")):
        name, arguments, use_id = block.get("name"), block.get("input"), block.get("id")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise ValueError(f"tool_use block {index} has no readable name or input")
        if not isinstance(use_id, str):
            raise ValueError(f"tool_use block {index} ({name}) has no id to match its result")
        if use_id in claimed:
            raise ValueError(f"two tool_use blocks share the id {use_id!r}")
        claimed.add(use_id)
        result, failed = payloads.pop(use_id, (None, False))
        calls.append(
            ToolCall(index=index, name=name, arguments=arguments, result=result, is_error=failed)
        )
    if payloads:
        # A result nobody asked for is evidence of a call this parser did
        # not see. Returning the shorter list would understate tool use.
        raise ValueError(f"tool results with no matching call: {sorted(payloads)}")
    return calls


def _tool_results(events: list[dict[str, Any]]) -> dict[str, tuple[Any, bool]]:
    """Every tool payload, keyed by the call it answers.

    Ids are unique per conversation, so a repeat means the transcript is
    not what it claims to be. Overwriting would silently discard one
    real payload and attach the other to a call it may not belong to.
    """
    payloads: dict[str, tuple[Any, bool]] = {}
    for block in _blocks(events, "user", "tool_result"):
        use_id = block.get("tool_use_id")
        if not isinstance(use_id, str):
            raise ValueError("tool_result block carries no tool_use_id")
        if use_id in payloads:
            raise ValueError(f"two tool_result blocks share the id {use_id!r}")
        payloads[use_id] = (block.get("content"), bool(block.get("is_error")))
    return payloads


def _blocks(
    events: list[dict[str, Any]], event_type: str, block_type: str
) -> Iterator[dict[str, Any]]:
    """Every content block of one kind, in transcript order."""
    for event in events:
        message = event.get("message")
        if event.get("type") != event_type or not isinstance(message, dict):
            continue
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == block_type:
                yield block


def _harness(events: list[dict[str, Any]], final: dict[str, Any]) -> HarnessTrace:
    """The CLI's own account of the tool environment for this case.

    Fail closed without an init event: an absent tool list is
    indistinguishable from an empty one, and "the model was offered no
    tools" is exactly the claim the control arm rests on.
    """
    init = _init_event(events)
    return HarnessTrace(
        exposed_tools=tuple(str(tool) for tool in _listed(init, "tools")),
        mcp_servers=tuple(_listed(init, "mcp_servers")),
        permission_denials=tuple(_listed(final, "permission_denials")),
    )


def _init_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The one init event; two of them describe two tool environments.

    Taking the first would let a case that gained MCP part-way through
    be recorded as toolless, so an ambiguous transcript fails instead.
    """
    inits = [
        event
        for event in events
        if event.get("type") == "system" and event.get("subtype") == "init"
    ]
    if not inits:
        raise ValueError("transcript carries no system init event")
    if len(inits) > 1:
        raise ValueError(
            f"transcript carries {len(inits)} system init events; "
            "which tool environment applied to this case is not decidable"
        )
    return inits[0]


def _listed(event: dict[str, Any], key: str) -> list[Any]:
    value = event.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{key} is {type(value).__name__}, not a list")
    return value


def _truncated(final: dict[str, Any]) -> bool:
    return final.get("subtype") == "error_max_turns" or final.get("stop_reason") == "max_tokens"


def build_result_record(
    identity: RunIdentity,
    case_id: str,
    parsed: ParsedCliResult,
    timing: CaseTiming,
) -> dict[str, object]:
    """A result_record.schema.json document for one case."""
    errors = [] if parsed.ok else [{"stage": "request", "message": parsed.error or "unknown"}]
    return {
        **identity.model_dump(),
        "case_id": case_id,
        "final_answer": parsed.final_answer,
        # mode="json" throughout: tuples are not JSON arrays to the schema
        # validator, so a dumped tuple would fail validation on write.
        "tool_calls": [call.model_dump(mode="json") for call in parsed.tool_calls],
        "turns": parsed.turns,
        "timing": timing.model_dump(),
        "usage": parsed.usage,
        "truncated": parsed.truncated,
        "harness": parsed.harness.model_dump(mode="json") if parsed.harness else None,
        "errors": errors,
        "completed": parsed.ok,
    }
