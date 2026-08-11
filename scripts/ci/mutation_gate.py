#!/usr/bin/env python3
"""Deterministic 0/1 gate over mutation-result.json.

NO policy lives here — `mutation_to_json.py` computes `gate`; this program reads
it, optionally prints a markdown summary, and exits. Never an LLM, never a
threshold decision.

Usage:
    mutation_gate.py result.json              # exit 0 if gate.passed else 1
    mutation_gate.py --summary result.json    # markdown job summary, exit 0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_result(path: Path) -> dict[str, object] | str:
    """Return the parsed result object, or an error message string.

    The artifact is untrusted data: a truncated or hand-mangled file must
    produce a clean failure, never a traceback.
    """
    try:
        r = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return f"cannot read mutation result: {e}"
    if not isinstance(r, dict):
        return f"malformed mutation result: not a JSON object ({type(r).__name__})"
    if r.get("schema_version") != 1:
        return f"unsupported schema_version: {r.get('schema_version')}"
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("result", type=Path)
    args = ap.parse_args()

    r = _load_result(args.result)
    if isinstance(r, str):
        print(r, file=sys.stderr)
        return 1

    try:
        m = r["mutants"]
        commit, score = r["commit"], r["score"]
        passed, reason = r["gate"]["passed"], r["gate"]["reason"]  # type: ignore[index]
        total, killed, survived, timeout = (
            m["total"],  # type: ignore[index]
            m["killed"],  # type: ignore[index]
            m["survived"],  # type: ignore[index]
            m["timeout"],  # type: ignore[index]
        )
    except (KeyError, TypeError) as e:
        print(f"malformed mutation result: {e!r}", file=sys.stderr)
        return 1

    if args.summary:
        print("## Mutation testing")
        print(f"- SHA: `{commit}`")
        print(
            f"- Total: {total} · Killed: {killed} · Survived: {survived}"
            f" · Timeout: {timeout} · Score: {score}"
        )
        print(f"- Gate: {'PASS' if passed else 'FAIL'} ({reason})")
        print(f"- Artifact: `mutation-result-{commit}`")
        return 0

    if passed:
        print(f"mutation gate PASS ({reason})")
        return 0
    print(f"mutation gate FAIL ({reason}); survivors: {survived}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
