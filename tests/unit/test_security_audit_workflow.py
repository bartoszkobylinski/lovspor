"""Local wiring for the shared security-audit workflow."""

import json
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/security-audit.yml"


def _workflow() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return loaded


def test_audit_runs_for_main_pushes_pull_requests_weekly_and_on_demand() -> None:
    # PyYAML's YAML 1.1 loader interprets the GitHub Actions key `on` as True.
    triggers = _workflow()[True]
    assert set(triggers) == {"push", "pull_request", "schedule", "workflow_dispatch"}
    assert triggers["push"] == {"branches": ["main"]}
    assert triggers["pull_request"] is None
    assert triggers["workflow_dispatch"] is None
    assert triggers["schedule"] == [{"cron": "17 5 * * 1"}]


def test_audit_permissions_are_read_only() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert job.get("permissions", workflow["permissions"]) == {"contents": "read"}


def test_audit_delegates_to_commit_pinned_workflow_on_hosted_runner() -> None:
    (job,) = _workflow()["jobs"].values()
    assert re.fullmatch(
        r"bartoszkobylinski/security-audit/\.github/workflows/audit\.yml@[0-9a-f]{40}",
        job["uses"],
    )
    # The reusable workflow takes its runner as JSON, including string quotes.
    assert json.loads(job["with"]["runner"]) == "ubuntu-latest"
