"""The nightly mutation lane: verdict mapping and workflow shape.

Mutation left the PR lane for a nightly report on main (docs/agentic-ci.md,
"Nightly mutation"). Two things must hold without a live run: a partial run is
never reported as clean, and only a completed run moves the base tag.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "nightly_mutation_verdict.py"
WORKFLOW = REPO / ".github" / "workflows" / "nightly-mutation.yml"


def _verdict(tmp_path: Path, payload: object | None) -> dict[str, str]:
    result = tmp_path / "mutation-result.json"
    if payload is not None:
        result.write_text(json.dumps(payload), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(SCRIPT), str(result)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return dict(line.split("=", 1) for line in out.splitlines())


@pytest.mark.parametrize(
    ("reason", "verdict", "complete"),
    [
        ("ok", "clean", "true"),
        ("not_applicable", "clean", "true"),
        ("equivalent_mutants_only", "clean", "true"),
        ("surviving_mutants", "survivors", "true"),
        ("timeout_mutants", "survivors", "true"),
        ("suspicious_mutants", "survivors", "true"),
        ("uncovered_mutants", "survivors", "true"),
        ("run_incomplete", "partial", "false"),
        ("unmeasured_mutants", "partial", "false"),
        ("budget_exceeded", "budget_exceeded", "false"),
        ("tool_failed", "tool_failure", "false"),
        ("baseline_tests_failed", "tool_failure", "false"),
        ("a_reason_added_later", "tool_failure", "false"),
    ],
)
def test_each_gate_reason_maps_to_one_nightly_verdict(
    tmp_path: Path, reason: str, verdict: str, complete: str
) -> None:
    got = _verdict(tmp_path, {"schema_version": 1, "gate": {"passed": False, "reason": reason}})
    assert got == {"verdict": verdict, "complete": complete, "reason": reason}


@pytest.mark.parametrize(
    "payload",
    [None, [], {"schema_version": 2, "gate": {"reason": "ok"}}, {"schema_version": 1}],
)
def test_a_missing_or_malformed_result_is_a_tool_failure(
    tmp_path: Path, payload: object | None
) -> None:
    got = _verdict(tmp_path, payload)
    assert (got["verdict"], got["complete"]) == ("tool_failure", "false")


def test_the_script_refuses_a_wrong_argument_count() -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 2
    assert "usage:" in done.stderr


def _job() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["nightly-mutation"]


def _step(name_prefix: str) -> dict[str, object]:
    steps = _job()["steps"]
    assert isinstance(steps, list)
    matches = [s for s in steps if str(s.get("name", "")).startswith(name_prefix)]
    assert len(matches) == 1, name_prefix
    return matches[0]


def test_the_lane_is_dispatched_not_scheduled() -> None:
    # launchd on the Mac mini dispatches it at a fixed time; GitHub's own
    # schedule started the 2026-11-12 night 4.5 h late.
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow[True]  # PyYAML reads the bare `on:` key as boolean True
    assert "schedule" not in triggers
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers and "push" not in triggers


def test_the_job_timeout_is_strictly_above_the_largest_budget() -> None:
    body = WORKFLOW.read_text(encoding="utf-8")
    cap = int(body.split("cap=", 1)[1].split()[0])
    timeout = _job()["timeout-minutes"]
    assert isinstance(timeout, int)
    assert timeout * 60 > cap
    assert timeout < 360  # the hosted runner's own 6 h kill


def test_only_a_completed_run_moves_the_base_tag() -> None:
    assert _step("Move the base tag")["if"] == "steps.verdict.outputs.complete == 'true'"


def test_the_report_runs_whatever_the_verdict() -> None:
    assert str(_step("Report").get("if", "")).startswith("always()")


def test_only_the_jobs_that_write_hold_write_scopes() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    assert _job()["permissions"] == {"contents": "write", "issues": "write"}


# Every verdict that is not complete, and none at all: a Verdict step that died.
@pytest.mark.parametrize("verdict", ["partial", "budget_exceeded", "tool_failure", ""])
def test_a_night_that_did_not_measure_its_range_ends_red(verdict: str) -> None:
    """Issue #879: every earlier step swallows its failure, so the run was green.

    Edits to the report issue send no mail, so a run that ends green on a
    partial, budget-exceeded or failed night reports it as clean everywhere a
    human looks without opening the issue, every night the tag stays put.
    """
    step = _step("Fail a night that did not measure its range")
    assert step["if"] == "always() && steps.verdict.outputs.complete != 'true'"
    done = subprocess.run(
        ["bash", "-c", str(step["run"])],
        capture_output=True,
        text=True,
        env={"VERDICT": verdict, "REASON": "r"},
        check=False,
    )
    assert done.returncode == 1
    assert f"verdict '{verdict or 'none'}'" in done.stdout


def test_the_red_night_comes_after_its_report() -> None:
    names = [str(s.get("name", "")) for s in _job()["steps"]]  # type: ignore[union-attr]
    report = next(i for i, n in enumerate(names) if n.startswith("Report"))
    fail = names.index("Fail a night that did not measure its range")
    assert fail == len(names) - 1
    assert fail > report


def test_the_incomplete_night_failure_is_not_swallowed() -> None:
    """The documented red run requires an unhandled step failure (#879)."""
    step = _step("Fail a night that did not measure its range")
    assert step.get("continue-on-error", False) is False
    assert _job().get("continue-on-error", False) is False


@pytest.mark.parametrize("missing_outputs", [False, True])
def test_the_failure_annotation_reports_the_gate_reason(missing_outputs: bool) -> None:
    step = _step("Fail a night that did not measure its range")
    assert step["env"] == {
        "VERDICT": "${{ steps.verdict.outputs.verdict }}",
        "REASON": "${{ steps.verdict.outputs.reason }}",
    }
    env = {} if missing_outputs else {"VERDICT": "partial", "REASON": "unmeasured_mutants"}
    done = subprocess.run(
        ["bash", "-e", "-c", str(step["run"])],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    verdict = env.get("VERDICT", "none")
    reason = env.get("REASON", "none")
    assert done.returncode == 1
    assert done.stdout == (
        f"::error::nightly mutation verdict '{verdict}' (gate reason: {reason}): "
        "the range was not fully measured and the base tag did not move\n"
    )
    assert done.stderr == ""


@pytest.mark.parametrize(
    ("reason", "red"),
    [
        ("ok", False),
        ("surviving_mutants", False),
        ("run_incomplete", True),
        ("budget_exceeded", True),
        ("tool_failed", True),
    ],
)
def test_the_red_step_fires_exactly_on_an_incomplete_verdict(
    tmp_path: Path, reason: str, red: bool
) -> None:
    """The step's `if` reads `complete`; tie it to what the verdict script prints."""
    complete = _verdict(tmp_path, {"schema_version": 1, "gate": {"reason": reason}})["complete"]
    step = _step("Fail a night that did not measure its range")
    assert step["if"] == "always() && steps.verdict.outputs.complete != 'true'"
    assert (complete != "true") is red


def test_the_budget_does_not_depend_on_the_start_time() -> None:
    step = _step("Budget")
    run = str(step["run"])
    assert "date" not in run  # no deadline arithmetic: a late start keeps the full budget
    assert "cap=18000" in run


@pytest.mark.parametrize("timezone", ["Europe/Oslo", "UTC"])
def test_the_budget_step_publishes_300_minutes(tmp_path: Path, timezone: str) -> None:
    """The documented fixed budget must reach GITHUB_OUTPUT, not just a variable."""
    output = tmp_path / "github-output"
    output.write_text("existing=value\n", encoding="utf-8")
    step = _step("Budget")
    done = subprocess.run(
        ["bash", "-c", str(step["run"])],
        capture_output=True,
        text=True,
        env={"GITHUB_OUTPUT": str(output), "TZ": timezone},
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert output.read_text(encoding="utf-8") == "existing=value\nseconds=18000\n"


def test_the_mutation_step_receives_the_published_total_budget() -> None:
    budget = _step("Budget")
    assert budget["id"] == "budget"
    step = _step("Nightly-scoped mutation testing")
    assert step["env"] == {"MUTMUT_PR_TOTAL_BUDGET_SECONDS": "${{ steps.budget.outputs.seconds }}"}
