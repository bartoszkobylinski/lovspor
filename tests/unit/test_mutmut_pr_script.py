"""The PR mutation script's own contract (scripts/mutmut-pr.sh).

The script decides what a mutation score means: which tests a mutant is
measured against, and whether a mutant that breaks the module counts as
killed. Both were wrong at some point and the suite stayed green either
way, so the parts that carry that meaning are pinned here.

The probes (`--tests-for`, `--runner-for`) exist for this: a full run
takes ten minutes, and the decisions worth pinning are made before the
first mutant.
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
        assert probe("--tests-for", "src/lovspor/llhb/fairness.py") == (
            "tests/unit/test_llhb_fairness.py"
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
        runner = probe("--runner-for", "src/lovspor/llhb/fairness.py")

        assert runner.startswith("sh -c '")
        assert runner.endswith("|| exit 1'")

    def test_runs_the_projects_own_interpreter(self) -> None:
        """`uv run` re-resolves the environment per mutant and has produced
        clean sweeps that were artifacts of the tooling."""
        runner = probe("--runner-for", "src/lovspor/llhb/fairness.py")

        assert str(REPO_ROOT / ".venv" / "bin" / "python") in runner
        assert "uv run" not in runner

    def test_measures_the_mutated_file_against_its_own_tests(self) -> None:
        runner = probe("--runner-for", "src/lovspor/llhb/orchestrator.py")

        assert "tests/unit/test_llhb_orchestrator.py" in runner
        assert "-x -q" in runner
