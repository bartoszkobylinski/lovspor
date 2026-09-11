"""Tests for scripts/ci/classify_lane_failure.py (issue #272).

A job that dies with its runner is recorded as `failure` with the step it was
running still `in_progress` — the step never completed and never failed,
because the machine stopped answering. Reporting that as "BLOCKED before the
tests ran" reads as a verdict about the pull request; on PR #269 that
misreading cost about three hours across two dead runs.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "classify_lane_failure.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("classify_lane_failure", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules[module]; an
    # unregistered module makes that lookup return None at class-creation time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


classify_lane_failure = _load()


def _job(name: str, conclusion: str, steps: list[tuple[str, str, str | None]]) -> dict[str, Any]:
    return {
        "name": name,
        "conclusion": conclusion,
        "steps": [
            {"name": step, "status": status, "conclusion": step_conclusion}
            for step, status, step_conclusion in steps
        ],
    }


def test_a_step_still_running_on_a_failed_job_is_infrastructure() -> None:
    jobs = [
        _job(
            "codex-author",
            "failure",
            [
                ("Set up job", "completed", "success"),
                ("Codex — independent PR test author", "in_progress", None),
                ("Scope guard", "queued", None),
            ],
        )
    ]

    verdict = classify_lane_failure.classify(jobs, ["codex-author", "codex-tests"])

    assert verdict.is_infrastructure
    assert verdict.job == "codex-author"
    assert verdict.step == "Codex — independent PR test author"


def test_a_job_that_failed_on_its_own_step_is_not_infrastructure() -> None:
    """Every step completed, one of them with `failure`: the job reached its own
    verdict, and its in-job escalation already spoke."""
    jobs = [
        _job(
            "codex-tests",
            "failure",
            [
                ("Set up job", "completed", "success"),
                ("Run tests on Codex additions", "completed", "failure"),
                ("Commit and push test additions", "completed", "skipped"),
            ],
        )
    ]

    verdict = classify_lane_failure.classify(jobs, ["codex-author", "codex-tests"])

    assert verdict.kind == "in_job"
    assert verdict.is_infrastructure is False
    assert verdict.step == ""


def test_a_successful_lane_is_never_classified() -> None:
    jobs = [_job("codex-author", "success", [("Set up job", "in_progress", None)])]

    assert classify_lane_failure.classify(jobs, ["codex-author"]).kind == "unknown"


def test_lane_order_decides_which_failure_is_reported() -> None:
    """The author lane is asked about first: it is the one that dies with the
    box, so when both lanes are red its death is the cause and the verifier's
    failure is the consequence."""
    jobs = [
        _job("codex-tests", "failure", [("Run tests", "completed", "failure")]),
        _job("codex-author", "failure", [("Codex", "in_progress", None)]),
    ]

    verdict = classify_lane_failure.classify(jobs, ["codex-author", "codex-tests"])

    assert verdict.job == "codex-author"
    assert verdict.is_infrastructure


def test_a_lane_missing_from_the_payload_is_not_a_verdict() -> None:
    """A fork PR skips the agent lane entirely; absence is not a death."""
    jobs = [_job("fast-ci", "failure", [("Unit tests", "completed", "failure")])]

    assert classify_lane_failure.classify(jobs, ["codex-author", "codex-tests"]).kind == "unknown"


@pytest.mark.parametrize("wrap", [True, False])
def test_the_payload_may_arrive_wrapped_or_bare(tmp_path: Path, wrap: bool) -> None:
    """`gh api .../jobs` returns {"total_count": n, "jobs": [...]}, but a caller
    that already unwrapped it with --jq hands over the bare list."""
    job = _job("codex-author", "failure", [("Codex", "in_progress", None)])
    payload: Any = {"total_count": 1, "jobs": [job]} if wrap else [job]
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = classify_lane_failure._load(str(path))

    assert classify_lane_failure.classify(loaded, ["codex-author"]).is_infrastructure


def test_the_cli_prints_the_three_output_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The workflow appends this straight to $GITHUB_OUTPUT, so the shape of
    these three lines is the contract with the reporting step."""
    path = tmp_path / "jobs.json"
    payload = {"jobs": [_job("codex-author", "failure", [("Codex", "in_progress", None)])]}
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert classify_lane_failure.main(["--jobs", str(path), "--lane", "codex-author"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "kind=infrastructure",
        "job=codex-author",
        "step=Codex",
    ]
