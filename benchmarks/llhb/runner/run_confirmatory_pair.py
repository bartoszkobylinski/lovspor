"""The confirmatory ceremony as a state machine that refuses to skip a step.

Ruling #30(d) fixes the order — plan hashed, run control, freeze/hash
control, run treatment, freeze/hash treatment, pair manifest, score,
report — and issue #169 asks for code that enforces it, so that in six
months nobody reconstructs the procedure from DECISIONS.md. This script
knows no results and makes no scientific decisions. It holds the host's
exclusive workload lock for the whole ceremony (issue #169), advances
through

    PRECHECKED -> CONTROL_RUNNING -> CONTROL_FROZEN -> TREATMENT_RUNNING
      -> TREATMENT_FROZEN -> FAIRNESS_VERIFIED -> MANIFEST_COMMITTED
      -> SCORED -> REPORTED

and commits every step it completes, so the ceremony is auditable from
git alone. ABORTED is the one terminal state off the path.

What it reads of a running arm is content-independent by construction
(ruling #30(d): completion status, MODEL_ERROR counts): each record's
``case_id`` and ``completed`` flag, nothing else, and only after the arm
finished. Response content stays unread until both arms are frozen —
the state file carries a field to disclose any known violation, because
the ruling says the report must, and no code can detect one.

Everything checkable without a model is checked first: clean checkout,
no foreign workload, frozen dataset against its lock, pinned corpus at
the pinned commit, tool surface, both arms' credentials, the CLI, disk.
Then the arms run in-process through ``run_arm.py``'s own helpers — not
as subprocesses, which would contend for the lock this process holds.

Usage:
    # Precheck only (no lock, no model call, no state written):
    uv run python benchmarks/llhb/runner/run_confirmatory_pair.py \\
        --model claude-fable-5 \\
        --corpus-path ~/Programming/Python/lovverk/.claude/worktrees/llhb-pin

    # The ceremony:
    uv run python benchmarks/llhb/runner/run_confirmatory_pair.py \\
        --model claude-fable-5 --corpus-path <pinned lovverk> --execute

    # Continue one that stopped after a frozen arm or a later step:
    uv run python benchmarks/llhb/runner/run_confirmatory_pair.py \\
        --model claude-fable-5 --corpus-path <pinned lovverk> \\
        --resume benchmarks/llhb/results/ceremonies/<ceremony-id>.json
"""

import argparse
import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import LovsporError
from lovspor.exclusive_workload import exclusive_workload
from lovspor.llhb.corpus_pin import git_head_sha, working_tree_clean
from lovspor.llhb.fairness import check_pair, frozen_violations, paired_completion_violations
from lovspor.llhb.metrics import MODEL_ERROR_MAX_PAIRS
from lovspor.llhb.pair_manifest import (
    build_pair_manifest,
    file_sha256,
    load_pair_manifest,
    records_path,
    verify_pair_manifest,
    write_pair_manifest,
)
from lovspor.sync.git_commit import add, commit, has_staged_changes

RUNNER_DIR = Path(__file__).resolve().parent
LLHB_DIR = RUNNER_DIR.parent
REPO_ROOT = LLHB_DIR.parents[1]
RUNS_ROOT = LLHB_DIR / "results" / "runs"
CEREMONIES_DIR = LLHB_DIR / "results" / "ceremonies"
MANIFESTS_DIR = LLHB_DIR / "results" / "pair-manifests"
DEFAULT_ANALYSIS_PLAN = LLHB_DIR / "ANALYSIS-PLAN-fable5-v1.md"
#: The name this process writes into the exclusive workload lock.
CEREMONY_WORKLOAD = "llhb-confirmatory-pair"
#: Below this the arms' raw transcripts and sandbox homes are at risk.
MIN_FREE_BYTES = 2 * 1024**3

State = Literal[
    "PRECHECKED",
    "CONTROL_RUNNING",
    "CONTROL_FROZEN",
    "TREATMENT_RUNNING",
    "TREATMENT_FROZEN",
    "FAIRNESS_VERIFIED",
    "MANIFEST_COMMITTED",
    "SCORED",
    "REPORTED",
    "ABORTED",
]
#: The path, in order. A ceremony resumes only from a state whose work is
#: committed — never from a RUNNING state, whose arm is half-written.
PATH: tuple[State, ...] = (
    "PRECHECKED",
    "CONTROL_RUNNING",
    "CONTROL_FROZEN",
    "TREATMENT_RUNNING",
    "TREATMENT_FROZEN",
    "FAIRNESS_VERIFIED",
    "MANIFEST_COMMITTED",
    "SCORED",
    "REPORTED",
)
RESUMABLE: frozenset[State] = frozenset(
    {"CONTROL_FROZEN", "TREATMENT_FROZEN", "FAIRNESS_VERIFIED", "MANIFEST_COMMITTED", "SCORED"}
)


