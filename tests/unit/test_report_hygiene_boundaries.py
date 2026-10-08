"""Git-state boundaries of the report allowlists documented in agentic-ci.md."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / ".gitignore").write_text(".agent-reports/\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "commit", "--quiet", "-m", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def _write(repo: Path, relative: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("report\n", encoding="utf-8")


def _run(repo: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("GITHUB_STEP_SUMMARY", None)
    return subprocess.run(
        [str(ROOT / script), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("state", ["committed", "staged", "unstaged", "untracked"])
@pytest.mark.parametrize("outside", ["tests/unit/run.md", "tests/integration/test_new.py"])
def test_remediation_allowlist_rejects_every_git_state(
    tmp_path: Path, state: str, outside: str
) -> None:
    """The documented lane allowlist applies to the whole agent round."""
    repo, base = _repo(tmp_path)
    _write(repo, outside)
    if state != "untracked":
        _git(repo, "add", outside)
    if state in {"committed", "unstaged"}:
        _git(repo, "commit", "--quiet", "-m", "report")
    if state == "unstaged":
        base = _git(repo, "rev-parse", "HEAD")
        (repo / outside).write_text("changed report\n", encoding="utf-8")
    _write(repo, "tests/unit/test_allowed.py")

    result = _run(repo, "scripts/ci/assert_codex_scope.sh", base, "remediation")

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"  {outside}\n" in result.stderr
    assert "tests/unit/test_allowed.py" not in result.stderr


def test_force_staged_ignored_report_is_still_a_scope_violation(tmp_path: Path) -> None:
    """Gitignore keeps untracked reports out, but cannot exempt staged files."""
    repo, base = _repo(tmp_path)
    report = ".agent-reports/mutation-remediation-report.md"
    _write(repo, report)
    _git(repo, "add", "--force", report)

    result = _run(repo, "scripts/ci/assert_codex_scope.sh", base, "remediation")

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"  {report}\n" in result.stderr


def test_tests_tree_gate_checks_tracked_reports_only_and_lists_all(tmp_path: Path) -> None:
    """The new gate promises tracked-file hygiene, including paths with spaces."""
    repo, _ = _repo(tmp_path)
    reports = ("tests/unit/run notes.md", "tests/integration/another.log")
    for report in reports:
        _write(repo, report)
    _write(repo, "tests/unit/test_allowed.py")
    _write(repo, "tests/unit/fixtures/run notes.md")
    _git(repo, "add", "tests/unit/test_allowed.py", "tests/unit/fixtures/run notes.md")

    untracked = _run(repo, "scripts/quality/check_tests_tree.sh")

    assert untracked.returncode == 0, untracked.stdout + untracked.stderr
    _git(repo, "add", *reports)

    tracked = _run(repo, "scripts/quality/check_tests_tree.sh")

    assert tracked.returncode == 1, tracked.stdout + tracked.stderr
    for report in reports:
        assert f"  {report}\n" in tracked.stdout
    assert "tests/unit/test_allowed.py" not in tracked.stdout
    assert "tests/unit/fixtures/run notes.md" not in tracked.stdout
    assert "::error::tracked files under tests/" in tracked.stdout
