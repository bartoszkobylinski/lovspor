"""Contract tests for the PR-scoped Mutmut 3 runner."""

import subprocess
from pathlib import Path

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


class TestScoreReport:
    def test_reports_killed_over_all_buckets(self) -> None:
        lines = probe("--score-for", "10", "2", "1", "1", "4", "3").splitlines()

        assert lines[0] == "killed:     10 / 21"
        assert "survived:   2" in lines
        assert "timed out:  1" in lines
        assert "suspicious: 1" in lines
        assert "untested:   3" in lines[-1]
        assert "not over the whole surface" in lines[-1]
