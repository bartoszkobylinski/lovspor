"""Contract tests for scripts/ci/await_followup_run.sh (#798).

After codex-tests pushes, `mutation` and `ready` are skipped by design: the
push fires a fresh `synchronize` run that reaches its own verdict. On PR #791
that run never came. The branch had the pushed SHA, the PR kept the old head
for 20 minutes, every check was green or skipped and no verdict arrived.

The script waits, bounded, for a pr-pipeline run on the exact pushed SHA and
escalates when none appears. `gh` is a stub that plays back scripted answers,
so the tests pin the script's decisions, not GitHub.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

_ROOT = Path(__file__).parents[2]
_SCRIPT = _ROOT / "scripts" / "ci" / "await_followup_run.sh"
_PUSHED = "4083c903" + "a" * 32
_BEFORE = "17dd2c53" + "b" * 32
_OTHER = "5bfa57c8" + "c" * 32

_GH_STUB = """#!/usr/bin/env python3
import json, os, pathlib, sys
state = pathlib.Path(os.environ["GH_STUB_STATE"])
with (state / "calls.jsonl").open("a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
key = "runs" if sys.argv[1] == "api" else "head" if sys.argv[1:3] == ["pr", "view"] else None
if key is None:
    sys.exit(0)
answers = json.loads((state / f"{key}.json").read_text())
seen = state / f"{key}.seen"
done = int(seen.read_text()) if seen.exists() else 0
seen.write_text(str(done + 1))
answer = answers[min(done, len(answers) - 1)]
if answer == "API_ERROR":
    print("untrusted partial response", end="")
    sys.exit(1)
print(answer)
"""


def _sandbox(tmp_path: Path, runs: list[int], heads: list[str]) -> dict[str, str]:
    ci = tmp_path / "scripts" / "ci"
    ci.mkdir(parents=True)
    shutil.copy(_SCRIPT, ci / _SCRIPT.name)
    sticky = ci / "pr_sticky_comment.sh"
    sticky.write_text(f'#!/bin/sh\necho "$1 $2" > "{tmp_path}/sticky"\ncp "$3" "{tmp_path}/body"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(_GH_STUB)
    for executable in (ci / _SCRIPT.name, sticky, gh):
        executable.chmod(0o755)
    (tmp_path / "runs.json").write_text(json.dumps([str(n) for n in runs]))
    (tmp_path / "head.json").write_text(json.dumps(heads))
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_STUB_STATE": str(tmp_path),
        "GH_REPO": "o/r",
        "FOLLOWUP_ATTEMPTS": "4",
        "FOLLOWUP_INTERVAL_SECONDS": "0",
    }


def _run(tmp_path: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "scripts" / "ci" / _SCRIPT.name
    return subprocess.run(
        [str(script), *args], env=env, capture_output=True, text=True, check=False
    )


def _calls(tmp_path: Path) -> list[list[str]]:
    path = tmp_path / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _run_queries(tmp_path: Path) -> list[list[str]]:
    return [call for call in _calls(tmp_path) if call[0] == "api"]


def test_a_run_for_the_pushed_sha_ends_the_wait_without_escalating(tmp_path: Path) -> None:
    env = _sandbox(tmp_path, runs=[0, 0, 1], heads=[_BEFORE])

    result = _run(tmp_path, env, "791", _PUSHED, _BEFORE)

    assert result.returncode == 0, result.stderr
    queries = _run_queries(tmp_path)
    assert len(queries) == 3
    for query in queries:
        assert f"head_sha={_PUSHED}" in query[1]
        assert "workflows/pr-pipeline.yml/runs" in query[1]
    assert not any(call[:2] == ["pr", "edit"] for call in _calls(tmp_path))
    assert not (tmp_path / "body").exists()


def test_no_run_on_a_stranded_head_escalates_after_the_bounded_wait(tmp_path: Path) -> None:
    env = _sandbox(tmp_path, runs=[0], heads=[_BEFORE])

    result = _run(tmp_path, env, "791", _PUSHED, _BEFORE)

    assert result.returncode == 1
    assert len(_run_queries(tmp_path)) == 4
    assert ["pr", "edit", "791", "--add-label", "needs-human:pipeline"] in _calls(tmp_path)
    assert (tmp_path / "sticky").read_text().split() == ["pipeline", "791"]
    body = (tmp_path / "body").read_text()
    assert _PUSHED in body
    assert _BEFORE in body
    assert "gh pr close 791" in body
    assert "gh pr reopen 791" in body


def test_a_head_that_took_the_push_without_a_run_still_escalates(tmp_path: Path) -> None:
    env = _sandbox(tmp_path, runs=[0], heads=[_PUSHED])

    result = _run(tmp_path, env, "791", _PUSHED, _BEFORE)

    assert result.returncode == 1
    assert ["pr", "edit", "791", "--add-label", "needs-human:pipeline"] in _calls(tmp_path)


def test_a_newer_head_is_left_to_its_own_run(tmp_path: Path) -> None:
    env = _sandbox(tmp_path, runs=[0], heads=[_BEFORE, _OTHER])

    result = _run(tmp_path, env, "791", _PUSHED, _BEFORE)

    assert result.returncode == 0, result.stderr
    assert _OTHER in result.stdout
    assert len(_run_queries(tmp_path)) == 2
    assert not (tmp_path / "body").exists()


def test_a_run_on_the_final_attempt_prevents_escalation(tmp_path: Path) -> None:
    env = _sandbox(tmp_path, runs=[0, 0, 0, 1], heads=[_BEFORE])

    result = _run(tmp_path, env, "791", _PUSHED, _BEFORE)

    assert result.returncode == 0, result.stderr
    assert len(_run_queries(tmp_path)) == 4
    assert len([call for call in _calls(tmp_path) if call[:2] == ["pr", "view"]]) == 3
    assert not any(call[:2] == ["pr", "edit"] for call in _calls(tmp_path))
    assert not (tmp_path / "body").exists()


@pytest.mark.parametrize("response", ["API_ERROR", "not-json", ""])
def test_unavailable_github_state_cannot_silently_clear_the_watchdog(
    tmp_path: Path, response: str
) -> None:
    env = _sandbox(tmp_path, runs=[0], heads=[response])
    (tmp_path / "runs.json").write_text(json.dumps([response]))

    result = _run(tmp_path, env, "791", _PUSHED, _BEFORE)

    assert result.returncode == 1
    assert len(_run_queries(tmp_path)) == 4
    assert len([call for call in _calls(tmp_path) if call[:2] == ["pr", "view"]]) == 4
    assert ["pr", "edit", "791", "--add-label", "needs-human:pipeline"] in _calls(tmp_path)
    assert "**BLOCKED" in (tmp_path / "body").read_text()


@pytest.mark.parametrize("attempts", [1, 4])
def test_the_wait_sleeps_only_between_attempts(tmp_path: Path, attempts: int) -> None:
    env = _sandbox(tmp_path, runs=[0], heads=[_BEFORE]) | {
        "FOLLOWUP_ATTEMPTS": str(attempts),
        "FOLLOWUP_INTERVAL_SECONDS": "300",
    }
    sleep = tmp_path / "bin" / "sleep"
    sleep.write_text('#!/bin/sh\necho "$1" >> "$GH_STUB_STATE/sleeps"\n')
    sleep.chmod(0o755)

    result = _run(tmp_path, env, "791", _PUSHED, _BEFORE)

    assert result.returncode == 1
    assert len(_run_queries(tmp_path)) == attempts
    sleeps = tmp_path / "sleeps"
    assert (sleeps.read_text().splitlines() if sleeps.exists() else []) == ["300"] * (attempts - 1)


@pytest.mark.parametrize("interval", ["-1", "301", "x", ""])
def test_invalid_intervals_are_refused_before_any_query(tmp_path: Path, interval: str) -> None:
    env = _sandbox(tmp_path, runs=[1], heads=[_BEFORE]) | {"FOLLOWUP_INTERVAL_SECONDS": interval}

    result = _run(tmp_path, env, "791", _PUSHED, _BEFORE)

    assert result.returncode == 2
    assert _calls(tmp_path) == []
    assert not (tmp_path / "body").exists()


@pytest.mark.parametrize("attempts", ["0", "61", "x", ""])
def test_an_unbounded_or_invalid_wait_is_refused_before_any_query(
    tmp_path: Path, attempts: str
) -> None:
    env = _sandbox(tmp_path, runs=[0], heads=[_BEFORE]) | {"FOLLOWUP_ATTEMPTS": attempts}

    result = _run(tmp_path, env, "791", _PUSHED, _BEFORE)

    assert result.returncode == 2
    assert _calls(tmp_path) == []


@pytest.mark.parametrize("args", [("791", "abc", _BEFORE), ("x", _PUSHED, _BEFORE), ("791",)])
def test_malformed_arguments_are_refused_before_any_query(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    env = _sandbox(tmp_path, runs=[1], heads=[_BEFORE])

    result = _run(tmp_path, env, *args)

    assert result.returncode == 2
    assert _calls(tmp_path) == []


def _pipeline() -> dict[str, object]:
    return yaml.safe_load((_ROOT / ".github" / "workflows" / "pr-pipeline.yml").read_text())


def test_the_follow_up_job_waits_only_after_a_push_with_minimum_permissions() -> None:
    jobs = _pipeline()["jobs"]
    job = jobs["follow-up"]

    assert job["needs"] == "codex-tests"
    assert job["if"] == "needs.codex-tests.outputs.pushed == 'true'"
    assert job["permissions"] == {
        "contents": "read",
        "actions": "read",
        "pull-requests": "write",
        "issues": "write",
    }
    wait = next(step for step in job["steps"] if "await_followup_run.sh" in step.get("run", ""))
    assert int(job["timeout-minutes"]) * 60 > int(wait["env"]["FOLLOWUP_ATTEMPTS"]) * int(
        wait["env"]["FOLLOWUP_INTERVAL_SECONDS"]
    )
    assert "${{ needs.codex-tests.outputs.pushed_sha }}" in wait["run"]
    assert "${{ github.event.pull_request.head.sha }}" in wait["run"]
    assert jobs["codex-tests"]["outputs"]["pushed_sha"] == "${{ steps.push.outputs.sha }}"


def test_the_push_step_records_the_sha_it_pushed() -> None:
    push = next(
        step for step in _pipeline()["jobs"]["codex-tests"]["steps"] if step.get("id") == "push"
    )
    script = push["run"]

    assert 'echo "sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"' in script
    assert script.index("push_with_retry.sh") < script.index("sha=$(git rev-parse HEAD)")


def test_ready_still_stands_down_for_the_follow_up_run() -> None:
    """A push hands the verdict to the follow-up run; ready must not claim it here.

    Mutation left the PR pipeline (nightly-mutation.yml), so the guard that was
    on the mutation job now sits on ready itself.
    """
    jobs = _pipeline()["jobs"]

    assert jobs["ready"]["needs"] == ["fast-ci", "codex-tests"]
    assert "needs.codex-tests.outputs.pushed != 'true'" in jobs["ready"]["if"]
