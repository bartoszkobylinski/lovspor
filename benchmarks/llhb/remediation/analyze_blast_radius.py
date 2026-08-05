"""Stage 3.6-A: deterministic root-cause blast-radius analysis (evidence only).

Reads the immutable Stage 3.5 review snapshot plus the candidate pool and
computes, per confirmed root cause, the OBJECTIVE affected-case lists that
stage D will quarantine automatically (owner decision 2026-08-05: objective
rule match → automatic quarantine, never automatic drop; borderline →
owner re-review). Writes:

    dataset/candidates/remediation/blast-radius.json

This script changes nothing: no candidate, no decision, no generator code.
Rules mirror the owner's review findings and are versioned here so the
later quarantine step is reproducible and auditable.

Usage:
    uv run python benchmarks/llhb/remediation/analyze_blast_radius.py \
        --corpus /path/to/pinned-lovverk
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.generation import topic_of
from lovspor.llhb.quotes import normalize_quote_text
from lovspor.mcp import CorpusReader

RULES_VERSION = "llhb-remediation-rules-v1"

DATA = Path(__file__).resolve().parents[1] / "dataset" / "candidates"

META_TOPIC = re.compile(
    r"virkeområde|verkeområde|definisjon|ikrafttred|ikraftset|iverkset|overgangs"
    r"|formål|innledende|alminnelige|avsluttende|sluttbestemmelser|fellesbestemmelser"
    r"|oppheving|endringer i andre|generelle bestemmelser",
)
"""Meta/structural heading topics that cannot anchor a discovery question
(RC4). Derived from the owner's C2/C8 drop notes; frozen with RULES_VERSION."""

VEILEDER_LAYER = re.compile(r"^\s*veileder", re.IGNORECASE)
"""Commentary layer: an embedded veileder heading is not a second statutory
section (owner decision; normative vedlegg with own § numbering stays)."""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _topic(reader: CorpusReader, case: dict[str, Any]) -> str | None:
    get_sec = case.get("ground_truth_evidence", {}).get("get_section")
    if not get_sec:
        return None
    try:
        section = reader.get_section(str(get_sec["slug"]), str(get_sec["section_id"]))
    except LovsporError:
        return None
    return topic_of(str(section["heading"]))


def _rc1(cases: list[dict[str, Any]]) -> list[str]:
    return [c["case_id"] for c in cases if c.get("subcategory") == "repealed-as-current"]


def _rc2(cases: list[dict[str, Any]]) -> list[str]:
    return [c["case_id"] for c in cases if c.get("subcategory") == "duplicate-section-id"]


def _rc3(reader: CorpusReader, cases: list[dict[str, Any]]) -> list[str]:
    """RC2 cases whose duplication exists ONLY via a veileder commentary layer."""
    affected = []
    for case in cases:
        if case.get("subcategory") != "duplicate-section-id":
            continue
        slug, section_id = str(case["expected_act_slug"]), str(case["expected_section_id"])
        chapters = [
            str(row["parent_chapter"] or "")
            for row in reader.list_sections(slug)
            if str(row["section_id"]) == section_id
        ]
        non_veileder = [c for c in chapters if not VEILEDER_LAYER.match(c)]
        if len(chapters) > 1 and len(non_veileder) <= 1:
            affected.append(case["case_id"])
    return affected


def _rc4(reader: CorpusReader, cases: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(objective, borderline) generic-topic discovery cases — C2 only.

    C8 has no unreviewed population (all 25 were owner-reviewed); C1 is
    exempt (the act is named, the owner kept every reviewed C1)."""
    objective, borderline = [], []
    for case in cases:
        if case["category"] != "C2":
            continue
        topic = _topic(reader, case)
        if topic is None:
            continue
        if META_TOPIC.search(topic):
            objective.append(case["case_id"])
        elif len(topic.split()) <= 2:  # noqa: PLR2004 — frozen rule constant
            borderline.append(case["case_id"])
    return objective, borderline


def _rc5(cases: list[dict[str, Any]]) -> list[str]:
    return [c["case_id"] for c in cases if c.get("subcategory") == "nonexistent-support"]


def _rc6(reader: CorpusReader, cases: list[dict[str, Any]]) -> dict[str, list[str]]:
    spans, mutations, fabricated = [], [], []
    for case in cases:
        if case["category"] != "C7":
            continue
        if case["subcategory"] == "authentic":
            ref = case["quote_ref"]
            body = normalize_quote_text(
                str(
                    reader.get_section(
                        str(ref["slug"]), str(ref["section_id"]), ref.get("occurrence")
                    )["body"],
                ),
            )
            end = int(ref["char_span"][1])
            if end < len(body) and body[end - 1] not in ".!?":
                spans.append(case["case_id"])
        elif case["subcategory"] == "modified":
            mutations.append(case["case_id"])
        else:
            fabricated.append(case["case_id"])
    return {"authentic_span": spans, "modified": mutations, "fabricated": fabricated}


def _rc7(reader: CorpusReader, cases: list[dict[str, Any]]) -> list[str]:
    affected = []
    for case in cases:
        if case.get("citation_exists") is not False or not case.get("claimed_section_id"):
            continue
        slug, trap = str(case["claimed_act_slug"]), str(case["claimed_section_id"])
        try:
            ids = {str(row["section_id"]) for row in reader.list_sections(slug)}
        except LovsporError:
            continue
        sibling = any(
            i.startswith(f"{trap}-") or re.fullmatch(re.escape(trap) + r"\s?[a-z]", i) for i in ids
        )
        if sibling:
            affected.append(case["case_id"])
    return affected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    args = parser.parse_args()
    reader = CorpusReader(args.corpus)
    cases = _load_jsonl(DATA / "candidates.jsonl")
    decisions = {d["case_id"]: d for d in _load_jsonl(DATA / "manual-review-decisions.jsonl")}
    rc4_objective, rc4_borderline = _rc4(reader, cases)
    report = {
        "rules_version": RULES_VERSION,
        "pool_size": len(cases),
        "owner_review": {
            "keep": sorted(c for c, d in decisions.items() if d["decision"] == "keep"),
            "drop": sorted(c for c, d in decisions.items() if d["decision"] == "drop"),
            "needs_fix": sorted(c for c, d in decisions.items() if d["decision"] == "needs_fix"),
        },
        "rc1_tombstone": {"action": "quarantine", "cases": _rc1(cases)},
        "rc2_duplicate_metadata": {"action": "remediate-metadata", "cases": _rc2(cases)},
        "rc3_veileder_false_positive": {
            "action": "await-parser-fix-then-reclassify",
            "cases": _rc3(reader, cases),
        },
        "rc4_generic_topic": {
            "objective": {"action": "quarantine", "cases": sorted(rc4_objective)},
            "borderline": {"action": "owner-review", "cases": sorted(rc4_borderline)},
        },
        "rc5_c6_framing": {"action": "regenerate-wording", "cases": _rc5(cases)},
        "rc6_c7_quotes": {"action": "regenerate-quote-material", **_rc6(reader, cases)},
        "rc7_trap_near_miss": {"action": "quarantine", "cases": _rc7(reader, cases)},
    }
    out = DATA / "remediation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "blast-radius.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    summary = {
        key: (
            {k: len(v["cases"]) for k, v in value.items() if isinstance(v, dict)}
            if key == "rc4_generic_topic"
            else len(value.get("cases", []))
            if isinstance(value, dict) and "cases" in value
            else None
        )
        for key, value in report.items()
        if key.startswith("rc")
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
