"""Stage 3.6-C: layer-aware full-corpus C5 rescan (evidence only).

Re-runs the duplicate-section-id scan on the pinned lovverk checkout with
the RC3 layer-aware parser (lovspor #26 / PR #27) and diffs the result
against the Stage 3 scan artifact (``dataset/candidates/ambiguity-scan.json``),
which predates the fix and counted veileder commentary echoes as sections.
Writes:

    dataset/candidates/remediation/c5-rescan.json

This script changes nothing: no candidate, no decision, no generator code.
The artifact is the evidence base for stage D (quarantine), stage E
(replacement generation) and stage G (feasibility vs the C5 target of 15
selected cases, METHODOLOGY.md).

Usage:
    uv run python benchmarks/llhb/remediation/rescan_c5.py \
        --corpus /path/to/pinned-lovverk
"""

import argparse
import json
from pathlib import Path
from typing import Any

from lovspor.llhb.corpus_pin import current_pin, verify_pin
from lovspor.llhb.generation import oracle_occurrences, scan_duplicate_ids
from lovspor.llhb.pool import DEFAULT_TARGETS
from lovspor.mcp import CorpusReader

RULES_VERSION = "llhb-c5-rescan-v1"
DATA = Path(__file__).resolve().parents[1] / "dataset" / "candidates"
BENCHMARK_C5_TARGET = 15
"""Selected-case target from METHODOLOGY.md §C5 — owner ruling: stays 15;
any change is an explicit pre-freeze methodology amendment."""


def _occurrence_evidence(reader: CorpusReader, slug: str, section_id: str) -> list[dict[str, Any]]:
    """Every parsed occurrence of the id, with its layer and chapter."""
    return [
        {
            "occurrence": int(row["occurrence"]),
            "layer": str(row["layer"]),
            "parent_chapter": str(row["parent_chapter"] or ""),
        }
        for row in reader.list_sections(slug)
        if str(row["section_id"]) == section_id
    ]


def _id_entry(reader: CorpusReader, slug: str, section_id: str) -> dict[str, Any]:
    valid = oracle_occurrences(reader, slug, section_id)
    verdict = "real-ambiguity" if len(valid) > 1 else "veileder-echo-only"
    return {
        "verdict": verdict,
        "valid_occurrences": valid,
        "occurrences": _occurrence_evidence(reader, slug, section_id),
    }


def _document_report(reader: CorpusReader, finding: dict[str, Any]) -> dict[str, Any]:
    """Layer evidence for every id the pre-fix scan flagged in one document."""
    slug = str(finding["slug"])
    ids = {
        section_id: _id_entry(reader, slug, section_id)
        for section_id in sorted(finding["duplicates"])
    }
    return {"slug": slug, "doc_id": str(finding["doc_id"]), "ids": ids}


def _summary(documents: list[dict[str, Any]], rescan: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = [entry["verdict"] for doc in documents for entry in doc["ids"].values()]
    real = verdicts.count("real-ambiguity")
    return {
        "docs_before": len(documents),
        "docs_after": len(rescan),
        "ids_before": len(verdicts),
        "ids_real_ambiguity": real,
        "ids_veileder_echo_only": verdicts.count("veileder-echo-only"),
        "pool_target_c5": DEFAULT_TARGETS["C5"],
        "benchmark_target_c5": BENCHMARK_C5_TARGET,
        "feasible_vs_benchmark_target": real >= BENCHMARK_C5_TARGET,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    args = parser.parse_args()
    pin = current_pin(args.corpus)
    verify_pin(args.corpus, pin)
    reader = CorpusReader(args.corpus)
    stage3 = json.loads((DATA / "ambiguity-scan.json").read_text(encoding="utf-8"))
    documents = [_document_report(reader, finding) for finding in stage3]
    rescan = scan_duplicate_ids(reader)
    stage3_slugs = {str(f["slug"]) for f in stage3}
    report = {
        "rules_version": RULES_VERSION,
        "corpus_pin": {
            "lovverk_commit": pin.lovverk_commit,
            "manifest_generated_at": pin.manifest_generated_at.isoformat(),
        },
        "summary": _summary(documents, rescan),
        "documents": documents,
        "rescan": rescan,
        "new_since_stage3": [f for f in rescan if str(f["slug"]) not in stage3_slugs],
    }
    out = DATA / "remediation" / "c5-rescan.json"
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"rescan written to {out}")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
