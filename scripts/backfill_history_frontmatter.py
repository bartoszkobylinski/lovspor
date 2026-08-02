"""One-off migration: backfill NLOD frontmatter into legacy ``history/*.md``.

2,580 history Markdown files (measured 2026-08-02: 150 lover + 2,430
forskrifter) predate the history frontmatter and carry no NLOD attribution,
violating the publication contract that every corpus Markdown file states its
licence. Their paired ``history/*.json`` is the structured source of truth
and regenerates every one of them with a byte-identical body — the read-only
analysis of 2026-08-02 classified all 2,580 diffs as frontmatter-only, with
zero anomalies. A file touched by a later real sync self-heals (that is how
the original 2,581 became 2,580); the dormant tail — including 2 tombstoned
documents and 468 histories whose manifest records predate the tombstone era
— will never be touched again, so it needs this one-off pass.

Fail-closed, all-or-nothing, two phases:

1. **Preflight** re-derives every legacy file from its JSON through the
   production renderer (``lovspor.history.render_history_markdown`` — the
   format is never reimplemented here) and proves, for the COMPLETE set,
   that the only change is the frontmatter block and that the JSON would
   not change at all. Any missing JSON, invalid JSON, body drift, or JSON
   round-trip drift aborts the whole migration with every anomaly listed
   and zero files written — a partial migration would leave the corpus
   claiming a contract it does not meet.
2. **Execution** (only behind ``--execute``; the default is a dry run)
   atomically replaces exactly the planned Markdown files. JSON, manifest,
   document Markdown and embeddings are never written. A second execution
   finds no legacy files and writes nothing.

The lovverk commit for the executed migration is intentionally a single
``migration:`` commit touching only ``*/history/*.md`` — history extraction
(ADR-0003) walks document paths only, so the commit is structurally
invisible to every document's event list.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from lovspor.atomic_io import atomic_write_text  # noqa: E402
from lovspor.history import HistoryRecord, render_history_markdown  # noqa: E402

DATASET_SUBDIRS = ("lover", "forskrifter")
FRONTMATTER_OPEN = "---\n"


@dataclass
class Plan:
    """Complete preflight result: every planned write, every anomaly."""

    writes: list[tuple[Path, str]] = field(default_factory=list)
    already_current: int = 0
    anomalies: list[str] = field(default_factory=list)


def build_plan(corpus_root: Path) -> Plan:
    """Phase 1: derive and verify the complete write set without writing.

    Every invariant failure is collected, none is fatal to the SCAN — the
    caller aborts on a non-empty anomaly list, so one broken file reports
    alongside every other broken file instead of masking them.
    """
    plan = Plan()
    for subdir in DATASET_SUBDIRS:
        history_dir = corpus_root / subdir / "history"
        if not history_dir.is_dir():
            plan.anomalies.append(f"MISSING_DIR: {subdir}/history")
            continue
        for md_path in sorted(history_dir.glob("*.md")):
            _preflight_one(plan, corpus_root, md_path)
    return plan


def _preflight_one(plan: Plan, corpus_root: Path, md_path: Path) -> None:
    rel = md_path.relative_to(corpus_root)
    text = md_path.read_text(encoding="utf-8")
    if text.startswith(FRONTMATTER_OPEN):
        plan.already_current += 1
        return
    json_path = md_path.with_suffix(".json")
    if not json_path.exists():
        plan.anomalies.append(f"MISSING_JSON: {rel}")
        return
    raw_json = json_path.read_text(encoding="utf-8")
    try:
        record = HistoryRecord.model_validate(json.loads(raw_json))
    except (json.JSONDecodeError, ValidationError) as exc:
        plan.anomalies.append(f"INVALID_JSON: {rel}: {type(exc).__name__}: {str(exc)[:120]}")
        return
    if _canonical_json(record) != raw_json:
        # The JSON is the source of truth and this migration must not owe it
        # a rewrite: a round-trip difference means the writer would churn it
        # on the document's next real event, contradicting "history JSON
        # files are unchanged" — surface it now instead.
        plan.anomalies.append(f"JSON_ROUNDTRIP_DRIFT: {rel.with_suffix('.json')}")
        return
    rendered = render_history_markdown(record)
    body = _body_after_frontmatter(rendered)
    if body is None:
        plan.anomalies.append(f"RENDER_SHAPE: {rel}: rendered output carries no frontmatter")
        return
    if body != text:
        plan.anomalies.append(
            f"BODY_MISMATCH: {rel}: regenerated body differs from the file on disk",
        )
        return
    plan.writes.append((md_path, rendered))


def _canonical_json(record: HistoryRecord) -> str:
    """Exactly the JSON payload ``lovspor.history.write_history`` produces."""
    return (
        json.dumps(record.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False)
        + "\n"
    )


def _body_after_frontmatter(rendered: str) -> str | None:
    """The rendered Markdown minus its frontmatter block and separator line.

    ``render_history_markdown`` emits ``---\\n<fields>\\n---\\n`` followed by
    a blank separator line and the body. The legacy files ARE that body, so
    equality here proves the migration adds the frontmatter and changes
    nothing else.
    """
    if not rendered.startswith(FRONTMATTER_OPEN):
        return None
    close = rendered.find("\n---\n", len(FRONTMATTER_OPEN))
    if close == -1:
        return None
    after = rendered[close + len("\n---\n") :]
    return after.removeprefix("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill NLOD frontmatter into legacy history/*.md (dry-run by default).",
    )
    parser.add_argument(
        "--corpus-path",
        type=Path,
        required=True,
        help="lovverk clone containing lover/ and forskrifter/",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="write the planned files; without this flag the script only reports",
    )
    args = parser.parse_args()

    plan = build_plan(args.corpus_path)
    print(
        f"history/*.md scanned: {plan.already_current + len(plan.writes) + len(plan.anomalies)} "
        f"(current: {plan.already_current}, legacy to migrate: {len(plan.writes)}, "
        f"anomalies: {len(plan.anomalies)})",
    )
    if plan.anomalies:
        print("\nABORT — preflight failed; zero files written. Every anomaly:")
        for anomaly in plan.anomalies:
            print(f"  {anomaly}")
        return 1
    if not args.execute:
        print("\nDry run (default): no files written. Re-run with --execute to migrate.")
        return 0
    for md_path, rendered in plan.writes:
        atomic_write_text(md_path, rendered)
    print(f"\nMigrated {len(plan.writes)} files (Markdown only; JSON untouched).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
