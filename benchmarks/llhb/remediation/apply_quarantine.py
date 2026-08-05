"""Stage 3.6-D: automatic quarantine from the versioned blast radius.

Owner ruling (DECISIONS.md #16): an objective, versioned rule match from a
review-confirmed defect class means automatic quarantine (exclusion from
the eligible pool), never automatic drop. Borderline classification stays
with the owner. Owner decisions from the immutable Stage 3.5 snapshot are
carried through; a kept case that still matches an objective rule is
quarantined fail-closed and flagged as a conflict for Stage F re-review.

Reads (all committed):
    dataset/candidates/candidates.jsonl
    dataset/candidates/manual-review-decisions.jsonl
    dataset/candidates/remediation/blast-radius.json
    dataset/candidates/remediation/c5-rescan.json   (RC3 resolution evidence)

Writes:
    dataset/candidates/remediation/quarantine.jsonl  (one row per pool case)

Status precedence per case (first match wins):
    1. owner drop                      -> dropped
    2. objective rule match            -> quarantined (reasons = rule ids)
    3. owner needs_fix                 -> quarantined (owner-needs-fix)
    4. rc4 borderline                  -> owner-review
    5. otherwise                       -> eligible

RC3 is no longer "await parser fix": the layer-aware rescan proves the
affected sections are veileder echoes. This script verifies that evidence
against c5-rescan.json and refuses to run on a mismatch.

Usage:
    uv run python benchmarks/llhb/remediation/apply_quarantine.py
"""

import json
import sys
from pathlib import Path
from typing import Any

RULES_VERSION = "llhb-remediation-rules-v1"
DATA = Path(__file__).resolve().parents[1] / "dataset" / "candidates"

_OBJECTIVE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rc1-tombstone", ("rc1_tombstone", "cases")),
    ("rc2-duplicate-metadata", ("rc2_duplicate_metadata", "cases")),
    ("rc3-veileder-echo", ("rc3_veileder_false_positive", "cases")),
    ("rc4-generic-topic", ("rc4_generic_topic", "objective", "cases")),
    ("rc5-c6-framing", ("rc5_c6_framing", "cases")),
    ("rc6-c7-quote-span", ("rc6_c7_quotes", "authentic_span")),
    ("rc6-c7-modified", ("rc6_c7_quotes", "modified")),
    ("rc6-c7-fabricated", ("rc6_c7_quotes", "fabricated")),
    ("rc7-trap-near-miss", ("rc7_trap_near_miss", "cases")),
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _dig(report: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    node: Any = report
    for key in keys:
        node = node[key]
    return [str(case_id) for case_id in node]


def _rules_by_case(report: dict[str, Any]) -> dict[str, list[str]]:
    rules: dict[str, list[str]] = {}
    for rule_id, keys in _OBJECTIVE_RULES:
        for case_id in _dig(report, keys):
            rules.setdefault(case_id, []).append(rule_id)
    return rules


def _verify_rc3(report: dict[str, Any], cases: dict[str, dict[str, Any]]) -> None:
    """RC3 quarantine must rest on rescan evidence, never on assertion."""
    rescan = json.loads((DATA / "remediation" / "c5-rescan.json").read_text(encoding="utf-8"))
    verdicts = {
        (doc["slug"], section_id): entry["verdict"]
        for doc in rescan["documents"]
        for section_id, entry in doc["ids"].items()
    }
    for case_id in _dig(report, ("rc3_veileder_false_positive", "cases")):
        case = cases[case_id]
        key = (str(case["expected_act_slug"]), str(case["expected_section_id"]))
        if verdicts.get(key) != "veileder-echo-only":
            sys.exit(f"RC3 evidence mismatch for {case_id}: {key} -> {verdicts.get(key)!r}")


def _status(owner: str | None, rules: list[str], borderline: bool) -> tuple[str, list[str]]:
    if owner == "drop":
        return "dropped", rules
    if rules:
        return "quarantined", rules
    if owner == "needs_fix":
        return "quarantined", ["owner-needs-fix"]
    if borderline:
        return "owner-review", ["rc4-borderline"]
    return "eligible", []


def main() -> None:
    report = json.loads((DATA / "remediation" / "blast-radius.json").read_text(encoding="utf-8"))
    cases = {str(c["case_id"]): c for c in _load_jsonl(DATA / "candidates.jsonl")}
    decisions = {
        str(d["case_id"]): str(d["decision"])
        for d in _load_jsonl(DATA / "manual-review-decisions.jsonl")
    }
    _verify_rc3(report, cases)
    rules_by_case = _rules_by_case(report)
    borderline = set(_dig(report, ("rc4_generic_topic", "borderline", "cases")))
    rows = []
    for case_id, case in sorted(cases.items()):
        owner = decisions.get(case_id)
        status, reasons = _status(owner, rules_by_case.get(case_id, []), case_id in borderline)
        rows.append(
            {
                "case_id": case_id,
                "category": str(case["category"]),
                "owner_decision": owner,
                "status": status,
                "reasons": reasons,
                "owner_conflict": owner == "keep" and status == "quarantined",
                "rules_version": RULES_VERSION,
            }
        )
    _write(rows)


def _write(rows: list[dict[str, Any]]) -> None:
    out = DATA / "remediation" / "quarantine.jsonl"
    out.write_text(
        "".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1
    conflicts = [str(r["case_id"]) for r in rows if r["owner_conflict"]]
    print(f"quarantine ledger written to {out}")
    print(json.dumps({"by_status": counts, "owner_conflicts": conflicts}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
