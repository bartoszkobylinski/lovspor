"""Run LLHB Stage 3 candidate-pool generation against a pinned lovverk checkout.

Repo-only developer tool (not shipped). Fails closed unless the corpus
checkout verifies as a clean, pinned state. Writes all Stage 3
artifacts under ``benchmarks/llhb/dataset/candidates/``.

Usage:
    uv run python benchmarks/llhb/generator/generate_pool.py \
        --corpus /path/to/lovverk-at-origin-main \
        --out benchmarks/llhb/dataset/candidates \
        --lovspor-commit <sha-of-generator-code>
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lovspor.llhb.corpus_pin import current_pin, verify_pin
from lovspor.llhb.pool import GenerationRun, PoolConfig, PoolResult, generate_pool
from lovspor.llhb.schema import canonical_jsonl
from lovspor.mcp import CorpusReader

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "case.schema.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lovspor-commit", required=True)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--id-offset", type=int, default=0)
    parser.add_argument(
        "--timestamp",
        default=None,
        help="ISO datetime stamped into run metadata and every case's "
        "validation record. Defaults to now; pass the committed manifest's "
        "timestamp to reproduce an existing artifact byte-for-byte.",
    )
    return parser.parse_args()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.write_text(lines, encoding="utf-8")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = _parse_args()
    pin = current_pin(args.corpus)
    verify_pin(args.corpus, pin)
    now = datetime.fromisoformat(args.timestamp) if args.timestamp else datetime.now(UTC)
    run = GenerationRun(
        lovspor_commit=args.lovspor_commit,
        created=now.date().isoformat(),
        timestamp=now.isoformat(),
    )
    options: dict[str, Any] = {"schema_path": SCHEMA_PATH, "id_offset": args.id_offset}
    if args.seed is not None:
        options["seed"] = args.seed
    config = PoolConfig(**options)
    result = generate_pool(CorpusReader(args.corpus), pin, config, run)
    _write_artifacts(args.out, result)
    print(f"pool written to {args.out}")
    print(json.dumps(result.distribution["by_category"], sort_keys=True))
    print(f"rejected: {len(result.rejected)}  review-queue: {len(result.review_queue)}")


def _write_artifacts(out: Path, result: PoolResult) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidates.jsonl").write_bytes(canonical_jsonl(result.candidates))
    _write_jsonl(out / "validation.jsonl", result.ledger)
    _write_jsonl(out / "rejected.jsonl", result.rejected)
    _write_jsonl(out / "manual-review.jsonl", result.review_queue)
    _write_json(out / "generation-manifest.json", result.generation_manifest)
    _write_json(out / "distribution.json", result.distribution)
    _write_json(out / "dedup-report.json", result.dedup_report)
    _write_json(out / "ambiguity-scan.json", result.ambiguity_scan)
    _write_json(out / "act-name-calibration.json", result.name_calibration)
    (out / "REPORT.md").write_text(_report(result), encoding="utf-8")


def _report(result: PoolResult) -> str:
    manifest = result.generation_manifest
    distribution = result.distribution
    rejected_codes: dict[str, int] = {}
    for entry in result.rejected:
        for issue in entry["issues"]:
            rejected_codes[issue["code"]] = rejected_codes.get(issue["code"], 0) + 1
    c5 = [c for c in result.candidates if c["category"] == "C5"]
    c8 = [c for c in result.candidates if c["category"] == "C8"]
    lines = [
        "# LLHB v1 — Stage 3 candidate-pool report",
        "",
        "Pre-freeze development artifact. No benchmark model has been run;",
        "no final selection has been made; nothing here is frozen.",
        "",
        "## Corpus pin",
        "",
        f"- lovverk commit: `{manifest['corpus_pin']['lovverk_commit']}`",
        f"- manifest generated_at: `{manifest['corpus_pin']['manifest_generated_at']}`",
        f"- lovspor generator commit: `{manifest['lovspor_commit']}`",
        f"- generated: {manifest['timestamp']} (seed {manifest['seed']})",
        f"- versions: {json.dumps(manifest['versions'], sort_keys=True)}",
        "",
        "## Counts",
        "",
        f"- emitted: {sum(manifest['emitted_by_category'].values())} "
        f"({json.dumps(manifest['emitted_by_category'], sort_keys=True)})",
        f"- valid after validation + dedup: {len(result.candidates)} "
        f"({json.dumps(distribution['by_category'], sort_keys=True)})",
        f"- targets: {json.dumps(manifest['targets'], sort_keys=True)}",
        f"- rejected/quarantined: {len(result.rejected)} "
        f"(codes: {json.dumps(rejected_codes, sort_keys=True)})",
        f"- exact duplicates removed: {len(result.dedup_report['exact_removed'])}",
        f"- near-duplicate flags: {len(result.dedup_report['near_duplicate_flags'])}",
        f"- phrasing: {json.dumps(manifest['phrasing'], sort_keys=True)} "
        "(template-only; no LLM phrasing used)",
        "",
        "## Diversity",
        "",
        f"- acts inventoried: {manifest['acts_inventoried']}",
        f"- unique acts in pool: {distribution['unique_acts']}",
        f"- top acts: {json.dumps(distribution['top_acts'][:8])}",
        f"- section-id shapes: {json.dumps(distribution['section_id_shapes'], sort_keys=True)}",
        f"- max provision reuse within a category: {distribution['max_provision_reuse']}",
        "",
        "## C5 ambiguity population (real, not manufactured)",
        "",
        f"- duplicate-section-id documents in corpus: {len(result.ambiguity_scan)}",
        f"- C5 candidates emitted: {len(c5)} "
        f"(duplicate-id: {sum(1 for c in c5 if c['subcategory'] == 'duplicate-section-id')}, "
        f"repealed-as-current: {sum(1 for c in c5 if c['subcategory'] == 'repealed-as-current')})",
        "",
        "## Manual review",
        "",
        f"- queue size: {len(result.review_queue)}",
        f"- C8 candidates (100% mandatory review): {len(c8)}",
        "",
        "## Act-name calibration",
        "",
        "- "
        + json.dumps(
            {k: v for k, v in result.name_calibration.items() if k != "collision_keys"},
            sort_keys=True,
            ensure_ascii=False,
        ),
        f"- collision keys: {len(result.name_calibration['collision_keys'])}",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
