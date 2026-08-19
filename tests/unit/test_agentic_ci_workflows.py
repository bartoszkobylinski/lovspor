"""Regression tests for the test-authoring and mutation-remediation workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _workflow(workflow_name: str) -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load(
        (_WORKFLOWS / workflow_name).read_text(encoding="utf-8")
    )
    return workflow


def _steps(workflow_name: str, job_name: str) -> list[dict[str, Any]]:
    return _workflow(workflow_name)["jobs"][job_name]["steps"]


def _named_step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(step for step in steps if step.get("name") == name)


@pytest.mark.parametrize(
    ("workflow_name", "job_name", "step_name"),
    [
        ("pr-pipeline.yml", "codex-tests", "Codex — independent PR test author"),
        (
            "mutation-remediation.yml",
            "remediate",
            "Codex — mutation remediation (tests only)",
        ),
    ],
)
def test_codex_account_homes_are_explicit_repository_configuration(
    workflow_name: str, job_name: str, step_name: str
) -> None:
    job = _workflow(workflow_name)["jobs"][job_name]
    command = _named_step(job["steps"], step_name)["run"].splitlines()

    assert "CODEX_HOME" not in job["env"]
    assert job["env"]["CODEX_PRIMARY_HOME"] == "${{ vars.CODEX_PRIMARY_HOME }}"
    assert job["env"]["CODEX_SECONDARY_HOME"] == "${{ vars.CODEX_SECONDARY_HOME }}"
    assert command.index(
        ': "${CODEX_PRIMARY_HOME:?Set repository variable CODEX_PRIMARY_HOME}"'
    ) < command.index("python3 scripts/ci/codex_account_failover.py \\")
    assert command.index(
        ': "${CODEX_SECONDARY_HOME:?Set repository variable CODEX_SECONDARY_HOME}"'
    ) < command.index("python3 scripts/ci/codex_account_failover.py \\")
    assert '  --primary-home "$CODEX_PRIMARY_HOME" \\' in command
    assert '  --secondary-home "$CODEX_SECONDARY_HOME" \\' in command


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
        "uv run ruff check --fix tests/",
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


def test_mutation_job_has_a_wallclock_backstop() -> None:
    """Issue #102: without this, a budget-machinery failure means a 6 h
    grind to GitHub's default kill with no verdict artifact."""
    job = _workflow("pr-pipeline.yml")["jobs"]["mutation"]

    assert job["timeout-minutes"] == 60


def test_mutation_report_treats_a_missing_tool_exit_code_as_failure() -> None:
    steps = _steps("pr-pipeline.yml", "mutation")
    build = _named_step(steps, "Build mutation-result.json")["run"]

    assert "--tool-exit-code \"${{ steps.mut.outputs.exit_code || '3' }}\"" in build


def test_remediation_routes_a_budget_cut_to_a_human_not_codex() -> None:
    """A budget_exceeded gate is not remediable by tests — the surface was
    never fully measured. It must label needs-human:mutation immediately,
    before any checkout or Codex step, and never enter a Codex cycle."""
    steps = _steps("mutation-remediation.yml", "remediate")
    decide = _named_step(steps, "Validate result as data; decide whether remediation applies")

    assert '"$(jq -r .gate.reason "$f")" = "budget_exceeded"' in decide["run"]
    assert 'echo "run=false" >> "$GITHUB_OUTPUT"' in decide["run"]
    assert 'echo "budget=true"' in decide["run"]

    blocked = _named_step(
        steps, "BLOCKED — budget exceeded, tests cannot fix an unmeasured surface"
    )
    assert blocked["if"] == "steps.gate.outputs.budget == 'true'"
    assert '--add-label "needs-human:mutation"' in blocked["run"]

    names = [step.get("name") for step in steps]
    assert names.index(blocked["name"]) < names.index("Resolve PR number and remediation cycle")

    checkout = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["if"] == "steps.gate.outputs.run == 'true'"


@pytest.mark.parametrize(
    ("workflow_name", "job_name", "step_name"),
    [
        ("pr-pipeline.yml", "codex-tests", "Codex — independent PR test author"),
        ("mutation-remediation.yml", "remediate", "Codex — mutation remediation (tests only)"),
    ],
)
def test_author_falls_back_to_claude_only_when_every_codex_account_is_limited(
    workflow_name: str, job_name: str, step_name: str
) -> None:
    """Exit 75 (EX_TEMPFAIL) is the failover script's 'no account below the
    threshold' signal — only THAT routes to the Claude fallback; any other
    failure stays fatal, and a successful Codex run records author=codex."""
    job = _workflow(workflow_name)["jobs"][job_name]
    author = _named_step(job["steps"], step_name)
    run = author["run"]

    assert author["id"] == "author"
    assert author["env"]["CLAUDE_TESTS_OAUTH_TOKEN"] == "${{ secrets.CLAUDE_TESTS_OAUTH_TOKEN }}"
    assert author["env"]["CLAUDE_TESTS_MODEL"] == "${{ vars.CLAUDE_TESTS_MODEL }}"
    assert 'if [ "$status" -eq 75 ]; then' in run
    assert "scripts/ci/claude_test_author.sh" in run
    assert 'echo "author=claude" >> "$GITHUB_OUTPUT"' in run
    assert 'echo "author=codex" >> "$GITHUB_OUTPUT"' in run
    assert 'exit "$status"' in run


