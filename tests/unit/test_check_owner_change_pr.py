"""The pin-file pull-request check, the author skip, and their wiring in CI."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts/ci/check_owner_change_pr.py"
SCOPE = ROOT / "scripts/ci/pr_diff_scope.py"
PIN = "ops/lovverk-owner-changes"


def _check(
    tmp_path: Path, entries: list[object], reported: int, subjects: list[str]
) -> subprocess.CompletedProcess[str]:
    files = tmp_path / "files.jsonl"
    files.write_text("\n".join(json.dumps(entry) for entry in entries), encoding="utf-8")
    subject_file = tmp_path / "subjects.txt"
    subject_file.write_text("\n".join(subjects), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(CHECK),
            "--changed-files",
            str(reported),
            "--files",
            str(files),
            "--subjects",
            str(subject_file),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_pin_file_alone_with_owner_commits_passes(tmp_path: Path) -> None:
    result = _check(tmp_path, [{"filename": PIN}], 1, ["ops: pin lovverk#42", "ops: re-pin"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{PIN}: changed alone\n"


def test_pull_request_without_pin_file_passes(tmp_path: Path) -> None:
    entries = [{"filename": "src/lovspor/cli.py"}, {"filename": "tests/unit/test_x.py"}]
    result = _check(tmp_path, entries, 2, ["test: x [agent:codex-tests]"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{PIN}: not changed\n"


@pytest.mark.parametrize(
    "entries",
    [
        [{"filename": PIN}, {"filename": "src/lovspor/cli.py"}],
        [{"filename": PIN}, {"filename": ".github/workflows/sync.yml"}],
        [{"filename": PIN}, {"filename": "tests/unit/test_owner_changes.py"}],
    ],
)
def test_pin_file_with_other_files_is_refused(tmp_path: Path, entries: list[object]) -> None:
    result = _check(tmp_path, entries, len(entries), ["ops: pin"])
    assert result.returncode == 1
    assert f"refused: {PIN} must be the only file changed: " in result.stderr


@pytest.mark.parametrize(
    "entry",
    [
        {"filename": PIN, "previous_filename": "ops/other"},
        {"filename": "ops/other", "previous_filename": PIN},
    ],
)
def test_rename_into_or_out_of_pin_file_is_refused(tmp_path: Path, entry: object) -> None:
    result = _check(tmp_path, [entry], 1, ["ops: move"])
    assert result.returncode == 1
    assert "must be the only file changed" in result.stderr


@pytest.mark.parametrize(
    "subject",
    [
        "test: add coverage for PR #9 [agent:codex-tests]",
        "test: kill survivors [agent:claude-mutation]",
        "anything [agent:x]  ",
    ],
)
def test_agent_commit_on_pin_pull_request_is_refused(tmp_path: Path, subject: str) -> None:
    result = _check(tmp_path, [{"filename": PIN}], 1, ["ops: pin", subject])
    assert result.returncode == 1
    assert "automated agent commit on a pin-file pull request" in result.stderr


def test_agent_word_inside_subject_is_not_a_stamp(tmp_path: Path) -> None:
    result = _check(tmp_path, [{"filename": PIN}], 1, ["ops: pin after [agent:x] review note"])
    assert result.returncode == 0, result.stderr


def test_truncated_listing_with_pin_file_is_refused(tmp_path: Path) -> None:
    result = _check(tmp_path, [{"filename": PIN}], 2, ["ops: pin"])
    assert result.returncode == 1


def test_truncated_listing_without_pin_file_is_refused(tmp_path: Path) -> None:
    """A listing shorter than reported may hide the pin file."""
    result = _check(tmp_path, [{"filename": "src/a.py"}], 3001, ["feat: big"])
    assert result.returncode == 1
    assert "listed 1 changed files" in result.stderr


@pytest.mark.parametrize("entry", [{}, {"filename": None}, [], "bad"])
def test_bad_input_exits_2(tmp_path: Path, entry: object) -> None:
    result = _check(tmp_path, [entry], 1, [])
    assert result.returncode == 2


def _scope(entries: list[object], reported: int) -> str:
    return subprocess.run(
        [sys.executable, str(SCOPE), "--changed-files", str(reported)],
        input="\n".join(json.dumps(entry) for entry in entries),
        text=True,
        capture_output=True,
        check=True,
    ).stdout


@pytest.mark.parametrize(
    ("entries", "reported", "answer"),
    [
        ([{"filename": PIN}], 1, "true"),
        ([{"filename": PIN}], 2, "false"),
        ([{"filename": PIN}, {"filename": "src/a.py"}], 2, "false"),
        ([{"filename": PIN, "previous_filename": "ops/x"}], 1, "false"),
        ([{"filename": "tests/a.py"}], 1, "false"),
        ([], 0, "false"),
    ],
)
def test_scope_says_pin_only(entries: list[object], reported: int, answer: str) -> None:
    assert f"pin_only={answer}\n" in _scope(entries, reported)


def _pipeline() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github/workflows/pr-pipeline.yml").read_text(encoding="utf-8")
    )
    return loaded


def test_fast_ci_runs_both_pin_checks() -> None:
    fast = _pipeline()["jobs"]["fast-ci"]
    assert fast["permissions"] == {"contents": "read", "pull-requests": "read"}
    steps = {step.get("name"): step for step in fast["steps"]}
    grammar = steps["Owner-change pin file follows the grammar"]
    assert "if" not in grammar
    assert (
        grammar["run"] == "uv run lovspor ops owner-changes check --file ops/lovverk-owner-changes"
    )
    alone = steps["Owner-change pin file is changed alone"]
    assert alone["if"] == "github.event_name == 'pull_request'"
    assert "set -euo pipefail" in alone["run"]
    assert "scripts/ci/check_owner_change_pr.py" in alone["run"]
    assert '"repos/$GH_REPO/pulls/$PR/commits"' in alone["run"]
    names = [step.get("name") for step in fast["steps"]]
    assert names.index("Owner-change pin file is changed alone") < names.index("Unit tests")


def test_author_is_not_started_for_a_pin_only_pull_request() -> None:
    jobs = _pipeline()["jobs"]
    assert jobs["diff-scope"]["outputs"]["pin_only"] == "${{ steps.scope.outputs.pin_only }}"
    assert "needs.diff-scope.outputs.pin_only != 'true'" in jobs["codex-author"]["if"]
    step = next(s for s in jobs["diff-scope"]["steps"] if s.get("id") == "scope")
    assert 'echo "pin_only=false"' in step["run"]


def test_pin_file_is_codeowned() -> None:
    owners = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8").splitlines()
    owned = {line.split()[0] for line in owners if line.strip() and not line.startswith("#")}
    assert "/ops/lovverk-owner-changes" in owned