def _load_script(name: str) -> ModuleType:
    """The runner scripts are files, not a package; load them the way the tests do."""
    path = RUNNER_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"llhb_{name}", path)
    if spec is None or spec.loader is None:
        raise LovsporError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: dataclasses and pydantic resolve a class's
    # annotations through sys.modules, and an unregistered module has none.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_arm_script = _load_script("run_arm")
check_fairness_script = _load_script("check_fairness")
score_run_script = _load_script("score_run")


class CeremonyError(LovsporError):
    """The ceremony refused a step. Nothing was skipped; the state says where it stands."""


class ArmRecord(BaseModel):
    """One arm as the ceremony saw it — counts and hashes, never content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    started_at: str
    finished_at: str | None = None
    cases_total: int | None = None
    cases_completed: int | None = None
    model_error_cases: int | None = None
    records_sha256: str | None = None
    freeze_commit: str | None = None


class Ceremony(BaseModel):
    """The ceremony's state file: what happened, in which order, bound to which bytes.

    Unknown fields are forbidden: this file is read back to decide what
    the ceremony may do next, and a field a newer writer added must not be
    dropped on the way.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    ceremony_id: str
    state: State
    model: str
    analysis_plan_path: str
    analysis_plan_sha256: str
    corpus_path: str
    corpus_commit: str
    runner_commit: str
    claude_cli: str
    model_error_abort_threshold: int
    started_at: str
    updated_at: str
    control: ArmRecord | None = None
    treatment: ArmRecord | None = None
    # Composed the moment the control arm finishes, BEFORE its freeze commit
    # moves HEAD: the fairness gate requires both arms to record the same
    # runner commit, and the freeze commit adds results, not runner code.
    # Kept here so a ceremony resumed after CONTROL_FROZEN runs the treatment
    # arm under the metadata it was bound to, not a fresh HEAD.
    treatment_metadata: dict[str, Any] | None = None
    model_error_pairs: int | None = None
    fairness_findings: list[str] = []
    tolerated_completion_findings: list[str] = []
    manifest_path: str | None = None
    manifest_commit: str | None = None
    report_path: str | None = None
    verdict: str | None = None
    confirmatory_eligible: bool | None = None
    # Ruling #30(d): "Any known violation MUST be disclosed in the run
    # report." Written by a human who knows of one; never inferred.
    content_inspection_violations: list[str] = []
    abort_reason: str | None = None
    history: list[str] = []


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_path(ceremony_id: str) -> Path:
    return CEREMONIES_DIR / f"{ceremony_id}.json"


def write_state(ceremony: Ceremony) -> Path:
    path = state_path(ceremony.ceremony_id)
    text = json.dumps(
        ceremony.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False
    )
    atomic_write_text(path, text + "\n")
    return path


def read_state(path: Path) -> Ceremony:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CeremonyError(f"{path}: unreadable ceremony state: {exc}") from exc
    try:
        return Ceremony.model_validate(data)
    except ValidationError as exc:
        raise CeremonyError(f"{path}: invalid ceremony state: {exc}") from exc


def advance(ceremony: Ceremony, state: State, note: str = "", **fields: Any) -> Ceremony:
    """Move to ``state`` and persist. Forward along PATH only, or to ABORTED."""
    if state != "ABORTED" and PATH.index(state) != PATH.index(ceremony.state) + 1:
        raise CeremonyError(f"cannot go from {ceremony.state} to {state}; a step would be skipped")
    now = _utc_now()
    entry = f"{now} {state}" + (f" — {note}" if note else "")
    updated = ceremony.model_copy(
        update={"state": state, "updated_at": now, "history": [*ceremony.history, entry], **fields}
    )
    write_state(updated)
    return updated


