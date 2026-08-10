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

    def test_puts_a_guard_directory_first_on_path(self) -> None:
        runner = probe("--runner-for", "src/lovspor/llhb/fairness.py")

        assert "PATH=" in runner
        assert runner.split("PATH=", 1)[1].startswith("/")

    def test_the_guard_actually_refuses_to_execute(self) -> None:
        """A mutant that breaks the orchestrator's env isolation otherwise
        inherits this PATH and makes a live, subscription-billed call. The
        stub has to run and fail, not merely exist."""
        assert probe("--check-guard").startswith("guard: blocks the real provider CLI")

    def test_measures_the_mutated_file_against_its_own_tests(self) -> None:
        runner = probe("--runner-for", "src/lovspor/llhb/orchestrator.py")

        assert "tests/unit/test_llhb_orchestrator.py" in runner
        assert "-x -q" in runner


class TestResolutionSafety:
    """Resolving a suspicious mutant means applying it to a source file.

    Doing that in the working tree put a reviewer's uncommitted work one
    interrupted script away from a mutated or reverted file, so the whole
    step moved into a throwaway worktree at HEAD. What follows checks the
    script says so and never names the repository's own source path.
    """

    def test_resolution_never_writes_to_the_working_tree(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")
        resolve = body.split("resolve_suspicious() {", 1)[1].split("\n}", 1)[0]

        assert "$resolve_tree" in resolve
        assert 'git -C "$repo_root" checkout' not in resolve
        assert "$repo_root/src" not in resolve

    def test_the_throwaway_tree_is_a_worktree_at_head(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert 'worktree add --detach -q "$resolve_tree" HEAD' in body

    def test_cleanup_runs_on_interruption_too(self) -> None:
        """An EXIT-only trap leaves the worktree and the stub behind when
        the run is killed, which is how the previous version leaked."""
        body = SCRIPT.read_text(encoding="utf-8")

        statements = [line for line in body.splitlines() if line.startswith("trap ")]

        assert statements == ["trap cleanup EXIT INT TERM"]

    def test_imports_resolve_to_the_copy_not_the_original(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert "PYTHONPATH=$resolve_tree/src" in body

    def test_the_resolution_path_can_be_exercised_without_a_full_run(self) -> None:
        """A suspicious verdict only appears under load, so the machinery
        would otherwise be reported as working without ever having run."""
        body = SCRIPT.read_text(encoding="utf-8")

        assert "--resolve F ID" in body
        assert 'resolve_suspicious "$resolve_probe" "$resolve_probe_id"' in body
