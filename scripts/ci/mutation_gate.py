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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("result", type=Path)
    args = ap.parse_args()

    try:
        r = json.loads(args.result.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"cannot read mutation result: {e}", file=sys.stderr)
        return 1

    if r.get("schema_version") != 1:
        print(f"unsupported schema_version: {r.get('schema_version')}", file=sys.stderr)
        return 1

    m = r["mutants"]
    if args.summary:
        print("## Mutation testing")
        print(f"- SHA: `{r['commit']}`")
        print(
            f"- Total: {m['total']} · Killed: {m['killed']} · Survived: {m['survived']}"
            f" · Timeout: {m['timeout']} · Score: {r['score']}"
        )
        print(f"- Gate: {'PASS' if r['gate']['passed'] else 'FAIL'} ({r['gate']['reason']})")
        print(f"- Artifact: `mutation-result-{r['commit']}`")
        return 0

    if r["gate"]["passed"]:
        print(f"mutation gate PASS ({r['gate']['reason']})")
        return 0
    print(f"mutation gate FAIL ({r['gate']['reason']}); survivors: {m['survived']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
