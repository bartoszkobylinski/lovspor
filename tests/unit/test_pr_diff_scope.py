"""Regression coverage for the tests-only author-lane decision (#986)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/ci/pr_diff_scope.py"


def _run(entries: list[object], reported: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), "--changed-files", str(reported)],
        input="\n".join(json.dumps(entry) for entry in entries),
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("entries", "reported", "answer"),
    [
        ([{"filename": "tests/unit/test_one.py"}], 1, "true"),
        ([{"filename": "tests/a.py"}, {"filename": "tests/fixtures/a.xml"}], 2, "true"),
        ([{"filename": "tests/a.py", "previous_filename": "tests/b.py"}], 1, "true"),
        ([{"filename": "tests/a.py"}, {"filename": "src/a.py"}], 2, "false"),
        ([{"filename": "tests/a.py", "previous_filename": "src/a.py"}], 1, "false"),
        ([{"filename": "src/a.py", "previous_filename": "tests/a.py"}], 1, "false"),
        ([{"filename": "tests-extra/a.py"}], 1, "false"),
        ([], 0, "false"),
        ([{"filename": "tests/a.py"}], 2, "false"),
        ([{"filename": "tests/a.py"}], 0, "false"),
    ],
)
def test_scope_requires_complete_tests_only_diff(
    entries: list[object], reported: int, answer: str
) -> None:
    result = _run(entries, reported)
    assert result.returncode == 0
    assert result.stdout == f"tests_only={answer}\n"


@pytest.mark.parametrize("entry", [{}, {"filename": None}, [], "bad"])
def test_invalid_file_record_cannot_earn_skip(entry: object) -> None:
    result = _run([entry], 1)
    assert result.returncode != 0
    assert "tests_only=true" not in result.stdout


def test_malformed_json_cannot_earn_skip() -> None:
    result = subprocess.run(
        [sys.executable, str(HELPER), "--changed-files", "1"],
        input="{broken\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "tests_only=true" not in result.stdout


def test_workflow_connects_scope_to_author_and_defaults_to_running() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/pr-pipeline.yml").read_text())
    jobs = workflow["jobs"]
    scope = jobs["diff-scope"]
    author = jobs["codex-author"]
    scope_condition = " ".join(scope["if"].split())
    author_condition = " ".join(author["if"].split())
    assert author_condition == (
        f"!cancelled() && {scope_condition} && needs.diff-scope.outputs.tests_only != 'true'"
    )
    assert "diff-scope" in author["needs"]
    assert "!cancelled()" in author["if"]
    assert "needs.diff-scope.outputs.tests_only != 'true'" in author["if"]
    assert scope["outputs"]["tests_only"] == "${{ steps.scope.outputs.tests_only }}"
    step = next(step for step in scope["steps"] if step.get("id") == "scope")
    assert step["env"]["CHANGED_FILES"] == "${{ github.event.pull_request.changed_files }}"
    assert 'gh api --paginate "repos/$GH_REPO/pulls/$PR/files"' in step["run"]
    assert "{filename, previous_filename}" in step["run"]
    assert "if [ -x scripts/ci/pr_diff_scope.py ]" in step["run"]
    assert 'echo "tests_only=false"' in step["run"]
    assert "set -euo pipefail" in step["run"]
