"""Pre-Stage-7 diagnostic: how much do two runs of the same cases differ?

This is NOT the Stage 7 scorer and produces no publishable metric. It
exists so the numbers quoted in DECISIONS.md ruling #26 are
regenerable rather than asserted, and so their sensitivity to the
chosen phrase pattern is visible instead of hidden.

Two things it reports:

* **exact** — byte-identity of answers between the two runs, which
  needs no pattern at all;
* **proxy** — an abstention count under each of three explicitly
  written patterns, because a single hand-picked regex silently
  decides the answer (narrow vs broad differ by several cases).

Usage:
    uv run python benchmarks/llhb/runner/stability_proxy.py \
        --run-a llhb-v1-run-20260809-pilot2 \
        --run-b llhb-v1-run-20260809-pilot3
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

RUNS_ROOT = Path(__file__).resolve().parents[1] / "results" / "runs"

# Written out in full so a reader can reproduce every number below.
# All are matched case-insensitively against the answer text as stored.
PATTERNS: dict[str, str] = {
    "narrow": r"kan ikke belegge",
    "medium": r"kan ikke (belegge|oppgi|gi deg|med sikkerhet)|ikke belegge",
    "broad": r"kan ikke|ikke belegge|usikker|ikke verifisert|vet ikke",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-a", required=True, help="first run id")
    parser.add_argument("--run-b", required=True, help="second run id")
    return parser.parse_args()


def load_answers(run_id: str) -> dict[str, str]:
    """case_id -> final answer text (empty string when the case errored)."""
    path = RUNS_ROOT / run_id / "records.jsonl"
    if not path.is_file():
        raise SystemExit(f"no records at {path}")
    answers: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record: dict[str, Any] = json.loads(line)
        answers[str(record["case_id"])] = record.get("final_answer") or ""
    return answers


def byte_identity(a: dict[str, str], b: dict[str, str]) -> tuple[int, int]:
    """(identical answers, shared cases) — exact, pattern-free."""
    shared = sorted(set(a) & set(b))
    return sum(1 for case_id in shared if a[case_id] == b[case_id]), len(shared)


def proxy_counts(a: dict[str, str], b: dict[str, str], pattern: str) -> tuple[int, int, int]:
    """(matches in a, matches in b, cases whose match flips between runs)."""
    regex = re.compile(pattern, re.IGNORECASE)
    shared = sorted(set(a) & set(b))
    hits_a = [bool(regex.search(a[case_id])) for case_id in shared]
    hits_b = [bool(regex.search(b[case_id])) for case_id in shared]
    flips = sum(1 for x, y in zip(hits_a, hits_b, strict=True) if x != y)
    return sum(hits_a), sum(hits_b), flips


def main() -> int:
    args = parse_args()
    a, b = load_answers(args.run_a), load_answers(args.run_b)
    identical, shared = byte_identity(a, b)
    print(f"runs: {args.run_a} vs {args.run_b}")
    print(f"shared cases: {shared}")
    print(f"byte-identical answers: {identical}/{shared}  (exact, no pattern involved)\n")
    print(f"{'pattern':8} {'a':>5} {'b':>5} {'flips':>6}   regex")
    for name, pattern in PATTERNS.items():
        hits_a, hits_b, flips = proxy_counts(a, b, pattern)
        print(f"{name:8} {hits_a:>5} {hits_b:>5} {flips:>6}   {pattern}")
    print("\nProxy counts are pattern-sensitive by construction; only the")
    print("byte-identity line above is definition-free. Stage 7 replaces both.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
