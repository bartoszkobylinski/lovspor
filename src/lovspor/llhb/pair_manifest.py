"""Pair manifest: the fail-closed gate between run artifacts and scoring.

DECISIONS.md ruling #30(d): "Aggregate scoring MUST NOT execute until a
valid pair manifest exists and all referenced hashes verify." The manifest
binds one control/treatment pair to the exact bytes it was scored from —
analysis plan, dataset, system prompt, both ``records.jsonl`` files — and
to the scorer and runner commits, so the scorer consumes a verified
snapshot, never two directories on trust.

Every hash is SHA-256 over the exact bytes of the named file (LF line
endings, final newline included) — the same byte-level convention as the
frozen-dataset checksum, so there is exactly one canonicalization story
in the apparatus.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from lovspor.errors import LovsporError
from lovspor.llhb.corpus_pin import git_head_sha, working_tree_clean

SCHEMA_VERSION = 1
_SHA256_LEN = 64
_GIT_SHA_LEN = 40
# The metadata fields the manifest cross-checks in each arm. Binding, not
# schema validation — check_fairness owns full schema validation.
_METADATA_FIELDS = (
    "runner_commit",
    "model_id",
    "lovverk_commit",
    "analysis_plan_sha256",
    "system_prompt_sha256",
)


class PairManifestError(LovsporError):
    """The manifest is missing, malformed, or a referenced hash fails to verify."""


class PairManifest(BaseModel):
    """One scored pair, bound to exact bytes and commits."""

    model_config = ConfigDict(frozen=True)

    schema_version: int
    benchmark: str
    analysis_plan_path: str
    analysis_plan_sha256: str
    dataset_path: str
    dataset_sha256: str
    system_prompt_path: str
    system_prompt_sha256: str
    scorer_commit: str
    runner_commit: str
    control_run_id: str
    control_run_sha256: str
    treatment_run_id: str
    treatment_run_sha256: str
    model_requested: str
    corpus_snapshot: str

    @field_validator(
        "analysis_plan_sha256",
        "dataset_sha256",
        "system_prompt_sha256",
        "control_run_sha256",
        "treatment_run_sha256",
    )
    @classmethod
    def _sha256_hex(cls, value: str) -> str:
        if len(value) != _SHA256_LEN or set(value) - set("0123456789abcdef"):
            raise ValueError(f"not a lowercase SHA-256 hex digest: {value!r}")
        return value

    @field_validator("scorer_commit", "runner_commit", "corpus_snapshot")
    @classmethod
    def _commit_hex(cls, value: str) -> str:
        if len(value) != _GIT_SHA_LEN or set(value) - set("0123456789abcdef"):
            raise ValueError(f"not a full lowercase git SHA: {value!r}")
        return value


def file_sha256(path: Path) -> str:
    """SHA-256 over the file's exact bytes; missing file is a manifest error."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PairManifestError(f"cannot hash {path}: {exc}") from exc


def records_path(runs_root: Path, run_id: str) -> Path:
    return runs_root / run_id / "records.jsonl"


def _read_metadata(runs_root: Path, run_id: str) -> dict[str, Any]:
    path = runs_root / run_id / "run-metadata.json"
    try:
        # Explicit UTF-8 is the cross-platform contract; UTF-8 aliases are equivalent.
        text = path.read_text(encoding="utf-8")  # pragma: no mutate
        metadata: dict[str, Any] = json.loads(text)
    except (OSError, ValueError) as exc:
        raise PairManifestError(f"cannot read {path}: {exc}") from exc
    missing = [field for field in _METADATA_FIELDS if field not in metadata]
    if missing:
        raise PairManifestError(f"{path} lacks field(s) {missing}; cannot bind the manifest")
    return metadata