def ceremony_id_for(model: str, date_utc: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    return f"llhb-v1-ceremony-{date_utc}-{slug}"


# --- arguments ---------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--model", required=True, help="exact model id for the CLI, e.g. claude-fable-5"
    )
    parser.add_argument(
        "--analysis-plan",
        type=Path,
        default=DEFAULT_ANALYSIS_PLAN,
        help="the committed, hashed preregistration both arms bind to",
    )
    parser.add_argument(
        "--corpus-path", type=Path, required=True, help="the pinned lovverk worktree"
    )
    parser.add_argument("--control-suffix", default="fablec1", help="run-id suffix, [a-z0-9]{4,12}")
    parser.add_argument(
        "--treatment-suffix", default="fablet1", help="run-id suffix, [a-z0-9]{4,12}"
    )
    parser.add_argument(
        "--model-error-abort",
        type=int,
        default=MODEL_ERROR_MAX_PAIRS,
        help="abort after the control arm when more cases than this ended in MODEL_ERROR: "
        "the plan's pair gate is then unreachable whatever treatment does",
    )
    parser.add_argument("--resume", type=Path, default=None, help="ceremony state file to continue")
    parser.add_argument(
        "--execute", action="store_true", help="run the ceremony; without it, precheck only"
    )
    args = parser.parse_args(argv)
    if args.resume is not None:
        args.execute = True
    return args


def _arm_argv(args: argparse.Namespace, condition: str) -> list[str]:
    suffix = args.control_suffix if condition == "control" else args.treatment_suffix
    argv = [
        "--condition",
        condition,
        "--suffix",
        suffix,
        "--frozen",
        "--model",
        args.model,
        "--execute",
    ]
    if condition == "lovspor":
        argv += ["--corpus-path", str(args.corpus_path)]
    return argv


# --- precheck ----------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    """What precheck established, handed to the arms. Not persisted."""

    control_args: argparse.Namespace
    treatment_args: argparse.Namespace
    cases: list[dict[str, Any]]
    lock: dict[str, Any]
    treatment_config: dict[str, Any]
    treatment_access: Any
    claude_cli: str


def foreign_workloads() -> list[str]:
    """Command lines of Observatory captures on this host, best effort.

    The lock covers sweeps (``nightly``, ``capture-all``); a per-source
    ``observatory capture`` takes no lock, so it is looked for by name.
    """
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,command="],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid == str(os.getpid()):
            continue
        if "observatory" in command and any(word in command for word in ("capture", "nightly")):
            found.append(command.strip())
    return found


def precheck(args: argparse.Namespace) -> Prepared:
    """Everything checkable without a model, in the order failures happen."""
    if not working_tree_clean(REPO_ROOT):
        raise CeremonyError(
            f"checkout {REPO_ROOT} is dirty; a ceremony commits as it goes and must start clean"
        )
    plan = args.analysis_plan.resolve()
    if plan != run_arm_script.ANALYSIS_PLAN_PATH.resolve():
        raise CeremonyError(
            f"--analysis-plan {plan} is not the plan the runner binds arms to "
            f"({run_arm_script.ANALYSIS_PLAN_PATH}); the runner hashes exactly one file"
        )
    busy = foreign_workloads()
    if busy:
        raise CeremonyError(
            "an Observatory capture is running on this host:\n  " + "\n  ".join(busy)
        )
    control_args = run_arm_script.parse_args(_arm_argv(args, "control"))
    treatment_args = run_arm_script.parse_args(_arm_argv(args, "lovspor"))
    cases, lock = run_arm_script.load_inputs(control_args)
    config = run_arm_script.treatment_config(treatment_args, lock)
    access = run_arm_script.tool_access(treatment_args, config)
    run_arm_script.child_env(control_args)
    run_arm_script.child_env(treatment_args)
    cli = run_arm_script.claude_version()
    if cli == "unknown":
        raise CeremonyError("`claude --version` gave nothing; the control arm cannot run")
    free = shutil.disk_usage(RUNS_ROOT).free
    if free < MIN_FREE_BYTES:
        raise CeremonyError(
            f"{free / 1024**3:.1f} GiB free under {RUNS_ROOT}; need {MIN_FREE_BYTES / 1024**3:.0f}"
        )
    return Prepared(
        control_args=control_args,
        treatment_args=treatment_args,
        cases=cases,
        lock=lock,
        treatment_config=config,
        treatment_access=access,
        claude_cli=cli,
    )


