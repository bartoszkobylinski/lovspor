"""LLHB Stage 3.5 manual-review CLI (repo-only, owner-facing).

Subcommands:
    packets      regenerate review packets + the empty decisions template
    status       completeness report (counts + what remains)
    check        Stage 4 gate: exits 1 while review is incomplete
    show ID      print one case's full review item
    show-source ID --corpus PATH   materialize referenced statutory text
                 locally from a pinned corpus checkout (prints, never writes)
    review --reviewer NAME [--category CX]   interactive loop over pending
                 cases; every decision is saved immediately, resume any time

The decisions file is benchmarks/llhb/dataset/candidates/
manual-review-decisions.jsonl. Only the owner fills it; this tool never
decides anything by itself and consults no model output.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lovspor.llhb.quotes import QuoteRef, materialize_quote
from lovspor.llhb.review import (
    REASONS_BY_DECISION,
    ReviewDecision,
    ReviewItem,
    build_review_items,
    completeness,
    init_decisions,
    load_decisions,
    near_duplicate_clusters,
    save_decisions,
)
from lovspor.mcp import CorpusReader

DATA_DIR = Path(__file__).resolve().parents[1] / "dataset" / "candidates"
DECISIONS_PATH = DATA_DIR / "manual-review-decisions.jsonl"
REVIEW_DIR = DATA_DIR / "review"


def _point_at(data_dir: Path) -> None:
    """Re-target every path at another pool's artifact dir (Stage 3.6-F:
    the regen queue gets its own decisions file; the immutable Stage 3.5
    snapshot is never opened for writing through this tool)."""
    global DATA_DIR, DECISIONS_PATH, REVIEW_DIR  # noqa: PLW0603 — CLI-scoped config
    DATA_DIR = data_dir
    DECISIONS_PATH = data_dir / "manual-review-decisions.jsonl"
    REVIEW_DIR = data_dir / "review"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _load_all() -> tuple[list[dict[str, Any]], list[ReviewItem]]:
    queue = _load_jsonl(DATA_DIR / "manual-review.jsonl")
    candidates = _load_jsonl(DATA_DIR / "candidates.jsonl")
    ledger = _load_jsonl(DATA_DIR / "validation.jsonl")
    dedup = json.loads((DATA_DIR / "dedup-report.json").read_text(encoding="utf-8"))
    items = build_review_items(queue, candidates, ledger, dedup["near_duplicate_flags"])
    return queue, items


def _item_lines(item: ReviewItem) -> list[str]:
    lines = [
        f"### {item.case_id} — {item.category}/{item.subcategory} ({item.difficulty})",
        "",
        f"**Question:** {item.question}",
        "",
        f"- queued because: {', '.join(item.queue_reasons)}",
        f"- expected behaviour: `{item.expected_behaviour}`",
        f"- expected: `{item.expected_act_slug}` § `{item.expected_section_id}`"
        + (f" (occurrence {item.expected_occurrence})" if item.expected_occurrence else ""),
        f"- claimed: `{item.claimed_act_slug}` § `{item.claimed_section_id}`"
        f" (citation_exists: {item.citation_exists})",
        f"- validator: {item.validator_status}"
        + (f" — issues: {item.validator_issues}" if item.validator_issues else ""),
        f"- provenance: {item.provenance.get('method')} "
        f"(generator {str(item.provenance.get('generator_commit'))[:7]})",
    ]
    if item.near_duplicates:
        lines.append(f"- near-duplicates: {', '.join(item.near_duplicates)}")
    if item.fabricated_quote_text:
        lines.append(f"- fabricated/modified quote text: «{item.fabricated_quote_text}»")
    if item.quote_ref:
        ref = item.quote_ref
        lines.append(
            f"- quote_ref (materialize locally, text NOT stored): slug=`{ref['slug']}` "
            f"section=`{ref['section_id']}` span={ref.get('char_span')} "
            f"sha256=`{str(ref['sha256_normalized'])[:16]}…`"
        )
        lines.append(
            f"  → `uv run python benchmarks/llhb/review/review_cli.py "
            f"show-source {item.case_id} --corpus <pinned-lovverk>`"
        )
    evidence = json.dumps(item.ground_truth_evidence, ensure_ascii=False)
    lines.append(f"- ground-truth evidence: `{evidence}`")
    for note in item.structural_notes:
        lines.append(f"- NOTE: {note}")
    lines.append("")
    return lines


def _packet(title: str, intro: str, items: list[ReviewItem]) -> str:
    lines = [f"# {title}", "", intro, "", f"Cases: {len(items)}", ""]
    for item in items:
        lines.extend(_item_lines(item))
    return "\n".join(lines)


def cmd_packets() -> None:
    queue, items = _load_all()
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    by_id = {i.case_id: i for i in items}
    c5 = [i for i in items if i.category == "C5"]
    c8 = [i for i in items if i.category == "C8"]
    dedup = json.loads((DATA_DIR / "dedup-report.json").read_text(encoding="utf-8"))
    clusters = near_duplicate_clusters(dedup["near_duplicate_flags"])
    stratified = [
        i
        for i in items
        if "stratified-10pct-sample" in i.queue_reasons and i.category not in ("C5", "C8")
    ]
    (REVIEW_DIR / "packet-A-c5.md").write_text(
        _packet(
            "Review packet A — C5 ambiguous citations (100% review)",
            "Decide per case: KEEP / DROP / NEEDS FIX. The ambiguity mechanism and its "
            "corpus evidence are stated per case; nothing was auto-classified.",
            c5,
        ),
        encoding="utf-8",
    )
    (REVIEW_DIR / "packet-B-c8.md").write_text(
        _packet(
            "Review packet B — C8 corpus-boundary (100% review)",
            "C8 is conservative by design: when absence of an adequate statutory answer "
            "cannot be reasonably established, DROP (scope-boundary-unclear).",
            c8,
        ),
        encoding="utf-8",
    )
    (REVIEW_DIR / "packet-C-near-duplicates.md").write_text(
        _near_dup_packet(clusters, by_id, items),
        encoding="utf-8",
    )
    (REVIEW_DIR / "packet-D-stratified.md").write_text(
        _packet(
            "Review packet D — stratified 10% sample (C1-C7)",
            "Judge linguistic naturalness, template artifacts, category correctness, "
            "believable adversarial construction and plausibility of a real user asking "
            "this. Legal correctness beyond the recorded deterministic ground truth is "
            "out of scope.",
            sorted(stratified, key=lambda i: i.case_id),
        ),
        encoding="utf-8",
    )
    if not DECISIONS_PATH.exists():
        save_decisions(DECISIONS_PATH, init_decisions(queue))
        print(f"decisions template created: {DECISIONS_PATH}")
    else:
        print(f"decisions file kept as-is: {DECISIONS_PATH}")
    print(f"packets written to {REVIEW_DIR} (queue {len(queue)}, C5 {len(c5)}, C8 {len(c8)})")


def _near_dup_packet(
    clusters: list[list[str]],
    by_id: dict[str, ReviewItem],
    items: list[ReviewItem],
) -> str:
    lines = [
        "# Review packet C — near-duplicate clusters",
        "",
        "Flagged by normalized-question token Jaccard ≥ 0.8 within a category. "
        "Choose per cluster: keep all / keep one / drop. Nothing was auto-removed.",
        "",
        f"Clusters: {len(clusters)}",
        "",
    ]
    queued_ids = {i.case_id for i in items}
    for number, cluster in enumerate(clusters, start=1):
        lines.append(f"## Cluster {number}")
        lines.append("")
        for case_id in cluster:
            item = by_id.get(case_id)
            if item is None:
                lines.append(
                    f"- {case_id}: (not in review queue — flagged partner only)"
                    if case_id not in queued_ids
                    else f"- {case_id}"
                )
                continue
            lines.append(
                f"- **{case_id}** [{item.category}] "
                f"exp `{item.expected_act_slug}` § `{item.expected_section_id}` — "
                f"“{item.question}”"
            )
        lines.append("")
    return "\n".join(lines)


def cmd_status() -> None:
    queue = _load_jsonl(DATA_DIR / "manual-review.jsonl")
    decisions = load_decisions(DECISIONS_PATH) if DECISIONS_PATH.exists() else []
    report = completeness(queue, decisions)
    print(json.dumps(report.model_dump(), indent=2, ensure_ascii=False))


def cmd_check() -> None:
    queue = _load_jsonl(DATA_DIR / "manual-review.jsonl")
    decisions = load_decisions(DECISIONS_PATH) if DECISIONS_PATH.exists() else []
    report = completeness(queue, decisions)
    if report.stage4_unblocked:
        print("Stage 4 UNBLOCKED — all manual-review dispositions final.")
        return
    print(
        f"Stage 4 BLOCKED — {report.remaining} manual-review decisions remain "
        f"(C5: {len(report.c5_remaining)}, C8: {len(report.c8_remaining)}, "
        f"needs_fix unresolved: {report.needs_fix}, "
        f"invalid records: {len(report.invalid_records)})."
    )
    sys.exit(1)


def cmd_show(case_id: str) -> None:
    _, items = _load_all()
    for item in items:
        if item.case_id == case_id:
            print("\n".join(_item_lines(item)))
            return
    sys.exit(f"{case_id} is not in the review queue")


def cmd_show_source(case_id: str, corpus: Path) -> None:
    _, items = _load_all()
    item = next((i for i in items if i.case_id == case_id), None)
    if item is None:
        sys.exit(f"{case_id} is not in the review queue")
    reader = CorpusReader(corpus)
    if item.quote_ref is not None:
        result = materialize_quote(reader, QuoteRef.model_validate(item.quote_ref))
        print(f"quote_ref status: {result.status}")
        print(result.text if result.text else result.reason)
        return
    slug = item.expected_act_slug or item.claimed_act_slug
    section = item.expected_section_id or item.claimed_section_id
    if slug is None or section is None:
        sys.exit("case has no statutory coordinates to materialize")
    print(reader.get_section(slug, section, item.expected_occurrence)["body"])


def cmd_review(reviewer: str, category: str | None) -> None:
    queue, items = _load_all()
    decisions = load_decisions(DECISIONS_PATH) if DECISIONS_PATH.exists() else init_decisions(queue)
    by_id = {d.case_id: d for d in decisions}
    pending = [
        i
        for i in items
        if by_id[i.case_id].decision is None and (category is None or i.category == category)
    ]
    print(f"{len(pending)} pending case(s). Enter k=keep d=drop f=needs_fix s=skip q=quit.")
    for item in pending:
        print("\n" + "\n".join(_item_lines(item)))
        if not _prompt_one(by_id[item.case_id], reviewer):
            break
        save_decisions(DECISIONS_PATH, decisions)
    save_decisions(DECISIONS_PATH, decisions)
    cmd_status()


def _prompt_one(record: ReviewDecision, reviewer: str) -> bool:
    choices = {"k": "keep", "d": "drop", "f": "needs_fix"}
    while True:
        answer = input("[k/d/f/s/q] > ").strip().lower()
        if answer == "q":
            return False
        if answer == "s":
            return True
        if answer in choices:
            decision = choices[answer]
            codes = REASONS_BY_DECISION[decision]
            for index, code in enumerate(codes, start=1):
                print(f"  {index}. {code}")
            picked = input("reason # > ").strip()
            if not picked.isdigit() or not 1 <= int(picked) <= len(codes):
                print("invalid reason number; case left pending")
                return True
            record.decision = decision
            record.reason_code = codes[int(picked) - 1]
            record.reviewer = reviewer
            record.reviewed_at = datetime.now(UTC).isoformat()
            record.notes = input("notes (optional) > ").strip() or None
            return True
        print("k=keep d=drop f=needs_fix s=skip q=quit")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Pool artifact dir to review (default: the Stage 3 pool). "
        "Stage 3.6-F: pass dataset/candidates/regen to review the "
        "regenerated queue with its own decisions file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("packets")
    sub.add_parser("status")
    sub.add_parser("check")
    show = sub.add_parser("show")
    show.add_argument("case_id")
    source = sub.add_parser("show-source")
    source.add_argument("case_id")
    source.add_argument("--corpus", type=Path, required=True)
    review = sub.add_parser("review")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--category", default=None)
    args = parser.parse_args()
    if args.data_dir is not None:
        _point_at(args.data_dir.resolve())
    if args.command == "packets":
        cmd_packets()
    elif args.command == "status":
        cmd_status()
    elif args.command == "check":
        cmd_check()
    elif args.command == "show":
        cmd_show(args.case_id)
    elif args.command == "show-source":
        cmd_show_source(args.case_id, args.corpus)
    else:
        cmd_review(args.reviewer, args.category)


if __name__ == "__main__":
    main()
