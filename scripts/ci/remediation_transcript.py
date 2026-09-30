#!/usr/bin/env python3
"""Tell a remediation round that never ran apart from one that changed nothing (#472).

On PR #469 the agent could not execute one command — the runner's
`codex-code-mode-host` was missing (#448) — yet `codex exec` exited 0, the patch
was empty, and the verifier posted the no-change wording: "12 survivor(s)
remediation called non-killable". No agent classified anything. A round whose
transcript holds no executed command is a remediation FAILURE, and says so.

The evidence is `codex exec`'s own transcript: every command it runs opens with
a line reading exactly `exec` (codex-cli 0.147, run 36531263464). A later CLI
that renamed the marker would make a round that ran read as one that did not;
that errs toward a human reading the round, never toward a verdict nobody gave.

Usage:
    remediation_transcript.py --log AGENT_LOG --result mutation-result.json

Prints one `not_run=<message>` line for $GITHUB_OUTPUT; the message is empty
when the agent ran a command, or when there is no transcript to judge.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from mutation_gate import _extract, _load_result, _not_killed, _tally

# GitHub's log view prefixes every line with a timestamp; the tee'd file does not.
_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")
_EXEC_MARKER = "exec"
_CODE_MODE_HOST = re.compile(r"failed to spawn code-mode host (?P<path>\S+?):")


def _lines(log: str) -> list[str]:
    return [_TIMESTAMP.sub("", line, count=1).rstrip() for line in log.splitlines()]


def not_run_reason(log: str) -> str:
    """Why the agent ran no command, or "" when it ran one."""
    lines = _lines(log)
    if _EXEC_MARKER in lines:
        return ""
    for line in lines:
        host = _CODE_MODE_HOST.search(line)
        if host:
            return (
                "the runner's Codex CLI could not start its execution tool:"
                f" {host.group('path')} is missing, #448"
            )
    return "the agent executed no command"


def _counts(result: Path) -> str:
    """The gate and the not-killed tally, which stay unclassified."""
    r = _load_result(result)
    extracted = r if isinstance(r, str) else _extract(r)
    if isinstance(r, str) or isinstance(extracted, str):
        return "Gate: FAIL. Unclassified: counts unavailable, see the artifact."
    return f"Gate: FAIL ({extracted[3]}). Unclassified: {_tally(_not_killed(r))}."


def not_run_message(log: str, result: Path) -> str:
    reason = not_run_reason(log)
    if not reason:
        return ""
    return (
        f"Mutation remediation did not run ({reason}) — no survivor was classified,"
        " and the round gave no non-killable or equivalence verdict."
        f" Remediation: FAILED (agent_did_not_run). {_counts(result)}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--result", type=Path, required=True)
    args = ap.parse_args()
    try:
        log = args.log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log = ""
    message = not_run_message(log, args.result) if log else ""
    print(f"not_run={message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
