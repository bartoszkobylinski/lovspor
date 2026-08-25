"""LLHB run orchestrator for both conditions (ruling #25).

Spawns the provider CLI once per case in a hermetic environment,
retains the raw transcript under ``raw/<case_id>.json`` in the run
directory, assembles schema-valid records via ``claude_cli`` and
appends them to a ``ResultsStore``.

Hermeticity is fail-closed and has two halves. The child environment is
built from a whitelist, never inherited wholesale: ``ANTHROPIC_API_KEY``
is banned outright — in ``-p`` mode a present key silently outranks
subscription OAuth and would move the whole run onto per-token billing
— and HOME points at a per-run sandbox. The child also *runs* in that
sandbox: the CLI discovers CLAUDE.md files from its working directory
upward, so a run started inside the repository silently answers with
this project's instructions in context (measured 2026-08-09; the
Stage 5 pilots were affected). An empty sandbox has nothing to find.
"""

import hashlib
import json
import os
import random
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from lovspor.errors import LovsporError
from lovspor.llhb.claude_cli import (
    CaseTiming,
    ParsedCliResult,
    RunIdentity,
    ToolAccess,
    ToolCall,
    build_argv,
    build_result_record,
    parse_stream_json,
)
from lovspor.llhb.openai_chat import (
    ChatEndpoint,
    ChatExchange,
    ChatSampling,
    build_request,
    parse_chat_completion,
    post_chat,
)
from lovspor.llhb.results import ResultsStore

# Counts `"type": "tool_use"` in the transcript text without walking events
# at all. Deliberately dumber than the parser: every undercount so far came
# from the block-walking logic, so the cross-check must not share it.
_TOOL_USE_RE = re.compile(r'"type"\s*:\s*"tool_use"')
# Seconds before retry N of a case (the last value repeats). Long enough
# to ride out a transient `529 Overloaded`, short enough that a full-arm
# run is not visibly slowed by a handful of retries (issue #80).
_RETRY_BACKOFF_S: tuple[float, ...] = (30.0, 60.0)
# `sh -c 'command not found'` and execute_argv's OSError path both mean the
# CLI binary itself is absent or broken — no retry can change that.
_CANNOT_EXECUTE = 127
_RAW_DIR = "raw"
_TOOLS_DIR = "tools"
_CASE_ID_RE = re.compile(r"^llhb-v1-C[1-8]-[0-9]{3}$")
# Keys that would defeat the sandbox or flip billing if a caller smuggled
# them in through extra_env — each one fails closed instead of merging.
_BANNED_ENV = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "HOME", "CLAUDE_CONFIG_DIR"})


class OrchestratorError(LovsporError):
    """A run cannot proceed without violating an orchestration invariant."""


class RunConfig(BaseModel):
    """Everything one run needs beyond the dataset itself.

    ``tool_access`` is the whole difference between the arms: ``None``
    for control, the pinned lovverk MCP server for treatment.
    """

    identity: RunIdentity
    system_prompt: str
    case_order_seed: int
    timeout_s: int = 600
    sandbox_home: Path
    tool_access: ToolAccess | None = None
    extra_env: dict[str, str] = {}
    # Total invocations allowed per case, first try included (issue #80).
    case_attempts: int = Field(default=3, ge=1)
    # ``claude-cli`` spawns the vendor CLI (ruling #25); ``openai-chat`` posts
    # one chat completion per case to ``chat_endpoint`` — control arm only.
    driver: Literal["claude-cli", "openai-chat"] = "claude-cli"
    chat_endpoint: ChatEndpoint | None = None
    chat_sampling: ChatSampling = ChatSampling()

    @model_validator(mode="after")
    def _check_driver(self) -> "RunConfig":
        if self.driver == "openai-chat":
            if self.chat_endpoint is None:
                raise ValueError("the openai-chat driver needs a chat_endpoint")
            if self.tool_access is not None:
                raise ValueError("the openai-chat driver serves the control arm only")
        elif self.chat_endpoint is not None:
            raise ValueError("chat_endpoint is only meaningful with the openai-chat driver")
        return self


