"""Descriptive summary of one run pair, from the versioned records only.

Behaviour, not quality: turn counts, tool usage, timings, answer
lengths. Nothing here scores an answer — Stage 7's deterministic scorer
does that, and no number printed here is a benchmark metric.

Reads `records.jsonl` alone, so it works on a clone where the
payload-bearing `raw/` and `tools/` directories were never versioned
(TOOLING.md, retention split). Every figure quoted in the docs for the
Stage 6 pilot is regenerable with:

    uv run python benchmarks/llhb/runner/pilot_summary.py \
        --control llhb-v1-run-20260809-pilot5 \
        --treatment llhb-v1-run-20260809-treat2
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

LLHB_DIR = Path(__file__).resolve().parents[1]
RUNS_ROOT = LLHB_DIR / "results" / "runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True, help="run id of the control run")
    parser.add_argument("--treatment", required=True, help="run id of the lovspor run")
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    return parser.parse_args()


def load_records(runs_root: Path, run_id: str) -> list[dict[str, Any]]:
    path = runs_root / run_id / "records.jsonl"
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return sorted(records, key=lambda record: str(record["case_id"]))


def summarize(label: str, records: list[dict[str, Any]]) -> None:
    calls = [len(record.get("tool_calls") or []) for record in records]
    turns = [record.get("turns") or 0 for record in records]
    seconds = [record["timing"]["total_ms"] / 1000 for record in records]
    lengths = [len(record.get("final_answer") or "") for record in records]
    print(f"\n=== {label}: {len(records)} cases")
    print(f"completed:     {sum(bool(record['completed']) for record in records)}/{len(records)}")
    print(f"turns:         total {sum(turns)}, per case {turns}")
    print(f"tool calls:    total {sum(calls)}, per case {calls}")
    print(f"seconds/case:  mean {sum(seconds) / len(seconds):.1f}, max {max(seconds):.1f}")
    print(f"answer chars:  mean {sum(lengths) / len(lengths):.0f}")
    _print_harness(records)


def _print_harness(records: list[dict[str, Any]]) -> None:
    offered = {len((record.get("harness") or {}).get("exposed_tools") or []) for record in records}
    connected = sum(
        1
        for record in records
        for server in (record.get("harness") or {}).get("mcp_servers") or []
        if server.get("status") == "connected"
    )
    denials = sum(
        len((record.get("harness") or {}).get("permission_denials") or []) for record in records
    )
    failed = sum(
        1 for record in records for call in record.get("tool_calls") or [] if call.get("is_error")
    )
    print(f"tools offered: {sorted(offered)} distinct counts across cases")
    print(f"servers connected: {connected}/{len(records)} cases; denied calls: {denials}")
    print(f"tool calls reporting an error: {failed}")
    names = Counter(
        str(call["name"]) for record in records for call in record.get("tool_calls") or []
    )
    if names:
        print("tools used:", dict(names.most_common()))


def print_per_case(control: list[dict[str, Any]], treatment: list[dict[str, Any]]) -> None:
    by_id = {str(record["case_id"]): record for record in treatment}
    print("\n=== per case: control answer chars -> treatment answer chars (tool calls)")
    for record in control:
        other = by_id.get(str(record["case_id"]))
        if other is None:
            print(f"{record['case_id']}: no treatment record")
            continue
        control_chars = len(record.get("final_answer") or "")
        treatment_chars = len(other.get("final_answer") or "")
        calls = len(other.get("tool_calls") or [])
        print(f"{record['case_id']}: {control_chars} -> {treatment_chars} ({calls} calls)")


def main() -> int:
    args = parse_args()
    control = load_records(args.runs_root, args.control)
    treatment = load_records(args.runs_root, args.treatment)
    summarize(f"control {args.control}", control)
    summarize(f"treatment {args.treatment}", treatment)
    print_per_case(control, treatment)
    print("\nDescriptive only. No answer here has been scored (Stage 7).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