def new_ceremony(args: argparse.Namespace, prepared: Prepared) -> Ceremony:
    now = _utc_now()
    ceremony = Ceremony(
        ceremony_id=ceremony_id_for(args.model, now[:10].replace("-", "")),
        state="PRECHECKED",
        model=args.model,
        analysis_plan_path=str(args.analysis_plan.resolve().relative_to(REPO_ROOT)),
        analysis_plan_sha256=file_sha256(args.analysis_plan),
        corpus_path=str(args.corpus_path.expanduser().resolve()),
        corpus_commit=str(prepared.lock["corpus_pin"]["lovverk_commit"]),
        runner_commit=git_head_sha(REPO_ROOT),
        claude_cli=prepared.claude_cli,
        model_error_abort_threshold=args.model_error_abort,
        started_at=now,
        updated_at=now,
        history=[f"{now} PRECHECKED"],
    )
    if state_path(ceremony.ceremony_id).exists():
        raise CeremonyError(
            f"{state_path(ceremony.ceremony_id)} exists; resume it with --resume "
            "or choose another day"
        )
    write_state(ceremony)
    return ceremony


# --- arms --------------------------------------------------------------------


def _record_flags(run_id: str) -> list[tuple[str, bool]]:
    """``(case_id, completed)`` per record — the content-independent view of an arm."""
    flags = []
    for line in records_path(RUNS_ROOT, run_id).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        flags.append((str(record["case_id"]), bool(record.get("completed"))))
    return flags


def _finished_arm(run_id: str, lock: dict[str, Any], arm: ArmRecord) -> ArmRecord:
    """The arm's counts after the runner finalized it, or a refusal."""
    metadata = json.loads((RUNS_ROOT / run_id / "run-metadata.json").read_text(encoding="utf-8"))
    finished_at = metadata.get("finished_at")
    if not finished_at:
        raise CeremonyError(f"{run_id} was not finalized by the runner; it cannot be frozen")
    flags = _record_flags(run_id)
    expected = int(lock["case_count"])
    if metadata.get("cases_total") != expected or len({case for case, _ in flags}) != expected:
        raise CeremonyError(
            f"{run_id} covers {len(flags)} records / cases_total={metadata.get('cases_total')}; "
            f"the frozen set has {expected}"
        )
    return arm.model_copy(
        update={
            "finished_at": str(finished_at),
            "cases_total": expected,
            "cases_completed": int(metadata["cases_completed"]),
            "model_error_cases": sum(1 for _, completed in flags if not completed),
        }
    )


def compose_treatment(prepared: Prepared) -> dict[str, Any]:
    """The treatment arm's run metadata, bound to the HEAD of this moment."""
    return run_arm_script.compose(
        prepared.treatment_args, prepared.cases, prepared.lock, prepared.treatment_config
    )


def run_arm(
    ceremony: Ceremony, prepared: Prepared, condition: str, metadata: dict[str, Any]
) -> tuple[Ceremony, ArmRecord]:
    """Record RUNNING, execute in-process under the given metadata, read the counts back."""
    if condition == "control":
        args, access = prepared.control_args, None
        running: State = "CONTROL_RUNNING"
    else:
        args, access = prepared.treatment_args, prepared.treatment_access
        running = "TREATMENT_RUNNING"
    arm = ArmRecord(run_id=str(metadata["run_id"]), started_at=str(metadata["started_at"]))
    ceremony = advance(ceremony, running, arm.run_id, **{condition_key(condition): arm})
    run_arm_script.execute(metadata, prepared.cases, args, access)
    arm = _finished_arm(arm.run_id, prepared.lock, arm)
    return ceremony, arm


def condition_key(condition: str) -> str:
    return "control" if condition == "control" else "treatment"


def freeze_arm(
    ceremony: Ceremony, condition: str, arm: ArmRecord, frozen_state: State, note: str = ""
) -> Ceremony:
    """Hash the exact records, make them read-only, commit them with the state."""
    run_dir = RUNS_ROOT / arm.run_id
    records = run_dir / "records.jsonl"
    metadata = run_dir / "run-metadata.json"
    sha = file_sha256(records)
    for path in (records, metadata):
        path.chmod(0o444)
    arm = arm.model_copy(update={"records_sha256": sha})
    ceremony = advance(
        ceremony, frozen_state, f"{arm.run_id} sha256 {sha[:12]}", **{condition_key(condition): arm}
    )
    subject = (
        f"feat(llhb): freeze the {condition_key(condition)} arm "
        f"of the {ceremony.model} confirmatory pair"
    )
    body = (
        f"{arm.run_id}: {arm.cases_completed}/{arm.cases_total} cases completed, "
        f"{arm.model_error_cases} MODEL_ERROR — content-independent counts only (ruling #30(d)).\n"
        f"records.jsonl sha256 {sha}\n"
        f"runner commit {ceremony.runner_commit}\n"
        f"ceremony {ceremony.ceremony_id}: {frozen_state}" + (f" — {note}" if note else "")
    )
    head = _commit([records, metadata, state_path(ceremony.ceremony_id)], f"{subject}\n\n{body}")
    # Held in memory, persisted by the next step's write: writing it now
    # would dirty the tree the next gate requires clean. Git has it either way.
    arm = arm.model_copy(update={"freeze_commit": head})
    return ceremony.model_copy(update={condition_key(condition): arm})