def _shared_metadata(repo_root: Path, runs_root: Path, run_ids: tuple[str, str]) -> dict[str, Any]:
    """The binding fields both arms must agree on, or no manifest exists.

    ``runner_commit`` alone may differ, in one direction: the ceremony
    (ruling #30(d)) freezes and commits the control arm before the
    treatment arm runs, so the treatment records the freeze commit as its
    HEAD. That commit added result files and nothing else — the runner
    code is byte-identical — which is exactly what ``_results_only_lineage``
    verifies before the difference is accepted. The manifest then pins the
    control arm's commit, the older of the two.
    """
    control, treatment = (_read_metadata(runs_root, run_id) for run_id in run_ids)
    for field in _METADATA_FIELDS:
        if control[field] == treatment[field]:
            continue
        if field == "runner_commit" and _results_only_lineage(
            repo_root, runs_root.parent, str(control[field]), str(treatment[field])
        ):
            continue
        raise PairManifestError(
            f"arms disagree on {field}: {control[field]!r} vs {treatment[field]!r}"
        )
    return control


def _results_only_lineage(repo_root: Path, results_dir: Path, older: str, newer: str) -> bool:
    """True when ``newer`` descends from ``older`` through commits that
    touched only files under ``results_dir``.

    A commit that adds frozen records, a manifest or a report does not
    change the code that produced or scores them, so a pin on ``older``
    still describes the code at ``newer``. Any path outside the results
    tree — one line of the scorer, one byte of the plan — breaks the
    lineage and the pin stands as written. Equal commits are trivially
    related; a results dir outside the repo can vouch for nothing.
    """
    if older == newer:
        return True
    try:
        prefix = results_dir.resolve().relative_to(repo_root.resolve()).as_posix() + "/"
    except ValueError:
        return False
    ancestry = _git(repo_root, "merge-base", "--is-ancestor", older, newer)
    if ancestry.returncode != 0:
        return False
    changed = _git(repo_root, "diff", "--name-only", f"{older}..{newer}")
    if changed.returncode != 0:
        raise PairManifestError(
            f"git diff {older[:12]}..{newer[:12]} failed in {repo_root}: {changed.stderr.strip()}"
        )
    paths = [line for line in changed.stdout.splitlines() if line.strip()]
    return bool(paths) and all(path.startswith(prefix) for path in paths)


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell, repo-local
        ["git", "-C", str(repo_root), *args],  # noqa: S607
        capture_output=True,
        text=True,
    )


def build_pair_manifest(
    repo_root: Path,
    runs_root: Path,
    run_ids: tuple[str, str],
    analysis_plan: Path,
) -> PairManifest:
    """Build a manifest from committed artifacts; requires a clean checkout.

    ``scorer_commit`` is the repo HEAD at build time — a dirty tree would
    record a commit the scoring code does not match, so it is refused.
    """
    if not working_tree_clean(repo_root):
        raise PairManifestError("working tree is dirty; a manifest must pin committed code")
    metadata = _shared_metadata(repo_root, runs_root, run_ids)
    dataset = repo_root / "benchmarks" / "llhb" / "dataset" / "frozen" / "llhb-v1.jsonl"
    analysis_plan_sha256 = file_sha256(analysis_plan)
    if analysis_plan_sha256 != str(metadata["analysis_plan_sha256"]):
        raise PairManifestError(
            f"committed analysis plan {analysis_plan} hashes to {analysis_plan_sha256}, "
            f"but the runs recorded {metadata['analysis_plan_sha256']!r}"
        )
    # The metadata path is repo-relative by contract (check_fairness
    # PROMPT_REPO_PATH). The manifest hashes the committed FILE and refuses
    # a run that recorded different prompt bytes — binding beats trust.
    prompt_path = str(metadata["system_prompt_path"])
    prompt_sha256 = file_sha256(repo_root / prompt_path)
    if prompt_sha256 != str(metadata["system_prompt_sha256"]):
        raise PairManifestError(
            f"committed prompt {prompt_path} hashes to {prompt_sha256}, but the runs "
            f"recorded {metadata['system_prompt_sha256']!r}"
        )
    return PairManifest(
        schema_version=SCHEMA_VERSION,
        benchmark=str(metadata.get("llhb_version", "llhb-v1")),
        analysis_plan_path=str(analysis_plan.relative_to(repo_root)),
        analysis_plan_sha256=analysis_plan_sha256,
        dataset_path=str(dataset.relative_to(repo_root)),
        dataset_sha256=file_sha256(dataset),
        system_prompt_path=prompt_path,
        system_prompt_sha256=prompt_sha256,
        scorer_commit=git_head_sha(repo_root),
        runner_commit=str(metadata["runner_commit"]),
        control_run_id=run_ids[0],
        control_run_sha256=file_sha256(records_path(runs_root, run_ids[0])),
        treatment_run_id=run_ids[1],
        treatment_run_sha256=file_sha256(records_path(runs_root, run_ids[1])),
        model_requested=str(metadata["model_id"]),
        corpus_snapshot=str(metadata["lovverk_commit"]),
    )


