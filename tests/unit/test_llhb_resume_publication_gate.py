"""A resumed run is diagnostic only: no primary published comparison takes it (#816).

Owner decision 2026-11-06 (on #814): a resumed LLHB run may be scored on its
own, for diagnostics, but it is excluded by default from the primary published
comparisons, and admitting one needs an explicit analysis-plan rule, which
does not exist. GPT-6's review of #814 found that the pair manifest, fairness
and scoring paths treated a resumed run like any other. Every publication
entrypoint is checked here against a completed resumed arm and a clean one.
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lovspor.errors import LovsporError
from lovspor.llhb.fairness import RunArtifacts, check_pair
from lovspor.llhb.orchestrator import run_arm
from lovspor.llhb.pair_manifest import PairManifestError, verify_pair_manifest
from lovspor.llhb.results import resumed_note, was_resumed
from tests.unit.test_llhb_fairness import EXPECTED, metadata, record, treatment_metadata
from tests.unit.test_llhb_fairness import treatment_record as fair_treatment_record
from tests.unit.test_llhb_orchestrator import RUN_ID, make_config, make_metadata
from tests.unit.test_llhb_pair_manifest import RUN_IDS, _build, _git, _runs_root, repo
from tests.unit.test_llhb_resume import _CASES, _interrupted_run, _store

__all__ = ["repo"]  # the pytest fixture, imported for the pair-manifest tests

_RUNNER = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb" / "runner"


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_gate", _RUNNER / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resumed_notes() -> str:
    return f"pilot; {resumed_note(2, 4)}"


def test_a_completed_resumed_run_is_marked_and_a_clean_one_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _interrupted_run(tmp_path, monkeypatch)
    store = _store(tmp_path)
    run_arm(make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), store)

    assert was_resumed(store.load_metadata(RUN_ID)) is True
    assert was_resumed(make_metadata()) is False
    assert was_resumed(make_metadata(notes="pilot; no resume here")) is False
    assert was_resumed(make_metadata(notes=None)) is False


def _mark_resumed(repo: Path, run_id: str) -> None:
    path = _runs_root(repo) / run_id / "run-metadata.json"
    stored: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    stored["notes"] = _resumed_notes()
    path.write_text(json.dumps(stored), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", f"resume {run_id}")


@pytest.mark.parametrize("run_id", RUN_IDS)
def test_a_pair_manifest_is_never_built_on_a_resumed_arm(repo: Path, run_id: str) -> None:
    _mark_resumed(repo, run_id)

    with pytest.raises(PairManifestError, match=f"{run_id} was resumed"):
        _build(repo)


def test_a_manifest_built_before_an_arm_was_resumed_no_longer_verifies(repo: Path) -> None:
    manifest = _build(repo)
    _mark_resumed(repo, RUN_IDS[1])

    with pytest.raises(PairManifestError, match="was resumed"):
        verify_pair_manifest(manifest, repo, _runs_root(repo))


def test_the_clean_pair_still_builds(repo: Path) -> None:
    assert _build(repo).control_run_id == RUN_IDS[0]


@pytest.mark.parametrize("arm", ["control", "treatment"])
def test_fairness_refuses_a_resumed_arm(arm: str) -> None:
    notes = {"notes": _resumed_notes()}
    control = RunArtifacts(
        metadata=metadata(cases_total=1, cases_completed=1, **(notes if arm == "control" else {})),
        records=[record("llhb-v1-C1-001")],
    )
    treatment = RunArtifacts(
        metadata=treatment_metadata(
            cases_total=1, cases_completed=1, **(notes if arm == "treatment" else {})
        ),
        records=[fair_treatment_record("llhb-v1-C1-001")],
    )

    problems = check_pair(control, treatment, EXPECTED)

    assert [p for p in problems if "resumed" in p] == [
        f"{arm} run was resumed: resumed runs are diagnostic only and excluded from "
        "primary published comparisons (#816)"
    ]


@pytest.mark.parametrize("arm", ["control", "treatment"])
def test_the_stability_report_refuses_a_resumed_repeat_before_scoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arm: str
) -> None:
    bin_dir = _interrupted_run(tmp_path, monkeypatch)
    store = _store(tmp_path)
    run_arm(make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), store)
    clean_id = "llhb-v1-run-20260808-clean1"
    store.open_run(make_metadata(run_id=clean_id))
    report = _script("stability_report")
    args = argparse.Namespace(
        cases=tmp_path / "absent-cases.jsonl",
        corpus_path=tmp_path / "absent-corpus",
        runs_root=tmp_path / "runs",
        control_ids=[clean_id, RUN_ID] if arm == "control" else [clean_id],
        treatment_ids=[clean_id, RUN_ID] if arm == "treatment" else [clean_id],
    )

    with pytest.raises(LovsporError, match=f"{RUN_ID} was resumed"):
        report.score_repeats(args)


@pytest.mark.parametrize(
    "notes",
    [
        "resumed: 0 of 4 cases were already recorded",
        "pilot; resumed: 2 of 4 cases were already recorded",
        "resumed: 4 of 4 cases were already recorded; diagnostic scoring",
        "pilot; resumed: 2 of 4 cases were already recorded; diagnostic scoring",
    ],
)
def test_resume_marker_is_recognized_among_operational_notes(notes: str) -> None:
    """The #816 gate must still recognize a resume after notes are appended."""
    assert was_resumed({"notes": notes}) is True
