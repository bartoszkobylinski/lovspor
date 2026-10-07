"""Regression tests for the test-authoring and mutation-remediation workflows."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
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


_SAMPLER_DEADLINE = "deadline=$((SECONDS + 3300))"
_SAMPLER_LOOP = 'while [ "$SECONDS" -lt "$deadline" ]; do'


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
        [str(_WORKFLOWS.parents[1] / gate["run"].strip())],
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
        [str(_WORKFLOWS.parents[1] / gate["run"].strip())],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_fast_ci_runs_the_fail_closed_security_scan() -> None:
    """Issue #323 criterion 12: the deep gate's security scan needs a CI
    counterpart, or an uninstalled or bypassed local hook produces a green PR
    over a scan nobody ran. It is a step in fast-ci because the ruleset requires
    fast-ci by name; a separate job would not block a merge on its own."""
    steps = _steps("pr-pipeline.yml", "fast-ci")
    names = [step.get("name") for step in steps]
    scan = _named_step(steps, "Security scan (fail-closed)")

    assert "scripts/quality/check_security_scan.py" in scan["run"]
    assert names.index(scan["name"]) < names.index("Unit tests")


_REGISTER_STEP = "Mutation-equivalents register is current"
_REGISTER_CHECK = "uv run python scripts/ci/mutation_to_json.py --check-equivalents"


def test_fast_ci_fails_on_a_stale_or_refused_equivalents_entry() -> None:
    """Issue #535: `--check-equivalents` ran in no job, so the stale
    `scan_into` waiver of #443 sat on main until someone ran it by hand. It is
    a fast-ci step because the ruleset requires fast-ci by name. The step calls
    the CLI and never names the register's path, so it holds whether the
    register is one file or a directory (#516)."""
    steps = _steps("pr-pipeline.yml", "fast-ci")
    names = [step.get("name") for step in steps]
    check = _named_step(steps, _REGISTER_STEP)

    assert check["run"].strip() == _REGISTER_CHECK
    assert "continue-on-error" not in check
    assert "if" not in check
    assert names.index(_REGISTER_STEP) < names.index("Unit tests")


@pytest.mark.parametrize(
    ("register", "verdict", "diagnostic"),
    [
        (
            '[[equivalent]]\nfile = "m.py"\nsymbol = "f"\nregistered = "2026-10-04, test"\n'
            'mutation = """\n-x = 1\n+x = 2\n"""\njustification = "the line moved on"\n',
            "1 registered, 0 refused, 1 stale",
            "STALE: m.py f: removed line not in file — 'x = 1'",
        ),
        (
            "[[equivalent]]\nfile = 3\n",
            "0 registered, 1 refused, 0 stale",
            "mutation-equivalents/m/f-00000000.toml: 3: missing",
        ),
    ],
)
def test_the_register_step_fails_on_what_the_check_reports(
    tmp_path: Path, register: str, verdict: str, diagnostic: str
) -> None:
    """The step's own command, run against a register the check rejects."""
    ci = tmp_path / "scripts" / "ci"
    ci.mkdir(parents=True)
    shutil.copy2(_REPO / "scripts" / "ci" / "mutation_to_json.py", ci / "mutation_to_json.py")
    (tmp_path / "m.py").write_text("x = 3\n", encoding="utf-8")
    entry = tmp_path / "mutation-equivalents" / "m" / "f-00000000.toml"
    entry.parent.mkdir(parents=True)
    entry.write_text(register, encoding="utf-8")
    run = _named_step(_steps("pr-pipeline.yml", "fast-ci"), _REGISTER_STEP)["run"]
    command = run.replace("uv run python", shlex.quote(sys.executable))

    done = subprocess.run(
        ["bash", "-e", "-c", command], cwd=tmp_path, capture_output=True, text=True, check=False
    )

    assert done.returncode == 1
    assert verdict in done.stdout
    assert diagnostic in done.stderr


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
def test_codex_account_homes_live_on_the_runner_host(
    workflow_name: str, job_name: str, step_name: str
) -> None:
    """Issue #445: a repository variable names one machine's paths, which is
    what pinned the lanes to /home/runner on the Linux box. The homes are the
    host's, under the runner user's HOME. The primary is checked for a login
    before the failover can mistake a missing one for a rate limit; the
    secondary is passed only when it has one."""
    job = _workflow(workflow_name)["jobs"][job_name]
    command = _named_step(job["steps"], step_name)["run"].splitlines()
    launch = command.index("python3 scripts/ci/codex_account_failover.py \\")

    assert "CODEX_HOME" not in job["env"]
    assert "CODEX_PRIMARY_HOME" not in job["env"]
    assert "CODEX_SECONDARY_HOME" not in job["env"]
    assert command.index('CODEX_PRIMARY_HOME="$HOME/.codex-lovspor"') < launch
    assert command.index('CODEX_SECONDARY_HOME="$HOME/.codex-lovspor-secondary"') < launch
    assert command.index('[ -f "$CODEX_PRIMARY_HOME/auth.json" ] || {') < launch
    assert '  --primary-home "$CODEX_PRIMARY_HOME" \\' in command
    assert '  ${secondary_args[@]+"${secondary_args[@]}"} \\' in command


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
    """Both lanes normalize through the one script whose contract
    tests/unit/test_normalize_agent_tests.py pins: format, safe fixes, and an
    explicit `# noqa` for what ruff cannot fix (issues #232, #256). An inline
    `ruff check --fix` here would bring back the hard-fail on a RUF001 that
    ended a round with a correct test in it."""
    steps = _steps(workflow_name, job_name)
    names = [step.get("name") for step in steps]
    normalize = _named_step(steps, "Normalize and lint Codex output")

    assert names.index("Scope guard") < names.index(normalize["name"])
    assert names.index(normalize["name"]) < names.index("Run tests on Codex additions")
    assert normalize["if"] == condition
    assert normalize["run"].strip() == "scripts/ci/normalize_agent_tests.sh tests/"


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
    # A missing tool host (#448) is reported once, by the verifier's classifier.
    assert escalation["if"] == (
        "(failure() || cancelled()) && steps.cycle.outputs.pr != '' && "
        "steps.tool.outcome != 'failure'"
    )
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


_UNMEASURED_BLOCK = "BLOCKED — mutants got no verdict, tests cannot fix an unmeasured surface"


def _decide_outputs(tmp_path: Path, reason: str, passed: bool) -> dict[str, str]:
    """Run the real decision step against a result carrying `reason`."""
    run = _named_step(
        _steps("mutation-remediation.yml", "remediate"),
        "Validate result as data; decide whether remediation applies",
    )["run"]
    run = run.replace("${{ runner.temp }}", str(tmp_path))
    run = run.replace("${{ steps.artifact.outcome }}", "success")
    (tmp_path / "mutation").mkdir()
    result = {
        "schema_version": 1,
        "commit": "a" * 40,
        "gate": {"passed": passed, "reason": reason},
        "survivors": [],
    }
    (tmp_path / "mutation" / "mutation-result.json").write_text(json.dumps(result))
    outputs = tmp_path / "outputs"
    env = {"PATH": os.environ["PATH"], "HEAD_SHA": "a" * 40, "GITHUB_OUTPUT": str(outputs)}
    subprocess.run(["bash", "-eu", "-o", "pipefail", "-c", run], env=env, check=True)
    lines = outputs.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines)


def test_remediation_routes_unmeasured_mutants_to_a_human_not_codex(tmp_path: Path) -> None:
    """Issue #283: a signal-killed mutant got no verdict — tests cannot kill
    what was never measured, so Codex must not get the round."""
    outputs = _decide_outputs(tmp_path, "unmeasured_mutants", passed=False)

    assert outputs == {"run": "false", "signal_killed": "true"}


def test_surviving_mutants_still_reach_codex(tmp_path: Path) -> None:
    outputs = _decide_outputs(tmp_path, "surviving_mutants", passed=False)

    assert outputs == {"run": "true"}


def test_the_unmeasured_block_labels_the_pr_before_any_codex_step() -> None:
    steps = _steps("mutation-remediation.yml", "remediate")
    blocked = _named_step(steps, _UNMEASURED_BLOCK)
    names = [step.get("name") for step in steps]

    assert blocked["if"] == "steps.gate.outputs.signal_killed == 'true'"
    assert '--add-label "needs-human:mutation"' in blocked["run"]
    assert ".mutants.unmeasured" in blocked["run"]
    assert names.index(blocked["name"]) < names.index("Resolve PR number and remediation cycle")


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


