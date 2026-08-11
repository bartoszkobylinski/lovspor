"""Regression tests for the test-authoring and mutation-remediation workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _steps(workflow_name: str, job_name: str) -> list[dict[str, Any]]:
    workflow: dict[str, Any] = yaml.safe_load(
        (_WORKFLOWS / workflow_name).read_text(encoding="utf-8")
    )
    return workflow["jobs"][job_name]["steps"]


def _named_step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(step for step in steps if step.get("name") == name)


@pytest.mark.parametrize(
    ("workflow_name", "job_name", "condition"),
    [
        (
            "pr-pipeline.yml",
            "codex-tests",
            "steps.antiloop.outputs.skip != 'true'",
        ),
        (
            "mutation-remediation.yml",
            "remediate",
            "steps.cycle.outputs.run == 'true'",
        ),
    ],
)
def test_codex_output_is_formatted_and_linted_before_tests(
    workflow_name: str, job_name: str, condition: str
) -> None:
    steps = _steps(workflow_name, job_name)
    names = [step.get("name") for step in steps]
    normalize = _named_step(steps, "Normalize and lint Codex output")

    assert names.index("Scope guard") < names.index(normalize["name"])
    assert names.index(normalize["name"]) < names.index("Run tests on Codex additions")
    assert normalize["if"] == condition
    assert normalize["run"].splitlines() == [
        "uv run ruff format tests/",
        "uv run ruff check tests/",
    ]


def test_remediation_rejected_push_is_ignored_only_for_a_superseded_head() -> None:
    steps = _steps("mutation-remediation.yml", "remediate")
    push = _named_step(steps, "Commit and push, or report BLOCKED")["run"]

    assert 'if ! git push origin "HEAD:$HEAD_BRANCH"; then' in push
    assert 'git fetch -q origin "$HEAD_BRANCH"' in push
    assert 'if [ "$(git rev-parse "origin/$HEAD_BRANCH")" != "$HEAD_SHA" ]; then' in push
    assert "abandoning superseded result" in push
    assert "exit 0" in push
    assert "exit 1" in push


def test_remediation_failure_escalates_only_after_pr_resolution() -> None:
    steps = _steps("mutation-remediation.yml", "remediate")
    escalation = _named_step(steps, "Escalate on remediation failure")
    command = escalation["run"]

    assert escalation["if"] == "failure() && steps.cycle.outputs.pr != ''"
    assert (
        'gh pr edit "${{ steps.cycle.outputs.pr }}" --add-label "needs-human:mutation"' in command
    )
    assert 'gh pr comment "${{ steps.cycle.outputs.pr }}"' in command
    run_url = "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
    assert run_url in command
