"""Contract tests for the PR-scoped Mutmut 3 runner."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "mutmut-pr.sh"


def probe(*args: str) -> str:
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    return result.stdout.strip()


class TestMutantPatternMapping:
    def test_maps_nested_module_without_src_prefix(self) -> None:
        assert probe("--patterns-for", "src/lovspor/llhb/results.py") == ("lovspor.llhb.results.*")

    def test_maps_top_level_module(self) -> None:
        assert probe("--patterns-for", "src/lovspor/mcp.py") == "lovspor.mcp.*"

    def test_script_scopes_real_runs_by_changed_function(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert "scripts/ci/mutation_scope.py" in body
        assert '"${patterns[@]}"' in body
        assert "--paths-to-mutate" not in body
        assert "--runner=" not in body


class TestRunner:
    def test_uses_mutmut_console_script(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert 'mutmut_bin="$repo_root/.venv/bin/mutmut"' in body
        assert '"$mutmut_bin" run' in body
        assert 'python_bin" -m mutmut' not in body

    def test_shadow_tree_bridges_virtualenv_without_copying_it(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert 'ln -sfn "../.venv" "mutants/.venv"' in body
        assert "mkdir -p mutants/data" in body

    def test_the_guard_actually_refuses_to_execute(self) -> None:
        assert probe("--check-guard").startswith("guard: blocks the real provider CLI")


class TestCleanup:
    def test_one_trap_covers_interruption(self) -> None:
        statements = [
            line
            for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if line.startswith("trap ")
        ]

        assert len(statements) == 1
        assert statements[0].endswith("EXIT INT TERM")


class TestFailureHandling:
    def test_missing_progress_is_never_a_clean_run(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert "mutmut produced no progress line" in body
        assert "no score for this PR — do not report one" in body
        assert "exit 3" in body

    def test_no_pipeline_closes_its_producer_early(self) -> None:
        code = [
            line
            for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]

        assert not [line for line in code if "| head " in line]
        assert any("set -euo pipefail" in line for line in code)


class TestExitCode:
    @pytest.mark.parametrize(
        ("survived", "timed_out", "suspicious", "expected"),
        [
            (0, 0, 0, "0"),
            (6, 0, 0, "2"),
            (0, 1, 0, "4"),
            (0, 0, 1, "8"),
            (6, 1, 0, "6"),
            (6, 0, 1, "10"),
            (0, 1, 1, "12"),
            (6, 1, 1, "14"),
        ],
    )
    def test_non_empty_buckets_set_their_bits(
        self,
        survived: int,
        timed_out: int,
        suspicious: int,
        expected: str,
    ) -> None:
        assert probe("--exit-code-for", str(survived), str(timed_out), str(suspicious)) == expected

    @pytest.mark.parametrize(
        ("survived", "timed_out", "suspicious", "budget", "expected"),
        [
            (0, 0, 0, 1, "16"),
            (6, 0, 0, 1, "18"),
            (0, 1, 0, 1, "20"),
            (6, 1, 1, 1, "30"),
        ],
    )
    def test_budget_cut_sets_its_own_bit(
        self,
        survived: int,
        timed_out: int,
        suspicious: int,
        budget: int,
        expected: str,
    ) -> None:
        assert (
            probe(
                "--exit-code-for",
                str(survived),
                str(timed_out),
                str(suspicious),
                str(budget),
            )
            == expected
        )


class TestBudget:
    def test_run_has_a_wallclock_budget(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert "MUTMUT_PR_FILE_BUDGET_SECONDS" in body
        assert "--signal=TERM" in body
        assert "--kill-after=30" in body

    def test_budget_kill_is_reported_not_hidden(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert '[ "$run_status" -eq 124 ] || [ "$run_status" -eq 137 ]' in body
        assert "mutation budget exceeded:" in body
        assert "budget_exceeded=1" in body


SILENT_MUTMUT = "#!/bin/sh\nexec sleep 30\n"
SPINNER_MUTMUT = "#!/bin/sh\nprintf 'collecting stats\\r'\nexec sleep 30\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo_with_one_changed_function(tmp_path: Path, mutmut: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts" / "ci").mkdir(parents=True)
    (repo / "src" / "lovspor").mkdir(parents=True)
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / "scripts" / "mutmut-pr.sh").write_bytes(SCRIPT.read_bytes())
    scope = REPO_ROOT / "scripts" / "ci" / "mutation_scope.py"
    (repo / "scripts" / "ci" / "mutation_scope.py").write_bytes(scope.read_bytes())
    (repo / ".venv" / "bin" / "python").symlink_to(sys.executable)
    fake = repo / ".venv" / "bin" / "mutmut"
    fake.write_text(mutmut, encoding="utf-8")
    fake.chmod(0o755)
    module = repo / "src" / "lovspor" / "mod.py"
    module.write_text("def answer() -> int:\n    return 1\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "scripts", "src")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    _git(repo, "tag", "base")
    module.write_text("def answer() -> int:\n    return 2\n", encoding="utf-8")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "change")
    return repo


@pytest.mark.skipif(shutil.which("timeout") is None, reason="needs timeout(1)")
class TestBudgetKillBeforeFirstTally:
    """Issue #365: the budget can expire during stats or the clean-test pass."""

    def _run(self, tmp_path: Path) -> subprocess.CompletedProcess[str]:
        repo = _repo_with_one_changed_function(tmp_path, SILENT_MUTMUT)
        return subprocess.run(
            ["bash", "scripts/mutmut-pr.sh", "base"],
            cwd=repo,
            capture_output=True,
            text=True,
            env={**os.environ, "MUTMUT_PR_FILE_BUDGET_SECONDS": "1"},
            timeout=60,
            check=False,
        )

    def test_is_reported_as_the_budget_not_as_a_missing_progress_line(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)

        assert "mutation budget exceeded: after 1s" in result.stdout
        assert "no progress line" not in result.stderr
        assert result.returncode == 16

    def test_names_every_mutant_unmeasured(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)

        assert "no mutant was measured before the budget ran out" in result.stdout

    def test_budget_verdict_starts_after_spinner_line(self, tmp_path: Path) -> None:
        repo = _repo_with_one_changed_function(tmp_path, SPINNER_MUTMUT)

        result = subprocess.run(
            ["bash", "scripts/mutmut-pr.sh", "base"],
            cwd=repo,
            capture_output=True,
            env={**os.environ, "MUTMUT_PR_FILE_BUDGET_SECONDS": "1"},
            timeout=60,
            check=False,
        )

        assert b"collecting stats\r\nmutation budget exceeded: after 1s" in result.stdout

    def test_the_gate_reads_it_as_the_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._run(tmp_path)
        monkeypatch.chdir(tmp_path)

        verdict = _mutation_to_json().build_result(
            "0" * 40, result.stdout + result.stderr, result.returncode
        )

        assert verdict["gate"] == {"passed": False, "reason": "budget_exceeded"}


