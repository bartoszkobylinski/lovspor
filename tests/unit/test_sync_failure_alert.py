"""A nightly sync that fails or is killed says so in an issue (#790).

On 2026-10-20 and 10-21 the keyed sync ran into the 120-minute job timeout
and was cancelled. The paid embeddings were lost, and nothing told anyone,
so the second night paid again. The alert step's real shell runs here
against a `gh` stub that records each call.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sync.yml"
_ALERT = "Report a sync that did not complete"
_GH_STUB = """#!/usr/bin/env python3
import json, os, pathlib, sys
state = pathlib.Path(os.environ["GH_STUB_STATE"])
with (state / "calls.jsonl").open("a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["issue", "list"]:
    print((state / "existing").read_text())
"""


def _job() -> dict[str, Any]:
    job: dict[str, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["sync"]
    return job


def _step(name: str) -> dict[str, Any]:
    return next(step for step in _job()["steps"] if step.get("name") == name)


def _run_alert(tmp_path: Path, existing: str, outcome: str | None = "failure") -> list[list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(_GH_STUB)
    gh.chmod(0o755)
    (tmp_path / "existing").write_text(existing)
    step = _step(_ALERT)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_STUB_STATE": str(tmp_path),
        "GITHUB_REPOSITORY": "o/r",
        "RUN_URL": "https://github.com/o/r/actions/runs/7",
    }
    if outcome is not None:
        env["SYNC_OUTCOME"] = outcome
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]


def test_the_alert_runs_when_the_job_fails_or_is_cancelled() -> None:
    step = _step(_ALERT)

    assert step["if"] == "failure() || cancelled()"
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert step["env"]["SYNC_OUTCOME"] == "${{ steps.sync.outcome }}"
    assert "continue-on-error" not in step
    assert _job()["steps"][-1]["name"] == _ALERT


def test_the_sync_step_times_out_before_the_job_so_the_alert_still_runs() -> None:
    sync = _step("Run lovspor sync")

    assert int(sync["timeout-minutes"]) <= int(_job()["timeout-minutes"]) - 10


def test_a_first_failure_files_an_issue_naming_the_run(tmp_path: Path) -> None:
    calls = _run_alert(tmp_path, existing="")

    [create] = [call for call in calls if call[:2] == ["issue", "create"]]
    body = (tmp_path / "sync-alert.md").read_text()
    assert "https://github.com/o/r/actions/runs/7" in body
    assert "`failure`" in body
    assert "#790" in body
    assert "--title" in create
    assert not any(call[:2] == ["issue", "comment"] for call in calls)


def test_a_repeat_failure_comments_on_the_open_issue_instead_of_filing_another(
    tmp_path: Path,
) -> None:
    calls = _run_alert(tmp_path, existing="812")

    assert ["issue", "comment", "812", "--repo", "o/r", "--body-file", "sync-alert.md"] in calls
    assert not any(call[:2] == ["issue", "create"] for call in calls)


@pytest.mark.parametrize("outcome", ["cancelled", "", None])
def test_alert_reports_cancelled_or_unreached_sync(tmp_path: Path, outcome: str | None) -> None:
    """The operations contract includes cancellation before sync was reached."""
    calls = _run_alert(tmp_path, existing="", outcome=outcome)

    body = (tmp_path / "sync-alert.md").read_text()
    expected = outcome or "not reached"
    assert f"`Run lovspor sync` outcome: `{expected}`" in body
    assert "https://github.com/o/r/actions/runs/7" in body
    assert sum(call[:2] == ["issue", "create"] for call in calls) == 1


def test_alert_looks_up_the_open_issue_by_title_in_the_engine_repo(tmp_path: Path) -> None:
    """Repeat alerts must find the issue created by the first failure."""
    calls = _run_alert(tmp_path, existing="")

    [lookup, create] = calls
    assert lookup == [
        "issue",
        "list",
        "--repo",
        "o/r",
        "--state",
        "open",
        "--limit",
        "100",
        "--json",
        "number,title",
        "--jq",
        '[.[] | select(.title == "Nightly sync did not complete")][0].number // empty',
    ]
    assert create == [
        "issue",
        "create",
        "--repo",
        "o/r",
        "--title",
        "Nightly sync did not complete",
        "--body-file",
        "sync-alert.md",
    ]


def test_alert_run_link_is_wired_to_the_current_workflow_run() -> None:
    assert _step(_ALERT)["env"]["RUN_URL"] == (
        "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
    )