def _commit(paths: list[Path], message: str) -> str:
    add(REPO_ROOT, paths)
    if not has_staged_changes(REPO_ROOT):
        raise CeremonyError(f"nothing staged for: {message.splitlines()[0]}")
    commit(REPO_ROOT, message)
    return git_head_sha(REPO_ROOT)


def abort(ceremony: Ceremony, reason: str) -> Ceremony:
    ceremony = advance(ceremony, "ABORTED", reason, abort_reason=reason)
    _commit(
        [state_path(ceremony.ceremony_id)],
        f"feat(llhb): abort the {ceremony.model} confirmatory ceremony\n\n{reason}\n"
        f"ceremony {ceremony.ceremony_id}: ABORTED",
    )
    return ceremony


# --- after both arms ---------------------------------------------------------


def _arms(ceremony: Ceremony) -> tuple[ArmRecord, ArmRecord]:
    """Both frozen arms, or a refusal naming the state that lacks one."""
    if ceremony.control is None or ceremony.treatment is None:
        raise CeremonyError(f"state {ceremony.state} has no frozen pair to work on")
    return ceremony.control, ceremony.treatment


def model_error_pairs(control_id: str, treatment_id: str) -> int:
    """Pairs with a terminal MODEL_ERROR in either arm — the plan's union."""
    failed = {case for case, completed in _record_flags(control_id) if not completed}
    failed |= {case for case, completed in _record_flags(treatment_id) if not completed}
    return len(failed)


def verify_fairness(ceremony: Ceremony) -> Ceremony:
    """check_fairness --frozen, with completion mismatches set aside.

    A case that produced no comparison is not an unfairness of the
    apparatus — the arms were equal, the provider failed — and the plan
    prices it through the MODEL_ERROR gate at scoring time. Every other
    finding is a defect in the pair and stops the ceremony.
    """
    control_arm, treatment_arm = _arms(ceremony)
    control = check_fairness_script.load_run(RUNS_ROOT, control_arm.run_id)
    treatment = check_fairness_script.load_run(RUNS_ROOT, treatment_arm.run_id)
    surface = check_fairness_script.load_expected_surface(check_fairness_script.SURFACE_PATH)
    expected = check_fairness_script.load_frozen_expectation()
    problems = [
        *check_pair(control, treatment, surface),
        *frozen_violations(control, "control", expected),
        *frozen_violations(treatment, "treatment", expected),
    ]
    completion = set(paired_completion_violations(control.records, treatment.records))
    tolerated = [problem for problem in problems if problem in completion]
    hard = [problem for problem in problems if problem not in completion]
    pairs = model_error_pairs(control_arm.run_id, treatment_arm.run_id)
    if hard:
        ceremony = ceremony.model_copy(
            update={
                "fairness_findings": hard,
                "tolerated_completion_findings": tolerated,
                "model_error_pairs": pairs,
            }
        )
        write_state(ceremony)
        return abort(ceremony, f"fairness gate: {len(hard)} finding(s); see fairness_findings")
    ceremony = advance(
        ceremony,
        "FAIRNESS_VERIFIED",
        f"{len(tolerated)} completion finding(s) over {pairs} MODEL_ERROR pair(s) set aside",
        tolerated_completion_findings=tolerated,
        model_error_pairs=pairs,
    )
    _commit(
        [state_path(ceremony.ceremony_id)],
        f"feat(llhb): fairness verified for the {ceremony.model} confirmatory pair\n\n"
        f"{control_arm.run_id} vs {treatment_arm.run_id}: frozen evaluation, "
        f"0 apparatus finding(s), {pairs} MODEL_ERROR pair(s) left to the plan's gate.\n"
        f"ceremony {ceremony.ceremony_id}: FAIRNESS_VERIFIED",
    )
    return ceremony


