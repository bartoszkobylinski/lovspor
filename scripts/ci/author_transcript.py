#!/usr/bin/env python3
"""Fail a test-author round that executed no command (#489).

On PR #469 (head 99e00f7, run 36677467828) `codex-author` reported success and
`codex-tests` went green, while the independent test author never ran one
command: the runner's `codex-code-mode-host` was missing (#448), every tool
call failed to spawn, and `codex exec` still exited 0. A green check there
claimed an independent review that never happened.

The evidence is the one the remediation lane reads (#472): `codex exec` opens
every command it runs with a line reading exactly `exec`. Unlike that lane,
this one fails closed on a missing or empty transcript too — its own green
check is the claim, so no evidence must never read as success.

Usage:
    author_transcript.py --log CODEX_AUTHOR_LOG

Prints one `not_run=<reason>` line for $GITHUB_OUTPUT and exits 1 when the
round ran no command; the reason is empty, and the exit 0, when it ran one.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from remediation_transcript import not_run_reason

NO_TRANSCRIPT = "the round left no transcript"


def author_not_run(log: str) -> str:
    """Why the round ran no command, or "" when it ran one."""
    if not log.strip():
        return NO_TRANSCRIPT
    return not_run_reason(log)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        log = args.log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log = ""
    reason = author_not_run(log)
    print(f"not_run={reason}")
    return 1 if reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
