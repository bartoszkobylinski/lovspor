"""Stage 5: run the control arm over pilot cases (repo-only, dry by default).

Without ``--execute`` this is a dry run: composes and prints the full
run metadata and the exact ``claude`` argv for the first case. The
model CLI is never spawned (only ``git rev-parse`` runs, for the commit
fields) and nothing lands on disk. With ``--execute`` it opens the run
and writes records + raw output + finalized metadata under
``results/runs/<run-id>/``.

Pilot scope (ruling #25): cases come from a candidates pool minus the
frozen 250 (the drops) — the frozen dataset is never run before
Stage 9. ANTHROPIC_API_KEY never reaches the child process; the CLI
authenticates via subscription OAuth from a sandboxed HOME.

Usage:
    uv run python benchmarks/llhb/runner/run_control.py \
        --suffix pilot1 --limit 10 --model claude-opus-5 [--execute]
"""

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.claude_cli import RunIdentity, build_control_argv
from lovspor.llhb.orchestrator import ControlRunConfig, run_control_arm
from lovspor.llhb.results import ResultsStore, new_run_id
from lovspor.llhb.run_setup import (
    ControlRunSpec,
    compose_control_metadata,
    pilot_cases,
    verify_frozen_against_lock,
)
from lovspor.llhb.schema import load_cases_jsonl

LLHB_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = LLHB_DIR.parents[1]
PROMPT_PATH = LLHB_DIR / "runner" / "system-prompt-v1.txt"
FROZEN_JSONL = LLHB_DIR / "dataset" / "frozen" / "llhb-v1.jsonl"
FROZEN_LOCK = LLHB_DIR / "dataset" / "frozen" / "llhb-v1.lock.json"
DEFAULT_CANDIDATES = LLHB_DIR / "dataset" / "candidates" / "regen-v5" / "candidates.jsonl"
RUNS_ROOT = LLHB_DIR / "results" / "runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suffix", required=True, help="run-id suffix, [a-z0-9]{4,12}")
    parser.add_argument("--limit", type=int, required=True, help="number of pilot cases")
    parser.add_argument("--model", required=True, help="exact model id for the CLI")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=int, default=600, help="per-case timeout, seconds")
    parser.add_argument("--execute", action="store_true", help="actually spawn the CLI")
    return parser.parse_args()


def git_head(repo: Path) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def claude_version() -> str:
    try:
        result = subprocess.run(
            ["claude", "--version"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return result.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def load_inputs(args: argparse.Namespace) -> tuple[list[dict[str, Any]], str]:
    frozen_cases = load_cases_jsonl(FROZEN_JSONL)
    lock = json.loads(FROZEN_LOCK.read_text(encoding="utf-8"))
    verify_frozen_against_lock(frozen_cases, lock)
    frozen_ids = {str(case["case_id"]) for case in frozen_cases}
    candidates = load_cases_jsonl(args.candidates)
    cases = pilot_cases(candidates, frozen_ids, args.limit)
    return cases, str(lock["corpus_pin"]["lovverk_commit"])


def compose(args: argparse.Namespace, cases: list[dict[str, Any]], lovverk: str) -> dict[str, Any]:
    now = datetime.datetime.now(datetime.UTC)
    head = git_head(REPO_ROOT)
    # claude --version only on --execute: a dry run must spawn nothing.
    cli = claude_version() if args.execute else "not-captured-dry-run"
    spec = ControlRunSpec(
        run_id=new_run_id(now.strftime("%Y%m%d"), args.suffix),
        model_id=args.model,
        system_prompt_text=PROMPT_PATH.read_text(encoding="utf-8"),
        system_prompt_path=str(PROMPT_PATH.relative_to(REPO_ROOT)),
        lovspor_commit=head,
        lovverk_commit=lovverk,
        runner_commit=head,
        case_order_seed=args.seed,
        started_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        notes=(
            f"pilot on discarded candidates from {args.candidates.name}; "
            f"NOT the frozen dataset; cli={cli}"
        ),
    )
    return compose_control_metadata(spec, cases)


def execute(metadata: dict[str, Any], cases: list[dict[str, Any]], timeout_s: int) -> None:
    sandbox = RUNS_ROOT / ".sandbox" / str(metadata["run_id"])
    sandbox.mkdir(parents=True, exist_ok=True)
    config = ControlRunConfig(
        identity=_identity(metadata),
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        case_order_seed=int(metadata["case_order_seed"]),
        timeout_s=timeout_s,
        sandbox_home=sandbox,
    )
    store = ResultsStore(runs_root=RUNS_ROOT, schema_dir=LLHB_DIR / "schema")
    summary = run_control_arm(config, cases, metadata, store)
    print(json.dumps(summary, indent=2))


def _identity(metadata: dict[str, Any]) -> RunIdentity:
    return RunIdentity(
        run_id=str(metadata["run_id"]),
        provider=str(metadata["provider"]),
        model_id=str(metadata["model_id"]),
        condition=str(metadata["condition"]),
    )


def main() -> int:
    args = parse_args()
    cases, lovverk = load_inputs(args)
    metadata = compose(args, cases, lovverk)
    print(json.dumps(metadata, indent=2, ensure_ascii=False))
    print(f"\ncases: {len(cases)} (drops only), runs root: {RUNS_ROOT}", flush=True)
    if not args.execute:
        argv = build_control_argv(_identity(metadata), str(cases[0]["question"]), "<system-prompt>")
        print("\nDRY RUN - first-case argv (prompt elided):")
        print(json.dumps(argv, ensure_ascii=False))
        print("\nre-run with --execute to spawn the CLI")
        return 0
    execute(metadata, cases, args.timeout)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LovsporError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
