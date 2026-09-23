"""The local quality gates (issue #323, docs/decisions.md §9d).

`scripts/quality/verify-fast.sh` is the commit-time gate and
`scripts/quality/verify-deep.sh` the push-time one (the fast gate, then the
fail-closed security scan, then the unit suite). Both run here for real,
with stub `uv` and `gitleaks` first on PATH. A stub records its command line and
the directory it ran in, and fails when told to, so every verdict below comes
from the scripts' own control flow rather than from reading their text.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FAST = REPO_ROOT / "scripts" / "quality" / "verify-fast.sh"
DEEP = REPO_ROOT / "scripts" / "quality" / "verify-deep.sh"

FAST_CHECKS = {
    "gitleaks": "gitleaks git --pre-commit --redact --staged --verbose",
    "ruff-check": "uv run ruff check",
    "ruff-format": "uv run ruff format --check",
    "mypy": "uv run mypy src/",
    "ratchets": "uv run python scripts/quality/check_ratchets.py",
}
# `-m "not network"`: a test that needs a live third-party credential answers
# for the operator's key, not for the change being pushed (issue #359).
UNIT_SUITE = "uv run pytest tests/unit/ -q -m not network"
SECURITY_SCAN = "uv run python scripts/quality/check_security_scan.py"
STUB_TOOLS = ("uv", "gitleaks")

# Logs "<cwd>\t<command>\t<hook git variables or unset>"; a command listed in GATE_STUB_FAIL
# fails, with its last line coloured the way gitleaks colours its log even into
# a pipe. The third field is how a test sees whether a hook's Git environment
# reached the check (issues #369, #370).
_STUB = """#!/bin/sh
line="@TOOL@ $*"
hook_env="${GIT_DIR-unset},${GIT_WORK_TREE-unset},${GIT_INDEX_FILE-unset},${GIT_PREFIX-unset}"
printf '%s\\t%s\\t%s\\n' "$(pwd -P)" "$line" "$hook_env" >> "$GATE_STUB_LOG"
if [ "@TOOL@" = uv ] && [ "${1-}" = run ] && [ "${2-}" = pytest ]; then
  printf 'pytest argc=%s marker=<%s>\\n' "$#" "${6-}"
fi
if printf '%s\\n' "$GATE_STUB_FAIL" | grep -Fqx "$line"; then
  echo "detail for $line"
  printf '\\033[31mstub: %s failed\\033[0m\\n\\n' "$line"
  exit 3
