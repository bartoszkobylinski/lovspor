"""Coverage and failure taxonomy over legal-time replay evidence files (#744, phase 2).

Reads the JSON reports ``lovspor ops legal-time replay`` writes and prints one
JSON document: per act, the members read, the operation kinds, the timing
statuses and their reasons, the end-state buckets, the problems behind every
unresolved operation, the unsupported instruction shapes, the verification
counts and the state ladder. Nothing here derives a date or a verdict; it only
counts what the reports already state, so the report is reproducible from the
same evidence files.

    uv run python scripts/legal_time_coverage.py docs/research/data/legal-time-replay-*.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_SHORT_HEAD_WORDS = 6  # a reason's head is a class name when it is this short; else it is prose


def _problem_class(problem: str) -> str:
    head = problem.split(":", maxsplit=1)[0]
    short = head.count(" ") < _SHORT_HEAD_WORDS
    return head[:70] if ":" in problem and short else problem[:70]


def _end_state(report: dict[str, Any]) -> dict[str, Any]:
    end = report["states"][-1]
    buckets = ("applied", "future", "pending", "unresolved", "withheld", "act_level_problems")
    problems = Counter(_problem_class(problem) for _, problem in end["unresolved"])
    act_level = Counter(_problem_class(p.split(": ", 1)[1]) for p in end["act_level_problems"])
    return {
        "at": end["at"],
        "status": end["status"],
        "buckets": {key: len(end[key]) for key in buckets},
        "unresolved_reasons": dict(problems.most_common()),
        "act_level_reasons": dict(act_level.most_common()),
    }


def _events(report: dict[str, Any]) -> dict[str, Any]:
    events = report["events"]
    ops = [event["operation"] for event in events]
    timing = Counter(event["timing"]["status"] for event in events)
    reasons = Counter(
        event["timing"]["reason"]
        for event in events
        if event["timing"]["status"] != "dated" and event["timing"]["reason"]
    )
    unsupported = Counter(op["instruction"][:90] for op in ops if op["kind"] == "unsupported")
    return {
        "events": len(events),
        "kinds": dict(Counter(op["kind"] for op in ops).most_common()),
        "timing": dict(timing),
        "timing_reasons": dict(reasons.most_common()),
        "unsupported_instructions": dict(unsupported.most_common(25)),
    }


def _verification(report: dict[str, Any]) -> dict[str, Any]:
    verifications = report.get("verification") or []
    if not verifications:
        return {"counts": None}
    last = verifications[-1]
    return {
        "counts": last["counts"],
        "text_differs": [s["key"] for s in last["sections"] if s["verdict"] == "text_differs"][:40],
        "notes_without_replay_event": len(last["notes_without_replay_event"]),
        "replay_events_without_note": len(last["replay_events_without_note"]),
    }


def _ladder(report: dict[str, Any]) -> list[dict[str, Any]]:
    keys = ("applied", "future", "pending", "unresolved", "withheld", "act_level_problems")
    return [
        {"at": state["at"], "status": state["status"], **{k: len(state[k]) for k in keys}}
        for state in report["states"]
    ]


def taxonomy(path: Path) -> dict[str, Any]:
    """Everything the coverage report says about one act's evidence file."""
    report = json.loads(path.read_text(encoding="utf-8"))
    members = Counter(member["role"] for member in report["members"])
    unattributed = sum(member.get("unattributed_operations") or 0 for member in report["members"])
    baseline = report["baseline_timing"]
    return {
        "act": report["act"],
        "replay_version": report["replay_version"],
        "archives": [{"archive": a["archive"], "sha256": a["sha256"]} for a in report["archives"]],
        "members": dict(members),
        "unattributed_operations": unattributed,
        "baseline": {k: baseline[k] for k in ("status", "effective", "reason")},
        **_events(report),
        "end_state": _end_state(report),
        "verification": _verification(report),
        "ladder": _ladder(report),
    }


def main(paths: list[str]) -> None:
    out = {Path(p).stem.split("-")[-1]: taxonomy(Path(p)) for p in sorted(paths)}
    print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
