"""Stage 5 control-arm orchestrator (ruling #25).

Spawns the provider CLI once per case in a hermetic environment,
retains raw stdout under ``raw/<case_id>.json`` in the run directory,
assembles schema-valid records via ``claude_cli`` and appends them to a
``ResultsStore``.

Hermeticity is fail-closed: the child environment is built from a
whitelist, never inherited wholesale. ``ANTHROPIC_API_KEY`` is banned
outright — in ``-p`` mode a present key silently outranks subscription
OAuth and would move the whole run onto per-token billing — and HOME
points at a per-run sandbox so user-level settings, hooks and MCP
configuration cannot leak into the benchmark conversation.
"""

import json
import os
import random
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from lovspor.errors import LovsporError
from lovspor.llhb.claude_cli import (
    CaseTiming,
    ParsedCliResult,
    RunIdentity,
    build_control_argv,
    build_result_record,
    parse_cli_output,
)
from lovspor.llhb.results import ResultsStore

_RAW_DIR = "raw"
_CASE_ID_RE = re.compile(r"^llhb-v1-C[1-8]-[0-9]{3}$")
# Keys that would defeat the sandbox or flip billing if a caller smuggled
# them in through extra_env — each one fails closed instead of merging.
_BANNED_ENV = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "HOME", "CLAUDE_CONFIG_DIR"})


class OrchestratorError(LovsporError):
    """A run cannot proceed without violating an orchestration invariant."""


class ControlRunConfig(BaseModel):
    """Everything one control-arm run needs beyond the dataset itself."""

    identity: RunIdentity
    system_prompt: str
    case_order_seed: int
    timeout_s: int = 600
    sandbox_home: Path
    extra_env: dict[str, str] = {}


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


def execute_argv(argv: list[str], env: dict[str, str], timeout_s: int) -> CliInvocation:
    """Run one CLI process; a timeout becomes a result, not an exception."""
    started = time.monotonic()
    try:
        # Bytes on purpose: text=True decodes strictly, so one non-UTF-8 byte
        # in CLI output would raise instead of becoming an error record.
        completed = subprocess.run(  # noqa: S603 — argv list built in-repo, shell never used
            argv, capture_output=True, env=env, timeout=timeout_s, check=False
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


def run_control_arm(
    config: ControlRunConfig,
    cases: list[dict[str, Any]],
    metadata: dict[str, Any],
    store: ResultsStore,
) -> dict[str, Any]:
    """Execute every case of one control-arm run and finalize its metadata."""
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


def _run_case(config: ControlRunConfig, case: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    argv = build_control_argv(config.identity, str(case["question"]), config.system_prompt)
    started_at = _utc_now()
    env = hermetic_env(config.sandbox_home, config.extra_env)
    invocation = execute_argv(argv, env, config.timeout_s)
    case_id = str(case["case_id"])
    timing = CaseTiming(started_at=started_at, total_ms=invocation.duration_ms)
    record = build_result_record(config.identity, case_id, _parse(invocation), timing)
    record["raw_response_ref"] = _write_raw(run_dir, case_id, invocation)
    return record


def _parse(invocation: CliInvocation) -> ParsedCliResult:
    if invocation.timed_out:
        return ParsedCliResult(ok=False, error="CLI timed out before completing the case")
    return parse_cli_output(invocation.stdout, invocation.returncode)


def _write_raw(run_dir: Path, case_id: str, invocation: CliInvocation) -> str:
    if not _CASE_ID_RE.match(case_id):
        raise OrchestratorError(f"invalid case id {case_id!r} for raw retention")
    raw_dir = run_dir / _RAW_DIR
    raw_dir.mkdir(exist_ok=True)
    payload = json.dumps(invocation.model_dump(), sort_keys=True, ensure_ascii=False, indent=2)
    (raw_dir / f"{case_id}.json").write_text(payload + "\n", encoding="utf-8")
    return f"{_RAW_DIR}/{case_id}.json"


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