def _blocked_phrase() -> str:
    script = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "codex_convergence.py"
    spec = importlib.util.spec_from_file_location("codex_convergence_phrase", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    phrase: str = module.BLOCKED_PHRASE
    return phrase


def test_the_verdict_and_the_round_counter_agree_on_the_blocked_phrase() -> None:
    """The round count is the number of times this phrase appears in the pipeline
    sticky comment. The verdict script owns the phrase; no job of the workflow
    may write a competing one, or the count drifts (issue #248). Every job,
    not only `codex-tests`: `ready` writes into the same comment (#255)."""
    phrase = _blocked_phrase()

    for job_name, job in _workflow("pr-pipeline.yml")["jobs"].items():
        for step in job.get("steps", []):
            assert phrase not in str(step.get("run", "")), f"{job_name}: {step.get('name')}"


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
    GitHub's 6 h default kill. The ceiling sits above the agent step's own
    (issue #382: that step budgets an hour of queueing for the box-wide lock
    plus the agent round), so the step reports before the job is cancelled."""
    job = _workflow(workflow_name)["jobs"][job_name]

    assert job["timeout-minutes"] == 120


@pytest.mark.parametrize(
    ("workflow_name", "job_name", "step_name"),
    [
        ("pr-pipeline.yml", "codex-author", "Codex — independent PR test author"),
        ("mutation-remediation.yml", "remediate", "Codex — mutation remediation (tests only)"),
    ],
)
def test_the_box_lock_wait_fits_inside_the_agent_step(
    workflow_name: str, job_name: str, step_name: str
) -> None:
    """Issue #382: five repositories shared /home/runner/.mikrus-agent.lock, so
    a lane routinely queued behind a FOREIGN agent round (~25-40 min) while
    this repo's runner read idle. The lock moved to the host (#445) and kept
    its budget. The lock is fd 9, which no later step
    inherits, so the queue and the round have to share one step: a wait the
    step ceiling cannot outlive is dead code, and a wait that eats the ceiling
    leaves the round nothing. -w 1200 failed #374 three times over."""
    step = _named_step(_steps(workflow_name, job_name), step_name)
    wait = re.search(r"scripts/ci/fd_lock\.py --wait (\d+) 9", step["run"])

    assert wait, f"{workflow_name}: the agent step must take the box lock with a bounded wait"
    assert int(wait.group(1)) == 3600
    assert step["timeout-minutes"] * 60 - int(wait.group(1)) >= 45 * 60, (
        "a full lock wait must still leave the agent round its 45 minutes"
    )
    assert "another agent job of this runner's user" in step["run"], (
        "the timeout message names the shared holder, not lovspor (#382)"
    )
    assert "this is runner contention, not a fault in this PR" in step["run"]


@pytest.mark.parametrize(
    ("workflow_name", "job_name", "step_name"),
    [
        ("pr-pipeline.yml", "codex-author", "Codex — independent PR test author"),
        ("mutation-remediation.yml", "remediate", "Codex — mutation remediation (tests only)"),
    ],
)
def test_every_agent_provider_runs_under_the_same_box_wide_lock(
    workflow_name: str, job_name: str, step_name: str
) -> None:
    """Issue #382: serialization is host-wide only when both lanes open the
    shared path, acquire its fd, and do so before the Codex/Claude failover can
    launch either provider. Since #445 the path is the runner user's home and
    the acquire is the repo's own helper: stock macOS has no `flock`."""
    command = _named_step(_steps(workflow_name, job_name), step_name)["run"]

    lock_path = 'agent_lock="$HOME/.agent-box.lock"'
    lock_open = 'exec 9>"$agent_lock"'
    lock_acquire = "python3 scripts/ci/fd_lock.py --wait 3600 9"
    provider_launch = "python3 scripts/ci/codex_account_failover.py"

    assert command.count(lock_path) == 1
    assert command.index(lock_path) < command.index(lock_open)
    assert command.count(lock_open) == 1
    assert command.count(lock_acquire) == 1
    assert command.index(lock_open) < command.index(lock_acquire) < command.index(provider_launch)


@pytest.mark.parametrize("job_name", ["remediate", "remediate-verify"])
def test_every_mutation_sticky_round_names_the_head_it_describes(job_name: str) -> None:
    """Issue #477: the sticky helper labels each round with this head and says
    STALE when the PR has left it; both remediation jobs hand it the head."""
    job = _workflow("mutation-remediation.yml")["jobs"][job_name]

    assert job["env"]["STICKY_HEAD_SHA"] == "${{ github.event.workflow_run.head_sha }}"
    assert job["env"]["STICKY_HEAD_SHA"] == job["env"]["HEAD_SHA"]
    assert job["permissions"]["pull-requests"] == "write"


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


def _triggers(workflow_name: str) -> dict[str, Any]:
    # PyYAML resolves the bare `on:` key to the boolean True.
    workflow = _workflow(workflow_name)
    triggers: dict[str, Any] = workflow.get("on", workflow.get(True))
    return triggers


_DEFAULT_PR_TYPES = {"opened", "synchronize", "reopened"}


@pytest.mark.parametrize("workflow_name", ["test.yml", "pr-pipeline.yml"])
def test_required_checks_run_on_pull_requests_to_any_base(workflow_name: str) -> None:
    """Issue #572. The main ruleset requires `test (3.x)`, `fast-ci` and
    `mutation`. A stacked PR's base is a feature branch; when that base merges,
    GitHub retargets the PR to main and fires only `edited`, which neither
    workflow listens to. A base-branch filter therefore meant Test never ran on
    the head SHA and the PR sat BLOCKED until closed and reopened. With no
    filter, the checks run on the head SHA from the start, and check runs stay
    attached to that SHA across the retarget."""
    pull_request = _triggers(workflow_name)["pull_request"] or {}

    assert "branches" not in pull_request
    assert "branches-ignore" not in pull_request
    assert set(pull_request.get("types", _DEFAULT_PR_TYPES)) >= _DEFAULT_PR_TYPES


def test_test_workflow_still_runs_on_pushes_to_main_only() -> None:
    """Dropping the pull_request filter must not widen `push`: a push to a
    feature branch with an open PR would then run the matrix twice."""
    assert _triggers("test.yml")["push"] == {"branches": ["main"]}


def test_test_workflow_never_assumes_the_base_is_main() -> None:
    """A stacked PR's base is a feature branch, so a job condition or a
    concurrency group keyed on the base would skip or collide exactly there."""
    workflow = _workflow("test.yml")
    conditions = [
        workflow.get("concurrency"),
        *(job.get("if") for job in workflow["jobs"].values()),
    ]

    assert not any("base" in str(condition) for condition in conditions if condition)


def _cancel_in_progress_workflows() -> list[str]:
    return sorted(
        path.name
        for path in _WORKFLOWS.glob("*.yml")
        if (_workflow(path.name).get("concurrency") or {}).get("cancel-in-progress") is True
    )


def test_some_workflow_cancels_stale_runs() -> None:
    assert "pr-pipeline.yml" in _cancel_in_progress_workflows()


@pytest.mark.parametrize("workflow_name", _cancel_in_progress_workflows())
def test_no_job_in_a_cancellable_workflow_outlives_its_cancellation(workflow_name: str) -> None:
    """Issue #101. On cancellation GitHub re-evaluates the `if` of every job
    still running and keeps the ones that evaluate true — `always()` does. The
    mutation job carried it, so a push landing while mutation ran left the
    superseded run holding the `pr-<PR#>` group and the new run pending with no
    jobs. `!cancelled()` survives a skipped or failed need the same way and
    still yields to the cancellation."""
    jobs = _workflow(workflow_name)["jobs"]
    outliving = [name for name, job in jobs.items() if "always()" in str(job.get("if", ""))]

    assert outliving == []


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

            assert step["timeout-minutes"] == 105
            assert job["timeout-minutes"] == 120
            assert step["timeout-minutes"] < job["timeout-minutes"], (
                f"{workflow_name}: the step ceiling must bite before the job's"
            )

    def test_a_failure_before_the_tests_still_escalates(self) -> None:
        """Issue #160: the lint step failed, the job went red, and the PR got
        no label and no comment because the escalation asked only about the
        pytest step."""
        steps = _steps("pr-pipeline.yml", "codex-tests")
        fallback = _named_step(steps, "Escalate — the pipeline failed before the tests ran")

        # The one carve-out (#493) is an author that never ran a step of its
        # own; `codex-tests-report` owns that report.
        assert fallback["if"].startswith("failure() && steps.verdict.outcome != 'failure' && ")
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

    def _report_step(self) -> dict[str, Any]:
        return _named_step(
            _steps("pr-pipeline.yml", "ready"),
            "Report READY TO MERGE where the blocked rounds were reported",
        )

    def test_a_green_run_reports_ready_where_the_blocked_rounds_were_reported(self) -> None:
        """Issue #255. The labels were retracted; the sticky comment was not.
        #250 reached READY with every check green and its `pipeline` comment
        still ending on a round-3 BLOCKED, so the durable text a reader opens
        contradicted the labels. READY is appended to that comment, after the
        labels go, with the run that proved it."""
        steps = _steps("pr-pipeline.yml", "ready")
        names = [step.get("name") for step in steps]
        command = self._report_step()["run"]

        assert names.index(self._ready_step()["name"]) < names.index(self._report_step()["name"])
        assert "READY TO MERGE" in command
        assert "scripts/ci/pr_sticky_comment.sh pipeline" in command
        assert (
            "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
            in command
        )

    def test_the_ready_round_is_appended_never_rewritten(self) -> None:
        """The convergence counter reads the blocked rounds out of this
        comment (`count_blocking_rounds`). A READY that replaced or deleted the
        history would reset every open PR's round count to zero; the helper
        appends, and this step goes only through the helper."""
        command = self._report_step()["run"]

        assert "--method PATCH" not in command
        assert "--method DELETE" not in command
        assert "gh pr comment" not in command
        assert _blocked_phrase() not in command

    def test_a_pr_that_was_never_blocked_gets_no_comment(self) -> None:
        """A green PR with no escalation history has no sticky comment, and
        creating one mails the author on every green PR — the noise the
        sticky helper exists to stop. The step asks first, like the retraction."""
        command = self._report_step()["run"]

        assert 'contains("<!-- lovspor-sticky:pipeline -->")' in command
        assert command.index('contains("<!-- lovspor-sticky:pipeline -->")') < command.index(
            "pr_sticky_comment.sh"
        )
        assert command.index("exit 0") < command.index("pr_sticky_comment.sh")

    def test_the_report_is_allowed_to_edit_comments(self) -> None:
        """The helper PATCHes an issue comment; a job that silently lacked the
        scope would leave #255 in place while reporting success."""
        permissions = _workflow("pr-pipeline.yml")["jobs"]["ready"]["permissions"]

        assert permissions["pull-requests"] == "write"
        assert permissions["issues"] == "write"


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
            ("pr-pipeline.yml", "ready"),
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
                "pr-pipeline.yml",
                "ready",
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
            "not_run": "${{ steps.ran.outputs.not_run }}",
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
        sampler is bounded twice: a deadline in the loop itself (no `timeout`
        binary on stock macOS, #445), and a kill step that runs even when the
        agent step failed."""
        steps = self._author()["steps"]
        sampler = _named_step(steps, "Sample memory while the agent runs")
        stop = _named_step(steps, "Stop the memory sampler")

        assert _SAMPLER_DEADLINE in sampler["run"]
        assert _SAMPLER_LOOP in sampler["run"]
        assert stop["if"].startswith("always()")
        assert "kill " in stop["run"]


class TestRemediationMustTryToKillEverySurvivor:
    """Issue #455. On PR #453 remediation called 8 of 8 survivors non-killable
    and wrote no test; every one fell to a test-only commit (`fdbe4a7`). A
    non-killable verdict has to mean "provably equivalent, and here is why",
    never "not attempted"."""

    def _prompt(self) -> str:
        path = _WORKFLOWS.parent / "codex" / "mutation-remediation.md"
        return " ".join(path.read_text(encoding="utf-8").split())

    def test_every_survivor_gets_a_killing_test_attempt(self) -> None:
        prompt = self._prompt()

        assert "Attempt a killing test for every survivor" in prompt
        assert "Only edit tests for killable_by_correct_test." not in prompt

    def test_a_non_killable_verdict_carries_an_equivalence_argument(self) -> None:
        prompt = self._prompt()

        assert "only with a stated equivalence argument" in prompt
        assert "for every input that can reach it" in prompt

    def test_the_pr_453_excuses_are_named_as_killable(self) -> None:
        prompt = self._prompt()

        for excuse in ("timezone", "boundary", "stderr", "torn or malformed input file"):
            assert excuse in prompt, excuse

    def test_the_report_names_the_test_or_the_argument_per_survivor(self) -> None:
        prompt = self._prompt()

        assert "the test that kills it, or the equivalence argument" in prompt

    def test_hand_applying_a_mutant_leaves_production_code_untouched(self) -> None:
        prompt = self._prompt()

        assert "`git checkout -- src/`" in prompt
        assert "modify files under tests/ only" in prompt


class TestRemediationNeverMocksTheModuleUnderMutation:
    """Issue #427. On PR #422 remediation killed `run_sync` survivors by
    monkeypatching the orchestrator's own functions and asserting on the
    arguments they received (`f8d3b58`, `d34ff22`). Two of those mutants were
    equivalent: only the mock could tell them apart. A kill like that pins a
    call shape, not behaviour, so a refactor that keeps behaviour turns it red
    while the equivalent mutant goes unregistered."""

    def _prompt(self) -> str:
        path = _WORKFLOWS.parent / "codex" / "mutation-remediation.md"
        return " ".join(path.read_text(encoding="utf-8").split())

    def test_monkeypatching_the_mutated_module_is_forbidden(self) -> None:
        prompt = self._prompt()

        assert "never monkeypatch a function, class or constant of the module under mutation" in (
            prompt
        )

    def test_a_mutant_only_a_mock_can_kill_is_reported_as_equivalent(self) -> None:
        prompt = self._prompt()

        assert "a mutant only a mock can kill is likely_equivalent" in prompt

    def test_the_real_code_path_is_named_as_the_way_to_kill(self) -> None:
        prompt = self._prompt()

        assert "drive the real code" in prompt
        assert "pytest-httpx" in prompt


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

    def test_the_hosted_verifier_checks_out_an_allowed_remediation_cycle(self) -> None:
        """The gate belongs to the agent lane. The hosted lane must consume its
        output before it can apply the patch or run any repository command."""
        steps = _steps("mutation-remediation.yml", "remediate-verify")
        checkout = next(
            step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")
        )

        assert checkout["if"] == "needs.remediate.outputs.run == 'true'"

    def test_the_agent_work_travels_as_a_patch(self) -> None:
        artifact = "remediation-tests-${{ github.event.workflow_run.head_sha }}"
        upload = _named_step(self._agent()["steps"], "Upload the agent's tests")
        download = _named_step(
            _steps("mutation-remediation.yml", "remediate-verify"), "Download the agent's tests"
        )
        apply_step = _named_step(
            _steps("mutation-remediation.yml", "remediate-verify"), "Apply the agent's tests"
        )

        assert self._agent()["outputs"] == {
            "run": "${{ steps.cycle.outputs.run }}",
            "pr": "${{ steps.cycle.outputs.pr }}",
            "count": "${{ steps.cycle.outputs.count }}",
            "author": "${{ steps.author.outputs.author }}",
            "before_sha": "${{ steps.base.outputs.before_sha }}",
            "patch": "${{ steps.patch.outputs.patch }}",
            "blocked": "${{ steps.blocked.outputs.message }}",
            "not_run": "${{ steps.ran.outputs.not_run }}",
        }
        assert upload["with"]["name"] == artifact
        assert download["with"]["name"] == artifact
        assert upload["if"] == "steps.patch.outputs.patch == 'true'"
        assert download["if"] == "needs.remediate.outputs.patch == 'true'"
        assert apply_step["if"] == "needs.remediate.outputs.patch == 'true'"
        assert "git apply --index" in apply_step["run"]

    def test_the_scope_guard_runs_on_both_lanes_against_the_recorded_base(self) -> None:
        for job_name in ("remediate", "remediate-verify"):
            guard = _named_step(_steps("mutation-remediation.yml", job_name), "Scope guard")
            assert guard["run"] == 'scripts/ci/assert_codex_scope.sh "$BEFORE_SHA"'

        verifier = _workflow("mutation-remediation.yml")["jobs"]["remediate-verify"]
        assert verifier["env"]["BEFORE_SHA"] == "${{ needs.remediate.outputs.before_sha }}"

    def test_the_verifier_refuses_to_be_green_when_the_agent_lane_died(self) -> None:
        """The verifier is the external verdict path when the small box dies,
        so it must run and fail before claiming that remediation was tested."""
        job = _workflow("mutation-remediation.yml")["jobs"]["remediate-verify"]
        steps = job["steps"]
        names = [step.get("name") for step in steps]
        guard = _named_step(steps, "The remediation lane did not finish")

        assert "!cancelled()" in job["if"]
        assert guard["if"] == "needs.remediate.result != 'success'"
        assert "exit 1" in guard["run"]
        assert names.index(guard["name"]) < names.index("Run tests on Codex additions")

    def test_the_memory_sampler_cannot_outlive_the_agent_lane(self) -> None:
        steps = self._agent()["steps"]
        sampler = _named_step(steps, "Sample memory while the agent runs")
        stop = _named_step(steps, "Stop the memory sampler")

        assert _SAMPLER_DEADLINE in sampler["run"]
        assert _SAMPLER_LOOP in sampler["run"]
        assert stop["if"].startswith("always()")
        assert "kill " in stop["run"]

    def test_the_verifier_only_runs_for_a_cycle_the_gate_allowed(self) -> None:
        """The gate, the cycle count and both BLOCKED paths stay on the agent
        lane, so the verifier must not start a round the gate refused. The job
        also runs when the agent lane died (#254) — only to report it, so every
        step of the round itself still waits on the gate's `run`."""
        steps = _steps("mutation-remediation.yml", "remediate-verify")
        round_steps = [
            "Sync dependencies",
            "Scope guard",
            "Normalize and lint Codex output",
            "Run tests on Codex additions",
            "Commit and push, or report BLOCKED",
        ]

        for name in round_steps:
            assert _named_step(steps, name)["if"] == "needs.remediate.outputs.run == 'true'"
        assert self._agent()["outputs"]["run"] == "${{ steps.cycle.outputs.run }}"


class TestADeadMachineIsNotAVerdictOnTheDiff:
    """Issue #272, option 4. Both deaths on PR #269 were reported as
    `codex-tests BLOCKED before the tests ran`, which reads as a claim about the
    pull request. The tests had in fact run; the machine stopped. The signature
    is mechanical — the job is `failure` while its own step is still
    `in_progress` — so the message can be mechanical too."""

    JOB = "codex-tests-report"

    def _steps(self) -> list[dict[str, Any]]:
        return _steps("pr-pipeline.yml", self.JOB)

    def test_the_reporter_classifies_before_it_speaks(self) -> None:
        names = [step.get("name") for step in self._steps()]
        classify = _named_step(self._steps(), "Classify the lane failure")

        assert "scripts/ci/classify_lane_failure.py" in classify["run"]
        assert "--lane codex-author" in classify["run"]
        assert "--lane codex-tests" in classify["run"]
        assert classify["id"] == "classify"
        assert names.index("Classify the lane failure") < names.index(
            "Report a codex-tests job that never reached its own escalation"
        )

    def test_a_classifier_failure_cannot_silence_the_external_reporter(self) -> None:
        """The reporter exists because the agent lane can fail without speaking.
        A transient jobs-API or classifier failure must not make this hosted
        fallback skip its own reporting step for the same reason."""
        report = _named_step(
            self._steps(), "Report a codex-tests job that never reached its own escalation"
        )

        assert report.get("if") == "always()"

    def test_the_classifier_is_reachable_from_the_sparse_checkout(self) -> None:
        """The reporter checks out `scripts/ci` only. A classifier outside that
        path would make this job die on a missing file — the silent BLOCKED of
        issue #193, reintroduced by the fix for #272."""
        checkout = _named_step(self._steps(), "Check out the escalation helper")

        assert checkout["with"]["sparse-checkout"] == "scripts/ci"
        assert (_WORKFLOWS.parents[1] / "scripts" / "ci" / "classify_lane_failure.py").exists()

    def test_an_infrastructure_death_is_reported_as_the_machine_not_the_diff(self) -> None:
        report = _named_step(
            self._steps(), "Report a codex-tests job that never reached its own escalation"
        )["run"]

        assert "steps.classify.outputs.kind" in report
        assert "INFRASTRUCTURE failure — the machine, not the diff" in report
        assert "steps.classify.outputs.job" in report
        assert "steps.classify.outputs.step" in report
        # The operator's next two moves, in the comment they are already reading.
        assert "gh api repos/${{ github.repository }}/actions/runners" in report
        assert "gh run rerun ${{ github.run_id }} --failed" in report

    def test_an_ordinary_failure_keeps_the_old_message(self) -> None:
        """Only the death-with-the-runner case changes wording. A lane that
        failed on its own still reports as a pipeline failure."""
        report = _named_step(
            self._steps(), "Report a codex-tests job that never reached its own escalation"
        )["run"]

        assert "codex-tests BLOCKED and reported nothing itself" in report
        assert 'gh pr edit "$PR" --add-label "needs-human:pipeline"' in report


class TestARunnerSetUpFailureIsNamedAsTheRunner:
    """Issue #493. Three times in a day `codex-author` failed in GitHub's own
    `Set up job` — the Mac runner timed out downloading a pinned action — and
    the PR was told `codex-tests BLOCKED before the tests ran`, a pipeline
    failure, with no word that no step had run and that a rerun clears it."""

    REPORT = "Report a codex-tests job that never reached its own escalation"
    FALLBACK = "Escalate — the pipeline failed before the tests ran"

    def _report(self, tmp_path: Path, kind: str, step: str) -> str:
        run = _named_step(_steps("pr-pipeline.yml", "codex-tests-report"), self.REPORT)["run"]
        values = {
            "steps.classify.outputs.kind": kind,
            "steps.classify.outputs.job": "codex-author",
            "steps.classify.outputs.step": step,
            "github.repository": "o/r",
            "github.run_id": "1",
            "github.server_url": "https://github.com",
        }
        env = _escalation_sandbox(tmp_path) | {
            "PR": "482",
            "SIGNATURE": "",
            "BLOCKED_LABELS": "needs-implementation-fix needs-human:mutation needs-human:pipeline",
        }
        _run_step(_render(run, values), tmp_path, env)
        return (tmp_path / "body").read_text(encoding="utf-8")

    def test_the_comment_names_the_runner_and_the_rerun(self, tmp_path: Path) -> None:
        body = self._report(tmp_path, "runner_setup", "Set up job")

        assert body.startswith("codex-tests BLOCKED by a RUNNER SET-UP failure")
        assert "not the diff and not Codex" in body
        assert "`codex-author` failed in `Set up job`" in body
        assert "no Codex round started" in body
        assert "`gh run rerun 1 --failed`" in body
        assert "#493" in body
        assert _RUN_URL in body
        assert "pr edit 482 --add-label needs-human:pipeline" in (tmp_path / "gh-calls").read_text()

    def test_an_ordinary_lane_failure_keeps_its_wording(self, tmp_path: Path) -> None:
        body = self._report(tmp_path, "in_job", "")

        assert body.startswith("codex-tests BLOCKED and reported nothing itself")
        assert "RUNNER SET-UP" not in body

    def test_the_verifier_leaves_an_author_that_never_ran_a_step_to_the_reporter(self) -> None:
        """`codex-tests` cannot read the jobs API, so it cannot tell a set-up
        death from any other; its generic round, written first, also made the
        reporter stand down on the label. An author that failed with no output
        at all never ran its first step: the hosted reporter, which classifies,
        owns that report — and its `if` fires on exactly that failure."""
        fallback = _named_step(_steps("pr-pipeline.yml", "codex-tests"), self.FALLBACK)
        reporter = _workflow("pr-pipeline.yml")["jobs"]["codex-tests-report"]

        assert fallback["if"] == (
            "failure() && steps.verdict.outcome != 'failure' && "
            "!(needs.codex-author.result == 'failure' && needs.codex-author.outputs.skip == '') "
            "&& needs.codex-author.outputs.not_run == ''"
        )
        assert "needs.codex-author.result == 'failure'" in reporter["if"]

    def test_the_skip_output_is_written_by_the_authors_first_own_step(self) -> None:
        """The proxy above holds only while `skip` is set unconditionally by the
        step straight after the checkout: an empty value then means the author
        never got that far."""
        steps = _steps("pr-pipeline.yml", "codex-author")
        antiloop = steps[1]

        assert antiloop["id"] == "antiloop"
        assert "if" not in antiloop
        assert 'echo "skip=true" >> "$GITHUB_OUTPUT"' in antiloop["run"]
        assert 'echo "skip=false" >> "$GITHUB_OUTPUT"' in antiloop["run"]
        author = _workflow("pr-pipeline.yml")["jobs"]["codex-author"]
        assert author["outputs"]["skip"] == "${{ steps.antiloop.outputs.skip }}"


class TestARejectedCredentialIsAnOperatorAction:
    """Issue #270, item 2. From 2026-09-10 08:55 UTC every `codex-tests` run
    died in `actions/checkout` on `fatal: could not read Username for
    'https://github.com': terminal prompts disabled` — the push token had
    expired. The pipeline called it a pipeline failure, so the first diagnosis
    chased the runner's clone, and a rerun reproduced it byte for byte. A
    rejected credential is an operator action, so the comment names it."""

    JOB = "codex-tests-report"
    REPORT = "Report a codex-tests job that never reached its own escalation"

    def _steps(self) -> list[dict[str, Any]]:
        return _steps("pr-pipeline.yml", self.JOB)

    def _report(self) -> dict[str, Any]:
        return _named_step(self._steps(), self.REPORT)

    def test_the_classifier_reads_the_failed_lane_jobs_log(self) -> None:
        """The jobs payload says a step failed, never why; the refusal is only
        in the job's log."""
        command = _named_step(self._steps(), "Classify the lane failure")["run"]

        assert "/actions/jobs/" in command
        assert "/logs" in command
        assert '--log "$RUNNER_TEMP/lane.log"' in command
        # A failed download leaves an empty log, which is no evidence rather
        # than a dead classifier (#193 one level up).
        assert ': > "$RUNNER_TEMP/lane.log"' in command
        assert command.index(': > "$RUNNER_TEMP/lane.log"') < command.index("/logs")

    def test_the_reporter_may_read_job_logs(self) -> None:
        assert self._job_permissions()["actions"] == "read"

    def _job_permissions(self) -> dict[str, str]:
        permissions: dict[str, str] = _workflow("pr-pipeline.yml")["jobs"][self.JOB]["permissions"]
        return permissions

    def test_a_rejected_credential_is_reported_as_one(self) -> None:
        command = self._report()["run"]

        assert '"${{ steps.classify.outputs.kind }}" = "credential"' in command
        assert "REJECTED CREDENTIAL" in command
        assert "not the diff" in command
        assert self._report()["env"]["SIGNATURE"] == "${{ steps.classify.outputs.signature }}"
        assert "$SIGNATURE" in command

    def test_the_comment_names_the_secret_the_failed_checkout_used(self) -> None:
        """The secret is named from the workflow itself: `codex-tests` checks
        out with the push token, so that is the one the comment tells the
        operator to renew."""
        checkout = _steps("pr-pipeline.yml", "codex-tests")[0]
        secret = checkout["with"]["token"].removeprefix("${{ secrets.").removesuffix(" }}")
        command = self._report()["run"]

        assert secret == "LOVSPOR_CI_PUSH_TOKEN"
        assert f'codex-tests) secret="{secret}"' in command
        assert "gh secret set $secret --repo ${{ github.repository }}" in command

    def test_the_credential_still_blocks_on_the_retractable_pipeline_label(self) -> None:
        """Same label as every other lane failure, so `ready` retracts it once
        the renewed token lets a run through."""
        command = self._report()["run"]

        assert command.count("--add-label") == 1
        assert command.index('--add-label "needs-human:pipeline"') < command.index("credential")


class TestEveryExpressionResolvesInItsOwnJob:
    """Both defects the independent author caught while the agent lanes were
    being split were the same mistake: a step moved to another job kept an
    expression that only resolved in the job it came from.

    `remediate-verify` inherited `if: steps.gate.outputs.run == 'true'` on its
    checkout. `steps.gate` lives on the agent lane, so on the hosted lane the
    expression evaluated to empty — falsy — and the checkout would never have
    run, leaving every later step against an empty workspace. GitHub does not
    error on an unresolvable `steps.<id>`; it silently yields nothing, which
    reads as false in a condition and as an empty string in a message.

    Contract tests that check step names and ordering do not see this. This one
    is mechanical: every `steps.<id>` must name a step in the same job, and every
    `needs.<job>` must be declared in that job's `needs`."""

    @staticmethod
    def _strings(node: Any) -> Iterator[str]:
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for value in node.values():
                yield from TestEveryExpressionResolvesInItsOwnJob._strings(value)
        elif isinstance(node, list):
            for value in node:
                yield from TestEveryExpressionResolvesInItsOwnJob._strings(value)

    @pytest.mark.parametrize("workflow_name", sorted(p.name for p in _WORKFLOWS.glob("*.yml")))
    def test_step_references_name_a_step_of_the_same_job(self, workflow_name: str) -> None:
        for job_name, job in _workflow(workflow_name)["jobs"].items():
            ids = {step["id"] for step in job.get("steps", []) if step.get("id")}
            for text in self._strings(job):
                for ref in re.findall(r"\bsteps\.([A-Za-z0-9_-]+)\.", text):
                    assert ref in ids, (
                        f"{workflow_name}:{job_name} refers to steps.{ref}, which is not a "
                        f"step of that job — the expression resolves to empty, not to an error"
                    )

    @pytest.mark.parametrize("workflow_name", sorted(p.name for p in _WORKFLOWS.glob("*.yml")))
    def test_needs_references_are_declared_dependencies(self, workflow_name: str) -> None:
        for job_name, job in _workflow(workflow_name)["jobs"].items():
            declared = job.get("needs") or []
            declared = [declared] if isinstance(declared, str) else declared
            for text in self._strings(job):
                for ref in re.findall(r"\bneeds\.([A-Za-z0-9_-]+)\.", text):
                    assert ref in declared, (
                        f"{workflow_name}:{job_name} refers to needs.{ref} without declaring it "
                        f"in `needs` — the expression resolves to empty, not to an error"
                    )


def test_dependabot_prs_skip_the_codex_lanes_and_still_reach_the_mutation_gate() -> None:
    """Issue #361: a dependabot PR carries no repository secrets, so the Codex
    lanes cannot even check out (`Input required and not supplied: token`) and
    every dependency bump landed in `needs-human:pipeline`. The lanes are
    skipped for that actor, and the mutation gate — which still runs fast-ci
    and the Test matrix behind it — accepts the skip from that actor only."""
    jobs = _workflow("pr-pipeline.yml")["jobs"]
    same_repo_non_dependabot = (
        "github.event.pull_request.head.repo.full_name == github.repository "
        "&& github.actor != 'dependabot[bot]'"
    )

    assert jobs["codex-author"]["if"] == same_repo_non_dependabot
    assert jobs["codex-tests"]["if"] == f"${{{{ !cancelled() && {same_repo_non_dependabot} }}}}"
    mutation_condition = " ".join(jobs["mutation"]["if"].split())
    assert mutation_condition == (
        "!cancelled() && needs.fast-ci.result == 'success' && (needs.codex-tests.result == "
        "'success' || (needs.codex-tests.result == 'skipped' && github.actor == "
        "'dependabot[bot]')) && needs.codex-tests.outputs.pushed != 'true'"
    )


_REPO = Path(__file__).resolve().parents[2]
_DECIDE = "Validate result as data; decide whether remediation applies"
_UNMEASURED = "BLOCKED — mutation did not run, nothing was measured"
_RUN_URL = "https://github.com/o/r/actions/runs/1"
_SHA = "a" * 40
_SYSTEM_PATH = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"


def _render(script: str, values: dict[str, str]) -> str:
    """Substitute the step's ${{ }} expressions; an unknown one fails the test."""
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: values[m.group(1)], script)


def _mutation_artifact(tmp_path: Path, raw: str, tool_exit_code: int) -> Path:
    folder = tmp_path / "mutation"
    folder.mkdir()
    (tmp_path / "raw.log").write_text(raw, encoding="utf-8")
    args = ["--commit", _SHA, "--raw", str(tmp_path / "raw.log")]
    args += ["--tool-exit-code", str(tool_exit_code)]
    args += ["--out", str(folder / "mutation-result.json")]
    script = _REPO / "scripts" / "ci" / "mutation_to_json.py"
    subprocess.run([sys.executable, str(script), *args], check=True, capture_output=True)
    return folder / "mutation-result.json"


def _run_step(script: str, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", script],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    )


def _decide(tmp_path: Path, raw: str, tool_exit_code: int) -> dict[str, str]:
    _mutation_artifact(tmp_path, raw, tool_exit_code)
    step = _named_step(_steps("mutation-remediation.yml", "remediate"), _DECIDE)
    values = {"runner.temp": str(tmp_path), "steps.artifact.outcome": "success"}
    output = tmp_path / "github-output"
    env = {"PATH": _SYSTEM_PATH, "HEAD_SHA": _SHA, "GITHUB_OUTPUT": str(output)}
    _run_step(_render(step["run"], values), tmp_path, env)
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


def _escalation_sandbox(tmp_path: Path) -> dict[str, str]:
    """scripts/ci as the helper checkout holds it, with GitHub stubbed at the CLI."""
    ci = tmp_path / "scripts" / "ci"
    ci.mkdir(parents=True)
    (ci / "mutation_gate.py").write_text(
        (_REPO / "scripts" / "ci" / "mutation_gate.py").read_text(encoding="utf-8")
    )
    sticky = ci / "pr_sticky_comment.sh"
    sticky.write_text(
        f'#!/bin/sh\necho "$@" > "{tmp_path}/sticky-args"\ncp "$3" "{tmp_path}/body"\n'
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        f'#!/bin/sh\necho "$@" >> "{tmp_path}/gh-calls"\n[ "$2" = list ] && echo 309\nexit 0\n'
    )
    for executable in (sticky, gh):
        executable.chmod(0o755)
    return {
        "PATH": f"{bin_dir}:{_SYSTEM_PATH}",
        "HEAD_SHA": _SHA,
        "HEAD_BRANCH": "fix/x",
        "RUNNER_TEMP": str(tmp_path),
    }


class TestUnmeasuredRunSkipsRemediation:
    """Issue #311: a gate that failed before any mutant was measured has no
    survivors for Codex to classify, and must reach a human saying why."""

    @pytest.mark.parametrize(
        ("raw", "exit_code"),
        [
            ("Failed to run clean test\nerror: mutmut run failed (exit 1)\n", 3),
            (
                "Tests failed when run without mutations\n"
                "1/1  🎉 1 🫥 0  ⏰ 0  🤔 0  🙁 0  🔇 0  🧙 0\n",
                0,
            ),
        ],
    )
    def test_a_failed_tool_or_baseline_goes_to_a_human(
        self, tmp_path: Path, raw: str, exit_code: int
    ) -> None:
        assert _decide(tmp_path, raw, exit_code) == {"run": "false", "unmeasured": "true"}

    def test_surviving_mutants_still_go_to_codex(self, tmp_path: Path) -> None:
        raw = "2/2  🎉 1 🫥 0  ⏰ 0  🤔 0  🙁 1  🔇 0  🧙 0\n"

        assert _decide(tmp_path, raw, 2) == {"run": "true"}

    def test_the_escalation_runs_before_any_checkout_or_codex_cycle(self) -> None:
        steps = _steps("mutation-remediation.yml", "remediate")
        names = [step.get("name") for step in steps]
        blocked = _named_step(steps, _UNMEASURED)

        assert blocked["if"] == "steps.gate.outputs.unmeasured == 'true'"
        assert names.index(_UNMEASURED) < names.index("Resolve PR number and remediation cycle")
        assert names.index(_UNMEASURED) < names.index("Codex — mutation remediation (tests only)")

    def test_the_comment_names_the_failure_not_survivor_classification(
        self, tmp_path: Path
    ) -> None:
        raw = "FAILED tests/unit/test_x.py::test_y - FailedHealthCheck\nFailed to run clean test\n"
        _mutation_artifact(tmp_path, raw, 3)
        env = _escalation_sandbox(tmp_path)
        step = _named_step(_steps("mutation-remediation.yml", "remediate"), _UNMEASURED)
        values = {"runner.temp": str(tmp_path), "github.event.workflow_run.html_url": _RUN_URL}

        _run_step(_render(step["run"], values), tmp_path, env)

        body = (tmp_path / "body").read_text()
        assert "the clean baseline test run failed" in body
        assert "FAILED tests/unit/test_x.py::test_y - FailedHealthCheck" in body
        assert f"mutation-result-{_SHA}" in body
        assert _RUN_URL in body
        assert "non-killable" not in body
        assert "--add-label needs-human:mutation" in (tmp_path / "gh-calls").read_text()
        assert (tmp_path / "sticky-args").read_text().split()[:2] == ["mutation", "309"]


_BLOCKED_BY = "Say what blocked the gate, should remediation change nothing"
_NO_CHANGE = "Commit and push, or report BLOCKED"


class TestTheNoChangeEscalationNamesWhatBlocked:
    """Issue #423: on PR #395 remediation changed nothing and the sticky said
    "survivors classified non-killable" over two timed-out mutants and zero
    survivors. The words now come from the artifact, read on the agent lane by
    the default-branch helper, and reach the verifier as a job output."""

    PR_395 = "132/33020  🎉 130 🫥 0  ⏰ 2  🤔 0  🙁 0  🔇 0  🧙 0\n"

    def test_the_agent_lane_reads_the_artifact_before_the_pr_checkout(self) -> None:
        steps = _steps("mutation-remediation.yml", "remediate")
        names = [step.get("name") for step in steps]
        blocked = _named_step(steps, _BLOCKED_BY)
        pr_checkout = next(
            i for i, s in enumerate(steps) if s.get("with", {}).get("fetch-depth") == 0
        )

        assert blocked["if"] == "steps.gate.outputs.run == 'true'"
        assert names.index(_DECIDE) < names.index(_BLOCKED_BY) < pr_checkout

    def test_the_agent_lane_hands_the_timeout_wording_on(self, tmp_path: Path) -> None:
        _mutation_artifact(tmp_path, self.PR_395, 4)
        env = _escalation_sandbox(tmp_path) | {"GITHUB_OUTPUT": str(tmp_path / "out")}
        step = _named_step(_steps("mutation-remediation.yml", "remediate"), _BLOCKED_BY)

        _run_step(_render(step["run"], {"runner.temp": str(tmp_path)}), tmp_path, env)

        (line,) = (tmp_path / "out").read_text().splitlines()
        assert line.startswith("message=Mutation remediation made no safe test-only change.")
        assert "2 timed-out mutant(s) got no verdict" in line
        assert "non-killable" not in line

    def test_the_output_reaches_the_verifier(self) -> None:
        job = _workflow("mutation-remediation.yml")["jobs"]["remediate"]
        step = _named_step(_steps("mutation-remediation.yml", "remediate-verify"), _NO_CHANGE)

        assert job["outputs"]["blocked"] == "${{ steps.blocked.outputs.message }}"
        assert step["env"]["BLOCKED_BY"] == "${{ needs.remediate.outputs.blocked }}"
        assert "non-killable" not in step["run"]

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        identity = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        done = subprocess.run(
            ["git", *identity, *args], cwd=repo, check=True, capture_output=True, text=True
        )
        return done.stdout.strip()

    def _no_change_body(self, tmp_path: Path, blocked_by: str, not_run: str = "") -> str:
        """A clean tree at BEFORE_SHA: the branch where the agent changed nothing."""
        tmp_path.mkdir(exist_ok=True)
        env = _escalation_sandbox(tmp_path) | {"BLOCKED_BY": blocked_by, "NOT_RUN": not_run}
        for args in (("init", "-q"), ("add", "-A"), ("commit", "-q", "-m", "head")):
            self._git(tmp_path, *args)
        env |= {"BEFORE_SHA": self._git(tmp_path, "rev-parse", "HEAD")}
        step = _named_step(_steps("mutation-remediation.yml", "remediate-verify"), _NO_CHANGE)
        values = {
            "needs.remediate.outputs.pr": "309",
            "needs.remediate.outputs.count": "0",
            "needs.remediate.outputs.author || 'codex'": "codex",
        }

        _run_step(_render(step["run"], values), tmp_path, env)

        return (tmp_path / "body").read_text()

    def test_the_sticky_carries_the_agent_lane_wording(self, tmp_path: Path) -> None:
        body = self._no_change_body(tmp_path, "Blocked by: 2 timed-out mutant(s).")

        assert body.startswith("Blocked by: 2 timed-out mutant(s). Human review required.")
        assert f"mutation-result-{_SHA}" in body

    def test_a_lost_output_falls_back_without_claiming_survivors(self, tmp_path: Path) -> None:
        body = self._no_change_body(tmp_path, "")

        assert body.startswith("Mutation remediation made no safe test-only change.")
        assert "non-killable" not in body
        assert "survivors" not in body


_RAN = "Did the agent run a command"
_REMEDIATION_FIXTURES = Path(__file__).parent / "fixtures" / "remediation"


class TestARemediationThatNeverRanSaysSo:
    """Issue #472: on PR #469 (run 36670610064) the agent ran no command, the
    patch was empty, and the sticky said "12 survivor(s) remediation called
    non-killable". The agent lane now reads its own transcript and the
    verifier reports a remediation that did not run."""

    def _agent_lane(self, tmp_path: Path, transcript: str) -> str:
        """Run the step on the real transcript and the real artifact of that round."""
        (tmp_path / "mutation").mkdir(parents=True)
        shutil.copy(
            _REMEDIATION_FIXTURES / "mutation-result-a85fe67.json",
            tmp_path / "mutation" / "mutation-result.json",
        )
        shutil.copy(_REMEDIATION_FIXTURES / transcript, tmp_path / "remediation-agent.log")
        ci = tmp_path / "scripts" / "ci"
        ci.mkdir(parents=True)
        for name in ("mutation_gate.py", "remediation_transcript.py"):
            shutil.copy2(_REPO / "scripts" / "ci" / name, ci / name)
        env = {"PATH": _SYSTEM_PATH, "RUNNER_TEMP": str(tmp_path)}
        env["GITHUB_OUTPUT"] = str(tmp_path / "out")
        step = _named_step(_steps("mutation-remediation.yml", "remediate"), _RAN)
        _run_step(_render(step["run"], {"runner.temp": str(tmp_path)}), tmp_path, env)
        (line,) = (tmp_path / "out").read_text(encoding="utf-8").splitlines()
        return line.removeprefix("not_run=")

    def test_the_agent_lane_keeps_the_transcript_it_reads(self) -> None:
        steps = _steps("mutation-remediation.yml", "remediate")
        author = _named_step(steps, "Codex — mutation remediation (tests only)")
        names = [step.get("name") for step in steps]

        assert '| tee "$RUNNER_TEMP/remediation-agent.log"' in author["run"]
        assert "status=${PIPESTATUS[0]}" in author["run"]
        assert names.index(author["name"]) < names.index(_RAN)
        assert _named_step(steps, _RAN)["if"] == "steps.author.outputs.author == 'codex'"

    def test_the_pr_469_round_is_reported_as_not_run(self, tmp_path: Path) -> None:
        message = self._agent_lane(tmp_path, "codex-no-command-run-36670610064.log")

        assert message.startswith("Mutation remediation did not run")
        assert "codex-code-mode-host is missing" in message
        assert "Unclassified: 12 survived" in message

    def test_a_round_that_ran_commands_leaves_the_output_empty(self, tmp_path: Path) -> None:
        assert self._agent_lane(tmp_path, "codex-ran-commands-run-36531263464.log") == ""

    def test_the_output_reaches_the_verifier(self) -> None:
        job = _workflow("mutation-remediation.yml")["jobs"]["remediate"]
        step = _named_step(_steps("mutation-remediation.yml", "remediate-verify"), _NO_CHANGE)

        assert job["outputs"]["not_run"] == "${{ steps.ran.outputs.not_run }}"
        assert step["env"]["NOT_RUN"] == "${{ needs.remediate.outputs.not_run }}"

    def test_the_sticky_says_not_run_and_never_non_killable(self, tmp_path: Path) -> None:
        not_run = self._agent_lane(tmp_path / "lane", "codex-no-command-run-36670610064.log")
        blocked_by = "Blocked by: 12 survivor(s) remediation called non-killable."
        verifier = TestTheNoChangeEscalationNamesWhatBlocked()

        body = verifier._no_change_body(tmp_path / "verify", blocked_by, not_run)

        assert body.startswith(f"{not_run} Human review required.")
        assert "called non-killable" not in body
        assert f"mutation-result-{_SHA}" in body


_DEAD_LANE = "Escalate a remediation lane that died without reporting"


def _dead_lane_sandbox(tmp_path: Path, pr: str, view: str) -> dict[str, str]:
    """scripts/ci as the helper checkout holds it; `gh` answers `pr list` with
    `pr` and `pr view` with `view` (head SHA, then one label per line)."""
    env = _escalation_sandbox(tmp_path)
    (tmp_path / "pr-list").write_text(pr, encoding="utf-8")
    (tmp_path / "pr-view").write_text(view, encoding="utf-8")
    (tmp_path / "bin" / "gh").write_text(
        f'#!/bin/sh\necho "$@" >> "{tmp_path}/gh-calls"\n'
        f'[ "$2" = list ] && cat "{tmp_path}/pr-list"\n'
        f'[ "$2" = view ] && cat "{tmp_path}/pr-view"\nexit 0\n'
    )
    env |= {"GH_REPO": "o/r", "RUN_URL": _RUN_URL}
    return env | {"RESULT": "failure", "KIND": "", "STEP": ""}


class TestADeadRemediationLaneStillEscalates:
    """Issue #254. When the self-hosted box dies mid-`remediate`, its in-job
    escalation dies with it, and the job's outputs are never evaluated — so
    `run` and `pr` reach the hosted lane empty (inferred from runs 34043549922
    and #193's evidence, not reproduced). The hosted lane used to wait on
    `run == 'true'` and so stayed skipped: a red PR with no label, no comment."""

    def _job(self) -> dict[str, Any]:
        return _workflow("mutation-remediation.yml")["jobs"]["remediate-verify"]

    def _run(self, tmp_path: Path, env: dict[str, str]) -> str:
        step = _named_step(self._job()["steps"], _DEAD_LANE)
        _run_step(step["run"], tmp_path, env)
        calls = tmp_path / "gh-calls"
        return calls.read_text() if calls.exists() else ""

    def test_the_hosted_lane_runs_when_the_agent_lane_failed_or_hit_its_ceiling(self) -> None:
        # `!cancelled()`, never `always()`: a run a human cancelled is not a
        # blocked PR. A job-level timeout is `cancelled` on the job, not the run.
        assert self._job()["if"] == (
            "!cancelled() && (needs.remediate.outputs.run == 'true' || "
            "needs.remediate.result == 'failure' || needs.remediate.result == 'cancelled')"
        )

    def test_the_helper_is_checked_out_from_the_default_branch_when_no_round_ran(self) -> None:
        steps = self._job()["steps"]
        names = [step.get("name") for step in steps]
        helper = _named_step(steps, "Check out the escalation helper")

        assert helper["if"] == "needs.remediate.outputs.run != 'true'"
        assert helper["with"] == {
            "ref": "${{ github.event.repository.default_branch }}",
            "sparse-checkout": "scripts/ci",
            "persist-credentials": False,
        }
        assert names.index(helper["name"]) < names.index("The remediation lane did not finish")
        assert names.index(helper["name"]) < names.index(_DEAD_LANE)

    def test_the_escalation_fires_only_where_nothing_else_could_report(self) -> None:
        step = _named_step(self._job()["steps"], _DEAD_LANE)

        assert step["if"] == (
            "failure() && needs.remediate.outputs.pr == '' && "
            "github.event.workflow_run.conclusion == 'failure'"
        )
        assert step["env"] == {
            "RESULT": "${{ needs.remediate.result }}",
            "KIND": "${{ steps.classify.outputs.kind }}",
            "STEP": "${{ steps.classify.outputs.step }}",
            "RUN_URL": "${{ github.server_url }}/${{ github.repository }}"
            "/actions/runs/${{ github.run_id }}",
        }

    def test_the_classifier_is_best_effort_and_reads_the_agent_lane(self) -> None:
        steps = self._job()["steps"]
        names = [step.get("name") for step in steps]
        classify = _named_step(steps, "Classify the lane failure")

        assert classify["continue-on-error"] is True
        assert "--lane remediate" in classify["run"]
        assert names.index(classify["name"]) < names.index(_DEAD_LANE)

    @pytest.mark.parametrize("result", ["failure", "cancelled"])
    def test_empty_outputs_still_label_the_open_pr(self, tmp_path: Path, result: str) -> None:
        env = _dead_lane_sandbox(tmp_path, "250\n", f"{_SHA}\n")
        env["RESULT"] = result

        calls = self._run(tmp_path, env)

        assert "pr list --head fix/x --state open --json number --jq .[0].number" in calls
        assert "pr edit 250 --add-label needs-human:mutation" in calls
        assert (tmp_path / "sticky-args").read_text().split()[:2] == ["mutation", "250"]
        body = (tmp_path / "body").read_text()
        assert f"ended '{result}'" in body
        assert "unknown" in body
        assert _RUN_URL in body

    def test_a_runner_death_is_named_as_infrastructure(self, tmp_path: Path) -> None:
        env = _dead_lane_sandbox(tmp_path, "250\n", f"{_SHA}\n")
        env |= {"KIND": "infrastructure", "STEP": "Codex — mutation remediation (tests only)"}

        self._run(tmp_path, env)

        body = (tmp_path / "body").read_text()
        assert "INFRASTRUCTURE" in body
        assert "`Codex — mutation remediation (tests only)`" in body
        assert "gh run rerun 1 --failed" in body

    @pytest.mark.parametrize(
        ("pr", "view"),
        [
            ("", ""),
            ("250\n", "b" * 40 + "\n"),
            ("250\n", f"{_SHA}\nneeds-human:mutation\n"),
        ],
        ids=["no-open-pr", "head-moved-on", "already-labelled"],
    )
    def test_no_label_where_there_is_nothing_to_report(
        self, tmp_path: Path, pr: str, view: str
    ) -> None:
        calls = self._run(tmp_path, _dead_lane_sandbox(tmp_path, pr, view))

        assert "--add-label" not in calls
        assert not (tmp_path / "sticky-args").exists()


class TestARemediationSetUpFailureIsNamedAsTheRunner:
    """Issue #499, the remediation twin of #493. A `remediate` lane the
    self-hosted runner never set up has no outputs, so the hosted lane's
    dead-lane step reports it — and it said only that the job "ended 'failure'
    without reporting its own state", which reads like a Codex or diff failure
    and says nothing of the rerun that clears it."""

    def _report(self, tmp_path: Path, kind: str, step: str) -> str:
        env = _dead_lane_sandbox(tmp_path, "482\n", f"{_SHA}\n")
        env |= {"KIND": kind, "STEP": step}
        run = _named_step(_steps("mutation-remediation.yml", "remediate-verify"), _DEAD_LANE)
        _run_step(run["run"], tmp_path, env)
        return (tmp_path / "body").read_text(encoding="utf-8")

    def test_the_comment_names_the_runner_and_the_rerun(self, tmp_path: Path) -> None:
        body = self._report(tmp_path, "runner_setup", "Set up job")

        assert body.startswith("Mutation remediation BLOCKED by a RUNNER SET-UP failure")
        assert "not the diff and not Codex" in body
        assert "`remediate` failed in `Set up job`" in body
        assert "no Codex round started" in body
        assert "`gh run rerun 1 --failed`" in body
        assert "#493" in body
        assert _RUN_URL in body
        # The label is unchanged: a remediation lane escalates to mutation review.
        assert "pr edit 482 --add-label needs-human:mutation" in (tmp_path / "gh-calls").read_text()
        assert (tmp_path / "sticky-args").read_text().split()[:2] == ["mutation", "482"]

    def test_a_lane_with_no_steps_at_all_is_named_too(self, tmp_path: Path) -> None:
        body = self._report(tmp_path, "runner_setup", "(no step started)")

        assert "`remediate` failed in `(no step started)`" in body

    def test_an_ordinary_dead_lane_keeps_its_wording(self, tmp_path: Path) -> None:
        body = self._report(tmp_path, "in_job", "")

        assert body.startswith("Mutation remediation ended 'failure' without reporting")
        assert "RUNNER SET-UP" not in body


_AGENT_LANES = [
    ("pr-pipeline.yml", "codex-author", "Codex — independent PR test author"),
    ("mutation-remediation.yml", "remediate", "Codex — mutation remediation (tests only)"),
]
# Read off the runners API for `mikrus-codex` on 2026-09-29 (#445).
_MIKRUS_LABELS = {"self-hosted", "linux", "x64", "codex"}
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIKRUS_FALLBACK_LOCK = (
    "if [ -e /home/runner/.mikrus-agent.lock ]; then agent_lock=/home/runner/.mikrus-agent.lock; fi"
)


def _run_block(workflow_name: str, step_name: str, first: str, last: str) -> str:
    """Cut the lines from `first` through `last` out of the step's real script."""
    job_name = next(job for name, job, step in _AGENT_LANES if name == workflow_name)
    run = _named_step(_steps(workflow_name, job_name), step_name)["run"]
    start = run.index(first)
    return run[start : run.index(last, start) + len(last)]


class TestTheCodexLanesRunOnTheMacMini:
    """Issue #445: the Codex lanes left the Mikrus box, whose lock five
    repositories share, for lovspor's own runner on the Mac mini."""

    @pytest.mark.parametrize(("workflow_name", "job_name", "step_name"), _AGENT_LANES)
    def test_the_lane_targets_lovspors_own_runner(
        self, workflow_name: str, job_name: str, step_name: str
    ) -> None:
        job = _workflow(workflow_name)["jobs"][job_name]

        assert job["runs-on"] == ["self-hosted", "macOS", "codex-lovspor"]

    @pytest.mark.parametrize("workflow_name", ["pr-pipeline.yml", "mutation-remediation.yml"])
    def test_no_job_can_land_on_the_mikrus_runner(self, workflow_name: str) -> None:
        """A job runs on a runner carrying every one of its labels, so a label
        set inside Mikrus's is a job Mikrus can pick up."""
        for name, job in _workflow(workflow_name)["jobs"].items():
            labels = job["runs-on"]
            if isinstance(labels, str) or "self-hosted" not in labels:
                continue
            assert not {label.lower() for label in labels} <= _MIKRUS_LABELS, (
                f"{workflow_name}:{name} can still be scheduled on mikrus-codex"
            )

    @pytest.mark.parametrize(("workflow_name", "job_name", "step_name"), _AGENT_LANES)
    def test_no_step_leans_on_a_gnu_or_linux_only_tool(
        self, workflow_name: str, job_name: str, step_name: str
    ) -> None:
        """Stock macOS has no `flock` or `timeout`, no `free`, and a BSD `ps`,
        `sed`, `date`, `stat`, `readlink` and `xargs`."""
        linux_only = re.compile(
            r"(?<![\w-])(flock|timeout|gtimeout) |/home/runner|--sort|ps -e|sed -i|date -d"
            r"|readlink -f|stat -c|xargs -r|apt(-get)? "
        )
        for step in _steps(workflow_name, job_name):
            lines = str(step.get("run", "")).splitlines()
            run = "\n".join(
                line
                for line in lines
                if not line.lstrip().startswith("#") and line.strip() != _MIKRUS_FALLBACK_LOCK
            )
            unguarded = run.replace("if command -v free >/dev/null; then free -m", "")
            assert not linux_only.search(run), f"{step.get('name')}: {run}"
            assert "free -m" not in unguarded, f"{step.get('name')} calls free unguarded"

    @pytest.mark.parametrize(("workflow_name", "job_name", "step_name"), _AGENT_LANES)
    def test_no_step_assumes_the_runner_user_is_the_owner(
        self, workflow_name: str, job_name: str, step_name: str
    ) -> None:
        """The runner runs as a dedicated account with no access to the owner's
        home (#445 owner decision): a path into it would fail on the runner, or
        worse, work only because the isolation was undone."""
        for step in _steps(workflow_name, job_name):
            run = str(step.get("run", ""))
            assert "/Users/" not in run, step.get("name")
            assert "/Volumes/" not in run, step.get("name")

    @pytest.mark.parametrize(("workflow_name", "job_name", "step_name"), _AGENT_LANES)
    def test_a_held_lock_fails_the_step_with_the_contention_message(
        self, tmp_path: Path, workflow_name: str, job_name: str, step_name: str
    ) -> None:
        """The step's own lock lines, run against a lock another process holds.
        Only the wait is shortened: an hour is the budget, not the behaviour."""
        block = _run_block(workflow_name, step_name, "agent_lock=", "\n}")
        ready = tmp_path / "held"
        holder = subprocess.Popen(
            [
                "bash",
                "-c",
                'exec 9>"$HOME/.agent-box.lock"; python3 scripts/ci/fd_lock.py --wait 10 9'
                ' && touch "$1" && sleep 20',
                "bash",
                str(ready),
            ],
            cwd=_REPO_ROOT,
            env={**os.environ, "HOME": str(tmp_path)},
        )
        try:
            while not ready.exists():
                assert holder.poll() is None, "the holder never took the lock"
            result = subprocess.run(
                ["bash", "-c", block.replace("--wait 3600", "--wait 1")],
                cwd=_REPO_ROOT,
                env={**os.environ, "HOME": str(tmp_path)},
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        finally:
            holder.kill()
            holder.wait()

        assert result.returncode == 1
        assert "by another agent job of this runner's user" in result.stderr
        assert f"({tmp_path}/.agent-box.lock, #382/#445)" in result.stderr

    @pytest.mark.parametrize(("workflow_name", "job_name", "step_name"), _AGENT_LANES)
    def test_the_mikrus_fallback_still_takes_the_box_wide_lock(
        self, workflow_name: str, job_name: str, step_name: str
    ) -> None:
        """mikrus-codex stays registered as a fallback (owner decision on #445).
        A lane pointed back at it must queue behind the lock the other
        repositories take there, and only there: the switch keys on that file,
        which no Mac has."""
        block = _run_block(workflow_name, step_name, "agent_lock=", 'exec 9>"$agent_lock"')
        lines = [line.strip() for line in block.splitlines()]

        assert lines[0] == 'agent_lock="$HOME/.agent-box.lock"'
        assert _MIKRUS_FALLBACK_LOCK in lines
        assert lines.index(_MIKRUS_FALLBACK_LOCK) < lines.index('exec 9>"$agent_lock"')

    @pytest.mark.parametrize(("workflow_name", "job_name", "step_name"), _AGENT_LANES)
    def test_a_free_lock_is_taken_under_the_runner_users_home(
        self, tmp_path: Path, workflow_name: str, job_name: str, step_name: str
    ) -> None:
        block = _run_block(workflow_name, step_name, "agent_lock=", "\n}")

        result = subprocess.run(
            ["bash", "-c", block],
            cwd=_REPO_ROOT,
            env={**os.environ, "HOME": str(tmp_path)},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        assert (tmp_path / ".agent-box.lock").exists()


# A stand-in for the Codex CLI, at the process boundary the real script talks
# to: `app-server` answers the rate-limit read from USAGE (keyed by the home's
# name), `exec` records which home ran the round. Every call is logged.
_FAKE_CODEX = """#!/usr/bin/env python3
import json, os, sys
home = os.path.basename(os.environ["CODEX_HOME"])
log = os.environ["FAKE_CODEX_LOG"]
with open(log, "a") as f:
    f.write(sys.argv[1] + " " + home + "\\n")
if sys.argv[1] == "exec":
    sys.exit(0)
usage = json.loads(os.environ["FAKE_CODEX_USAGE"])[home]
for line in sys.stdin:
    message = json.loads(line)
    if message.get("id") == 0:
        print(json.dumps({"id": 0, "result": {}}), flush=True)
    elif message.get("id") == 1:
        result = {"rateLimits": {"primary": {"usedPercent": usage}}}
        print(json.dumps({"id": 1, "result": result}), flush=True)
"""
_FALLBACK_AUTHOR = '#!/usr/bin/env bash\necho "claude $*" >> "$FAKE_CODEX_LOG"\n'


class TestTheAgentStepRunsOnOneOrTwoCodexAccounts:
    """The owner has one ChatGPT account (#445, run 36576423655 died at the
    guard over the missing secondary). The primary login is required; the
    secondary is optional. Without it the real failover script asks the
    primary alone, and a limited primary goes to the Claude fallback, exactly
    as an exhausted secondary would. Each test runs the whole agent step, as
    written in the workflow, against the real helper scripts."""

    def _workspace(self, tmp_path: Path, logins: tuple[str, ...]) -> Path:
        work = tmp_path / "work"
        (work / "scripts" / "ci").mkdir(parents=True)
        (work / ".github" / "codex").mkdir(parents=True)
        for name in ("fd_lock.py", "codex_account_failover.py"):
            source = _REPO_ROOT / "scripts" / "ci" / name
            (work / "scripts" / "ci" / name).write_text(source.read_text(encoding="utf-8"))
        author = work / "scripts" / "ci" / "claude_test_author.sh"
        author.write_text(_FALLBACK_AUTHOR, encoding="utf-8")
        author.chmod(0o755)
        for prompt in ("pr-tests.md", "mutation-remediation.md"):
            (work / ".github" / "codex" / prompt).write_text("prompt\n", encoding="utf-8")
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "codex").write_text(_FAKE_CODEX, encoding="utf-8")
        (tmp_path / "bin" / "codex").chmod(0o755)
        for home in logins:
            (tmp_path / "home" / home).mkdir(parents=True)
            (tmp_path / "home" / home / "auth.json").write_text("{}", encoding="utf-8")
        (tmp_path / "home").mkdir(exist_ok=True)
        return work

    def _run(
        self, tmp_path: Path, workflow_name: str, logins: tuple[str, ...], usage: dict[str, float]
    ) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
        job_name, step_name = next((j, s) for w, j, s in _AGENT_LANES if w == workflow_name)
        run = _named_step(_steps(workflow_name, job_name), step_name)["run"]
        script = run.replace("${{ runner.temp }}", str(tmp_path))
        assert "${{" not in script
        work = self._workspace(tmp_path, logins)
        log, output = tmp_path / "calls.log", tmp_path / "github_output"
        env = {
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_NAME": "mac-mini-lovspor",
            "GITHUB_OUTPUT": str(output),
            "FAKE_CODEX_LOG": str(log),
            "FAKE_CODEX_USAGE": json.dumps(usage),
        }
        result = subprocess.run(
            ["bash", "-e", "-c", script],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        written = output.read_text(encoding="utf-8") if output.exists() else ""
        return result, calls, written

    @pytest.mark.parametrize("workflow_name", ["pr-pipeline.yml", "mutation-remediation.yml"])
    def test_primary_only_runs_the_round_on_the_primary(
        self, tmp_path: Path, workflow_name: str
    ) -> None:
        result, calls, output = self._run(
            tmp_path, workflow_name, (".codex-lovspor",), {".codex-lovspor": 10}
        )

        assert result.returncode == 0, result.stderr
        assert calls == ["app-server .codex-lovspor", "exec .codex-lovspor"]
        assert "author=codex" in output
        assert "::warning::no Codex login at" in result.stdout
        assert ".codex-lovspor-secondary" in result.stdout
        assert "account failover is disabled" in result.stdout

    @pytest.mark.parametrize("workflow_name", ["pr-pipeline.yml", "mutation-remediation.yml"])
    def test_both_accounts_fail_over_to_the_secondary(
        self, tmp_path: Path, workflow_name: str
    ) -> None:
        usage = {".codex-lovspor": 99, ".codex-lovspor-secondary": 10}
        result, calls, output = self._run(
            tmp_path, workflow_name, (".codex-lovspor", ".codex-lovspor-secondary"), usage
        )

        assert result.returncode == 0, result.stderr
        assert calls == [
            "app-server .codex-lovspor",
            "app-server .codex-lovspor-secondary",
            "exec .codex-lovspor-secondary",
        ]
        assert "author=codex" in output
        assert "::warning::" not in result.stdout

    @pytest.mark.parametrize("workflow_name", ["pr-pipeline.yml", "mutation-remediation.yml"])
    @pytest.mark.parametrize("logins", [(), (".codex-lovspor-secondary",)])
    def test_a_missing_primary_fails_before_any_agent(
        self, tmp_path: Path, workflow_name: str, logins: tuple[str, ...]
    ) -> None:
        result, calls, output = self._run(
            tmp_path, workflow_name, logins, {".codex-lovspor-secondary": 10}
        )

        assert result.returncode == 1
        assert "no Codex login at" in result.stderr
        assert ".codex-lovspor on runner mac-mini-lovspor" in result.stderr
        assert calls == []
        assert "author=" not in output

    @pytest.mark.parametrize("workflow_name", ["pr-pipeline.yml", "mutation-remediation.yml"])
    def test_a_limited_primary_without_a_secondary_goes_to_the_fallback_author(
        self, tmp_path: Path, workflow_name: str
    ) -> None:
        result, calls, output = self._run(
            tmp_path, workflow_name, (".codex-lovspor",), {".codex-lovspor": 99}
        )

        assert result.returncode == 0, result.stderr
        assert calls[0] == "app-server .codex-lovspor"
        assert calls[1].startswith("claude ")
        assert len(calls) == 2, "no secondary was asked and no Codex round ran"
        assert "author=claude" in output


# Issue #381: a pull_request run takes its workflow from the merge ref but checks
# out the PR head, so a step calling a scripts/ci/ file the head predates dies with
# exit 127. Only these calls may run unguarded: every branch the pipeline can run
# on already has them. Any other call carries `[ -x scripts/ci/NAME ]` and a
# fallback in its own run block; retiring the guard adds the name here
# (docs/agentic-ci.md, "New scripts/ci/ calls are guarded for one release cycle").
_ESTABLISHED_CI_SCRIPTS = frozenset(
    {
        "assert_codex_scope.sh",
        "classify_lane_failure.py",
        "claude_test_author.sh",
        "codex_account_failover.py",
        "codex_convergence.py",
        "fd_lock.py",
        "mutation_gate.py",
        "mutation_survivors.py",
        "mutation_to_json.py",
        "normalize_agent_tests.sh",
        "pr_sticky_comment.sh",
        "publish_tree_digest.py",
        "token_expiry.py",
    }
)
_CI_SCRIPT_CALL = re.compile(r"scripts/ci/([A-Za-z0-9_.-]+)")
_CI_SCRIPTS = _WORKFLOWS.parents[1] / "scripts" / "ci"
_ALL_WORKFLOWS = sorted(_WORKFLOWS.glob("*.yml"))


def _run_blocks(workflow_path: Path) -> Iterator[str]:
    workflow: dict[str, Any] = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "run" in step:
                yield step["run"]


def _new_ci_calls(workflow_path: Path) -> set[tuple[str, str]]:
    """Every (script, run block) pair calling a scripts/ci/ file not yet established."""
    return {
        (name, run)
        for run in _run_blocks(workflow_path)
        for name in _CI_SCRIPT_CALL.findall(run)
        if name not in _ESTABLISHED_CI_SCRIPTS
    }


def _is_guarded(name: str, run: str) -> bool:
    return re.search(rf'\[ -x "?scripts/ci/{re.escape(name)}"? \]', run) is not None


def _unguarded_ci_calls(workflow_path: Path) -> list[str]:
    calls = _new_ci_calls(workflow_path)
    return sorted({name for name, run in calls if not _is_guarded(name, run)})


_WORKFLOW_TEMPLATE = """\
name: t
on: pull_request
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - name: step
        run: |
{body}
"""


def _write_workflow(tmp_path: Path, *body_lines: str) -> Path:
    body = "\n".join(f"          {line}" for line in body_lines)
    path = tmp_path / "wf.yml"
    path.write_text(_WORKFLOW_TEMPLATE.format(body=body), encoding="utf-8")
    return path


def test_an_unguarded_call_to_a_new_ci_script_is_flagged(tmp_path: Path) -> None:
    workflow = _write_workflow(tmp_path, "scripts/ci/brand_new.sh tests/")

    assert _unguarded_ci_calls(workflow) == ["brand_new.sh"]


@pytest.mark.parametrize(
    "guard", ["[ -x scripts/ci/brand_new.sh ]", '[ -x "scripts/ci/brand_new.sh" ]']
)
def test_a_guarded_new_call_and_an_established_call_pass(tmp_path: Path, guard: str) -> None:
    workflow = _write_workflow(
        tmp_path,
        f"if {guard}; then",
        "  scripts/ci/brand_new.sh tests/",
        "else",
        '  echo "brand_new.sh not on this head; skipping" >&2',
        "fi",
        "scripts/ci/pr_sticky_comment.sh pipeline 1 x.md",
    )

    assert _unguarded_ci_calls(workflow) == []


def test_a_guard_in_another_step_does_not_cover_the_call(tmp_path: Path) -> None:
    path = _write_workflow(tmp_path, "[ -x scripts/ci/brand_new.sh ] || exit 0")
    other_step = "      - name: other\n        run: scripts/ci/brand_new.sh\n"
    path.write_text(path.read_text(encoding="utf-8") + other_step, encoding="utf-8")

    assert _unguarded_ci_calls(path) == ["brand_new.sh"]


def test_a_guard_for_another_script_does_not_cover_the_call(tmp_path: Path) -> None:
    workflow = _write_workflow(
        tmp_path, "[ -x scripts/ci/brand_new.sh.bak ] && scripts/ci/brand_new.sh"
    )

    assert _unguarded_ci_calls(workflow) == ["brand_new.sh"]


@pytest.mark.parametrize("workflow_path", _ALL_WORKFLOWS, ids=lambda path: path.name)
def test_every_new_ci_script_call_in_a_workflow_is_guarded(workflow_path: Path) -> None:
    assert _unguarded_ci_calls(workflow_path) == []


@pytest.mark.parametrize("workflow_path", _ALL_WORKFLOWS, ids=lambda path: path.name)
def test_a_guarded_ci_script_is_executable_on_main(workflow_path: Path) -> None:
    """`[ -x ]` on a script committed without the executable bit is false on
    every head, so the fallback would run forever and nothing would say so."""
    for name, _run in _new_ci_calls(workflow_path):
        assert os.access(_CI_SCRIPTS / name, os.X_OK), name


def test_every_established_ci_script_exists() -> None:
    missing = sorted(name for name in _ESTABLISHED_CI_SCRIPTS if not (_CI_SCRIPTS / name).is_file())

    assert missing == []


_TOOL_STOP = "Stop when the agent could not start its execution tool"
# Verbatim from run 36670610064 (PR #469), as `tee` writes it: codex's own
# timestamp only.
_TOOL_HOST_ERROR = (
    "2026-09-30T04:50:18.893013Z ERROR codex_core::tools::router: error=failed to spawn"
    " code-mode host /Users/ci-lovspor/.local/bin/codex-code-mode-host: No such file or"
    " directory (os error 2)\n"
)


class TestAMissingToolHostFailsTheRemediationLane:
    """Issue #448: codex-cli 0.159.0 on the Mac runner lacks
    `codex-code-mode-host`, the agent could not run one command, `codex exec`
    exited 0, and the round went down the no-change path as if it had
    classified the survivors. It now fails the lane, and the verifier's
    classifier names it an infrastructure failure of the runner's install."""

    def _stop(self, tmp_path: Path, transcript: str | None) -> subprocess.CompletedProcess[str]:
        if transcript is not None:
            (tmp_path / "remediation-agent.log").write_text(transcript, encoding="utf-8")
        step = _named_step(_steps("mutation-remediation.yml", "remediate"), _TOOL_STOP)
        env = {"PATH": _SYSTEM_PATH, "RUNNER_TEMP": str(tmp_path), "RUNNER_NAME": "mac"}
        script = ["bash", "-e", "-c", step["run"]]
        return subprocess.run(
            script, cwd=tmp_path, env=env, capture_output=True, text=True, check=False
        )

    def test_the_pr_469_transcript_fails_the_lane_loudly(self, tmp_path: Path) -> None:
        done = self._stop(tmp_path, f"codex\nI'll read the survivors.\n{_TOOL_HOST_ERROR}")

        assert done.returncode == 1
        assert "::error::" in done.stdout
        assert "codex-code-mode-host is missing" in done.stdout
        assert "#448" in done.stdout

    @pytest.mark.parametrize("transcript", ["exec\n/bin/bash -lc ls\n succeeded in 2ms:\n", None])
    def test_a_working_or_absent_transcript_passes(
        self, tmp_path: Path, transcript: str | None
    ) -> None:
        assert self._stop(tmp_path, transcript).returncode == 0

    def test_the_stop_reads_the_transcript_the_codex_step_keeps(self) -> None:
        steps = _steps("mutation-remediation.yml", "remediate")
        names = [step.get("name") for step in steps]
        author = _named_step(steps, "Codex — mutation remediation (tests only)")

        assert '| tee "$RUNNER_TEMP/remediation-agent.log"' in author["run"]
        assert "status=${PIPESTATUS[0]}" in author["run"]
        assert names.index(author["name"]) < names.index(_TOOL_STOP) < names.index("Scope guard")
        assert _named_step(steps, _TOOL_STOP)["if"] == "steps.author.outputs.author == 'codex'"

    def test_the_verifier_classifies_every_failure_with_the_lane_log(self) -> None:
        job = _workflow("mutation-remediation.yml")["jobs"]["remediate-verify"]
        classify = _named_step(job["steps"], "Classify the lane failure")

        assert classify["if"] == "failure()"
        assert '--log "$RUNNER_TEMP/lane.log"' in classify["run"]
        assert job["permissions"]["actions"] == "read"

    def _escalate(self, tmp_path: Path, kind: str) -> str:
        env = _escalation_sandbox(tmp_path) | {"KIND": kind, "RUN_URL": _RUN_URL}
        steps = _steps("mutation-remediation.yml", "remediate-verify")
        step = _named_step(steps, "Escalate on remediation failure")
        _run_step(_render(step["run"], {"needs.remediate.outputs.pr": "309"}), tmp_path, env)
        return (tmp_path / "body").read_text(encoding="utf-8")

    def test_the_runner_tool_is_reported_as_infrastructure(self, tmp_path: Path) -> None:
        body = self._escalate(tmp_path, "runner_tool")

        assert body.startswith("Mutation remediation BLOCKED by an INFRASTRUCTURE failure")
        assert "no survivor was classified" in body
        assert "called non-killable" not in body
        assert "gh run rerun 1 --failed" in body
        assert _RUN_URL in body

    def test_any_other_failure_keeps_its_wording(self, tmp_path: Path) -> None:
        body = self._escalate(tmp_path, "in_job")

        assert body.startswith("Mutation remediation run FAILED before completing")
        assert _RUN_URL in body


_AUTHOR_STEP = "Codex — independent PR test author"
_AUTHOR_RAN = "Fail a round that executed no command"
_AUTHOR_NOT_RUN = "The independent test author executed no command (#489)"


class TestAnAuthorRoundThatRanNoCommandFails:
    """Issue #489: on PR #469 (head 99e00f7, run 36677467828) `codex-author`
    and `codex-tests` were green while the independent test author never ran a
    command — `codex-code-mode-host` was missing (#448) and `codex exec` exited
    0. A round with no executed command, or with no transcript at all, now
    fails the author lane, and the hosted reporter names it a lane failure."""

    FALLBACK = "Escalate — the pipeline failed before the tests ran"

    def _stop(
        self, tmp_path: Path, transcript: str | None, *, helper: bool = True
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        if transcript is not None:
            shutil.copy(_REMEDIATION_FIXTURES / transcript, tmp_path / "codex-author.log")
        if helper:
            ci = tmp_path / "scripts" / "ci"
            ci.mkdir(parents=True)
            for name in ("author_transcript.py", "remediation_transcript.py", "mutation_gate.py"):
                shutil.copy2(_REPO / "scripts" / "ci" / name, ci / name)
        out = tmp_path / "out"
        out.touch()
        env = {"PATH": _SYSTEM_PATH, "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(out)}
        step = _named_step(_steps("pr-pipeline.yml", "codex-author"), _AUTHOR_RAN)
        done = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return done, out.read_text(encoding="utf-8")

    def test_the_stop_reads_the_transcript_the_codex_step_keeps(self) -> None:
        steps = _steps("pr-pipeline.yml", "codex-author")
        names = [step.get("name") for step in steps]
        author = _named_step(steps, _AUTHOR_STEP)
        stop = _named_step(steps, _AUTHOR_RAN)

        assert '| tee "$RUNNER_TEMP/codex-author.log"' in author["run"]
        assert "status=${PIPESTATUS[0]}" in author["run"]
        assert names.index(_AUTHOR_STEP) < names.index(_AUTHOR_RAN) < names.index("Scope guard")
        # The Claude fallback's transcript has no `exec` marker (#472).
        assert stop["if"] == "steps.author.outputs.author == 'codex'"
        assert stop["id"] == "ran"

    def test_the_pr_469_round_fails_the_lane_and_names_the_runner(self, tmp_path: Path) -> None:
        done, out = self._stop(tmp_path, "codex-no-command-run-36670610064.log")

        assert done.returncode == 1
        assert f"::error::{_AUTHOR_NOT_RUN}" in done.stdout
        assert "codex-code-mode-host is missing" in done.stdout
        assert out.startswith("not_run=the runner's Codex CLI could not start its execution tool")

    def test_a_round_that_ran_commands_passes_silently(self, tmp_path: Path) -> None:
        done, out = self._stop(tmp_path, "codex-ran-commands-run-36531263464.log")

        assert done.returncode == 0
        assert "::error::" not in done.stdout
        assert out == ""

    def test_a_missing_transcript_fails_closed(self, tmp_path: Path) -> None:
        done, out = self._stop(tmp_path, None)

        assert done.returncode == 1
        assert f"::error::{_AUTHOR_NOT_RUN}: the round left no transcript" in done.stdout
        assert out == "not_run=the round left no transcript\n"

    @pytest.mark.parametrize(
        ("transcript", "status"),
        [
            ("codex-ran-commands-run-36531263464.log", 0),
            ("codex-no-command-run-36670610064.log", 1),
            (None, 1),
        ],
    )
    def test_a_head_without_the_helper_still_fails_closed(
        self, tmp_path: Path, transcript: str | None, status: int
    ) -> None:
        """A branch cut before the helper landed runs this workflow with its
        own scripts/ci; the guard's fallback must not turn into a pass."""
        done, _out = self._stop(tmp_path, transcript, helper=False)

        assert done.returncode == status

    def test_the_annotation_carries_the_classifiers_signature(self) -> None:
        stop = _named_step(_steps("pr-pipeline.yml", "codex-author"), _AUTHOR_RAN)
        classifier = (_CI_SCRIPTS / "classify_lane_failure.py").read_text(encoding="utf-8")

        assert f"::error::{_AUTHOR_NOT_RUN}" in stop["run"]
        assert f'AGENT_NOT_RUN_SIGNATURE = "{_AUTHOR_NOT_RUN}"' in classifier

    def test_the_reason_reaches_the_verifier_which_leaves_it_to_the_reporter(self) -> None:
        """`codex-tests` cannot read the jobs API: its generic round would claim
        the label first and make the classifying reporter stand down."""
        author = _workflow("pr-pipeline.yml")["jobs"]["codex-author"]
        fallback = _named_step(_steps("pr-pipeline.yml", "codex-tests"), self.FALLBACK)

        assert author["outputs"]["not_run"] == "${{ steps.ran.outputs.not_run }}"
        assert fallback["if"].endswith("&& needs.codex-author.outputs.not_run == ''")

    def _report(self, tmp_path: Path, kind: str) -> str:
        return TestARunnerSetUpFailureIsNamedAsTheRunner()._report(tmp_path, kind, _AUTHOR_RAN)

    def test_a_no_command_round_is_reported_as_a_lane_failure(self, tmp_path: Path) -> None:
        body = self._report(tmp_path, "agent_did_not_run")

        assert body.startswith("codex-tests BLOCKED — the independent test author ran no command")
        assert "no independent test reviewed this PR" in body
        assert "#489" in body
        assert "`gh run rerun 1 --failed`" in body
        assert _RUN_URL in body
        assert "pr edit 482 --add-label needs-human:pipeline" in (tmp_path / "gh-calls").read_text()

    def test_a_missing_tool_host_is_reported_as_the_runners_install(self, tmp_path: Path) -> None:
        body = self._report(tmp_path, "runner_tool")

        assert body.startswith("codex-tests BLOCKED by an INFRASTRUCTURE failure")
        assert "the runner's Codex install, not the diff" in body
        assert "no independent test reviewed this PR" in body
        assert "#448" in body
        assert "`gh run rerun 1 --failed`" in body
        assert _RUN_URL in body


_JOB_LOG_FIXTURE = "codex-author-job-log-run-37205273475.log"
_ESCAPE_FLAG = "--allow-escape-sequences"
# Verbatim from the codex-tests-report job of run 37205273475 (attempt 1).
_ESCAPE_REFUSAL = (
    "the response contains terminal escape sequences; pass --allow-escape-sequences "
    "to output it anyway"
)
_FAKE_GH_API = f"""\
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
endpoint = next(arg for arg in args if arg.startswith("repos/"))
if endpoint.endswith("/jobs"):
    sys.stdout.write(Path(os.environ["FAKE_JOBS"]).read_text(encoding="utf-8"))
    raise SystemExit(0)
flagged = "{_ESCAPE_FLAG}" in args
with (Path(os.environ["RUNNER_TEMP"]) / "log-fetches.jsonl").open("a") as calls:
    calls.write(json.dumps(args) + "\\n")
if os.environ["FAKE_GH"] == "unavailable":
    sys.stdout.buffer.write(Path(os.environ["FAKE_LOG"]).read_bytes())
    sys.stderr.write("log download interrupted\\n")
    raise SystemExit(1)
if os.environ["FAKE_GH"] == "old" and flagged:
    sys.stderr.write("unknown flag: {_ESCAPE_FLAG}\\n")
    raise SystemExit(1)
log = Path(os.environ["FAKE_LOG"]).read_bytes()
if os.environ["FAKE_GH"] == "strict" and b"\\x1b" in log and not flagged:
    sys.stderr.write("{_ESCAPE_REFUSAL}\\n")
    raise SystemExit(1)
sys.stdout.buffer.write(log)
"""


def _lane_jobs(lane: str, failed_step: str) -> dict[str, Any]:
    """The run's jobs payload as the classifier reads it: the lane failed in
    its own step, and every step completed."""
    steps = [
        {"name": "Set up job", "status": "completed", "conclusion": "success"},
        {"name": failed_step, "status": "completed", "conclusion": "failure"},
        {"name": "Complete job", "status": "completed", "conclusion": "success"},
    ]
    job = {"id": 111445054823, "name": lane, "conclusion": "failure", "steps": steps}
    return {"total_count": 1, "jobs": [job]}


class TestTheReporterReadsTheLaneLogGitHubsCliRefused:
    """Issue #542. On PR #541 (run 37205273475, attempt 1) `codex-author`
    stopped a round whose Codex could not spawn `codex-code-mode-host` (#448),
    and the hosted reporter still posted the generic "reported nothing itself"
    comment. The classifier was never handed the log: the runner image's `gh`
    refuses to print a response holding terminal escape sequences —

        the response contains terminal escape sequences; pass
        --allow-escape-sequences to output it anyway

    — and every job log holds them (the runner echoes each `run:` script in
    `ESC[36;1m`). The fetch's `|| : >` fallback then left `lane.log` empty, so
    the verdict was `in_job`. The fixture is lines 409-448 of that run's
    `codex-author` log, escape bytes included. An older `gh` (2.96 here) has
    no such flag and rejects it, so the fetch must work with both."""

    _REPORTER = ("pr-pipeline.yml", "codex-tests-report", "codex-author")
    _VERIFIER = ("mutation-remediation.yml", "remediate-verify", "remediate")

    def _classify(
        self, tmp_path: Path, log: bytes, gh: str, where: tuple[str, str, str]
    ) -> dict[str, str]:
        workflow, job, lane = where
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True)
        fake = bin_dir / "gh"
        fake.write_text(f"#!{sys.executable}\n{_FAKE_GH_API}", encoding="utf-8")
        fake.chmod(0o755)
        (tmp_path / "lane-job.log").write_bytes(log)
        jobs = _lane_jobs(lane, _AUTHOR_RAN)
        (tmp_path / "jobs-payload.json").write_text(json.dumps(jobs), encoding="utf-8")
        ci = tmp_path / "scripts" / "ci"
        ci.mkdir(parents=True)
        shutil.copy2(_CI_SCRIPTS / "classify_lane_failure.py", ci)
        output = tmp_path / "github-output"
        env = {
            "PATH": f"{bin_dir}:{_SYSTEM_PATH}",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(output),
            "GH_REPO": "o/r",
            "GITHUB_RUN_ID": "1",
            "FAKE_GH": gh,
            "FAKE_LOG": str(tmp_path / "lane-job.log"),
            "FAKE_JOBS": str(tmp_path / "jobs-payload.json"),
        }
        step = _named_step(_steps(workflow, job), "Classify the lane failure")
        values = {"github.repository": "o/r", "github.run_id": "1"}
        result = subprocess.run(
            ["bash", "-eu", "-o", "pipefail", "-c", _render(step["run"], values)],
            cwd=tmp_path,
            env=env,
            check=True,
            capture_output=True,
        )
        (tmp_path / "classify-stdout").write_bytes(result.stdout)
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    @staticmethod
    def _log(*, host_missing: bool) -> bytes:
        log = (_REMEDIATION_FIXTURES / _JOB_LOG_FIXTURE).read_bytes()
        if host_missing:
            return log
        # The same round without codex's spawn error: a no-`exec` round (#489).
        lines = log.splitlines(keepends=True)
        return b"".join(line for line in lines if b"codex_core::tools::router" not in line)

    def test_the_fixture_has_the_shape_the_cli_refuses(self) -> None:
        assert b"\x1b[36;1m" in self._log(host_missing=True)

    @pytest.mark.parametrize("gh", ["strict", "old"])
    @pytest.mark.parametrize("where", [_REPORTER, _VERIFIER], ids=["reporter", "verifier"])
    def test_a_missing_code_mode_host_is_the_runners_tool(
        self, tmp_path: Path, gh: str, where: tuple[str, str, str]
    ) -> None:
        out = self._classify(tmp_path, self._log(host_missing=True), gh, where)

        assert out["kind"] == "runner_tool"
        assert out["signature"] == "failed to spawn code-mode host"

    @pytest.mark.parametrize("gh", ["strict", "old"])
    def test_a_round_with_no_exec_is_an_agent_that_did_not_run(
        self, tmp_path: Path, gh: str
    ) -> None:
        out = self._classify(tmp_path, self._log(host_missing=False), gh, self._REPORTER)

        assert out["kind"] == "agent_did_not_run"
        assert out["step"] == _AUTHOR_RAN

    def test_the_pr_541_round_gets_the_comment_naming_the_missing_binary(
        self, tmp_path: Path
    ) -> None:
        log = self._log(host_missing=True)
        out = self._classify(tmp_path / "classify", log, "strict", self._REPORTER)
        (tmp_path / "report").mkdir()
        report = TestARunnerSetUpFailureIsNamedAsTheRunner()._report

        body = report(tmp_path / "report", out["kind"], out["step"])

        assert "`codex-code-mode-host`" in body
        assert "#448" in body
        assert "reported nothing itself" not in body

    def test_a_log_that_cannot_be_fetched_is_said_out_loud(self) -> None:
        for workflow, job, _lane in (self._REPORTER, self._VERIFIER):
            run = _named_step(_steps(workflow, job), "Classify the lane failure")["run"]

            assert "::warning::" in run

    @pytest.mark.parametrize("where", [_REPORTER, _VERIFIER], ids=["reporter", "verifier"])
    def test_failed_fetches_discard_partial_evidence_and_warn(
        self, tmp_path: Path, where: tuple[str, str, str]
    ) -> None:
        """Both workflows promise an empty log and a warning after two failures.

        A failed download can still emit the host-error signature: it must not
        refine the verdict using evidence from an incomplete response.
        """
        out = self._classify(tmp_path, self._log(host_missing=True), "unavailable", where)

        assert (tmp_path / "lane.log").read_bytes() == b""
        assert out["kind"] == "in_job"
        assert out["job"] == where[2]
        warning = (tmp_path / "classify-stdout").read_text(encoding="utf-8")
        assert warning == (
            f"::warning::could not fetch the {where[2]} log; classifying without it\n"
        )
        calls = [
            json.loads(line) for line in (tmp_path / "log-fetches.jsonl").read_text().splitlines()
        ]
        endpoint = "repos/o/r/actions/jobs/111445054823/logs"
        assert calls == [["api", _ESCAPE_FLAG, endpoint], ["api", endpoint]]

    @pytest.mark.parametrize("gh", ["strict", "old"])
    @pytest.mark.parametrize("where", [_REPORTER, _VERIFIER], ids=["reporter", "verifier"])
    def test_successful_fetch_keeps_all_bytes_and_stops_retrying(
        self, tmp_path: Path, gh: str, where: tuple[str, str, str]
    ) -> None:
        log = self._log(host_missing=True)
        self._classify(tmp_path, log, gh, where)

        assert (tmp_path / "lane.log").read_bytes() == log
        assert (tmp_path / "classify-stdout").read_bytes() == b""
        calls = [
            json.loads(line) for line in (tmp_path / "log-fetches.jsonl").read_text().splitlines()
        ]
        endpoint = "repos/o/r/actions/jobs/111445054823/logs"
        expected = [["api", _ESCAPE_FLAG, endpoint]]
        if gh == "old":
            expected.append(["api", endpoint])
        assert calls == expected
