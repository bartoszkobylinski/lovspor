"""The git hook configuration carries the quality gates; it holds no policy
(issue #323, docs/decisions.md §9d).

`.pre-commit-config.yaml` names no check itself. The commit stage runs
`scripts/quality/verify-fast.sh` and the push stage `scripts/quality/verify-deep.sh`,
so a hook and a manual run of the same script cannot disagree. Which script
each stage reaches is decided by pre-commit itself, run against the real config
in a scratch repository, not by re-implementing pre-commit's stage rules here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
GATE_SCRIPTS = ("scripts/quality/verify-fast.sh", "scripts/quality/verify-deep.sh")

# Stands in for a gate script in the scratch repository: records the hook reached it.
_RECORDER = """#!/bin/sh
basename "$0" >> "$GATE_HOOK_LOG"
"""


def _hooks() -> list[dict[str, Any]]:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return [hook for repo in config["repos"] for hook in repo["hooks"]]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _pre_commit(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PRE_COMMIT_HOME": str(repo.parent / "pre-commit-home"),
        "GATE_HOOK_LOG": str(repo.parent / "hooks.log"),
    }
    return subprocess.run(
        [sys.executable, "-m", "pre_commit", *args],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _scratch_repo(tmp_path: Path) -> Path:
    """A git repository with the real hook config and recording gate scripts."""
    repo = tmp_path / "repo"
    (repo / "scripts" / "quality").mkdir(parents=True)
    _git(repo, "init", "--quiet")
    shutil.copy(CONFIG, repo / ".pre-commit-config.yaml")
    for script in GATE_SCRIPTS:
        recorder = repo / script
        recorder.write_text(_RECORDER, encoding="utf-8")
        recorder.chmod(0o755)
    _git(repo, "add", ".")
    return repo


def _scripts_reached_at(tmp_path: Path, stage: str) -> list[str]:
    repo = _scratch_repo(tmp_path)

    result = _pre_commit(repo, "run", "--all-files", "--hook-stage", stage)

    assert result.returncode == 0, result.stdout + result.stderr
    log = tmp_path / "hooks.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


class TestStages:
    def test_the_commit_stage_runs_the_fast_gate_and_nothing_else(self, tmp_path: Path) -> None:
        assert _scripts_reached_at(tmp_path, "pre-commit") == ["verify-fast.sh"]

    def test_the_push_stage_runs_the_deep_gate_and_nothing_else(self, tmp_path: Path) -> None:
        """The unit suite left the commit stage for this one; it did not leave."""
        assert _scripts_reached_at(tmp_path, "pre-push") == ["verify-deep.sh"]

    def test_a_plain_install_installs_both_hook_types(self, tmp_path: Path) -> None:
        """Without this, `pre-commit install` would quietly leave the push
        stage, and with it the unit suite, uninstalled."""
        repo = _scratch_repo(tmp_path)

        result = _pre_commit(repo, "install")

        assert result.returncode == 0, result.stdout + result.stderr
        assert (repo / ".git" / "hooks" / "pre-commit").is_file()
        assert (repo / ".git" / "hooks" / "pre-push").is_file()


class TestTransportOnly:
    def test_every_hook_entry_is_an_executable_gate_script(self) -> None:
        """A check written straight into the config would run at commit time but
        not in `verify-fast.sh`, which is the drift this layout rules out."""
        entries = sorted(str(hook["entry"]) for hook in _hooks())

        assert entries == sorted(GATE_SCRIPTS)
        for entry in entries:
            assert os.access(REPO_ROOT / entry, os.X_OK), entry
