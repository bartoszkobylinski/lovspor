"""Build the Stage-4 pre-selection C2/C4 full-review slice (repo-only).

Owner audit finding (2026-08-08, pre-Stage-4 audit): the v5 stratified
sample measured a semantic-genericity defect rate the generator cannot
filter deterministically — 6 of 10 reviewed C2 and 1 of 5 reviewed C4
were dropped as ``ambiguous-ground-truth``. The unreviewed remainder of
those two categories would otherwise enter frozen-set selection sight
unseen, so every ELIGIBLE C2/C4 case not already individually reviewed
gets its own review round before selection.

Deterministic: inputs are the committed v5 pool artifacts and the
owner's v5 decisions; the slice is every C2/C4 candidate that is
neither in the v5 review queue nor dropped, ordered by case_id. Output
is a self-contained ``--data-dir`` for ``review_cli.py`` (queue, the
matching candidate/validation subsets, internal near-duplicate flags,
and an empty decisions template — only the owner fills it).

Usage:
    uv run python benchmarks/llhb/review/build_c2c4_slice.py \
        --pool benchmarks/llhb/dataset/candidates/regen-v5 \
        --out benchmarks/llhb/dataset/candidates/regen-v5/review-c2c4
"""

import argparse
import json
from pathlib import Path
from typing import Any

from lovspor.llhb.review import init_decisions, save_decisions

SLICE_REASON = "c2c4-genericity-slice-100pct"
SLICE_CATEGORIES = ("C2", "C4")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.write_text(lines, encoding="utf-8")


def slice_case_ids(
    candidates: list[dict[str, Any]],
    queue: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    *,
    include_queued: bool = False,
) -> list[str]:
    """C2/C4 cases with no individual review: not queued, not dropped.

    ``include_queued`` builds a FULL-category slice instead (top-up pools:
    every case is new material, the pool's own stratified queue would
    split the owner's decisions across two files)."""
    queued = set() if include_queued else {str(entry["case_id"]) for entry in queue}
    dropped = {str(d["case_id"]) for d in decisions if d.get("decision") == "drop"}
    return sorted(
        str(c["case_id"])
        for c in candidates
        if c["category"] in SLICE_CATEGORIES
        and str(c["case_id"]) not in queued
        and str(c["case_id"]) not in dropped
    )


def build_slice(
    pool: Path,
    out: Path,
    *,
    include_queued: bool = False,
    reason: str = SLICE_REASON,
) -> dict[str, int]:
    candidates = _load_jsonl(pool / "candidates.jsonl")
    ledger = _load_jsonl(pool / "validation.jsonl")
    queue = _load_jsonl(pool / "manual-review.jsonl")
    decisions_source = pool / "manual-review-decisions.jsonl"
    # a fresh pool has no decisions yet — nothing is dropped
    decisions = _load_jsonl(decisions_source) if decisions_source.exists() else []
    dedup = json.loads((pool / "dedup-report.json").read_text(encoding="utf-8"))
    ids = slice_case_ids(candidates, queue, decisions, include_queued=include_queued)
    id_set = set(ids)
    out.mkdir(parents=True, exist_ok=True)
    slice_queue = [{"case_id": case_id, "reasons": [reason]} for case_id in ids]
    _write_jsonl(out / "manual-review.jsonl", slice_queue)
    _write_jsonl(out / "candidates.jsonl", [c for c in candidates if c["case_id"] in id_set])
    _write_jsonl(out / "validation.jsonl", [r for r in ledger if r["case_id"] in id_set])
    flags = [f for f in dedup["near_duplicate_flags"] if f["a"] in id_set and f["b"] in id_set]
    (out / "dedup-report.json").write_text(
        json.dumps(
            {"exact_removed": [], "near_duplicate_flags": flags},
            sort_keys=True,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    decisions_path = out / "manual-review-decisions.jsonl"
    if not decisions_path.exists():
        save_decisions(decisions_path, init_decisions(slice_queue))
    return {"slice": len(ids), "flags": len(flags)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--include-queued",
        action="store_true",
        help="Full-category slice: keep cases from the pool's own queue in "
        "this slice too (top-up pools — one decisions file, no split).",
    )
    parser.add_argument("--reason", default=SLICE_REASON, help="Queue reason for slice rows.")
    args = parser.parse_args()
    counts = build_slice(
        args.pool, args.out, include_queued=args.include_queued, reason=args.reason
    )
    print(f"slice queue written to {args.out}: {counts['slice']} cases, {counts['flags']} flags")


if __name__ == "__main__":
    main()
