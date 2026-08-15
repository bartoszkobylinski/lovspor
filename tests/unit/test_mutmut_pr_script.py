"""The PR mutation script's own contract (scripts/mutmut-pr.sh).

The script decides what a mutation score means: which tests a mutant is
measured against, and whether a mutant that breaks the module counts as
killed. Both were wrong at some point and the suite stayed green either
way, so the parts that carry that meaning are pinned here.

The probes (`--tests-for`, `--runner-for`, `--check-guard`) exist for
this: a full run takes ten minutes, and the decisions worth pinning are
made before the first mutant.
"""

import shlex
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
        words = shlex.split(shlex.split(probe("--runner-for", "src/lovspor/llhb/results.py"))[2])

        assert words[0].startswith("PATH=/")
        assert words[0].endswith(":$PATH")

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
    def test_runner_paths_survive_shell_word_splitting(self) -> None:
        """A checkout under a directory with a space otherwise splits the
        runner into two words. The runner then fails to start, and mutmut
        reads that failure as a killed mutant — on every mutant of the run."""
        words = shlex.split(shlex.split(probe("--runner-for", "src/lovspor/llhb/results.py"))[2])

        assert words[1] == str(REPO_ROOT / ".venv" / "bin" / "python")
        assert words[-4:] == ["tests/unit/test_llhb_results.py", "||", "exit", "1"]

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


class TestHostilePaths:
    def build_checkout(self, root: Path) -> Path:
        """A checkout whose path contains an apostrophe, with a stub
        interpreter standing in for .venv/bin/python."""
        checkout = root / "it's a dir"
        (checkout / ".venv" / "bin").mkdir(parents=True)
        (checkout / "scripts").mkdir()
        (checkout / "tests" / "unit").mkdir(parents=True)
        (checkout / "scripts" / "mutmut-pr.sh").write_text(
            SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
        )
        interpreter = checkout / ".venv" / "bin" / "python"
        interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        interpreter.chmod(0o755)
        (checkout / "tests" / "unit" / "test_llhb_results.py").touch()
        subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
        return checkout

    def test_the_runner_survives_an_apostrophe_in_the_checkout_path(self, tmp_path: Path) -> None:
        """mutmut splits the runner with shlex before exec, so a path that
        breaks the quoting makes the runner fail to start — and mutmut reads
        a runner that fails to start as a killed mutant, on every mutant of
        the run. A broken path would read as a perfect score."""
        checkout = self.build_checkout(tmp_path)

        runner = subprocess.run(
            ["bash", "scripts/mutmut-pr.sh", "--runner-for", "src/lovspor/llhb/results.py"],
            capture_output=True,
            text=True,
            cwd=checkout,
            check=True,
        ).stdout.strip()

        argv = shlex.split(runner, posix=True)
        assert argv[:2] == ["sh", "-c"]
        assert len(argv) == 3
        assert subprocess.run(argv, capture_output=True, check=False).returncode == 0


class TestUnkilledBuckets:
    def test_timeouts_are_counted_and_named(self) -> None:
        """mutmut's exit code 4 is a timeout, not an untested mutant. A
        timed-out mutant hung the tests rather than failing them, so it is a
        gap in the suite — leaving the bucket uncounted reported
        `survived: 0, suspicious: 0` and exit 0 for a run full of them."""
        body = SCRIPT.read_text(encoding="utf-8")

        assert "for bucket in killed survived timeout suspicious skipped untested; do" in body
        assert "4 timed out" in body
        assert probe("--score-for", "0", "0", "2", "0", "0", "0").splitlines()[2] == (
            "timed out:  2"
        )


class TestLongOutput:
    def test_the_diff_extractor_survives_a_long_producer(self) -> None:
        """`head` closes the pipe once it has its lines, the producer takes
        SIGPIPE, and under `pipefail` that killed the whole script with 141
        — losing the score over a survivor whose diff happened to be long."""
        extractor = SCRIPT.read_text(encoding="utf-8").split("awk '", 1)[1].split("' \\", 1)[0]
        script = f"set -euo pipefail; seq 1 200000 | sed 's/^/+/' | awk '{extractor}'"

        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)

        assert result.returncode == 0
        assert len(result.stdout.splitlines()) == 6

    def test_no_pipeline_closes_its_producer_early(self) -> None:
        code = [
            line
            for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]

        assert not [line for line in code if "| head " in line]
        assert any("set -euo pipefail" in line for line in code)


