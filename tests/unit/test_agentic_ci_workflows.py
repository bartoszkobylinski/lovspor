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

    # Widened to cover cancellation in #157: a job killed by its ceiling is
    # not a failed job, and the PR-resolution guard is what this test pins.
    assert escalation["if"] == "(failure() || cancelled()) && steps.cycle.outputs.pr != ''"
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


def test_committer_identity_names_the_actual_author() -> None:
    """The committer identity must track the author step's output, like the
    marker does — hardcoding codex-ci attributed Claude fallback commits to
    Codex in git blame and `git log --author` (fabricated provenance)."""
    push = _named_step(_steps("pr-pipeline.yml", "codex-tests"), "Commit and push test additions")[
        "run"
    ]

    assert "author=\"${{ steps.author.outputs.author || 'codex' }}\"" in push
    assert 'git config user.name "${author}-ci"' in push
    assert 'git config user.email "${author}-ci@users.noreply.github.com"' in push
    assert 'git config user.name "codex-ci"' not in push
    assert 'git config user.email "codex-ci@users.noreply.github.com"' not in push


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


def test_antiloop_matches_the_marker_only_in_the_subject_line() -> None:
    """Issue #117: matching over the whole message (%B) let a human commit that
    merely wrote about the marker — pipeline docs, a revert quoting a subject —
    set skip=true, so the independent test author reported success without
    running. The subject is where the push step appends the marker, and only
    at the end of it."""
    antiloop = _named_step(_steps("pr-pipeline.yml", "codex-tests"), "Anti-loop check")["run"]

    assert "--pretty=%s" in antiloop
    assert "--pretty=%B" not in antiloop
    assert r"\[agent:(codex|claude)-(tests|mutation)\]$" in antiloop


def test_agent_jobs_are_never_serialized_by_a_shared_concurrency_group() -> None:
    """Issue #139: a concurrency group holds ONE pending entry, so a second PR's
    agent job evicted the first before it got a runner — conclusion cancelled,
    zero steps, and no escalation, because every reporting step is downstream of
    one that never ran. The single `codex`-labelled runner queues them properly.
    A per-branch group is still fine: there, superseding an older head is wanted."""
    pipeline_text = (_WORKFLOWS / "pr-pipeline.yml").read_text(encoding="utf-8")
    remediation_text = (_WORKFLOWS / "mutation-remediation.yml").read_text(encoding="utf-8")

    assert "concurrency" not in _workflow("pr-pipeline.yml")["jobs"]["codex-tests"]
    assert "lovspor-codex-subscription" not in pipeline_text
    assert "lovspor-codex-subscription" not in remediation_text
    assert "head_branch" in _workflow("mutation-remediation.yml")["concurrency"]["group"], (
        "a remediation group must not span branches"
    )


@pytest.mark.parametrize(
    ("workflow_name", "job_name"),
    [("pr-pipeline.yml", "codex-tests"), ("mutation-remediation.yml", "remediate")],
)
def test_agent_jobs_have_a_wallclock_backstop(workflow_name: str, job_name: str) -> None:
    """Issue #101: a job that hangs holds the lane against every later PR until
    GitHub's 6 h default kill. The box lock alone waits 20 minutes before it
    gives up, so the job needs its own ceiling above that."""
    job = _workflow(workflow_name)["jobs"][job_name]

    assert job["timeout-minutes"] == 60


def test_remediation_concurrency_group_is_scoped_to_head_branch() -> None:
    """Issue #139 fix: the group must interpolate head_branch verbatim so a PR's
    own newer remediation run supersedes only its own prior run, never a
    different PR's pending one. cancel-in-progress stays false: a running
    remediation is left to finish rather than being killed mid-push."""
    concurrency = _workflow("mutation-remediation.yml")["concurrency"]

    assert (
        concurrency["group"] == "mutation-remediation-${{ github.event.workflow_run.head_branch }}"
    )
    assert concurrency["cancel-in-progress"] is False


def test_pr_pipeline_workflow_scoped_concurrency_still_cancels_stale_runs() -> None:
    """The job-level lovspor-codex-subscription group was removed from
    codex-tests (issue #139), but the workflow-scoped pr-<PR#> group must
    remain: it is what still cancels a stale codex-tests run when a new SHA
    lands on the same PR, now that no other group does."""
    concurrency = _workflow("pr-pipeline.yml")["concurrency"]

    assert concurrency["group"] == "pr-${{ github.event.pull_request.number }}"
    assert concurrency["cancel-in-progress"] is True