def commit_manifest(ceremony: Ceremony) -> Ceremony:
    control_arm, treatment_arm = _arms(ceremony)
    ids = (control_arm.run_id, treatment_arm.run_id)
    manifest = build_pair_manifest(
        REPO_ROOT, RUNS_ROOT, ids, (REPO_ROOT / ceremony.analysis_plan_path).resolve()
    )
    out = MANIFESTS_DIR / f"{ids[0]}-vs-{ids[1]}.json"
    write_pair_manifest(manifest, out)
    ceremony = advance(
        ceremony, "MANIFEST_COMMITTED", out.name, manifest_path=str(out.relative_to(REPO_ROOT))
    )
    head = _commit(
        [out, state_path(ceremony.ceremony_id)],
        f"feat(llhb): pair manifest for the {ceremony.model} confirmatory pair\n\n"
        f"{ids[0]} vs {ids[1]}; scorer commit {manifest.scorer_commit}; "
        f"plan sha256 {manifest.analysis_plan_sha256}.\n"
        f"ceremony {ceremony.ceremony_id}: MANIFEST_COMMITTED",
    )
    # As with the freeze commits: persisted by the SCORED write, not now.
    return ceremony.model_copy(update={"manifest_commit": head})


def score(ceremony: Ceremony) -> Ceremony:
    if ceremony.manifest_path is None:
        raise CeremonyError(f"state {ceremony.state} has no manifest to score")
    manifest = load_pair_manifest(REPO_ROOT / ceremony.manifest_path)
    # The gate of ruling #30(d), verbatim in the manifest module: no
    # aggregate scoring until every referenced hash verifies.
    verify_pair_manifest(manifest, REPO_ROOT, RUNS_ROOT)
    out, report, _ = score_run_script.write_report(
        manifest, Path(ceremony.corpus_path), RUNS_ROOT, None
    )
    return advance(
        ceremony,
        "SCORED",
        f"{out.name}: {report.primary.verdict}",
        report_path=str(out.relative_to(REPO_ROOT)),
        verdict=str(report.primary.verdict),
        confirmatory_eligible=bool(report.eligibility.confirmatory_eligible),
    )


def report(ceremony: Ceremony) -> Ceremony:
    if ceremony.report_path is None:
        raise CeremonyError(f"state {ceremony.state} has no report to commit")
    ceremony = advance(ceremony, "REPORTED")
    _commit(
        [REPO_ROOT / ceremony.report_path, state_path(ceremony.ceremony_id)],
        f"feat(llhb): score report for the {ceremony.model} confirmatory pair\n\n"
        f"{ceremony.report_path}; primary verdict: {ceremony.verdict}; "
        f"confirmatory_eligible: {ceremony.confirmatory_eligible}; "
        f"MODEL_ERROR pairs: {ceremony.model_error_pairs}.\n"
        f"ceremony {ceremony.ceremony_id}: REPORTED",
    )
    return ceremony


# --- the machine -------------------------------------------------------------


def run_ceremony(args: argparse.Namespace, prepared: Prepared, ceremony: Ceremony) -> Ceremony:
    """Advance from wherever the state stands to REPORTED, or ABORTED."""
    if ceremony.state == "PRECHECKED":
        control_metadata = run_arm_script.compose(
            prepared.control_args, prepared.cases, prepared.lock, None
        )
        ceremony, arm = run_arm(ceremony, prepared, "control", control_metadata)
        # Same HEAD as the control arm — see Ceremony.treatment_metadata.
        ceremony = ceremony.model_copy(update={"treatment_metadata": compose_treatment(prepared)})
        if (
            arm.model_error_cases is not None
            and arm.model_error_cases > ceremony.model_error_abort_threshold
        ):
            ceremony = freeze_arm(
                ceremony, "control", arm, "CONTROL_FROZEN", "kept as evidence, ruling #27"
            )
            return abort(
                ceremony,
                f"control arm: {arm.model_error_cases} MODEL_ERROR cases > "
                f"{ceremony.model_error_abort_threshold}; the pair gate is unreachable",
            )
        ceremony = freeze_arm(ceremony, "control", arm, "CONTROL_FROZEN")
    if ceremony.state == "CONTROL_FROZEN":
        if ceremony.treatment_metadata is None:
            raise CeremonyError(
                "CONTROL_FROZEN without composed treatment metadata; the state file is damaged"
            )
        ceremony, arm = run_arm(ceremony, prepared, "lovspor", ceremony.treatment_metadata)
        # The run directory carries it from here; the state file need not.
        ceremony = ceremony.model_copy(update={"treatment_metadata": None})
        ceremony = freeze_arm(ceremony, "treatment", arm, "TREATMENT_FROZEN")
    if ceremony.state == "TREATMENT_FROZEN":
        ceremony = verify_fairness(ceremony)
        if ceremony.state == "ABORTED":
            return ceremony
    if ceremony.state == "FAIRNESS_VERIFIED":
        ceremony = commit_manifest(ceremony)
    if ceremony.state == "MANIFEST_COMMITTED":
        ceremony = score(ceremony)
    if ceremony.state == "SCORED":
        ceremony = report(ceremony)
    return ceremony


