"""Contract tests for scripts/ci/push_with_retry.sh (#782).

On 2026-11-04 codex-tests on PR #771 passed its tests and then lost the round to
one `remote: Internal Server Error` on the push. The script pushes again, a
bounded number of times, only when GitHub answered with a server error. A
rejection that means something — non-fast-forward, authorization, a protected
branch, a declined hook — is never retried, and neither is an error the script
does not recognise: retrying is the exception, not the default.

`git` is a stub that plays back one scripted answer per call, so the tests pin
the script's decisions, not the network.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_agentic_ci_partial_round import (
    _ESCALATE,
    _SHA,
    _escalation_sandbox,
    _named_step,
    _render,
    _run_step,
)

_SCRIPT = Path(__file__).parents[2] / "scripts" / "ci" / "push_with_retry.sh"

_GIT_STUB = """#!/usr/bin/env python3
import json, os, pathlib, sys
state = pathlib.Path(os.environ["PUSH_STUB_STATE"])
answers = json.loads((state / "answers.json").read_text())
calls = state / "calls.jsonl"
done = sum(1 for _ in calls.open()) if calls.exists() else 0
with calls.open("a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
code, stderr = answers[min(done, len(answers) - 1)]
sys.stderr.write(stderr)
sys.exit(code)
"""

OK = (0, "To https://github.com/o/r\n   1111111..2222222  HEAD -> feat/x\n")
SERVER_500 = (
    1,
    "remote: Internal Server Error\n"
    "To https://github.com/o/r\n"
    " ! [remote rejected]   HEAD -> feat/x (Internal Server Error)\n"
    "error: failed to push some refs to 'https://github.com/o/r'\n",
)
SERVER_502 = (
    1,
    "error: RPC failed; HTTP 502 curl 22 The requested URL returned error: 502\n"
    "fatal: the remote end hung up unexpectedly\n",
)
SERVER_503 = (
    1,
    "fatal: unable to access 'https://github.com/o/r/': The requested URL returned error: 503\n",
)
NON_FAST_FORWARD = (
    1,
    "To https://github.com/o/r\n"
    " ! [rejected]        HEAD -> feat/x (fetch first)\n"
    "error: failed to push some refs to 'https://github.com/o/r'\n"
    "hint: Updates were rejected because the remote contains work that you do not\n",
)
FORBIDDEN = (
    1,
    "remote: Permission to o/r.git denied to github-actions[bot].\n"
    "fatal: unable to access 'https://github.com/o/r/': The requested URL returned error: 403\n",
)
UNAUTHORIZED = (1, "fatal: Authentication failed for 'https://github.com/o/r/'\n")
PROTECTED = (
    1,
    "remote: error: GH006: Protected branch update failed for refs/heads/main.\n"
    " ! [remote rejected] HEAD -> main (protected branch hook declined)\n",
)
HOOK = (1, " ! [remote rejected] HEAD -> feat/x (pre-receive hook declined)\n")
WORKFLOW_SCOPE = (
    1,
    " ! [remote rejected] HEAD -> feat/x (refusing to allow a GitHub App to create or "
    "update workflow `.github/workflows/x.yml` without `workflows` permission)\n",
)
UNKNOWN = (1, "fatal: something nobody has seen before\n")


def _run(
    tmp_path: Path,
    answers: list[tuple[int, str]],
    attempts: str = "3",
    delay: str | None = "0",
) -> tuple[int, str, list[list[str]]]:
    state = tmp_path / "state"
    bin_dir = tmp_path / "bin"
    state.mkdir()
    bin_dir.mkdir()
    (state / "answers.json").write_text(json.dumps(answers))
    stub = bin_dir / "git"
    stub.write_text(_GIT_STUB)
    stub.chmod(0o755)
    sleep_stub = bin_dir / "sleep"
    sleep_stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$PUSH_STUB_STATE/sleeps"\n')
    sleep_stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PUSH_STUB_STATE": str(state),
        "PUSH_RETRY_ATTEMPTS": attempts,
    }
    env.pop("PUSH_RETRY_DELAY_SECONDS", None)
    if delay is not None:
        env["PUSH_RETRY_DELAY_SECONDS"] = delay
    completed = subprocess.run(
        ["bash", str(_SCRIPT), "origin", "HEAD:feat/x"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls_file = state / "calls.jsonl"
    calls = (
        [json.loads(line) for line in calls_file.read_text().splitlines()]
        if calls_file.exists()
        else []
    )
    return completed.returncode, completed.stderr, calls


def test_a_clean_push_is_pushed_once(tmp_path: Path) -> None:
    code, _, calls = _run(tmp_path, [OK])

    assert code == 0
    assert calls == [["push", "origin", "HEAD:feat/x"]]


@pytest.mark.parametrize("server", [SERVER_500, SERVER_502, SERVER_503], ids=["500", "502", "503"])
def test_a_server_error_is_pushed_again_and_can_succeed(
    tmp_path: Path, server: tuple[int, str]
) -> None:
    code, stderr, calls = _run(tmp_path, [server, OK])

    assert code == 0
    assert len(calls) == 2
    assert all(call == ["push", "origin", "HEAD:feat/x"] for call in calls)
    assert "GitHub server error on push attempt 1 of 3; retrying" in stderr


def test_server_errors_stop_at_the_bound_and_say_so(tmp_path: Path) -> None:
    code, stderr, calls = _run(tmp_path, [SERVER_500, SERVER_500, SERVER_500, OK])

    assert code == 2
    assert len(calls) == 3
    assert "push failed: GitHub answered with a server error on all 3 attempts" in stderr


@pytest.mark.parametrize(
    "refusal",
    [NON_FAST_FORWARD, FORBIDDEN, UNAUTHORIZED, PROTECTED, HOOK, WORKFLOW_SCOPE, UNKNOWN],
    ids=["non-fast-forward", "403", "auth", "protected", "hook", "workflow-scope", "unknown"],
)
def test_a_meaningful_rejection_is_never_retried(tmp_path: Path, refusal: tuple[int, str]) -> None:
    code, stderr, calls = _run(tmp_path, [refusal, OK])

    assert code == 1
    assert len(calls) == 1
    assert "push failed and was not retried: not a GitHub server error" in stderr


def test_a_rejection_after_a_server_error_stops_at_once(tmp_path: Path) -> None:
    code, _, calls = _run(tmp_path, [SERVER_500, NON_FAST_FORWARD, OK])

    assert code == 1
    assert len(calls) == 2


@pytest.mark.parametrize(
    "refusal",
    [NON_FAST_FORWARD, FORBIDDEN, UNAUTHORIZED, PROTECTED, HOOK, WORKFLOW_SCOPE],
    ids=["non-fast-forward", "403", "auth", "protected", "hook", "workflow-scope"],
)
def test_a_server_error_mixed_with_a_policy_refusal_is_not_retried(
    tmp_path: Path, refusal: tuple[int, str]
) -> None:
    mixed = (1, SERVER_500[1] + refusal[1])

    code, _, calls = _run(tmp_path, [mixed, OK])

    assert code == 1
    assert len(calls) == 1
    assert not (tmp_path / "state" / "sleeps").exists()


def test_default_backoff_waits_fifteen_then_thirty_seconds(tmp_path: Path) -> None:
    code, _, calls = _run(tmp_path, [SERVER_500], delay=None)

    assert code == 2
    assert len(calls) == 3
    assert (tmp_path / "state" / "sleeps").read_text().splitlines() == ["15", "30"]


def test_one_attempt_never_sleeps_or_pushes_again(tmp_path: Path) -> None:
    code, _, calls = _run(tmp_path, [SERVER_500, OK], attempts="1")

    assert code == 2
    assert calls == [["push", "origin", "HEAD:feat/x"]]
    assert not (tmp_path / "state" / "sleeps").exists()


@pytest.mark.parametrize("delay", ["-1", "1.5", "invalid"])
def test_invalid_delay_is_refused_before_any_push(tmp_path: Path, delay: str) -> None:
    code, stderr, calls = _run(tmp_path, [OK], delay=delay)

    assert code == 1
    assert calls == []
    assert "PUSH_RETRY_DELAY_SECONDS must be a whole number" in stderr
    assert not (tmp_path / "state" / "sleeps").exists()


def test_gits_own_output_reaches_the_log(tmp_path: Path) -> None:
    _, stderr, _ = _run(tmp_path, [SERVER_500, OK])

    assert "(Internal Server Error)" in stderr


@pytest.mark.parametrize("attempts", ["0", "-1", "x", "11"])
def test_a_bound_outside_one_to_ten_is_refused_before_any_push(
    tmp_path: Path, attempts: str
) -> None:
    code, stderr, calls = _run(tmp_path, [OK], attempts=attempts)

    assert code == 1
    assert calls == []
    assert "PUSH_RETRY_ATTEMPTS" in stderr


def _escalation_body(tmp_path: Path, push_outcome: str | None) -> str:

    env = _escalation_sandbox(tmp_path)
    if push_outcome is not None:
        env["PUSH_OUTCOME"] = push_outcome
    values = {
        "github.event.pull_request.number": "771",
        "github.event.pull_request.head.sha": _SHA,
        "github.server_url": "https://github.com",
        "github.repository": "o/r",
        "github.run_id": "41809225667",
    }
    step = _named_step("codex-tests", _ESCALATE)
    assert step["env"]["PUSH_OUTCOME"] == "${{ steps.push.outcome }}"
    result = _run_step(_render(step["run"], values), tmp_path, env)
    assert result.returncode == 0, result.stderr
    return (tmp_path / "body").read_text(encoding="utf-8")


def test_the_codex_tests_push_goes_through_the_retry_script() -> None:

    push = _named_step("codex-tests", "Commit and push test additions")["run"]

    ref = '"HEAD:${{ github.event.pull_request.head.ref }}"'
    guarded = (
        "if [ -x scripts/ci/push_with_retry.sh ]; then\n"
        f"  scripts/ci/push_with_retry.sh origin {ref}\n"
        "else\n"
        f"  git push origin {ref}\n"
        "fi\n"
    )
    assert guarded in push
    assert push.count("git push") == 1
    assert push.index("push_with_retry.sh") < push.index('echo "pushed=true"')


def test_a_failed_push_is_reported_as_after_the_tests_passed(tmp_path: Path) -> None:
    body = _escalation_body(tmp_path, "failure")

    assert "codex-tests BLOCKED after the tests passed: pushing the commit" in body
    assert "before the tests ran" not in body


@pytest.mark.parametrize("outcome", [None, "", "success", "skipped"])
def test_any_other_pipeline_failure_keeps_its_wording(tmp_path: Path, outcome: str | None) -> None:
    body = _escalation_body(tmp_path, outcome)

    assert "codex-tests BLOCKED before the tests ran" in body
    assert "after the tests passed" not in body
