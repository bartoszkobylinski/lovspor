"""Build the pair manifest for one committed control/treatment pair.

Step "pair manifest" of the confirmatory ceremony (DECISIONS.md ruling
#30(d), ANALYSIS-PLAN §6): run only AFTER both arms are complete and
committed, from a clean checkout. The manifest binds the pair to the
exact bytes of the dataset, the analysis plan, the system prompt and both
``records.jsonl`` files, plus the scorer commit (HEAD at build time) —
``score_run.py`` refuses to aggregate anything it cannot verify against
this document.

Usage:
    uv run python benchmarks/llhb/runner/make_pair_manifest.py \
        --control llhb-v1-run-<...> --treatment llhb-v1-run-<...> \
        --analysis-plan benchmarks/llhb/ANALYSIS-PLAN-fable5-v1.md \
        [--out benchmarks/llhb/results/pair-manifests/<name>.json]
"""

import argparse
import sys
from pathlib import Path

from lovspor.errors import LovsporError
from lovspor.llhb.pair_manifest import build_pair_manifest, write_pair_manifest

LLHB_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = LLHB_DIR.parents[1]
RUNS_ROOT = LLHB_DIR / "results" / "runs"
MANIFESTS_DIR = LLHB_DIR / "results" / "pair-manifests"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True, help="run id of the control run")
    parser.add_argument("--treatment", required=True, help="run id of the treatment run")
    parser.add_argument("--analysis-plan", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_pair_manifest(
        REPO_ROOT,
        args.runs_root,
        (args.control, args.treatment),
        args.analysis_plan.resolve(),
    )
    out = args.out or MANIFESTS_DIR / f"{args.control}-vs-{args.treatment}.json"
    write_pair_manifest(manifest, out)
    # No verify here on purpose: the freshly-written manifest is itself an
    # uncommitted file, so the clean-tree pin would fail by construction.
    # The ceremony is: commit the manifest, then score_run verifies it.
    print(f"manifest: {out}")
    print(f"  scorer_commit:   {manifest.scorer_commit}")
    print(f"  control_sha256:  {manifest.control_run_sha256}")
    print(f"  treatment_sha256:{manifest.treatment_run_sha256}")
    print(f"  plan_sha256:     {manifest.analysis_plan_sha256}")
    print("commit this manifest, then run score_run.py --manifest against it")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LovsporError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
