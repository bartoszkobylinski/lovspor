"""Generate layer 3 for the frozen Opus pair (ruling #30(a), issue #168).

Reads the two committed arms, verifies they are the pair this analysis is
defined for, scores them with the frozen scorer at the pinned corpus, and writes
the supplementary artifact next to the layers it does not replace.

No model is called anywhere in this path: the scorer is cue- and
structure-based, so running it over frozen answers at a verified corpus pin is a
reproducibility exercise, not a new measurement.

The existing reports are never touched. Layer 1 (metrics as scored at freeze)
and layer 2 (the scorer-v2 diagnostic) stay exactly where they are; ruling
#30(a) keeps three layers, none deleted, none rewritten.

Usage:
    uv run python benchmarks/llhb/runner/posthoc_supplementary.py \
        --corpus-path ~/Programming/Python/lovverk/.claude/worktrees/llhb-pin
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.corpus_pin import CorpusPin, verify_pin
from lovspor.llhb.posthoc import (
    CONTROL_RUN_ID,
    TREATMENT_RUN_ID,
    supplementary_report,
    verify_frozen_pair,
)
from lovspor.llhb.reporting import score_arm
from lovspor.llhb.results import ResultsStore
from lovspor.llhb.schema import load_cases_jsonl
from lovspor.llhb.scoring import CaseScorer
from lovspor.mcp import CorpusReader

LLHB_DIR = Path(__file__).resolve().parents[1]
RUNS_ROOT = LLHB_DIR / "results" / "runs"
SCHEMA_DIR = LLHB_DIR / "schema"
DATASET = LLHB_DIR / "dataset" / "frozen" / "llhb-v1.jsonl"
OUT_JSON = LLHB_DIR / "results" / "reports" / "opus-frozen-pair-posthoc-supplementary-v1.json"
#: The default markdown path. Not read by `main`, which derives the path
#: from whatever `--out` actually is — it stands as the sentinel a test
#: patches to prove that a custom output never touches the committed one.
OUT_MD = OUT_JSON.with_suffix(".md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-path", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    parser.add_argument("--out", type=Path, default=OUT_JSON)
    return parser.parse_args()


def build_report(corpus_path: Path, runs_root: Path) -> dict[str, Any]:
    """Verify, score, aggregate — in that order, refusing before reading."""
    verify_frozen_pair(
        runs_root / CONTROL_RUN_ID / "records.jsonl",
        runs_root / TREATMENT_RUN_ID / "records.jsonl",
    )
    cases = load_cases_jsonl(DATASET)
    verify_pin(corpus_path, CorpusPin(**cases[0]["corpus_pin"]))
    cases_by_id = {str(case["case_id"]): case for case in cases}
    store = ResultsStore(runs_root=runs_root, schema_dir=SCHEMA_DIR)
    scorer = CaseScorer(CorpusReader(corpus_path))
    control = score_arm(scorer, cases_by_id, store.read_records(CONTROL_RUN_ID))
    treatment = score_arm(scorer, cases_by_id, store.read_records(TREATMENT_RUN_ID))
    return supplementary_report(control, treatment)


#: Metrics whose per-answer mean is a proportion — every case contributes 0 or 1,
#: so a percentage is the honest rendering.
PROPORTIONS = ("unconditional_h1", "citation_coverage")

#: Metrics that count instances, of which one answer can carry several. A
#: percentage here is not merely ugly, it is false: 431 valid instances over 250
#: answers is 1.72 per answer, and "172.4%" invites reading it as a rate of
#: something. The plan says these are reported as volumes, never hidden behind a
#: rate, and the rendering has to honour that too.
VOLUMES = ("valid_citation_instances", "invalid_citation_instances")

REPORTED_METRICS = PROPORTIONS + VOLUMES


def _cell(metric: dict[str, Any], name: str) -> str:
    mean = metric["mean_per_answer"]
    return f"{mean * 100:.1f}%" if name in PROPORTIONS else f"{mean:.2f} per answer"


def _row(name: str, control: dict[str, Any], treatment: dict[str, Any]) -> str:
    c, t = control[name], treatment[name]
    return (
        f"| {name.replace('_', ' ')} | {c['count']} | {_cell(c, name)} "
        f"| {t['count']} | {_cell(t, name)} |"
    )


def render_markdown(report: dict[str, Any]) -> str:
    """The same numbers with the label a reader cannot skip."""
    control = report["metrics"]["control"]
    treatment = report["metrics"]["treatment"]
    rows = "\n".join(_row(name, control, treatment) for name in REPORTED_METRICS)
    unscored = (
        f"control {control['unscored']}, treatment {treatment['unscored']}, "
        f"out of {report['cases_per_arm']} each"
    )
    return f"""# POST-HOC SUPPLEMENTARY ANALYSIS — NOT CONFIRMATORY

Layer 3 of the frozen Opus pair's published lineage (DECISIONS.md ruling #30(a)).
It does not replace layer 1 (metrics as scored at freeze) or layer 2 (the
scorer-v2 diagnostic results); all three stand.

**This is not a confirmatory result and cannot become one.** The scorer-v2 cue
extensions were informed by inspecting this pair's answers — calibration on
evaluation data. The aggregation below is careful; that does not change what the
scorer learned.

- control: `{report["control_run"]}`
- treatment: `{report["treatment_run"]}`
- scorer: `{report["semantic_scorer"]}`, {report["cases_per_arm"]} cases per arm

| metric | control n | control | treatment n | treatment |
| --- | ---: | ---: | ---: | ---: |
{rows}

Counts sit beside every rate on purpose: invalid citation instances are a
volume, and a rate without its numerator hides how much evidence is behind it.

Cases carrying no score — a terminal model or scorer error — are counted rather
than folded in: **{unscored}**. Each contributes
nothing to every numerator above, so an uncounted one is indistinguishable from a
clean answer. That is the defect ruling #30(c) closed for C8, and it would return
through a denominator that quietly contains cases nobody could measure.

Generated by `benchmarks/llhb/runner/posthoc_supplementary.py`, which refuses
any pair but this one. No model was called: the scorer is cue- and
structure-based, so this is a re-aggregation of frozen answers, not a re-run.
"""


def main() -> int:
    args = parse_args()
    try:
        report = build_report(args.corpus_path, args.runs_root)
    except LovsporError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    # The markdown follows the JSON wherever it goes. Writing it to a fixed
    # path while the JSON moved would have let a run with --out overwrite the
    # committed markdown from a report nobody committed — the two files are
    # meant to be the same document in two forms, and a test already pins that
    # they agree.
    markdown_path = args.out.with_suffix(".md")
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"wrote {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
