"""The nightly mutation lane: verdict mapping and workflow shape.

Mutation left the PR lane for a nightly report on main (docs/agentic-ci.md,
"Nightly mutation"). Two things must hold without a live run: a partial run is
never reported as clean, and only a completed run moves the base tag.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
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


def test_the_lane_runs_nightly_and_on_demand() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow[True]  # PyYAML reads the bare `on:` key as boolean True
    assert triggers["schedule"] == [{"cron": "30 23 * * *"}]
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


# The range step decides what a re-run measures: run its own shell, in a real
# repository, instead of asserting its text.
OLD = "2020-01-01T00:00:00+00:00"


def _commit(repo: Path, message: str, date: str | None = None) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t"}
    env |= {"GIT_COMMITTER_EMAIL": "t@t", "PATH": os.environ["PATH"]}
    if date is not None:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", message], cwd=repo, env=env, check=True
    )
    return _git(repo, "rev-parse", "HEAD")


def _ago(delta: timedelta) -> str:
    """A date `delta` before now; the one-hour margins dwarf the test's own runtime."""
    return (datetime.now(UTC) - delta).isoformat(timespec="seconds")


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _resolve_range(repo: Path) -> tuple[int, dict[str, str]]:
    output = repo.parent / "github-output"
    output.write_text("", encoding="utf-8")
    done = subprocess.run(
        ["bash", "-c", str(_step("Resolve the range")["run"])],
        cwd=repo,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "BASE_TAG": "nightly-mutation-base",
            "GITHUB_OUTPUT": str(output),
        },
        check=False,
    )
    lines = output.read_text(encoding="utf-8").splitlines()
    return done.returncode, dict(line.split("=", 1) for line in lines)


@pytest.fixture
def main_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return repo


def test_a_rerun_after_a_completed_night_measures_an_empty_range(main_repo: Path) -> None:
    _commit(main_repo, "old", OLD)
    head = _commit(main_repo, "new")
    _git(main_repo, "tag", "nightly-mutation-base", head)

    status, out = _resolve_range(main_repo)

    assert status == 0
    assert out["base"] == out["head"] == head
    assert out["source"].startswith("tag `nightly-mutation-base`")


@pytest.mark.parametrize("annotated", [False, True])
def test_a_valid_tag_needs_no_week_old_history(main_repo: Path, annotated: bool) -> None:
    """A usable completed-night tag takes precedence over the seven-day fallback."""
    tagged = _commit(main_repo, "measured", _ago(timedelta(hours=2)))
    if annotated:
        _git(
            main_repo,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "tag",
            "-a",
            "nightly-mutation-base",
            "-m",
            "completed night",
            tagged,
        )
    else:
        _git(main_repo, "tag", "nightly-mutation-base", tagged)
    head = _commit(main_repo, "not measured yet")

    status, out = _resolve_range(main_repo)

    assert status == 0
    assert (out["base"], out["head"]) == (tagged, head)
    assert out["source"].startswith("tag `nightly-mutation-base`")


def test_a_rerun_of_an_incomplete_night_measures_the_same_range(main_repo: Path) -> None:
    _commit(main_repo, "old", OLD)
    tagged = _commit(main_repo, "measured by the last completed night")
    _git(main_repo, "tag", "nightly-mutation-base", tagged)
    head = _commit(main_repo, "not measured yet")

    first = _resolve_range(main_repo)
    second = _resolve_range(main_repo)

    assert first == second
    assert (first[1]["base"], first[1]["head"]) == (tagged, head)


@pytest.mark.parametrize("tag", ["missing", "not-an-ancestor"])
def test_an_unusable_tag_falls_back_to_the_last_week_old_commit(main_repo: Path, tag: str) -> None:
    _commit(main_repo, "old", OLD)
    old = _commit(main_repo, "just over a week", _ago(timedelta(days=7, hours=1)))
    _commit(main_repo, "just under a week", _ago(timedelta(days=6, hours=23)))
    if tag == "not-an-ancestor":
        _git(main_repo, "checkout", "-q", "-b", "rewritten")
        _git(main_repo, "tag", "nightly-mutation-base", _commit(main_repo, "elsewhere"))
        _git(main_repo, "checkout", "-q", "main")
    head = _commit(main_repo, "new")

    status, out = _resolve_range(main_repo)

    assert status == 0
    assert (out["base"], out["head"]) == (old, head)
    assert out["source"].startswith("fallback:")


def test_no_tag_and_no_week_old_commit_fails_the_range_step(main_repo: Path) -> None:
    """A failed range step leaves no result: the verdict is tool_failure, so the run is red."""
    _commit(main_repo, "just under a week", _ago(timedelta(days=6, hours=23)))
    _commit(main_repo, "new")

    status, out = _resolve_range(main_repo)

    assert status == 1
    assert "base" not in out


def _move_tag(repo: Path, head: str) -> int:
    """The workflow's own tag step, for a night whose verdict was complete."""
    body = str(_step("Move the base tag")["run"]).replace("${{ steps.range.outputs.head }}", head)
    done = subprocess.run(
        ["bash", "-e", "-c", body],
        cwd=repo,
        capture_output=True,
        env={"PATH": os.environ["PATH"], "BASE_TAG": "nightly-mutation-base"},
        check=False,
    )
    return done.returncode


def test_the_night_after_an_incomplete_one_still_measures_its_commits(main_repo: Path) -> None:
    _commit(main_repo, "old", OLD)
    tagged = _commit(main_repo, "measured")
    _git(main_repo, "tag", "nightly-mutation-base", tagged)
    _commit(main_repo, "night 1 head: budget exceeded, the tag step is skipped")
    later = _commit(main_repo, "landed the next day")

    status, out = _resolve_range(main_repo)

    assert status == 0
    assert (out["base"], out["head"]) == (tagged, later)
    assert _git(main_repo, "rev-parse", "nightly-mutation-base") == tagged


def test_a_completed_night_moves_the_tag_and_the_next_range_starts_there(
    main_repo: Path, tmp_path: Path
) -> None:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    _git(main_repo, "remote", "add", "origin", str(origin))
    _commit(main_repo, "old", OLD)
    _git(main_repo, "tag", "nightly-mutation-base", _commit(main_repo, "measured"))
    night = _commit(main_repo, "night head")
    _, first = _resolve_range(main_repo)

    assert _move_tag(main_repo, first["head"]) == 0
    later = _commit(main_repo, "landed after the night")
    _, second = _resolve_range(main_repo)

    assert _git(origin, "rev-parse", "nightly-mutation-base") == night
    assert (second["base"], second["head"]) == (night, later)