class TestExitCode:
    """mutmut bit-ORs its statuses (2 survived, 4 timed out, 8 suspicious)
    precisely so one run can report several at once. Returning only the
    first non-empty bucket hid the rest from anything reading the code: a
    run with survivors and timeouts came back 4, and a caller checking for
    2 concluded nothing had survived."""

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
    def test_every_non_empty_bucket_sets_its_bit(
        self, survived: int, timed_out: int, suspicious: int, expected: str
    ) -> None:
        assert probe("--exit-code-for", str(survived), str(timed_out), str(suspicious)) == expected

    @pytest.mark.parametrize(
        ("survived", "timed_out", "suspicious", "budget", "expected"),
        [
            (0, 0, 0, 1, "16"),
            (6, 0, 0, 1, "18"),
            (0, 1, 0, 1, "20"),
            (6, 1, 1, 1, "30"),
            (6, 0, 0, 0, "2"),
        ],
    )
    def test_a_budget_cut_sets_a_bit_of_its_own(
        self, survived: int, timed_out: int, suspicious: int, budget: int, expected: str
    ) -> None:
        """Bit 16 is the script's own, above mutmut's 2/4/8 (issue #102): a
        wall-clock budget cut the run short, so even a clean-looking bucket
        count must not exit 0 — the score is not over the whole surface."""
        assert (
            probe("--exit-code-for", str(survived), str(timed_out), str(suspicious), str(budget))
            == expected
        )


class TestFileBudget:
    """Issue #102: mutating a large module (mcp.py) against its own test
    module cannot finish in CI time, and the old behaviour was a 6 h job
    kill with no verdict. The budget machinery must cut the file, surface
    the gap, and keep harvesting — pinned at source level here, the
    exit-code half through the probes above."""

    def test_the_run_is_wrapped_in_a_wallclock_timeout(self) -> None:
        text = SCRIPT.read_text()

        assert "MUTMUT_PR_FILE_BUDGET_SECONDS" in text
        assert "--signal=TERM" in text
        assert "--kill-after=30" in text

    def test_a_budget_kill_is_a_marker_not_a_fatal(self) -> None:
        """124/137 come from timeout(1), not mutmut — routing them into the
        fatal branch would discard the buckets the run DID measure."""
        text = SCRIPT.read_text()

        assert '[ "$run_status" -eq 124 ] || [ "$run_status" -eq 137 ]' in text
        assert "mutation budget exceeded:" in text
        assert "budget_exceeded=1" in text

    def test_the_final_exit_carries_the_budget_flag(self) -> None:
        text = SCRIPT.read_text()

        assert (
            'exit_code_for "$survived_total" "$timeout_total" "$suspicious_total"'
            ' "$budget_exceeded"' in text
        )


class TestScoreReport:
    """AGENTS.md asks for X/Y killed. Without the killed bucket the script
    left that number to be assembled by hand from mutmut's per-file
    progress lines, which is exactly where a reported score goes wrong."""

    def test_reports_killed_over_every_mutant_the_run_produced(self) -> None:
        lines = probe("--score-for", "45", "6", "0", "0", "0", "0").splitlines()

        assert lines[0] == "killed:     45 / 51"

    def test_the_denominator_counts_every_bucket(self) -> None:
        lines = probe("--score-for", "10", "2", "1", "1", "4", "3").splitlines()

        assert lines[0] == "killed:     10 / 21"

    def test_states_each_unkilled_bucket_separately(self) -> None:
        report = probe("--score-for", "10", "2", "1", "1", "0", "0")

        assert "survived:   2" in report
        assert "timed out:  1" in report
        assert "suspicious: 1" in report

    def test_an_untested_bucket_says_the_score_is_partial(self) -> None:
        """Untested mutants were never measured, so the ratio above them is
        not over the whole surface — silence there would overstate it."""
        report = probe("--score-for", "10", "2", "0", "0", "0", "3")

        assert "untested:   3" in report
        assert "not over the whole surface" in report

    def test_a_clean_run_says_nothing_about_empty_buckets(self) -> None:
        report = probe("--score-for", "51", "0", "0", "0", "0", "0")

        assert report.startswith("killed:     51 / 51")
        assert "untested" not in report
        assert "skipped" not in report
