"""Stage 5 control-arm driver for the Claude Code CLI (ruling #25).

Builds the exact ``claude -p`` invocation for the control condition —
built-in tools disabled via ``--tools ""`` and MCP hard-blocked via
``--strict-mcp-config`` with an empty server map, so a control run
cannot reach any tool — and turns the CLI's ``--output-format json``
document into a schema-valid result record. No subprocess is spawned
here; executing the argv belongs to the run orchestrator.
"""

import json

from pydantic import BaseModel

_EMPTY_MCP_CONFIG = '{"mcpServers":{}}'


class RunIdentity(BaseModel):
    """The identity fields every record of one run must share."""

    run_id: str
    provider: str
    model_id: str
    condition: str


class CaseTiming(BaseModel):
    """Wall-clock capture for one case, measured by the orchestrator."""

    started_at: str
    total_ms: int


class ParsedCliResult(BaseModel):
    """Outcome of one CLI invocation, normalized across success and failure."""

    ok: bool
    final_answer: str | None = None
    turns: int | None = None
    usage: dict[str, object] | None = None
    error: str | None = None


def build_control_argv(identity: RunIdentity, question: str, system_prompt: str) -> list[str]:
    """The full ``claude -p`` argv for one control-arm case."""
    if identity.condition != "control":
        raise ValueError(f"control argv requested for condition {identity.condition!r}")
    return [
        "claude",
        "-p",
        question,
        "--output-format",
        "json",
        "--model",
        identity.model_id,
        "--system-prompt",
        system_prompt,
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        _EMPTY_MCP_CONFIG,
    ]


def parse_cli_output(stdout: str, returncode: int) -> ParsedCliResult:
    """Normalize CLI stdout into a ParsedCliResult; never raises on bad output."""
    if returncode != 0:
        return ParsedCliResult(ok=False, error=f"claude exited with exit code {returncode}")
    try:
        payload = json.loads(stdout)
    except ValueError as exc:
        # ValueError, not just JSONDecodeError: syntactically valid JSON can
        # still blow CPython's 4300-digit int-conversion limit.
        return ParsedCliResult(ok=False, error=f"stdout is not parseable JSON: {exc}")
    if not isinstance(payload, dict):
        return ParsedCliResult(ok=False, error="stdout JSON is not an object")
    return _from_payload(payload)


def _from_payload(payload: dict[str, object]) -> ParsedCliResult:
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


def build_result_record(
    identity: RunIdentity,
    case_id: str,
    parsed: ParsedCliResult,
    timing: CaseTiming,
) -> dict[str, object]:
    """A result_record.schema.json document for one control-arm case."""
    errors = [] if parsed.ok else [{"stage": "request", "message": parsed.error or "unknown"}]
    return {
        **identity.model_dump(),
        "case_id": case_id,
        "final_answer": parsed.final_answer,
        "tool_calls": [],
        "turns": parsed.turns,
        "timing": timing.model_dump(),
        "usage": parsed.usage,
        "errors": errors,
        "completed": parsed.ok,
    }
