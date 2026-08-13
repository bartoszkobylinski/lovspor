"""Run one LLHB arm over pilot cases (repo-only, dry by default).

One driver for both conditions, on purpose: fairness invariant 1 says
the arms differ in exactly one respect, and two separate scripts would
be two places for that to stop being true. ``--condition control`` runs
with no tools at all; ``--condition lovspor`` adds a local stdio
``lovspor mcp`` server bound to the pinned lovverk checkout, and
nothing else changes — same prompt bytes, same cases, same seed.

Without ``--execute`` this is a dry run: it composes and prints the
full run metadata and the exact ``claude`` argv for the first case. The
model CLI is never spawned (only ``git rev-parse`` runs, for the commit
fields, and the treatment path opens the pinned corpus locally to read
its tool surface) and nothing lands on disk.

Pilot scope (ruling #25): cases come from a candidates pool minus the
frozen 250 (the drops). The frozen dataset runs only through the one
explicit door: ``--frozen`` (Stage 9) selects the whole verified frozen
set, refuses the pilot-only knobs (``--limit``, ``--candidates``), and
labels the run as the frozen evaluation.

Auth: macOS keychain credentials do NOT survive the sandboxed HOME
(verified 2026-08-09, pilot1: 10/10 "Not logged in"), so ``--execute``
requires ``LLHB_CLAUDE_CODE_OAUTH_TOKEN`` in ``.env`` — a long-lived
subscription token minted by ``claude setup-token`` — passed to the
child as ``CLAUDE_CODE_OAUTH_TOKEN``. ``ANTHROPIC_API_KEY`` and
``ANTHROPIC_AUTH_TOKEN`` stay banned: they would flip the run onto
per-token billing. A treatment run also needs ``OPENAI_API_KEY``,
without which ``semantic_search`` is served but fails on every call —
a treatment arm quietly weaker than the one METHODOLOGY §5 describes.

Usage:
    uv run python benchmarks/llhb/runner/run_arm.py \
        --condition control --suffix pilot4 --limit 10 \
        --model claude-opus-5 [--execute]

    uv run python benchmarks/llhb/runner/run_arm.py \
        --condition lovspor --suffix treat1 --limit 10 \
        --model claude-opus-5 --corpus-path <pinned lovverk> [--execute]

    # Stage 9, the frozen evaluation:
    uv run python benchmarks/llhb/runner/run_arm.py \
        --condition control --suffix frozen1 --frozen \
        --model claude-opus-5 [--execute]

    # Stability subset (ruling #26): the committed 30 cases, one of 5 repeats:
    uv run python benchmarks/llhb/runner/run_arm.py \
        --condition control --suffix stabc1 --stability --repeat 1 \
        --model claude-opus-5 [--execute]
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from lovspor.errors import LovsporError
from lovspor.llhb.claude_cli import RunIdentity, ToolAccess, build_argv
from lovspor.llhb.corpus_pin import CorpusPin, verify_pin
from lovspor.llhb.mcp_surface import (
    server_config_json,
    tool_config,
    tool_surface,
    verify_server_command,
)
from lovspor.llhb.orchestrator import RunConfig, run_arm
from lovspor.llhb.results import ResultsStore, new_run_id
from lovspor.llhb.run_setup import (
    RunSpec,
    compose_run_metadata,
    pilot_cases,
    verify_frozen_against_lock,
)
from lovspor.llhb.schema import canonical_jsonl, dataset_checksum, load_cases_jsonl

LLHB_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = LLHB_DIR.parents[1]
PROMPT_PATH = LLHB_DIR / "runner" / "system-prompt-v1.txt"
FROZEN_JSONL = LLHB_DIR / "dataset" / "frozen" / "llhb-v1.jsonl"
FROZEN_LOCK = LLHB_DIR / "dataset" / "frozen" / "llhb-v1.lock.json"
STABILITY_SUBSET = LLHB_DIR / "dataset" / "frozen" / "llhb-v1-stability30.json"
DEFAULT_CANDIDATES = LLHB_DIR / "dataset" / "candidates" / "regen-v5" / "candidates.jsonl"
DEFAULT_SERVER_COMMAND = REPO_ROOT / ".venv" / "bin" / "lovspor"
RUNS_ROOT = LLHB_DIR / "results" / "runs"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", required=True, choices=["control", "lovspor"])
    parser.add_argument("--suffix", required=True, help="run-id suffix, [a-z0-9]{4,12}")
    parser.add_argument(
        "--frozen",
        action="store_true",
        help="run the whole frozen llhb-v1 dataset (Stage 9); excludes --limit/--candidates",
    )
    parser.add_argument("--limit", type=int, help="number of pilot cases (pilot runs only)")
    parser.add_argument(
        "--stability",
        action="store_true",
        help="run the committed 30-case stability subset (ruling #26); requires --repeat",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        choices=range(1, 6),
        help="stability repeat index, 1..5; recorded in the run notes",
    )
    parser.add_argument("--model", required=True, help="exact model id for the CLI")
    parser.add_argument("--candidates", type=Path, default=None)
    parser.add_argument("--corpus-path", type=Path, help="pinned lovverk checkout (lovspor arm)")
    parser.add_argument("--server-command", type=Path, default=DEFAULT_SERVER_COMMAND)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=int, default=600, help="per-case timeout, seconds")
    parser.add_argument(
        "--without-semantic-search",
        action="store_true",
        help="run the lovspor arm with no OPENAI_API_KEY, recording the weaker surface",
    )
    parser.add_argument("--execute", action="store_true", help="actually spawn the CLI")
    args = parser.parse_args(argv)
    pilot_knobs = args.limit is not None or args.candidates is not None
    if args.stability and (args.frozen or pilot_knobs):
        parser.error("--stability runs the committed subset; no --frozen/--limit/--candidates")
    if args.stability != (args.repeat is not None):
        parser.error("--stability and --repeat go together (repeat index 1..5, ruling #26)")
    if args.frozen and (args.limit is not None or args.candidates is not None):
        parser.error("--frozen runs the whole frozen dataset; --limit/--candidates are pilot-only")
    if not args.frozen and not args.stability and args.limit is None:
        parser.error("--limit is required for a pilot run (or pass --frozen / --stability)")
    if args.candidates is None:
        args.candidates = DEFAULT_CANDIDATES
    return args


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


def load_inputs(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frozen_cases = load_cases_jsonl(FROZEN_JSONL)
    lock = json.loads(FROZEN_LOCK.read_text(encoding="utf-8"))
    verify_frozen_against_lock(frozen_cases, lock)
    if args.frozen:
        return sorted(frozen_cases, key=lambda case: str(case["case_id"])), lock
    if args.stability:
        return stability_cases(frozen_cases, lock), lock
    frozen_ids = {str(case["case_id"]) for case in frozen_cases}
    candidates = load_cases_jsonl(args.candidates)
    return pilot_cases(candidates, frozen_ids, args.limit), lock


def stability_cases(
    frozen_cases: list[dict[str, Any]], lock: dict[str, Any]
) -> list[dict[str, Any]]:
    """The committed 30-case subset, fail-closed against any drift."""
    subset = json.loads(STABILITY_SUBSET.read_text(encoding="utf-8"))
    _check_subset_ids(subset, lock)
    wanted = {str(case_id) for case_id in subset["case_ids"]}
    picked = [case for case in frozen_cases if str(case["case_id"]) in wanted]
    if len(picked) != len(wanted):
        raise LovsporError(
            f"{len(wanted) - len(picked)} stability subset ids missing from the frozen dataset"
        )
    if dataset_checksum(canonical_jsonl(picked)) != subset["subset_sha256"]:
        raise LovsporError(
            "stability subset checksum does not match the cases it names; "
            "re-run select_stability_subset.py and review the diff"
        )
    return sorted(picked, key=lambda case: str(case["case_id"]))


def _check_subset_ids(subset: dict[str, Any], lock: dict[str, Any]) -> None:
    """The subset document must be internally consistent and drawn from
    the locked frozen dataset before any id is even looked up."""
    ids = [str(case_id) for case_id in subset["case_ids"]]
    if len(ids) != len(set(ids)):
        raise LovsporError(
            f"duplicate case ids in the stability subset: {len(set(ids))} unique "
            f"of {len(ids)} listed"
        )
    if subset["dataset_sha256"] != lock["dataset_sha256"]:
        raise LovsporError(
            "stability subset was drawn from a different frozen dataset; "
            "re-run select_stability_subset.py and review the diff"
        )


def treatment_config(args: argparse.Namespace, lock: dict[str, Any]) -> dict[str, Any]:
    """The tool config for a treatment run, after proving the pin.

    ``verify_pin`` is the fail-closed part: the treatment arm must serve
    the same corpus state the dataset was built against, and a checkout
    on the wrong commit or with a dirty tree serves content from no
    commit at all.
    """
    corpus_path = _required_corpus_path(args)
    verify_pin(corpus_path, CorpusPin(**lock["corpus_pin"]))
    verify_server_command(args.server_command)
    surface = tool_surface(corpus_path)
    return tool_config(surface, str(lock["corpus_pin"]["lovverk_commit"]))


def _required_corpus_path(args: argparse.Namespace) -> Path:
    if args.corpus_path is None:
        raise LovsporError("--corpus-path is required for --condition lovspor")
    return args.corpus_path.expanduser().resolve()


def tool_access(args: argparse.Namespace, config: dict[str, Any]) -> ToolAccess:
    """The argv-level tool access matching an already-verified config."""
    corpus_path = _required_corpus_path(args)
    return ToolAccess(
        mcp_config_json=server_config_json(args.server_command.resolve(), corpus_path),
        allowed_tools=tuple(config["tools"]),
    )


def compose(
    args: argparse.Namespace,
    cases: list[dict[str, Any]],
    lock: dict[str, Any],
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    now = datetime.datetime.now(datetime.UTC)
    head = git_head(REPO_ROOT)
    spec = RunSpec(
        run_id=new_run_id(now.strftime("%Y%m%d"), args.suffix),
        model_id=args.model,
        condition=args.condition,
        system_prompt_text=PROMPT_PATH.read_text(encoding="utf-8"),
        system_prompt_path=str(PROMPT_PATH.relative_to(REPO_ROOT)),
        lovspor_commit=head,
        lovverk_commit=str(lock["corpus_pin"]["lovverk_commit"]),
        runner_commit=head,
        case_order_seed=args.seed,
        started_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        tool_config=config,
        notes=_notes(args),
    )
    return compose_run_metadata(spec, cases)


def _notes(args: argparse.Namespace) -> str:
    # claude --version only on --execute: a dry run must spawn nothing.
    cli = claude_version() if args.execute else "not-captured-dry-run"
    degraded = (
        "; semantic_search is served but non-functional (no OPENAI_API_KEY): "
        "15 of 16 tools usable, embedding-based retrieval untested in this run"
        if args.condition == "lovspor" and args.without_semantic_search
        else ""
    )
    if args.stability:
        return (
            f"STABILITY subset 30x5 (ruling #26), repeat {args.repeat}/5 for this "
            f"provider-condition arm; cases drawn from the frozen llhb-v1 dataset "
            f"({STABILITY_SUBSET.name}); cli={cli}; the CLI exposes no temperature "
            f"or max-turn control, so both are recorded as unset{degraded}"
        )
    if args.frozen:
        return (
            f"FROZEN dataset llhb-v1 (Stage 9 evaluation); cli={cli}; the CLI "
            f"exposes no temperature or max-turn control, so both are recorded "
            f"as unset{degraded}"
        )
    return (
        f"pilot on discarded candidates from {args.candidates.name}; "
        f"NOT the frozen dataset; cli={cli}; the CLI exposes no temperature "
        f"or max-turn control, so both are recorded as unset{degraded}"
    )


def subscription_token() -> str:
    token = os.environ.get("LLHB_CLAUDE_CODE_OAUTH_TOKEN", "")
    if not token.startswith("sk-ant-oat"):
        raise LovsporError(
            "LLHB_CLAUDE_CODE_OAUTH_TOKEN missing from .env or not an sk-ant-oat "
            "subscription token; mint one with `claude setup-token`"
        )
    return token


def child_env(args: argparse.Namespace) -> dict[str, str]:
    """Credentials the child needs, and nothing else.

    ``.env`` is loaded here, once, before any lookup: leaving it to a
    side effect of whichever helper happens to run first makes the
    result depend on statement order rather than on configuration.
    """
    load_dotenv(REPO_ROOT / ".env")
    env = {"CLAUDE_CODE_OAUTH_TOKEN": subscription_token()}
    if args.condition == "control":
        return env
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        return {**env, "OPENAI_API_KEY": key}
    if not args.without_semantic_search:
        raise LovsporError(
            "OPENAI_API_KEY missing from .env; without it the MCP server still "
            "serves semantic_search but every call fails, so the treatment arm "
            "would be quietly weaker than the one METHODOLOGY §5 describes. "
            "Pass --without-semantic-search to accept that, recorded in notes."
        )
    return env


def execute(
    metadata: dict[str, Any],
    cases: list[dict[str, Any]],
    args: argparse.Namespace,
    access: ToolAccess | None,
) -> None:
    sandbox = RUNS_ROOT / ".sandbox" / str(metadata["run_id"])
    sandbox.mkdir(parents=True, exist_ok=True)
    config = RunConfig(
        identity=_identity(metadata),
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        case_order_seed=int(metadata["case_order_seed"]),
        timeout_s=args.timeout,
        sandbox_home=sandbox,
        tool_access=access,
        extra_env=child_env(args),
    )
    store = ResultsStore(runs_root=RUNS_ROOT, schema_dir=LLHB_DIR / "schema")
    print(json.dumps(run_arm(config, cases, metadata, store), indent=2))


def _identity(metadata: dict[str, Any]) -> RunIdentity:
    return RunIdentity(
        run_id=str(metadata["run_id"]),
        provider=str(metadata["provider"]),
        model_id=str(metadata["model_id"]),
        condition=str(metadata["condition"]),
    )


def _report_dry_run(
    metadata: dict[str, Any], case: dict[str, Any], access: ToolAccess | None
) -> None:
    argv = build_argv(_identity(metadata), str(case["question"]), "<system-prompt>", access)
    print("\nDRY RUN - first-case argv (prompt elided):")
    print(json.dumps(argv, ensure_ascii=False))
    if access is not None:
        print(f"\ntools offered: {len(access.allowed_tools)}")
    print("\nre-run with --execute to spawn the CLI")


def main() -> int:
    args = parse_args()
    cases, lock = load_inputs(args)
    config = treatment_config(args, lock) if args.condition == "lovspor" else None
    access = tool_access(args, config) if config is not None else None
    metadata = compose(args, cases, lock, config)
    print(json.dumps(metadata, indent=2, ensure_ascii=False))
    if args.frozen:
        pool = "frozen llhb-v1"
    elif args.stability:
        pool = "stability-30 of frozen llhb-v1"
    else:
        pool = "drops only"
    print(f"\ncases: {len(cases)} ({pool}), runs root: {RUNS_ROOT}", flush=True)
    if not args.execute:
        _report_dry_run(metadata, cases[0], access)
        return 0
    execute(metadata, cases, args, access)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LovsporError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