def test_commit_markers_name_the_actual_author() -> None:
    """A Claude-written commit stamped [agent:codex-…] would be fabricated
    provenance; the marker interpolates the author step's output."""
    pr_push = _named_step(
        _steps("pr-pipeline.yml", "codex-tests"), "Commit and push test additions"
    )["run"]
    rem_push = _named_step(
        _steps("mutation-remediation.yml", "remediate"), "Commit and push, or report BLOCKED"
    )["run"]

    assert "[agent:${{ steps.author.outputs.author || 'codex' }}-tests]" in pr_push
    assert "[agent:${{ steps.author.outputs.author || 'codex' }}-mutation]" in rem_push


def test_antiloop_and_cycle_counting_recognise_the_claude_markers() -> None:
    """A fallback-authored HEAD must not retrigger test generation, and a
    Claude remediation commit must count toward the two-cycle limit —
    otherwise the fallback author gets unlimited cycles."""
    antiloop = _named_step(_steps("pr-pipeline.yml", "codex-tests"), "Anti-loop check")["run"]
    cycle = _named_step(
        _steps("mutation-remediation.yml", "remediate"), "Resolve PR number and remediation cycle"
    )["run"]

    assert r"\[agent:(codex|claude)-(tests|mutation)\]" in antiloop
    assert '*"[agent:claude-mutation]"*' in cycle
    assert '*"[agent:claude-tests]"*' in cycle


def test_remediation_fallback_hands_claude_the_same_prompt_as_codex() -> None:
    run = _named_step(
        _steps("mutation-remediation.yml", "remediate"), "Codex — mutation remediation (tests only)"
    )["run"]

    assert "cat .github/codex/mutation-remediation.md" in run
    assert "remediation-prompt.md" in run


def test_codex_test_failure_is_not_hidden_by_tee() -> None:
    steps = _steps("pr-pipeline.yml", "codex-tests")
    pytest_step = _named_step(steps, "Run tests on Codex additions")

    assert pytest_step["id"] == "codex-pytest"
    assert pytest_step["run"].splitlines() == [
        "set +e",
        'uv run pytest tests/unit/ 2>&1 | tee "$RUNNER_TEMP/codex-pytest.log"',
        'exit "${PIPESTATUS[0]}"',
    ]


def test_codex_test_failure_preserves_untracked_tests_and_log() -> None:
    steps = _steps("pr-pipeline.yml", "codex-tests")
    condition = "failure() && steps.codex-pytest.outcome == 'failure'"
    preserve = _named_step(steps, "Preserve Codex tests on failure")
    upload = _named_step(steps, "Upload Codex tests and log")

    assert preserve["if"] == condition
    assert preserve["run"].splitlines() == [
        "git add -N tests/",
        'git diff "$BEFORE_SHA" -- tests/ > "$RUNNER_TEMP/codex-tests.patch"',
    ]
    assert upload["if"] == condition
    assert upload["with"] == {
        "name": "codex-tests-${{ github.event.pull_request.head.sha }}",
        "path": "${{ runner.temp }}/codex-tests.patch\n${{ runner.temp }}/codex-pytest.log\n",
    }


def test_codex_failure_artifacts_are_never_written_to_the_workspace() -> None:
    steps = _steps("pr-pipeline.yml", "codex-tests")
    artifact_names = ("codex-pytest.log", "codex-tests.patch", "escalation.md")

    for step in steps:
        commands = str(step.get("run", "")).splitlines()
        upload_paths = str(step.get("with", {}).get("path", "")).splitlines()
        for line in (*commands, *upload_paths):
            if any(name in line for name in artifact_names):
                assert "$RUNNER_TEMP/" in line or "${{ runner.temp }}/" in line


def test_codex_test_failure_escalates_on_the_current_pr() -> None:
    workflow = _workflow("pr-pipeline.yml")
    job = workflow["jobs"]["codex-tests"]
    escalation = _named_step(job["steps"], "Escalate — Codex test exposed an implementation defect")
    command = escalation["run"]

    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
    }
    assert escalation["if"] == "failure() && steps.codex-pytest.outcome == 'failure'"
    assert escalation["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    assert (
        'gh pr edit "${{ github.event.pull_request.number }}" '
        '--add-label "needs-implementation-fix"' in command
    )
    assert (
        'gh pr comment "${{ github.event.pull_request.number }}" '
        '--body-file "$RUNNER_TEMP/escalation.md"' in command
    )
    assert "codex-tests BLOCKED: Codex-authored tests fail against this head." in command
    assert "grep -E '^FAILED ' \"$RUNNER_TEMP/codex-pytest.log\" | head -10" in command
    assert "codex-tests-${{ github.event.pull_request.head.sha }}" in command
    run_url = "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
    assert run_url in command
