#!/usr/bin/env python3
"""Turn a partial Codex round's own log into the failures it already found (#220).

A `codex-author` round can die after it has done real work: on PR #217 the
provider refused the model ("Selected model is at capacity") after the agent
had run pytest and had a named failing test in hand. The escalation then said
"no evidence about the implementation", and the finding survived only in the
job log. The owner's decision: no model fallback, but a partial round surfaces
the failing tests it already found in the sticky comment.

This reads the author's captured output and writes a Markdown section listing
every distinct pytest `FAILED <nodeid>` line, in the order first seen. An
empty file means the round found nothing, or left no log: the escalation then
appends nothing. Never fatal — a missing section downgrades the comment, it
must not silence it.

Usage: partial_round_failures.py --log <author log> --out <section.md>
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

# A long list is still one comment; the full log stays in the run.
MAX_LISTED = 30

# pytest's short-summary line: `FAILED <path>::<test>[ - <message>]`. Anchored
# after an optional runner timestamp, so prose that merely says FAILED is not
# a finding.
_FAILED = re.compile(r"^(?:\S+Z\s+)?(FAILED\s+\S+::\S+(?:\s+-\s+.*)?)$")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_HEADER = (
    "**The author's round ended partial (#220).** Before it did, pytest in its own "
    "session reported the tests below as FAILED. The round did not finish, so neither "
    "the author nor the verifier re-ran them against the final tree: read them as "
    "leads on the implementation, not as a verdict."
)


def found_failures(log: str) -> list[str]:
    """Every distinct `FAILED` summary line in `log`, first occurrence first."""
    found: list[str] = []
    for raw in log.splitlines():
        match = _FAILED.match(_ANSI.sub("", raw).strip())
        if match is None:
            continue
        line = match.group(1).replace("```", "'''")
        if line not in found:
            found.append(line)
    return found


def section(failures: list[str]) -> str:
    """The Markdown the escalation appends, or "" when there is nothing to say."""
    if not failures:
        return ""
    listed = failures[:MAX_LISTED]
    body = [_HEADER, "", "```text", *listed, "```"]
    left_out = len(failures) - len(listed)
    if left_out:
        body.append(f"_…and {left_out} more in the run log._")
    return "\n".join(body) + "\n"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.write_text(section(found_failures(_read(args.log))), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