def _mutation_to_json() -> ModuleType:
    path = REPO_ROOT / "scripts" / "ci" / "mutation_to_json.py"
    spec = importlib.util.spec_from_file_location("mutation_to_json", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestScoreReport:
    def test_reports_killed_over_all_buckets(self) -> None:
        lines = probe("--score-for", "10", "2", "1", "1", "4", "3").splitlines()

        assert lines[0] == "killed:     10 / 21"
        assert "survived:   2" in lines
        assert "timed out:  1" in lines
        assert "suspicious: 1" in lines
        assert "untested:   3" in lines[-1]
        assert "not over the whole surface" in lines[-1]


class TestUnmeasuredMutants:
    def test_shortfall_excludes_every_printed_bucket_and_type_checked_mutants(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert (
            "unmeasured=$((done_count - killed - no_tests - timed_out - suspicious "
            "- survived - skipped - type_checked))"
        ) in body
        assert 'if [ "$unmeasured" -gt 0 ]; then' in body
        assert (
            'echo "unmeasured: $unmeasured  — signal-killed (mutmut: segfault), no verdict"' in body
        )


EDITABLE_PTH = Path(".venv/lib/python3.12/site-packages/_editable_impl_lovspor.pth")
TALLY = "1/1  🎉 1 🫥 0  ⏰ 0  🤔 0  🙁 0  🔇 0  🧙 0"
REPOINTING_MUTMUT = (
    "#!/bin/sh\n"
    '[ "$1" = run ] || exit 0\n'
    f'printf %s "$PWD/mutants/src" > "{EDITABLE_PTH}"\n'
    f"printf '%s\\n' '{TALLY}'\n"
)
REPOINTING_FAILED_MUTMUT = REPOINTING_MUTMUT + "exit 23\n"
REPAIRING_UV = (
    f'#!/bin/sh\nprintf "%s\\n" "$*" >> uv-calls.log\nprintf %s "$PWD/src" > "{EDITABLE_PTH}"\n'
)
BROKEN_UV = '#!/bin/sh\nprintf "%s\\n" "$*" >> uv-calls.log\nexit 1\n'


def _run_with_uv(
    tmp_path: Path, mutmut: str, uv: str
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    repo = _repo_with_one_changed_function(tmp_path, mutmut)
    (repo / EDITABLE_PTH).parent.mkdir(parents=True)
    (repo / EDITABLE_PTH).write_text(str(repo / "src"), encoding="utf-8")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "uv").write_text(uv, encoding="utf-8")
    (stubs / "uv").chmod(0o755)
    result = subprocess.run(
        ["bash", "scripts/mutmut-pr.sh", "base"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"},
        timeout=60,
        check=False,
    )
    return repo, result


class TestEditableInstallAfterRun:
    """Issue #400: a run can re-point .venv's editable install at mutants/src."""

    def test_a_repointed_install_is_repaired_and_said_so(self, tmp_path: Path) -> None:
        repo, result = _run_with_uv(tmp_path, REPOINTING_MUTMUT, REPAIRING_UV)

        assert (repo / EDITABLE_PTH).read_text(encoding="utf-8") == str(repo / "src")
        assert (repo / "uv-calls.log").read_text(encoding="utf-8") == (
            "sync --frozen --reinstall-package lovspor\n"
        )
        assert f"re-pointed .venv's editable lovspor install at {repo}/mutants/src" in (
            result.stderr
        )
        assert "repaired: .venv imports lovspor from" in result.stderr
        assert result.returncode == 0

    def test_an_unrepairable_install_fails_the_run(self, tmp_path: Path) -> None:
        repo, result = _run_with_uv(tmp_path, REPOINTING_MUTMUT, BROKEN_UV)

        assert f"error: .venv still imports lovspor from {repo}/mutants/src" in result.stderr
        assert "killed:" not in result.stdout
        assert result.returncode == 3

    def test_a_failed_mutmut_run_still_repairs_the_install(self, tmp_path: Path) -> None:
        repo, result = _run_with_uv(tmp_path, REPOINTING_FAILED_MUTMUT, REPAIRING_UV)

        assert (repo / EDITABLE_PTH).read_text(encoding="utf-8") == str(repo / "src")
        assert (repo / "uv-calls.log").read_text(encoding="utf-8") == (
            "sync --frozen --reinstall-package lovspor\n"
        )
        assert "error: mutmut run failed (exit 23)" in result.stderr
        assert "no score for this PR — do not report one" in result.stderr
        assert result.returncode == 3

    def test_an_intact_install_is_left_alone(self, tmp_path: Path) -> None:
        intact = REPOINTING_MUTMUT.replace("/mutants/src", "/src")
        repo, result = _run_with_uv(tmp_path, intact, BROKEN_UV)

        assert not (repo / "uv-calls.log").exists()
        assert "killed:     1 / 1" in result.stdout
        assert result.returncode == 0