fi
echo "stub: $line ok"
"""


class GateRun(NamedTuple):
    returncode: int
    output: str
    commands: list[str]
    cwds: set[str]
    hook_git_envs: set[str]

    def fail_lines(self) -> list[str]:
        return [line for line in self.output.splitlines() if line.startswith("FAIL ")]


def _sandbox_env(
    tmp_path: Path, failing: tuple[str, ...], tools: tuple[str, ...], git_dir: str | None = None
) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in tools:
        stub = bin_dir / tool
        stub.write_text(_STUB.replace("@TOOL@", tool), encoding="utf-8")
        stub.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "GATE_STUB_LOG": str(tmp_path / "commands.log"),
        "GATE_STUB_FAIL": "\n".join(failing),
    }
    if git_dir is not None:
        env["GIT_DIR"] = git_dir
        env["GIT_WORK_TREE"] = str(tmp_path / "worktree")
        env["GIT_INDEX_FILE"] = str(tmp_path / "index")
        env["GIT_PREFIX"] = "nested/"
    return env


def _run_gate(
    script: Path,
    tmp_path: Path,
    failing: tuple[str, ...] = (),
    tools: tuple[str, ...] = STUB_TOOLS,
    git_dir: str | None = None,
) -> GateRun:
    """Run a gate from a directory outside the repository."""
    env = _sandbox_env(tmp_path, failing, tools, git_dir)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = subprocess.run(
        [str(script)], cwd=elsewhere, env=env, check=False, text=True, capture_output=True
    )
    log = Path(env["GATE_STUB_LOG"])
    records = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    rows = [record.split("\t") for record in records]
    return GateRun(
        returncode=result.returncode,
        output=result.stdout + result.stderr,
        commands=[command for _, command, _ in rows],
        cwds={cwd for cwd, _, _ in rows},
        hook_git_envs={git_env for _, _, git_env in rows},
    )


class TestFastGate:
    def test_runs_every_fast_check_from_the_repo_root_whatever_the_cwd(
        self, tmp_path: Path
    ) -> None:
        run = _run_gate(FAST, tmp_path)

        assert run.returncode == 0, run.output
        assert run.commands == list(FAST_CHECKS.values())
        assert run.cwds == {str(REPO_ROOT)}
        assert run.fail_lines() == []
        assert run.output.rstrip().endswith("verify-fast: all checks passed")

    def test_never_runs_the_unit_suite(self, tmp_path: Path) -> None:
        """~256 s of tests on every commit is exactly what issue #323 moved out."""
        run = _run_gate(FAST, tmp_path)

        assert [command for command in run.commands if "pytest" in command] == []

    def test_runs_the_ratchet_at_commit_time(self, tmp_path: Path) -> None:
        """A function or file that crosses a CLAUDE.md limit is cheapest to
        split in the commit that wrote it, not one push or one review later."""
        run = _run_gate(FAST, tmp_path)

        assert FAST_CHECKS["ratchets"] in run.commands

    def test_never_runs_the_security_scan(self, tmp_path: Path) -> None:
        """It belongs to the push gate; the commit loop stays about a second."""
        run = _run_gate(FAST, tmp_path)

        assert SECURITY_SCAN not in run.commands

    @pytest.mark.parametrize(("name", "command"), sorted(FAST_CHECKS.items()))
    def test_a_failing_check_fails_the_gate_in_one_line_naming_it(
        self, tmp_path: Path, name: str, command: str
    ) -> None:
        run = _run_gate(FAST, tmp_path, failing=(command,))

        assert run.returncode != 0
        assert run.fail_lines() == [f"FAIL {name}: stub: {command} failed (exit 3)"]

    def test_the_tool_output_stays_visible_for_the_repair(self, tmp_path: Path) -> None:
        run = _run_gate(FAST, tmp_path, failing=(FAST_CHECKS["mypy"],))

        assert f"detail for {FAST_CHECKS['mypy']}" in run.output

    def test_a_failure_does_not_stop_the_remaining_checks(self, tmp_path: Path) -> None:
        run = _run_gate(FAST, tmp_path, failing=(FAST_CHECKS["gitleaks"],))

        assert set(FAST_CHECKS.values()) <= set(run.commands)

    def test_every_failure_is_reported_not_only_the_first(self, tmp_path: Path) -> None:
        failing = (FAST_CHECKS["ruff-check"], FAST_CHECKS["mypy"])

        run = _run_gate(FAST, tmp_path, failing=failing)

        assert run.returncode != 0
        assert [line.partition(":")[0] for line in run.fail_lines()] == [
            "FAIL ruff-check",
            "FAIL mypy",
        ]

    def test_a_missing_tool_fails_closed(self, tmp_path: Path) -> None:
        """A scanner that is not installed has scanned nothing."""
        run = _run_gate(FAST, tmp_path, tools=("uv",))

        assert run.returncode != 0
        [line] = run.fail_lines()
        assert line.startswith("FAIL gitleaks: ")
        assert "not found" in line

    def test_the_hooks_git_dir_never_reaches_a_check(self, tmp_path: Path) -> None:
        """Git exports GIT_DIR to a hook; from a worktree that is
        .git/worktrees/<name>, and a check that inherits it — the unit suite
        spawning git in temp directories — works on the real repository
        (issues #369, #370)."""
        run = _run_gate(FAST, tmp_path, git_dir=str(tmp_path / ".git" / "worktrees" / "x"))

        assert run.returncode == 0, run.output
        assert run.hook_git_envs == {"unset,unset,unset,unset"}

    def test_the_failure_line_carries_no_terminal_colour_codes(self, tmp_path: Path) -> None:
        """Real gitleaks colours its log even into a pipe; the summary line is
        read by agents and in CI logs, where escape codes are noise."""
        run = _run_gate(FAST, tmp_path, failing=(FAST_CHECKS["gitleaks"],))

        assert run.fail_lines() != []
        assert "\x1b" not in "".join(run.fail_lines())


class TestDeepGate:
    def test_runs_the_fast_gate_then_the_security_scan_then_the_unit_suite(
        self, tmp_path: Path
    ) -> None:
        run = _run_gate(DEEP, tmp_path)

        assert run.returncode == 0, run.output
        assert run.commands == [*FAST_CHECKS.values(), SECURITY_SCAN, UNIT_SUITE]
        assert run.cwds == {str(REPO_ROOT)}
        assert run.output.rstrip().endswith("verify-deep: all checks passed")

    def test_passes_the_network_marker_expression_as_one_pytest_argument(
        self, tmp_path: Path
    ) -> None:
        """Without shell quoting, pytest treats `network` as a test path."""
        run = _run_gate(DEEP, tmp_path)

        assert "pytest argc=6 marker=<not network>" in run.output

    def test_a_failing_security_scan_fails_the_gate_naming_it(self, tmp_path: Path) -> None:
        """A scanner that skipped files reports through this gate or nowhere."""
        run = _run_gate(DEEP, tmp_path, failing=(SECURITY_SCAN,))

        assert run.returncode != 0
        assert run.fail_lines() == [f"FAIL security-scan: stub: {SECURITY_SCAN} failed (exit 3)"]

    def test_a_failing_unit_suite_fails_the_gate_naming_it(self, tmp_path: Path) -> None:
        run = _run_gate(DEEP, tmp_path, failing=(UNIT_SUITE,))

        assert run.returncode != 0
        assert run.fail_lines() == [f"FAIL unit-suite: stub: {UNIT_SUITE} failed (exit 3)"]

    def test_a_failing_fast_gate_stops_before_the_unit_suite(self, tmp_path: Path) -> None:
        run = _run_gate(DEEP, tmp_path, failing=(FAST_CHECKS["mypy"],))

        assert run.returncode != 0
        assert run.fail_lines() == [f"FAIL mypy: stub: {FAST_CHECKS['mypy']} failed (exit 3)"]
        assert UNIT_SUITE not in run.commands
        assert SECURITY_SCAN not in run.commands