def _commit_that_added(path: Path) -> str | None:
    """The newest commit touching ``path``, or None when git has none."""
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell, repo-local
        ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%H", "--", str(path)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    head = result.stdout.strip()
    return head if result.returncode == 0 and head else None


def backfill_commits(ceremony: Ceremony) -> Ceremony:
    """Commit shas the previous process committed but never wrote back.

    Each step writes the sha of its commit into the state at the NEXT
    step's write; a ceremony killed in between has the commit in git and a
    None in the file. Git is the record, so read it from there.
    """
    updates: dict[str, Any] = {}
    for key in ("control", "treatment"):
        arm = getattr(ceremony, key)
        if arm is not None and arm.freeze_commit is None and arm.records_sha256 is not None:
            found = _commit_that_added(records_path(RUNS_ROOT, arm.run_id))
            if found is not None:
                updates[key] = arm.model_copy(update={"freeze_commit": found})
    if ceremony.manifest_path is not None and ceremony.manifest_commit is None:
        updates["manifest_commit"] = _commit_that_added(REPO_ROOT / ceremony.manifest_path)
    return ceremony.model_copy(update=updates) if updates else ceremony


def resume(args: argparse.Namespace) -> Ceremony:
    ceremony = read_state(args.resume.resolve())
    if ceremony.state not in RESUMABLE:
        raise CeremonyError(
            f"{args.resume}: state {ceremony.state} cannot be resumed — a RUNNING arm is "
            f"half-written and a finished ceremony is finished; start a new one"
        )
    if ceremony.model != args.model:
        raise CeremonyError(f"{args.resume} is a {ceremony.model} ceremony, not {args.model}")
    if not working_tree_clean(REPO_ROOT):
        raise CeremonyError(
            f"checkout {REPO_ROOT} is dirty; the last committed step is the state of record"
        )
    return backfill_commits(ceremony)


def _print_summary(ceremony: Ceremony) -> None:
    print(f"ceremony {ceremony.ceremony_id}: {ceremony.state}")
    for arm_name in ("control", "treatment"):
        arm = getattr(ceremony, arm_name)
        if arm is not None:
            print(
                f"  {arm_name}: {arm.run_id} {arm.cases_completed}/{arm.cases_total} completed, "
                f"{arm.model_error_cases} MODEL_ERROR, frozen at {arm.freeze_commit}"
            )
    if ceremony.model_error_pairs is not None:
        print(f"  MODEL_ERROR pairs: {ceremony.model_error_pairs}")
    if ceremony.verdict is not None:
        print(
            f"  primary verdict: {ceremony.verdict} "
            f"(confirmatory_eligible={ceremony.confirmatory_eligible})"
        )
    if ceremony.abort_reason:
        print(f"  aborted: {ceremony.abort_reason}")
    print(f"  state file: {state_path(ceremony.ceremony_id)}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.execute:
        prepared = precheck(args)
        print(
            f"precheck OK: {len(prepared.cases)} frozen cases, corpus pinned at "
            f"{prepared.lock['corpus_pin']['lovverk_commit'][:12]}, CLI {prepared.claude_cli}; "
            f"no lock taken, no model called — re-run with --execute"
        )
        return 0
    with exclusive_workload(CEREMONY_WORKLOAD):
        prepared = precheck(args)
        ceremony = resume(args) if args.resume else new_ceremony(args, prepared)
        ceremony = run_ceremony(args, prepared, ceremony)
    _print_summary(ceremony)
    return 0 if ceremony.state == "REPORTED" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LovsporError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
