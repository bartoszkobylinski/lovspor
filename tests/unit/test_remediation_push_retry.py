"""The mutation-remediation push retries a GitHub server error (#805).

`git push` ran once in `Commit and push, or report BLOCKED`: a superseded head
was benign, and anything else, a transient 5xx included, failed the job and lost
a green remediation round, the #782 class that `push_with_retry.sh` fixed for
codex-tests. The push block of the step runs here as written, against a `git`
stub that plays back one scripted answer per call.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml  # type: ignore[import-untyped]

_ROOT = Path(__file__).parents[2]
_HEAD = "a" * 40
_OTHER = "b" * 40
_GIT_STUB = """#!/usr/bin/env python3
import json, os, pathlib, sys
state = pathlib.Path(os.environ["GIT_STUB_STATE"])
answers = json.loads((state / "answers.json").read_text())
calls = state / "calls.jsonl"
done = sum(1 for _ in calls.open()) if calls.exists() else 0
with calls.open("a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
code, out, err = answers[min(done, len(answers) - 1)]
sys.stdout.write(out)
sys.stderr.write(err)
sys.exit(code)
"""
_OK = (0, "", "To https://github.com/o/r\n")
_SERVER_502 = (1, "", "error: RPC failed; HTTP 502 curl 22 The requested URL returned error: 502\n")
_REJECTED = (1, "", " ! [rejected]        HEAD -> feat/x (fetch first)\n")


def _push_block() -> str:
    workflow = yaml.safe_load(
        (_ROOT / ".github" / "workflows" / "mutation-remediation.yml").read_text()
    )
    step = next(
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == "Commit and push, or report BLOCKED"
    )
    run = step["run"]
    return run[run.index("# Issue #67") : run.index("# Progress, not an escalation")]


def _run(tmp_path: Path, answers: list[tuple[int, str, str]]) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text(_GIT_STUB)
    git.chmod(0o755)
    (tmp_path / "answers.json").write_text(json.dumps(answers))
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GIT_STUB_STATE": str(tmp_path),
        "HEAD_BRANCH": "feat/x",
        "HEAD_SHA": _HEAD,
        "PUSH_RETRY_DELAY_SECONDS": "0",
    }
    return subprocess.run(
        ["bash", "-e", "-c", _push_block()],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _pushes(tmp_path: Path) -> list[list[str]]:
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    return [call for call in calls if call[0] == "push"]


def test_a_server_error_on_the_remediation_push_is_retried(tmp_path: Path) -> None:
    result = _run(tmp_path, [_SERVER_502, _OK])

    assert result.returncode == 0, result.stderr
    assert _pushes(tmp_path) == [["push", "origin", "HEAD:feat/x"]] * 2


def test_a_superseded_head_is_still_abandoned_quietly(tmp_path: Path) -> None:
    result = _run(tmp_path, [_REJECTED, (0, "", ""), (0, f"{_OTHER}\n", "")])

    assert result.returncode == 0, result.stderr
    assert "abandoning superseded result" in result.stdout
    assert len(_pushes(tmp_path)) == 1


def test_a_rejection_on_the_same_head_still_fails_the_job(tmp_path: Path) -> None:
    result = _run(tmp_path, [_REJECTED, (0, "", ""), (0, f"{_HEAD}\n", "")])

    assert result.returncode == 1
    assert len(_pushes(tmp_path)) == 1


def test_server_errors_on_every_attempt_fail_the_job(tmp_path: Path) -> None:
    result = _run(
        tmp_path, [_SERVER_502, _SERVER_502, _SERVER_502, (0, "", ""), (0, f"{_HEAD}\n", "")]
    )

    assert result.returncode == 1
    assert len(_pushes(tmp_path)) == 3