def load_pair_manifest(path: Path) -> PairManifest:
    try:
        # Explicit UTF-8 is the cross-platform contract; UTF-8 aliases are equivalent.
        text = path.read_text(encoding="utf-8")  # pragma: no mutate
        document = json.loads(text)
        manifest = PairManifest(**document)
    except (OSError, ValueError, TypeError, ValidationError) as exc:
        raise PairManifestError(f"cannot load pair manifest at {path}: {exc}") from exc
    if manifest.schema_version != SCHEMA_VERSION:
        raise PairManifestError(
            f"unsupported pair-manifest schema_version {manifest.schema_version}"
        )
    return manifest


def write_pair_manifest(manifest: PairManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # All manifest fields are JSON-native, so alternate model_dump modes are equivalent.
    fields = manifest.model_dump(mode="json")  # pragma: no mutate
    document = json.dumps(fields, indent=2, sort_keys=True)
    payload = document + "\n"
    # Explicit UTF-8 is the cross-platform contract; UTF-8 aliases are equivalent.
    path.write_text(payload, encoding="utf-8")  # pragma: no mutate


def verify_pair_manifest(manifest: PairManifest, repo_root: Path, runs_root: Path) -> None:
    """Every referenced hash and commit verifies, or scoring must not run."""
    _verify_files(manifest, repo_root, runs_root)
    _verify_scorer_commit(manifest, repo_root, runs_root)
    _verify_arm_metadata(manifest, repo_root, runs_root)


def _verify_files(manifest: PairManifest, repo_root: Path, runs_root: Path) -> None:
    expectations = (
        (repo_root / manifest.analysis_plan_path, manifest.analysis_plan_sha256),
        (repo_root / manifest.dataset_path, manifest.dataset_sha256),
        (repo_root / manifest.system_prompt_path, manifest.system_prompt_sha256),
        (records_path(runs_root, manifest.control_run_id), manifest.control_run_sha256),
        (records_path(runs_root, manifest.treatment_run_id), manifest.treatment_run_sha256),
    )
    for path, expected in expectations:
        actual = file_sha256(path)
        if actual != expected:
            raise PairManifestError(
                f"{path} hashes to {actual}, manifest says {expected}; refusing to score"
            )


def _verify_scorer_commit(manifest: PairManifest, repo_root: Path, runs_root: Path) -> None:
    """HEAD is the pinned scorer commit, or descends from it through
    results-only commits — the manifest's own commit is one of those, and
    the ceremony commits it before scoring (make_pair_manifest.py)."""
    head = git_head_sha(repo_root)
    if not _results_only_lineage(repo_root, runs_root.parent, manifest.scorer_commit, head):
        raise PairManifestError(
            f"checkout HEAD {head} is not the manifest scorer_commit "
            f"{manifest.scorer_commit}; refusing to score with unpinned code"
        )
    if not working_tree_clean(repo_root):
        raise PairManifestError("working tree is dirty; the scorer_commit pin is meaningless")


def _verify_arm_metadata(manifest: PairManifest, repo_root: Path, runs_root: Path) -> None:
    shared = _shared_metadata(
        repo_root, runs_root, (manifest.control_run_id, manifest.treatment_run_id)
    )
    bindings = (
        ("runner_commit", manifest.runner_commit),
        ("model_id", manifest.model_requested),
        ("lovverk_commit", manifest.corpus_snapshot),
        ("analysis_plan_sha256", manifest.analysis_plan_sha256),
        ("system_prompt_sha256", manifest.system_prompt_sha256),
    )
    for field, expected in bindings:
        if str(shared[field]) != expected:
            raise PairManifestError(
                f"run metadata {field} is {shared[field]!r}, manifest says {expected!r}"
            )
