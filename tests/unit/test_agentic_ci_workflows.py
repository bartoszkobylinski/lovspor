"""Regression tests for the test-authoring and mutation-remediation workflows."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
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


def test_fast_ci_rejects_committed_conflict_markers_before_lint(tmp_path: Path) -> None:
    steps = _steps("pr-pipeline.yml", "fast-ci")
    names = [step.get("name") for step in steps]
    gate = _named_step(steps, "No committed conflict markers")

    assert names.index(gate["name"]) < names.index("Ruff lint")

    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("<<<<<<< HEAD\nkept\n=======\nother\n>>>>>>> branch\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)

    result = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", gate["run"]],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "tracked.txt:1:<<<<<<< HEAD" in result.stdout
    assert "tracked.txt:5:>>>>>>> branch" in result.stdout
    assert "::error::unresolved merge-conflict markers are committed" in result.stdout


@pytest.mark.parametrize(
    "contents",
    [
        "ordinary text\n=======\n",
        "example: <<<<<<< HEAD\n",
        "<<<<<<<HEAD\n>>>>>>>branch\n",
    ],
)
def test_fast_ci_conflict_marker_gate_accepts_non_marker_boundaries(
    tmp_path: Path, contents: str
) -> None:
    gate = _named_step(_steps("pr-pipeline.yml", "fast-ci"), "No committed conflict markers")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text(contents, encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)

    result = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", gate["run"]],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("workflow_name", "job_name", "step_name"),
    [
        ("pr-pipeline.yml", "codex-author", "Codex — independent PR test author"),
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
            "needs.codex-author.outputs.skip != 'true'",
        ),
        (
            "mutation-remediation.yml",
            "remediate-verify",
            "needs.remediate.outputs.run == 'true'",
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
    steps = _steps("mutation-remediation.yml", "remediate-verify")
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
    assert 'scripts/ci/pr_sticky_comment.sh mutation "${{ steps.cycle.outputs.pr }}"' in command
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

    working_checkout = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
        and step.get("with", {}).get("ref") == "${{ github.event.workflow_run.head_branch }}"
    )
    assert working_checkout["if"] == "steps.gate.outputs.run == 'true'"
    assert names.index(blocked["name"]) < names.index(working_checkout.get("name"))


@pytest.mark.parametrize(
    ("workflow_name", "job_name", "step_name"),
    [
        ("pr-pipeline.yml", "codex-author", "Codex — independent PR test author"),
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
        _steps("mutation-remediation.yml", "remediate-verify"),
        "Commit and push, or report BLOCKED",
    )["run"]

    assert "[agent:${{ needs.codex-author.outputs.author || 'codex' }}-tests]" in pr_push
    assert "[agent:${{ needs.remediate.outputs.author || 'codex' }}-mutation]" in rem_push


def test_committer_identity_names_the_actual_author() -> None:
    """The committer identity must track the author step's output, like the
    marker does — hardcoding codex-ci attributed Claude fallback commits to
    Codex in git blame and `git log --author` (fabricated provenance)."""
    push = _named_step(_steps("pr-pipeline.yml", "codex-tests"), "Commit and push test additions")[
        "run"
    ]

    assert "author=\"${{ needs.codex-author.outputs.author || 'codex' }}\"" in push
    assert 'git config user.name "${author}-ci"' in push
    assert 'git config user.email "${author}-ci@users.noreply.github.com"' in push
    assert 'git config user.name "codex-ci"' not in push
    assert 'git config user.email "codex-ci@users.noreply.github.com"' not in push


def test_antiloop_and_cycle_counting_recognise_the_claude_markers() -> None:
    """A fallback-authored HEAD must not retrigger test generation, and a
    Claude remediation commit must count toward the two-cycle limit —
    otherwise the fallback author gets unlimited cycles."""
    antiloop = _named_step(_steps("pr-pipeline.yml", "codex-author"), "Anti-loop check")["run"]
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
    """The pytest step records its own exit status through the tee (PIPESTATUS)
    and hands it to the verdict as junit; it no longer fails the job itself,
    because a red suite is evidence and the convergence verdict is the judge
    (issue #248)."""
    steps = _steps("pr-pipeline.yml", "codex-tests")
    pytest_step = _named_step(steps, "Run tests on Codex additions")

    assert pytest_step["id"] == "codex-pytest"
    lines = pytest_step["run"].splitlines()
    assert lines[0] == "set +e"
    assert '--junitxml="$RUNNER_TEMP/codex-junit.xml"' in lines[1]
    assert '| tee "$RUNNER_TEMP/codex-pytest.log"' in lines[2]
    assert lines[3] == 'echo "status=${PIPESTATUS[0]}" >> "$GITHUB_OUTPUT"'
    assert not any(line.startswith("exit") for line in lines)


def test_convergence_verdict_is_the_only_step_that_fails_on_author_tests() -> None:
    """Issue #248. The verdict reads the pipeline sticky comment for the round
    count, runs the classifier with --apply, and exits 1 only when the verdict
    blocks — so every failure() step downstream keys off THIS step."""
    steps = _steps("pr-pipeline.yml", "codex-tests")
    verdict = _named_step(steps, "Convergence verdict")
    run = verdict["run"]

    assert verdict["id"] == "verdict"
    assert verdict["env"]["CODEX_BLOCKING_CAP"] == "${{ vars.CODEX_BLOCKING_CAP || '3' }}"
    assert 'contains("<!-- lovspor-sticky:pipeline -->")' in run
    assert "scripts/ci/codex_convergence.py" in run
    assert '--junit "$RUNNER_TEMP/codex-junit.xml"' in run
    assert '--sticky-body "$RUNNER_TEMP/sticky-pipeline.md"' in run
    assert '--cap "$CODEX_BLOCKING_CAP"' in run
    assert '--comment "$RUNNER_TEMP/escalation.md"' in run
    assert "--apply" in run
    assert 'if [ "$blocks" = "true" ]; then' in run
    assert '--pytest-status "$PYTEST_STATUS"' in run
    # An absent status (the pytest step never wrote one) must read as a failure,
    # never as 0 — the default is the fail-closed direction.
    assert verdict["env"]["PYTEST_STATUS"] == "${{ steps.codex-pytest.outputs.status || '1' }}"
    # advisory tests were rewritten in place: prove green before the commit step
    assert "uv run pytest tests/unit/ -q" in run
    for name in (
        "Preserve Codex tests on failure",
        "Upload Codex tests and log",
        "Escalate — Codex test exposed an implementation defect",
    ):
        assert _named_step(steps, name)["if"] == "failure() && steps.verdict.outcome == 'failure'"
    advisory = _named_step(steps, "Report advisory proposals")
    assert "steps.verdict.outputs.advisory != '0'" in advisory["if"]
    assert "pr_sticky_comment.sh pipeline" in advisory["run"]  # one marker per workflow


def test_codex_test_failure_preserves_untracked_tests_and_log() -> None:
    steps = _steps("pr-pipeline.yml", "codex-tests")
    condition = "failure() && steps.verdict.outcome == 'failure'"
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
    assert escalation["if"] == "failure() && steps.verdict.outcome == 'failure'"
    assert escalation["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    assert (
        'gh pr edit "${{ github.event.pull_request.number }}" '
        '--add-label "needs-implementation-fix"' in command
    )
    assert (
        'scripts/ci/pr_sticky_comment.sh pipeline "${{ github.event.pull_request.number }}" '
        '"$RUNNER_TEMP/escalation.md"' in command
    )
    # The verdict writes the escalation body (round number + the counted phrase);
    # this step only appends the artifact pointer, so it must APPEND, not overwrite.
    assert '>> "$RUNNER_TEMP/escalation.md"' in command
    assert '> "$RUNNER_TEMP/escalation.md"' not in command.replace('>> "$RUNNER_TEMP', "")
    assert "codex-tests-${{ github.event.pull_request.head.sha }}" in command


def test_the_verdict_and_the_round_counter_agree_on_the_blocked_phrase() -> None:
    """The round count is the number of times this phrase appears in the pipeline
    sticky comment. The verdict script owns the phrase; the workflow must not
    write a competing one, or the count drifts (issue #248)."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "codex_convergence.py"
    spec = importlib.util.spec_from_file_location("codex_convergence_phrase", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    steps = _steps("pr-pipeline.yml", "codex-tests")
    for step in steps:
        assert module.BLOCKED_PHRASE not in str(step.get("run", "")), step["name"]


def test_antiloop_matches_the_marker_only_in_the_subject_line() -> None:
    """Issue #117: matching over the whole message (%B) let a human commit that
    merely wrote about the marker — pipeline docs, a revert quoting a subject —
    set skip=true, so the independent test author reported success without
    running. The subject is where the push step appends the marker, and only
    at the end of it."""
    antiloop = _named_step(_steps("pr-pipeline.yml", "codex-author"), "Anti-loop check")["run"]

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

    assert "concurrency" not in _workflow("pr-pipeline.yml")["jobs"]["codex-author"]
    assert "concurrency" not in _workflow("pr-pipeline.yml")["jobs"]["codex-tests"]
    assert "lovspor-codex-subscription" not in pipeline_text
    assert "lovspor-codex-subscription" not in remediation_text
    assert "head_branch" in _workflow("mutation-remediation.yml")["concurrency"]["group"], (
        "a remediation group must not span branches"
    )


@pytest.mark.parametrize(
    ("workflow_name", "job_name"),
    [("pr-pipeline.yml", "codex-author"), ("mutation-remediation.yml", "remediate")],
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
            ("pr-pipeline.yml", "codex-author", "Codex — independent PR test author"),
            ("mutation-remediation.yml", "remediate", "Codex — mutation remediation (tests only)"),
        ):
            step = _named_step(_steps(workflow_name, job_name), step_name)
            job = _workflow(workflow_name)["jobs"][job_name]

            assert step["timeout-minutes"] == 45
            assert job["timeout-minutes"] == 60
            assert step["timeout-minutes"] < job["timeout-minutes"], (
                f"{workflow_name}: the step ceiling must bite before the job's"
            )

    def test_a_failure_before_the_tests_still_escalates(self) -> None:
        """Issue #160: the lint step failed, the job went red, and the PR got
        no label and no comment because the escalation asked only about the
        pytest step."""
        steps = _steps("pr-pipeline.yml", "codex-tests")
        fallback = _named_step(steps, "Escalate — the pipeline failed before the tests ran")

        assert fallback["if"] == "failure() && steps.verdict.outcome != 'failure'"
        assert "needs-human:pipeline" in fallback["run"]
        assert "scripts/ci/pr_sticky_comment.sh pipeline" in fallback["run"]

    def test_agent_work_survives_a_failure_before_the_tests(self) -> None:
        """Issue #160, second half: the independent author's whole run was
        discarded, so the next round starts from zero. #95 preserved the tests
        when they FAILED; they must also survive the pipeline failing."""
        steps = _steps("pr-pipeline.yml", "codex-tests")
        preserve = _named_step(steps, "Preserve agent work when the pipeline fails")

        assert preserve["if"] == "failure() && steps.verdict.outcome != 'failure'"
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
            "scripts/ci/pr_sticky_comment.sh pipeline "
            '"${{ github.event.pull_request.number }}" '
            '"$RUNNER_TEMP/pipeline-escalation.md"' in command
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

    def test_remediation_escalation_runs_after_every_step_it_reports_on(self) -> None:
        """A failure can only be reported by a later step. In particular, a
        rejected push must not become another red, silent remediation run. Both
        remediation lanes end in their own escalation: the agent lane can die
        with its box before the verifier ever starts."""
        for job_name in ("remediate", "remediate-verify"):
            names = [step.get("name") for step in _steps("mutation-remediation.yml", job_name)]
            assert names[-1] == "Escalate on remediation failure"

        verify_names = [
            step.get("name") for step in _steps("mutation-remediation.yml", "remediate-verify")
        ]
        assert verify_names.index("Escalate on remediation failure") > verify_names.index(
            "Commit and push, or report BLOCKED"
        )


class TestAGreenRunRetractsItsOwnVerdict:
    """Issue #191. The pipeline's verdict is durable only in the labels —
    checks scroll off a PR, labels sit on it and on every filtered list. Six
    `--add-label` calls existed across the two workflows and no `--remove-label`
    anywhere, so #187 finished with every check green, `mergeStateStatus:
    CLEAN`, and `needs-implementation-fix` still on it from the round before
    the fix. A label that outlives its verdict inverts the signal it exists to
    carry, and the direction of the error is the expensive one: a genuinely
    blocked PR then looks exactly like a resolved one."""

    def _ready_step(self) -> dict[str, Any]:
        return _named_step(
            _steps("pr-pipeline.yml", "ready"), "Retract the blocked labels this run disproved"
        )

    def test_a_green_run_clears_the_labels_a_blocked_round_wrote(self) -> None:
        command = self._ready_step()["run"]

        assert "for label in $BLOCKED_LABELS; do" in command
        assert 'gh pr edit "$PR" --remove-label "$label"' in command

    def test_every_label_the_pipeline_can_apply_is_one_it_can_retract(self) -> None:
        """The guard that survives the next label. Adding a `--add-label` with
        no matching retraction reintroduces exactly this bug, so the two sets
        are compared rather than a fixed list being asserted."""
        applied = set()
        for workflow_name in ("pr-pipeline.yml", "mutation-remediation.yml"):
            text = (_WORKFLOWS / workflow_name).read_text(encoding="utf-8")
            applied.update(re.findall(r'--add-label "([^"]+)"', text))
        retracted = set(self._ready_step()["env"]["BLOCKED_LABELS"].split())

        assert applied, "no --add-label found; the regex or the workflows moved"
        assert applied <= retracted, f"never retracted: {sorted(applied - retracted)}"

    def test_the_retraction_waits_for_the_gate_it_speaks_for(self) -> None:
        """READY is a claim about the mutation gate, so it must not be made
        when that job was skipped — which is what happens on the run where the
        test author pushed and a fresh run is already starting."""
        job = _workflow("pr-pipeline.yml")["jobs"]["ready"]

        assert set(job["needs"]) == {"fast-ci", "codex-tests", "mutation"}
        assert job["if"] == "needs.mutation.result == 'success'"

    def test_the_retraction_is_allowed_to_write_labels(self) -> None:
        """A step that silently lacks the scope would leave the bug in place
        while reporting success."""
        job = _workflow("pr-pipeline.yml")["jobs"]["ready"]

        assert job["permissions"]["pull-requests"] == "write"

    def test_a_label_that_is_not_there_is_not_removed(self) -> None:
        """`gh pr edit --remove-label` on an absent label is an API call whose
        failure would fail the job at the one moment the pipeline is trying to
        say everything passed. The step asks first."""
        command = self._ready_step()["run"]

        assert "gh pr view" in command
        assert "grep -Fxq" in command
        assert command.index("grep -Fxq") < command.index('--remove-label "$label"')


class TestAJobThatDiesWithItsRunnerStillReports:
    """Issue #193. Both `codex-tests` escalations are steps of that job, and a
    step cannot run on a runner that no longer exists — so no in-job condition,
    `failure()` or `always()` or `cancelled()`, can report a job that died with
    its runner. #157 and #160 fixed the cases where the job survived to reach a
    later step; on #192 it did not: five steps ran, the self-hosted box went
    offline mid-Codex-step, and the PR ended red with no label and no comment.

    The reporting job therefore lives outside that job, on a hosted runner, so
    it cannot share the failure mode it exists to report."""

    JOB = "codex-tests-report"

    def _job(self) -> dict[str, Any]:
        return _workflow("pr-pipeline.yml")["jobs"][self.JOB]

    def _step(self) -> dict[str, Any]:
        return _named_step(
            _steps("pr-pipeline.yml", self.JOB),
            "Report a codex-tests job that never reached its own escalation",
        )

    def test_the_reporter_does_not_run_on_the_lane_it_reports_on(self) -> None:
        """A reporter on the `codex` runner would be offline in exactly the
        case it exists for. It watches both lanes: `codex-author` is the one
        that dies with the box, and `codex-tests` can still fail before it
        reaches its own escalation."""
        assert self._job()["runs-on"] == "ubuntu-latest"
        assert _workflow("pr-pipeline.yml")["jobs"]["codex-author"]["runs-on"] != "ubuntu-latest"
        assert self._job()["needs"] == ["codex-author", "codex-tests"]

    def test_the_reporter_speaks_for_a_failure_and_stays_out_of_a_cancellation(self) -> None:
        """`always()` would fire on a concurrency cancellation too, and a run
        cancelled by the next push is not a blocked PR — labelling it would put
        `needs-human:pipeline` on healthy work. `!cancelled()` is the form that
        survives a failed dependency without claiming a cancelled one."""
        assert self._job()["if"] == (
            "${{ !cancelled() && (needs.codex-tests.result == 'failure'"
            " || needs.codex-author.result == 'failure') }}"
        )

    def test_the_reporter_is_silent_when_the_job_already_reported_itself(self) -> None:
        """The in-job escalation runs before `codex-tests` completes, so this
        job sees its label. Two labels and two comments for one failure is
        noise that trains a reader to ignore both."""
        command = self._step()["run"]

        assert "gh pr view" in command
        assert "grep -Fxq" in command
        assert "already reported" in command
        assert command.index("exit 0") < command.index('--add-label "needs-human:pipeline"')

    def test_the_reporter_recognises_every_blocking_verdict(self) -> None:
        """Any blocking label means an earlier escalation already left the
        durable verdict. The outside reporter must not add a second verdict and
        comment merely because that escalation used a different blocked label."""
        reporter_labels = set(self._step()["env"]["BLOCKED_LABELS"].split())
        ready_labels = set(
            _named_step(
                _steps("pr-pipeline.yml", "ready"),
                "Retract the blocked labels this run disproved",
            )["env"]["BLOCKED_LABELS"].split()
        )

        assert reporter_labels == ready_labels

    def test_the_reporter_labels_and_links_the_run(self) -> None:
        """A label says a human is needed; the run URL is the only thing that
        says why, since the job's own log is what went missing."""
        command = self._step()["run"]

        assert '--add-label "needs-human:pipeline"' in command
        assert "scripts/ci/pr_sticky_comment.sh pipeline" in command
        assert (
            "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
            in command
        )

    def test_the_reporter_is_allowed_to_write_labels(self) -> None:
        permissions = self._job()["permissions"]

        assert permissions["pull-requests"] == "write"
        assert permissions["issues"] == "write"

    def test_a_skipped_agent_lane_is_not_a_failure(self) -> None:
        """`codex-tests` is skipped on fork PRs by design. Reporting that as a
        pipeline defect would label every external contribution."""
        assert "needs.codex-tests.result == 'failure'" in self._job()["if"]
        assert "!= 'skipped'" not in self._job()["if"]


class TestEscalationsShareOneCommentPerWorkflow:
    """A new comment mails the PR author; an edit does not. PR #230 sent ten
    mails carrying nine escalations, so the rounds are appended to one sticky
    comment per workflow. The escalations themselves are unchanged."""

    @staticmethod
    def _sticky_jobs() -> list[tuple[str, str]]:
        return [
            ("pr-pipeline.yml", "codex-tests"),
            ("pr-pipeline.yml", "codex-tests-report"),
            ("mutation-remediation.yml", "remediate"),
        ]

    @pytest.mark.parametrize("workflow_name", ["pr-pipeline.yml", "mutation-remediation.yml"])
    def test_no_workflow_creates_a_fresh_comment_per_round(self, workflow_name: str) -> None:
        text = (_WORKFLOWS / workflow_name).read_text(encoding="utf-8")

        assert "gh pr comment" not in text

    @pytest.mark.parametrize(("workflow_name", "job_name"), _sticky_jobs())
    def test_the_helper_is_checked_out_before_it_is_called(
        self, workflow_name: str, job_name: str
    ) -> None:
        steps = _steps(workflow_name, job_name)
        first_call = next(
            i for i, step in enumerate(steps) if "pr_sticky_comment.sh" in str(step.get("run", ""))
        )
        checkouts = [
            i
            for i, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("actions/checkout@")
        ]

        assert checkouts, f"{job_name} calls the helper with nothing checked out"
        assert min(checkouts) < first_call

    @pytest.mark.parametrize(
        ("workflow_name", "job_name", "ref"),
        [
            (
                "pr-pipeline.yml",
                "codex-tests-report",
                "${{ github.event.pull_request.head.sha }}",
            ),
            (
                "mutation-remediation.yml",
                "remediate",
                "${{ github.event.repository.default_branch }}",
            ),
        ],
    )
    def test_the_helper_checkout_is_unconditional_and_pinned(
        self, workflow_name: str, job_name: str, ref: str
    ) -> None:
        """`remediate` runs on `workflow_run` holding a write token, so it takes
        the helper from the default branch: a PR must not rewrite the script that
        reports on it. `codex-tests-report` runs on `pull_request` with no such
        context and takes the head — off the default branch, the PR that
        introduces the helper would have none to call and the reporter would die
        on a missing file, which is the silent BLOCKED of issue #193."""
        helper = _named_step(_steps(workflow_name, job_name), "Check out the escalation helper")

        assert "if" not in helper, "an escalation helper that may be absent is not a helper"
        assert helper["with"]["ref"] == ref
        assert helper["with"]["sparse-checkout"] == "scripts/ci"
        assert helper["with"]["persist-credentials"] is False

    def test_a_remediation_cycle_reports_progress_without_mailing_the_author(self) -> None:
        """The cycle notice carries no label and asks nothing of a human, so it
        belongs in the run summary. It was two of PR #230's ten mails."""
        steps = _steps("mutation-remediation.yml", "remediate-verify")
        push = _named_step(steps, "Commit and push, or report BLOCKED")["run"]

        assert "pipeline rerunning." in push
        assert '>> "$GITHUB_STEP_SUMMARY"' in push
        assert "pr_sticky_comment.sh mutation" in push, "the BLOCKED path still escalates"

    @pytest.mark.parametrize(
        ("workflow_name", "marker"),
        [("pr-pipeline.yml", "pipeline"), ("mutation-remediation.yml", "mutation")],
    )
    def test_each_workflow_owns_its_marker(self, workflow_name: str, marker: str) -> None:
        """Both workflows can be in flight at once. A shared comment would let
        one workflow's read-modify-write drop the other's round."""
        text = (_WORKFLOWS / workflow_name).read_text(encoding="utf-8")
        used = set(re.findall(r"pr_sticky_comment\.sh (\w+)", text))

        assert used == {marker}


def test_convergence_verdict_cannot_ignore_a_nonzero_pytest_status() -> None:
    """A pytest failure with an absent or incomplete JUnit report must not be
    converted into a green verdict merely because no failures were parsed.
    Authored by the CI test author on PR #261, the mechanism's first live round.
    One deviation from the verbatim test: it asserted the status reference as an
    exact env value, which would forbid the fail-closed `|| '1'` default — an
    absent status must read as a failure, not as an empty string."""
    steps = _steps("pr-pipeline.yml", "codex-tests")
    pytest_step = _named_step(steps, "Run tests on Codex additions")
    verdict = _named_step(steps, "Convergence verdict")

    assert 'echo "status=${PIPESTATUS[0]}" >> "$GITHUB_OUTPUT"' in pytest_step["run"]
    assert any(
        "steps.codex-pytest.outputs.status" in value for value in verdict["env"].values()
    ) or ("steps.codex-pytest.outputs.status" in verdict["run"])


class TestTheAgentLaneOnlyHoldsTheAgent:
    """Issue #272. The `codex`-labelled box is 1 shared core and 2 GB with no
    swap, and it exists for one reason: the Codex session's `auth.json` is a
    long-lived ChatGPT credential that cannot be handed to a hosted runner.
    Running the unit suite there as well killed the machine twice in one
    afternoon, the second death 28 minutes into `Run tests on Codex additions`.
    The suite now runs on the hosted verdict lane; these tests are what keeps it
    from drifting back."""

    def _author(self) -> dict[str, Any]:
        return _workflow("pr-pipeline.yml")["jobs"]["codex-author"]

    def _author_text(self) -> str:
        return yaml.safe_dump(self._author())

    def test_the_agent_lane_never_runs_the_whole_suite(self) -> None:
        for step in self._author()["steps"]:
            assert "pytest tests/unit/" not in str(step.get("run", "")), (
                f"{step.get('name')} runs the whole suite on the 2 GB box"
            )

    def test_the_prompt_forbids_a_whole_suite_run_on_the_box(self) -> None:
        """The workflow cannot stop the agent from typing the command itself:
        the first death was inside the Codex step, and the prompt used to ask
        for `uv run pytest tests/unit/` in as many words."""
        prompt = (
            Path(__file__).resolve().parents[2] / ".github" / "codex" / "pr-tests.md"
        ).read_text(encoding="utf-8")

        assert "Do NOT run `uv run pytest tests/unit/`" in prompt
        assert "run ONLY the test files you touched" in prompt

    def test_the_suite_runs_on_the_hosted_verdict_lane(self) -> None:
        job = _workflow("pr-pipeline.yml")["jobs"]["codex-tests"]
        pytest_step = _named_step(job["steps"], "Run tests on Codex additions")

        assert job["runs-on"] == "ubuntu-latest"
        assert "uv run pytest tests/unit/" in pytest_step["run"]

    def test_the_agent_lane_cannot_reach_the_branch(self) -> None:
        """The box writes a patch; only the hosted lane pushes. A push token on
        the machine that runs an agent session is a credential the agent can
        reach."""
        text = self._author_text()
        checkout = next(
            step
            for step in self._author()["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )

        assert self._author()["permissions"] == {"contents": "read"}
        assert checkout["with"]["persist-credentials"] is False
        assert "token" not in checkout["with"]
        assert "LOVSPOR_CI_PUSH_TOKEN" not in text
        assert "git push" not in text

    def test_the_agent_work_travels_as_a_patch(self) -> None:
        artifact = "agent-tests-${{ github.event.pull_request.head.sha }}"
        upload = _named_step(self._author()["steps"], "Upload the agent's tests")
        download = _named_step(
            _steps("pr-pipeline.yml", "codex-tests"), "Download the agent's tests"
        )
        apply_step = _named_step(
            _steps("pr-pipeline.yml", "codex-tests"), "Apply the agent's tests"
        )

        assert self._author()["outputs"] == {
            "skip": "${{ steps.antiloop.outputs.skip }}",
            "author": "${{ steps.author.outputs.author }}",
            "before_sha": "${{ steps.base.outputs.before_sha }}",
            "patch": "${{ steps.patch.outputs.patch }}",
        }
        assert upload["with"]["name"] == artifact
        assert download["with"]["name"] == artifact
        assert upload["if"] == "steps.patch.outputs.patch == 'true'"
        assert download["if"] == "needs.codex-author.outputs.patch == 'true'"
        assert apply_step["if"] == "needs.codex-author.outputs.patch == 'true'"
        # --index, so the scope guard on the verifier sees staged files the way
        # it saw them on the box, and an unapplyable patch fails loudly instead
        # of leaving a run of the pre-existing suite to pass as a fresh round.
        assert "git apply --index" in apply_step["run"]

    def test_the_scope_guard_runs_on_both_lanes(self) -> None:
        """The prompt is not a boundary, and neither is an artifact: the guard
        must hold on the machine that produced the patch and on the one that
        pushes it."""
        for job_name in ("codex-author", "codex-tests"):
            guard = _named_step(_steps("pr-pipeline.yml", job_name), "Scope guard")
            assert guard["run"] == 'scripts/ci/assert_codex_scope.sh "$BEFORE_SHA"'

    def test_the_verifier_refuses_to_be_green_when_the_author_lane_died(self) -> None:
        """A box that dies mid-session leaves `codex-author` failed. The verdict
        lane still runs — it is the escalation path for exactly that — so it
        must fail rather than report a green independent round that never
        happened (issues #193, #272)."""
        steps = _steps("pr-pipeline.yml", "codex-tests")
        names = [step.get("name") for step in steps]
        guard = _named_step(steps, "The author lane did not finish")

        assert guard["if"] == "needs.codex-author.result != 'success'"
        assert "exit 1" in guard["run"]
        assert names.index("The author lane did not finish") < names.index(
            "Run tests on Codex additions"
        )

    def test_the_memory_sampler_cannot_outlive_the_job(self) -> None:
        """This runner does not force-kill process trees on cancellation, so a
        background loop started by a step outlives the job that started it. The
        sampler is bounded twice: `timeout` in the loop, and a kill step that
        runs even when the agent step failed."""
        steps = self._author()["steps"]
        sampler = _named_step(steps, "Sample memory while the agent runs")
        stop = _named_step(steps, "Stop the memory sampler")

        assert "nohup timeout " in sampler["run"]
        assert stop["if"].startswith("always()")
        assert "kill " in stop["run"]


class TestTheRemediationLaneOnlyHoldsTheAgent:
    """Issue #272, second half. The remediation workflow ran the same shape as
    the PR pipeline did — agent session AND `uv run pytest tests/unit/` on the
    same 2 GB box, and a remediation job overlapping a PR-pipeline job is the
    pair that wedged the machine on 2026-09-06."""

    def _agent(self) -> dict[str, Any]:
        return _workflow("mutation-remediation.yml")["jobs"]["remediate"]

    def test_the_agent_lane_never_runs_the_whole_suite(self) -> None:
        for step in self._agent()["steps"]:
            assert "pytest tests/unit/" not in str(step.get("run", "")), (
                f"{step.get('name')} runs the whole suite on the 2 GB box"
            )

    def test_the_prompt_forbids_a_whole_suite_run_on_the_box(self) -> None:
        prompt = (
            Path(__file__).resolve().parents[2] / ".github" / "codex" / "mutation-remediation.md"
        ).read_text(encoding="utf-8")

        assert "Do NOT run `uv run pytest tests/unit/`" in prompt

    def test_the_suite_runs_on_the_hosted_verifier(self) -> None:
        job = _workflow("mutation-remediation.yml")["jobs"]["remediate-verify"]
        suite = _named_step(job["steps"], "Run tests on Codex additions")

        assert job["runs-on"] == "ubuntu-latest"
        assert job["needs"] == ["remediate"]
        assert "uv run pytest tests/unit/" in suite["run"]

    def test_the_agent_work_travels_as_a_patch(self) -> None:
        artifact = "remediation-tests-${{ github.event.workflow_run.head_sha }}"
        upload = _named_step(self._agent()["steps"], "Upload the agent's tests")
        download = _named_step(
            _steps("mutation-remediation.yml", "remediate-verify"), "Download the agent's tests"
        )
        apply_step = _named_step(
            _steps("mutation-remediation.yml", "remediate-verify"), "Apply the agent's tests"
        )

        assert upload["with"]["name"] == artifact
        assert download["with"]["name"] == artifact
        assert "git apply --index" in apply_step["run"]

    def test_the_verifier_only_runs_for_a_cycle_the_gate_allowed(self) -> None:
        """The gate, the cycle count and both BLOCKED paths stay on the agent
        lane, so the verifier must not start a round the gate refused."""
        job = _workflow("mutation-remediation.yml")["jobs"]["remediate-verify"]

        assert job["if"] == "${{ !cancelled() && needs.remediate.outputs.run == 'true' }}"
        assert self._agent()["outputs"]["run"] == "${{ steps.cycle.outputs.run }}"