class TestEscalationCoversEveryFailure:
    """Issues #157 and #160. The pipeline's contract is that a blocked PR ends
    labelled and commented (docs/agentic-ci.md). Twice in one day it ended red
    and silent instead: once when an unfixable lint in agent output failed the
    normalize step before the tests ran, once when the job's own 60-minute
    ceiling cancelled a hung Codex CLI. Both failures happened outside the one
    step the escalation was watching."""

    def test_the_agent_step_carries_its_own_ceiling(self) -> None:
        """A ceiling on the JOB cancels it, and a cancelled job skips the
        escalation steps that would have reported why. On the STEP, the same
        hang fails the step and the escalation still runs."""
        for workflow_name, job_name, step_name in (
            ("pr-pipeline.yml", "codex-tests", "Codex — independent PR test author"),
            ("mutation-remediation.yml", "remediate", "Codex — mutation remediation (tests only)"),
        ):
            step = _named_step(_steps(workflow_name, job_name), step_name)

            assert (
                step["timeout-minutes"]
                < _workflow(workflow_name)["jobs"][job_name]["timeout-minutes"]
            ), f"{workflow_name}: the step ceiling must bite before the job's"

    def test_a_failure_before_the_tests_still_escalates(self) -> None:
        """Issue #160: the lint step failed, the job went red, and the PR got
        no label and no comment because the escalation asked only about the
        pytest step."""
        steps = _steps("pr-pipeline.yml", "codex-tests")
        fallback = _named_step(steps, "Escalate — the pipeline failed before the tests ran")

        assert fallback["if"] == "failure() && steps.codex-pytest.outcome != 'failure'"
        assert "needs-human:pipeline" in fallback["run"]
        assert "gh pr comment" in fallback["run"]

    def test_agent_work_survives_a_failure_before_the_tests(self) -> None:
        """Issue #160, second half: the independent author's whole run was
        discarded, so the next round starts from zero. #95 preserved the tests
        when they FAILED; they must also survive the pipeline failing."""
        steps = _steps("pr-pipeline.yml", "codex-tests")
        preserve = _named_step(steps, "Preserve agent work when the pipeline fails")

        assert preserve["if"] == "failure() && steps.codex-pytest.outcome != 'failure'"
        # Written by the independent test author, which caught the first
        # version diffing the worktree instead of BEFORE_SHA: the agent
        # commits its own work, so a bare `git diff` preserves nothing.
        assert [
            line for line in preserve["run"].splitlines() if not line.strip().startswith("#")
        ] == [
            "git add -N tests/",
            'git diff "$BEFORE_SHA" -- tests/ > "$RUNNER_TEMP/agent-work.patch" || true',
        ]

        upload = _named_step(steps, "Upload preserved agent work")
        assert upload["if"] == preserve["if"]
        assert upload["with"] == {
            "name": "agent-work-${{ github.event.pull_request.head.sha }}",
            "path": "${{ runner.temp }}/agent-work.patch",
            "if-no-files-found": "ignore",
        }

    def test_the_pipeline_escalation_runs_after_every_step_it_reports_on(self) -> None:
        """Also the author's, and the sharper of its two catches: steps run in
        order, so a failure in step N cannot trigger a step at N-1. Placed
        before the push, this escalation could never report a push that
        failed — and a rejected non-fast-forward push is a real mode, seen in
        issue #67. It must sit last."""
        names = [step.get("name") for step in _steps("pr-pipeline.yml", "codex-tests")]

        assert names.index("Escalate — the pipeline failed before the tests ran") > names.index(
            "Commit and push test additions"
        )
        assert names[-1] == "Escalate — the pipeline failed before the tests ran"

    def test_the_pipeline_escalation_names_the_pr_and_the_preserved_artifact(self) -> None:
        """Also the author's: a comment that does not say where the work went
        leaves the operator with a label and no way to recover the round."""
        fallback = _named_step(
            _steps("pr-pipeline.yml", "codex-tests"),
            "Escalate — the pipeline failed before the tests ran",
        )
        command = fallback["run"]

        assert fallback["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
        assert "agent-work-${{ github.event.pull_request.head.sha }}" in command
        assert (
            'gh pr comment "${{ github.event.pull_request.number }}" '
            '--body-file "$RUNNER_TEMP/pipeline-escalation.md"' in command
        )
        run_url = (
            "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
        )
        assert run_url in command

    def test_remediation_escalates_even_when_it_is_cancelled(self) -> None:
        """Issue #157: a cancelled job is not a failed one, so `failure()`
        alone let the timeout path end without a label."""
        steps = _steps("mutation-remediation.yml", "remediate")
        escalate = _named_step(steps, "Escalate on remediation failure")

        assert "cancelled()" in escalate["if"]
        assert "failure()" in escalate["if"]
