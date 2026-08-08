"""Stage 4: select the frozen 250 and build the freeze artifacts (repo-only).

Runs the SELECTION.md rule (rulings #23/#24) over the two source pools
and, only when every gate passes AND ``--write`` is given, emits the
FREEZE.md artifacts under ``dataset/frozen/``:

    llhb-v1.jsonl            canonical dataset (250 cases)
    llhb-v1.lock.json        corpus + per-document pins, checksum
    selection-report.json    auditable walk: eligibles, picks, cap skips

Without ``--write`` this is a dry run: gates + selection + report to
stdout, nothing on disk. The freeze commit and the ``llhb-v1-freeze``
tag stay owner acts (FREEZE.md §2.5 sign-off included).

Usage:
    uv run python benchmarks/llhb/generator/select_freeze.py \
        --corpus /path/to/pinned-lovverk --lovspor-commit <sha> [--write]
"""

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.corpus_pin import current_pin, verify_pin
from lovspor.llhb.freeze import build_lock
from lovspor.llhb.review import completeness, load_decisions
from lovspor.llhb.schema import canonical_jsonl, load_schema, validate_case
from lovspor.llhb.selection import select
from lovspor.mcp import CorpusReader

LLHB_DIR = Path(__file__).resolve().parents[1]
CANDIDATES = LLHB_DIR / "dataset" / "candidates"
SCHEMA_PATH = LLHB_DIR / "schema" / "case.schema.json"

REVIEW_SURFACES: tuple[tuple[Path, str], ...] = (
    (CANDIDATES / "regen-v5", "v5 queue"),
    (CANDIDATES / "regen-v5" / "review-c2c4", "C2/C4 genericity slice"),
    (CANDIDATES / "topup-c4" / "review-full", "C4 top-up slice"),
)

SOURCE_POOLS: tuple[Path, ...] = (CANDIDATES / "regen-v5", CANDIDATES / "topup-c4")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _gate_reviews() -> dict[str, str]:
    """SELECTION.md §5.1: every review surface final; returns decisions."""
    merged: dict[str, str] = {}
    for surface, label in REVIEW_SURFACES:
        queue = _load_jsonl(surface / "manual-review.jsonl")
        decisions = load_decisions(surface / "manual-review-decisions.jsonl")
        report = completeness(queue, decisions)
        if not report.stage4_unblocked:
            raise LovsporError(f"review surface not final: {label} ({report.remaining} pending)")
        for decision in decisions:
            if decision.case_id in merged:
                raise LovsporError(f"case decided twice across surfaces: {decision.case_id}")
            merged[decision.case_id] = str(decision.decision)
    return merged


def _gate_pins(corpus: Path) -> Any:
    """SELECTION.md §5.2-3: pool pins match the corpus; pin on origin/main."""
    pin = current_pin(corpus)
    verify_pin(corpus, pin)
    for pool in SOURCE_POOLS:
        manifest = json.loads((pool / "generation-manifest.json").read_text(encoding="utf-8"))
        recorded = manifest["corpus_pin"]
        if recorded["lovverk_commit"] != pin.lovverk_commit:
            raise LovsporError(f"{pool.name}: pool pinned to {recorded['lovverk_commit']}")
        recorded_at = datetime.datetime.fromisoformat(recorded["manifest_generated_at"])
        if recorded_at != pin.manifest_generated_at:
            raise LovsporError(f"{pool.name}: manifest generated_at drifted")
    # S603/S607: git is a trusted system command invoked with list args
    subprocess.run(  # noqa: S603
        ["git", "-C", str(corpus), "fetch", "origin"],  # noqa: S607
        check=True,
        capture_output=True,
    )
    ancestor = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "-C",
            str(corpus),
            "merge-base",
            "--is-ancestor",
            pin.lovverk_commit,
            "origin/main",
        ],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise LovsporError(f"pin {pin.lovverk_commit} is not on lovverk origin/main")
    return pin


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--lovspor-commit", required=True)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    decisions = _gate_reviews()
    pin = _gate_pins(args.corpus)
    candidates = [c for pool in SOURCE_POOLS for c in _load_jsonl(pool / "candidates.jsonl")]
    result = select(candidates, decisions)
    schema = load_schema(SCHEMA_PATH)
    for case in result.selected:
        issues = validate_case(case, schema)
        if issues:
            raise LovsporError(f"selected case fails schema: {case['case_id']}: {issues}")
    _report(result)
    if args.write:
        _write(args, pin, result)
    else:
        print("dry run — no artifacts written (pass --write to freeze)")


def _report(result: Any) -> None:
    for category, row in sorted(result.report.items()):
        print(
            f"{category}: eligible {row['eligible']:3d}  selected {len(row['selected']):3d}"
            f"  cap-skipped {len(row['cap_skipped'])}"
        )
    print(f"total selected: {len(result.selected)}")


def _write(args: argparse.Namespace, pin: Any, result: Any) -> None:
    # Codex PR #48 finding: build EVERYTHING before touching disk — a lock
    # failure after a partial write would leave a half-frozen dataset.
    timestamp = args.timestamp or datetime.datetime.now(datetime.UTC).isoformat()
    dataset = canonical_jsonl(result.selected)
    lock = build_lock(
        result.selected,
        CorpusReader(args.corpus).manifest,
        pin,
        lovspor_commit=args.lovspor_commit,
        selection_rule=f"benchmarks/llhb/SELECTION.md@{args.lovspor_commit}",
        timestamp=timestamp,
    )
    lock_text = json.dumps(lock, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    report_text = json.dumps(result.report, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    out = LLHB_DIR / "dataset" / "frozen"
    out.mkdir(parents=True, exist_ok=True)
    (out / "llhb-v1.jsonl").write_bytes(dataset)
    (out / "llhb-v1.lock.json").write_text(lock_text, encoding="utf-8")
    (out / "selection-report.json").write_text(report_text, encoding="utf-8")
    print(f"frozen artifacts written to {out}")
    print(f"dataset sha256: {hashlib.sha256(dataset).hexdigest()}")
    sys.exit(0)


if __name__ == "__main__":
    main()