class CliInvocation(BaseModel):
    """Raw outcome of one CLI process, before any parsing."""

    stdout: str
    stderr: str
    returncode: int
    duration_ms: int
    timed_out: bool = False


def case_order(case_ids: list[str], seed: int) -> list[str]:
    """Deterministic per-run shuffle, independent of input order."""
    duplicates = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    if duplicates:
        raise OrchestratorError(f"duplicate case ids: {duplicates}")
    ordered = sorted(case_ids)
    random.Random(seed).shuffle(ordered)  # noqa: S311 — reproducible ordering, not crypto
    return ordered


def hermetic_env(sandbox_home: Path, extra_env: dict[str, str]) -> dict[str, str]:
    """Whitelist-built child environment; never inherits the parent's."""
    banned = sorted(_BANNED_ENV & set(extra_env))
    if banned:
        raise OrchestratorError(
            f"extra_env keys {banned} are banned: credentials flip the run onto "
            "per-token billing, HOME/CLAUDE_CONFIG_DIR would defeat the sandbox"
        )
    env = {
        "HOME": str(sandbox_home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TERM": "dumb",
    }
    env.update(extra_env)
    return env


def execute_argv(argv: list[str], env: dict[str, str], timeout_s: int, cwd: Path) -> CliInvocation:
    """Run one CLI process; a timeout becomes a result, not an exception."""
    started = time.monotonic()
    try:
        # Bytes on purpose: text=True decodes strictly, so one non-UTF-8 byte
        # in CLI output would raise instead of becoming an error record.
        completed = subprocess.run(  # noqa: S603 — argv list built in-repo, shell never used
            argv, capture_output=True, env=env, cwd=cwd, timeout=timeout_s, check=False
        )
        stdout, stderr, returncode, timed_out = (
            _text(completed.stdout),
            _text(completed.stderr),
            completed.returncode,
            False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, returncode, timed_out = (_text(exc.stdout), _text(exc.stderr), -1, True)
    except OSError as exc:
        # Missing/non-executable CLI must become an error record, not abort the run.
        stdout, stderr, returncode, timed_out = ("", f"cannot execute {argv[0]}: {exc}", 127, False)
    duration_ms = int((time.monotonic() - started) * 1000)
    return CliInvocation(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        duration_ms=duration_ms,
        timed_out=timed_out,
    )


def run_arm(
    config: RunConfig,
    cases: list[dict[str, Any]],
    metadata: dict[str, Any],
    store: ResultsStore,
) -> dict[str, Any]:
    """Execute every case of one run and finalize its metadata."""
    ids = _checked_case_ids(cases)
    ordered = case_order(ids, config.case_order_seed)
    run_dir = store.open_run(metadata)
    by_id = {str(case["case_id"]): case for case in cases}
    completed_count = 0
    for case_id in ordered:
        record = _run_case(config, by_id[case_id], run_dir)
        store.append_record(record)
        completed_count += 1 if record["completed"] else 0
    summary = {
        "finished_at": _utc_now(),
        "cases_total": len(by_id),
        "cases_completed": completed_count,
        "errors_total": len(by_id) - completed_count,
    }
    store.finalize_run(config.identity.run_id, summary)
    return summary


def _checked_case_ids(cases: list[dict[str, Any]]) -> list[str]:
    """Every case id, validated before anything touches disk."""
    ids = [str(case.get("case_id", "")) for case in cases]
    invalid = sorted(case_id for case_id in ids if not _CASE_ID_RE.match(case_id))
    if invalid:
        raise OrchestratorError(f"invalid case ids: {invalid}")
    return ids


def _reconcile_tool_calls(record: dict[str, Any], invocation: CliInvocation) -> None:
    """Fail the run when the parsed trace disagrees with the transcript.

    Undercounting tool calls is the one error this pipeline cannot make
    and has made three times, each in a different part of the parser. A
    second, independent count means a fourth occurrence stops the run
    instead of becoming a number in a published result. It errs toward
    stopping: a literal `"type": "tool_use"` inside a tool payload would
    trip it, and a failed run is cheaper than a wrong one.
    """
    parsed = len(record["tool_calls"])
    in_transcript = len(_TOOL_USE_RE.findall(invocation.stdout))
    if parsed != in_transcript:
        raise OrchestratorError(
            f"case {record['case_id']}: the parser found {parsed} tool call(s) but the "
            f"transcript contains {in_transcript}; the run is stopped rather than "
            "reported, because a miscounted trace is the one result this benchmark "
            "must not publish"
        )


def _run_case(config: RunConfig, case: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """One case within a bounded retry budget (issue #80).

    A transient CLI failure (a `529 Overloaded` exit, a timeout) is
    retried after a backoff instead of burning the whole arm: scoring is
    fail-closed on incomplete records, so one unretried transient makes
    250 cases unscoreable. Every retry is recorded in the final record
    and the failed attempt's transcript is retained beside the run —
    silent self-repair would be silent loss, inverted.
    """
    case_id = str(case["case_id"])
    failures: list[dict[str, Any]] = []
    for attempt in range(1, config.case_attempts + 1):
        record, invocation, parsed = _attempt_case(config, case)
        if record["completed"] or not _retryable(invocation, attempt, config.case_attempts):
            record["errors"] = failures + record["errors"]
            record["tool_calls"] = _stored_tool_calls(run_dir, case_id, parsed.tool_calls)
            record["raw_response_ref"] = _write_raw(run_dir, case_id, invocation)
            return record
        failures.append(_retry_note(attempt, record))
        _retain_attempt(run_dir, case_id, invocation, attempt)
        time.sleep(_backoff_s(attempt))
    raise OrchestratorError(f"case {case_id}: retry loop exited without a record")


def _attempt_case(
    config: RunConfig, case: dict[str, Any]
) -> tuple[dict[str, Any], CliInvocation, ParsedCliResult]:
    if config.driver == "openai-chat":
        return _attempt_chat(config, case)
    argv = build_argv(
        config.identity, str(case["question"]), config.system_prompt, config.tool_access
    )
    started_at = _utc_now()
    env = hermetic_env(config.sandbox_home, config.extra_env)
    invocation = execute_argv(argv, env, config.timeout_s, config.sandbox_home)
    timing = CaseTiming(started_at=started_at, total_ms=invocation.duration_ms)
    parsed = _parse(invocation)
    record = build_result_record(config.identity, str(case["case_id"]), parsed, timing)
    _reconcile_tool_calls(record, invocation)
    return record, invocation, parsed


def _attempt_chat(
    config: RunConfig, case: dict[str, Any]
) -> tuple[dict[str, Any], CliInvocation, ParsedCliResult]:
    """One chat completion, recorded in the same shape as a CLI attempt."""
    if config.chat_endpoint is None:
        raise OrchestratorError("the openai-chat driver needs a chat_endpoint")
    request = build_request(
        config.identity, str(case["question"]), config.system_prompt, config.chat_sampling
    )
    started_at = _utc_now()
    exchange = post_chat(config.chat_endpoint, request)
    invocation = _as_invocation(exchange)
    timing = CaseTiming(started_at=started_at, total_ms=exchange.duration_ms)
    parsed = parse_chat_completion(exchange)
    record = build_result_record(config.identity, str(case["case_id"]), parsed, timing)
    _reconcile_tool_calls(record, invocation)
    return record, invocation, parsed


def _as_invocation(exchange: ChatExchange) -> CliInvocation:
    """The HTTP exchange in the shape the retry loop and raw retention expect.

    The response body stands in for the transcript, so the raw file keeps
    the thinking block the parser stripped, and the tool-count cross-check
    runs over it unchanged. A rejected credential maps to the exit code the
    retry loop already treats as permanent.
    """
    if exchange.permanent_failure:
        returncode = _CANNOT_EXECUTE
    elif exchange.succeeded:
        returncode = 0
    else:
        returncode = 1
    return CliInvocation(
        stdout=exchange.body,
        stderr=exchange.error or "",
        returncode=returncode,
        duration_ms=exchange.duration_ms,
        timed_out=exchange.timed_out,
    )


def _retryable(invocation: CliInvocation, attempt: int, budget: int) -> bool:
    """A missing/non-executable CLI (127) is permanent; anything else that
    failed — a nonzero exit, a timeout — is worth the bounded budget."""
    return attempt < budget and invocation.returncode != _CANNOT_EXECUTE


def _retry_note(attempt: int, record: dict[str, Any]) -> dict[str, Any]:
    first = record["errors"][0]["message"] if record["errors"] else "no error captured"
    return {
        "stage": "other",
        "message": f"attempt {attempt} failed and was retried: {first}",
        "at": _utc_now(),
    }


def _retain_attempt(run_dir: Path, case_id: str, invocation: CliInvocation, attempt: int) -> None:
    """Failed attempt's raw transcript, kept beside the final one."""
    raw_dir = _checked_dir(run_dir, case_id, _RAW_DIR)
    (raw_dir / f"{case_id}.attempt{attempt}.json").write_text(
        _raw_payload(invocation), encoding="utf-8"
    )


def _backoff_s(attempt: int) -> float:
    return _RETRY_BACKOFF_S[min(attempt, len(_RETRY_BACKOFF_S)) - 1]


def _parse(invocation: CliInvocation) -> ParsedCliResult:
    if invocation.timed_out:
        return ParsedCliResult(ok=False, error="CLI timed out before completing the case")
    return parse_stream_json(invocation.stdout, invocation.returncode)


def _stored_tool_calls(run_dir: Path, case_id: str, calls: list[ToolCall]) -> list[dict[str, Any]]:
    """Hash every tool payload and keep the bytes beside the run, not in it.

    A tool result is regenerable from (tool, arguments, corpus pin), so
    ruling #27 keeps it out of the versioned record: what stays is the
    SHA-256 an audit checks against and a reference to the payload
    beside the run. The key is dropped rather than set to null, and the
    schema constrains it to null, so no future writer can inline one.
    """
    stored: list[dict[str, Any]] = []
    for call in calls:
        entry = call.model_dump(mode="json", exclude={"result"})
        if call.result is None:
            stored.append(entry)
            continue
        canonical = _canonical(call.result)
        entry["result_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        entry["result_ref"] = _write_tool_payload(run_dir, case_id, call.index, canonical)
        stored.append(entry)
    return stored


def _write_tool_payload(run_dir: Path, case_id: str, index: int, canonical: str) -> str:
    tools_dir = _checked_dir(run_dir, case_id, _TOOLS_DIR)
    name = f"{case_id}-{int(index):03d}.json"
    (tools_dir / name).write_text(canonical + "\n", encoding="utf-8")
    return f"{_TOOLS_DIR}/{name}"


def _write_raw(run_dir: Path, case_id: str, invocation: CliInvocation) -> str:
    raw_dir = _checked_dir(run_dir, case_id, _RAW_DIR)
    (raw_dir / f"{case_id}.json").write_text(_raw_payload(invocation), encoding="utf-8")
    return f"{_RAW_DIR}/{case_id}.json"


def _raw_payload(invocation: CliInvocation) -> str:
    return json.dumps(invocation.model_dump(), sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def _checked_dir(run_dir: Path, case_id: str, name: str) -> Path:
    """A per-run subdirectory, refused unless the case id can name a file."""
    if not _CASE_ID_RE.match(case_id):
        raise OrchestratorError(f"invalid case id {case_id!r} for artifact retention")
    directory = run_dir / name
    directory.mkdir(exist_ok=True)
    return directory


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
