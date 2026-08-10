"""The PR mutation script's own contract (scripts/mutmut-pr.sh).

The script decides what a mutation score means: which tests a mutant is
measured against, and whether a mutant that breaks the module counts as
killed. Both were wrong at some point and the suite stayed green either
way, so the parts that carry that meaning are pinned here.

The probes (`--tests-for`, `--runner-for`, `--check-guard`) exist for
this: a full run takes ten minutes, and the decisions worth pinning are
made before the first mutant.
"""

import subprocess
from pathlib import Path

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


class TestTestModuleMapping:
    def test_maps_a_module_to_its_own_test_file(self) -> None:
        assert probe("--tests-for", "src/lovspor/llhb/results.py") == (
            "tests/unit/test_llhb_results.py"
        )

    def test_maps_a_top_level_module(self) -> None:
        assert probe("--tests-for", "src/lovspor/mcp.py") == "tests/unit/test_mcp.py"

    def test_falls_back_to_the_whole_unit_suite(self) -> None:
        """Silently running nothing would score every mutant of that file
        as survived, which reads as a test gap that is not there."""
        assert probe("--tests-for", "src/lovspor/nowhere/absent.py") == "tests/unit/"


class TestRunner:
    def test_collapses_every_failure_onto_exit_one(self) -> None:
        """mutmut reads "tests passed" as `returncode != 1`, so a mutant
        that breaks the module makes pytest exit 2 and would be filed as
        survived. Without this wrapper the score flatters the suite."""
        runner = probe("--runner-for", "src/lovspor/llhb/results.py")

        assert runner.startswith("sh -c '")
        assert runner.endswith("|| exit 1'")

    def test_runs_the_projects_own_interpreter(self) -> None:
        """`uv run` re-resolves the environment per mutant and has produced
        clean sweeps that were artifacts of the tooling."""
        runner = probe("--runner-for", "src/lovspor/llhb/results.py")

        assert str(REPO_ROOT / ".venv" / "bin" / "python") in runner
        assert "uv run" not in runner

    def test_measures_the_mutated_file_against_its_own_tests(self) -> None:
        runner = probe("--runner-for", "src/lovspor/llhb/results.py")

        assert "tests/unit/test_llhb_results.py" in runner
        assert "-x -q" in runner

    def test_puts_a_guard_directory_first_on_path(self) -> None:
        runner = probe("--runner-for", "src/lovspor/llhb/results.py")

        assert 'PATH="' in runner
        assert runner.split('PATH="', 1)[1].startswith("/")

    def test_the_guard_actually_refuses_to_execute(self) -> None:
        """A mutant that breaks a subprocess call's env isolation otherwise
        inherits this PATH and makes a live, subscription-billed call. The
        stub has to run and fail, not merely exist."""
        assert probe("--check-guard").startswith("guard: blocks the real provider CLI")


class TestCleanup:
    def test_one_trap_covers_interruption_too(self) -> None:
        """An EXIT-only trap leaves the stub directory behind when the run
        is killed, and a second `trap ... EXIT` replaces the first rather
        than adding to it — which is how the leak happened."""
        statements = [
            line
            for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if line.startswith("trap ")
        ]

        assert len(statements) == 1
        assert statements[0].endswith("EXIT INT TERM")


class TestFailureHandling:
    def test_runner_paths_are_quoted(self) -> None:
        """A checkout under a directory with a space otherwise splits the
        runner into two words. The runner then fails to start, and mutmut
        reads that failure as a killed mutant — on every mutant of the run."""
        runner = probe("--runner-for", "src/lovspor/llhb/results.py")

        assert 'PATH="' in runner
        assert f'"{REPO_ROOT / ".venv" / "bin" / "python"}"' in runner
        assert '"tests/unit/test_llhb_results.py"' in runner

    def test_a_failing_mutmut_is_not_reported_as_a_clean_run(self) -> None:
        """mutmut encodes verdicts in its exit code (2 survived, 4 untested,
        8 suspicious). Swallowing every non-zero status reported
        `survived: 0, suspicious: 0` for a run that measured nothing."""
        body = SCRIPT.read_text(encoding="utf-8")

        assert "0 | 2 | 4 | 6 | 8 | 10 | 12 | 14" in body
        assert "no score for this PR — do not report one" in body
        mutation_loop = body.split("while IFS= read -r file; do", 2)[2]
        assert "|| true" not in mutation_loop

    def test_result_ids_failure_stops_the_run(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert 'if ! ids="$("$python_bin" -m mutmut result-ids "$bucket")"; then' in body
