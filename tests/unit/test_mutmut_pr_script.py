"""Contract tests for the PR-scoped Mutmut 3 runner."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from mutmut.configuration import config, reset_config

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "mutmut-pr.sh"


def probe(*args: str) -> str:
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=_harness_env(),
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

    def test_setup_has_its_own_allowance_on_top_of_the_files(self) -> None:
        """Issue #622: mutmut's setup over the whole suite outlasted a one-file budget."""
        body = SCRIPT.read_text(encoding="utf-8")

        assert 'setup_budget="${MUTMUT_PR_SETUP_BUDGET_SECONDS:-2400}"' in body
        assert "total_budget=$((setup_budget + file_budget * count))" in body

    def test_budget_kill_is_reported_not_hidden(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8")

        assert '[ "$run_status" -eq 124 ] || [ "$run_status" -eq 137 ]' in body
        assert "mutation budget exceeded:" in body
        assert "budget_exceeded=1" in body


TOTAL_BUDGET = "MUTMUT_PR_TOTAL_BUDGET_SECONDS"


def _harness_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """The child environment for a mutmut-pr.sh run under test (#896).

    The nightly lane exports MUTMUT_PR_TOTAL_BUDGET_SECONDS on the step that runs
    mutmut, and mutmut's stats pass runs this suite with it. Inherited, it replaces
    the setup + per-file budget a test sets, so it is dropped unless the test sets
    it itself (#888 did the same for test_mutation_setup_budget.py).
    """
    env = {name: value for name, value in os.environ.items() if name != TOTAL_BUDGET}
    env.update(overrides or {})
    return env


SILENT_MUTMUT = "#!/bin/sh\nexec sleep 30\n"
NO_SETUP_ALLOWANCE = {"MUTMUT_PR_SETUP_BUDGET_SECONDS": "0"}
SPINNER_BUDGET_SECONDS = 20
SPINNER_MUTMUT = "#!/bin/sh\nprintf 'collecting stats\\r'\n"


def _budget_timeout_stand_in(tmp_path: Path, status: int) -> Path:
    """A timeout(1) whose budget runs out right after the command has finished writing.

    The order "spinner first, then the budget" has to be a fact of the test input:
    a real short budget raced the fake's startup (issue #491), and a fake raising
    SIGALRM at its own timeout(1) raced that process's fork() (issue #878) — in
    coreutils 9.4 a SIGALRM that lands before the parent has stored the child's
    pid makes it _exit(143), not 124. Real expiry stays covered by this class's
    other tests, whose budget is timeout(1)'s own timer.
    """
    bin_dir = tmp_path / "timeout-bin"
    bin_dir.mkdir()
    stand_in = bin_dir / "timeout"
    args_file = tmp_path / "timeout-args"
    stand_in.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$1" "$2" "$3" > \'{args_file}\'\n'
        f'shift 3\n"$@"\nexit {status}\n',
        encoding="utf-8",
    )
    stand_in.chmod(0o755)
    return bin_dir


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
            env=_harness_env({**NO_SETUP_ALLOWANCE, "MUTMUT_PR_FILE_BUDGET_SECONDS": "1"}),
            timeout=60,
            check=False,
        )

    def test_is_reported_as_the_budget_not_as_a_missing_progress_line(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)

        assert "mutation budget exceeded: after 1s" in result.stdout
        assert "no progress line" not in result.stderr
        assert result.returncode == 16

    def test_the_setup_allowance_adds_to_the_files_budget(self, tmp_path: Path) -> None:
        repo = _repo_with_one_changed_function(tmp_path, SILENT_MUTMUT)
        budgets = {"MUTMUT_PR_SETUP_BUDGET_SECONDS": "1", "MUTMUT_PR_FILE_BUDGET_SECONDS": "1"}

        result = subprocess.run(
            ["bash", "scripts/mutmut-pr.sh", "base"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=_harness_env(budgets),
            timeout=60,
            check=False,
        )

        assert "budget: 1s for mutmut's setup + 1s x 1 changed file(s) = 2s" in result.stdout
        assert "mutation budget exceeded: after 2s" in result.stdout
        assert result.returncode == 16

    def test_the_nightly_total_budget_replaces_the_sum(self, tmp_path: Path) -> None:
        """nightly-mutation.yml hands over the seconds left until its 06:00 stop."""
        repo = _repo_with_one_changed_function(tmp_path, SILENT_MUTMUT)
        budgets = {
            "MUTMUT_PR_SETUP_BUDGET_SECONDS": "100",
            "MUTMUT_PR_FILE_BUDGET_SECONDS": "100",
            "MUTMUT_PR_TOTAL_BUDGET_SECONDS": "1",
        }

        result = subprocess.run(
            ["bash", "scripts/mutmut-pr.sh", "base"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=_harness_env(budgets),
            timeout=60,
            check=False,
        )

        assert (
            "budget: 1s wall clock for the whole run (MUTMUT_PR_TOTAL_BUDGET_SECONDS), "
            "1 changed file(s)" in result.stdout
        )
        assert "mutation budget exceeded: after 1s" in result.stdout
        assert result.returncode == 16

    @pytest.mark.parametrize("value", ["0", "-5", "1.5", "soon"])
    def test_a_malformed_total_budget_is_refused(self, tmp_path: Path, value: str) -> None:
        repo = _repo_with_one_changed_function(tmp_path, SILENT_MUTMUT)

        result = subprocess.run(
            ["bash", "scripts/mutmut-pr.sh", "base"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=_harness_env({TOTAL_BUDGET: value}),
            timeout=60,
            check=False,
        )

        assert result.returncode == 2
        assert f"MUTMUT_PR_TOTAL_BUDGET_SECONDS must be a positive whole number, got '{value}'" in (
            result.stderr
        )

    def test_names_every_mutant_unmeasured(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)

        assert "no mutant was measured before the budget ran out" in result.stdout

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


class TestBudgetVerdictAfterSpinner:
    """Issue #365: the verdict gets its own line after the spinner's bare CR.

    No real timeout(1) needed: the budget is a stand-in (issue #878), so this runs
    wherever bash does, macOS without coreutils included.
    """

    # 124: timeout(1)'s TERM ended the run; 137: the --kill-after SIGKILL did.
    @pytest.mark.parametrize("timeout_status", [124, 137])
    def test_budget_verdict_starts_after_spinner_line(
        self, tmp_path: Path, timeout_status: int
    ) -> None:
        repo = _repo_with_one_changed_function(tmp_path, SPINNER_MUTMUT)
        bin_dir = _budget_timeout_stand_in(tmp_path, timeout_status)

        result = subprocess.run(
            ["bash", "scripts/mutmut-pr.sh", "base"],
            cwd=repo,
            capture_output=True,
            env=_harness_env(
                {
                    **NO_SETUP_ALLOWANCE,
                    "MUTMUT_PR_FILE_BUDGET_SECONDS": str(SPINNER_BUDGET_SECONDS),
                    "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                }
            ),
            timeout=60,
            check=False,
        )

        handed_over = (tmp_path / "timeout-args").read_text(encoding="utf-8").split()
        assert handed_over == ["--signal=TERM", "--kill-after=30", str(SPINNER_BUDGET_SECONDS)]
        verdict = f"mutation budget exceeded: after {SPINNER_BUDGET_SECONDS}s".encode()
        assert b"collecting stats\r\n" + verdict in result.stdout
        assert result.returncode == 16


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
        env=_harness_env({"PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}),
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


class TestPerMutantTimeLimit:
    """Issue #423 (owner decision (c)): mutmut's per-mutant wall limit is
    `(estimated_time + timeout_constant) * timeout_multiplier`
    (mutmut 3.8.0 `workers/isolation.py:875`), and `estimated_time` sums only the
    pytest `call` phase. On PR #395 two real survivors ran out of the default
    limit: 0.71 s call phase, 4.75 s setup+teardown, 6.3 s wall locally,
    limit (0.71 + 1) * 15 ≈ 25.7 s. The constant carries the setup time."""

    PR_395_CALL_SECONDS = 0.71
    DEFAULT_LIMIT_SECONDS = (0.71 + 1.0) * 15.0

    @staticmethod
    def _config() -> tuple[float, float]:
        """Read through mutmut's own loader, so a misspelt key cannot pass."""
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(REPO_ROOT)
            reset_config()
            try:
                cfg = config()
                return cfg.timeout_constant, cfg.timeout_multiplier
            finally:
                reset_config()

    def test_the_repo_pins_the_timeout_values(self) -> None:
        assert self._config() == (7.0, 15.0)

    def test_the_pr_395_survivors_get_four_times_the_limit_they_ran_out_of(self) -> None:
        constant, multiplier = self._config()

        limit = (self.PR_395_CALL_SECONDS + constant) * multiplier

        assert limit >= 4 * self.DEFAULT_LIMIT_SECONDS


# `run` is faked (it would run a test suite); `results` and `show` are the
# installed mutmut, reading the mutants the script itself generated.
LEGACY_MUTMUT = (
    "#!/bin/sh\n"
    'if [ "$1" = run ]; then\n'
    '  shift; printf "%s\\n" "$@" > run-args.txt\n'
    f"  printf '%s\\n' '{TALLY}'\n"
    "  exit 0\n"
    "fi\n"
    f'exec "{sys.executable}" -c '
    "'import sys; from mutmut.__main__ import cli; sys.exit(cli())' \"$@\"\n"
)
LEGACY_BASELINE = (
    '[[entry]]\nrule = "function-lines"\npath = "src/lovspor/mod.py"\n'
    'name = "answer"\nvalue = 30\nreason = "legacy"\n'
)


def _repo_with_a_changed_legacy_function(tmp_path: Path) -> Path:
    repo = _repo_with_one_changed_function(tmp_path, LEGACY_MUTMUT)
    # A symlinked interpreter leaves the virtualenv behind, and with it mutmut,
    # which generating the mutants imports.
    python = repo / ".venv" / "bin" / "python"
    python.unlink()
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    legacy = REPO_ROOT / "scripts" / "ci" / "mutation_legacy.py"
    (repo / "scripts" / "ci" / "mutation_legacy.py").write_bytes(legacy.read_bytes())
    (repo / "scripts" / "quality").mkdir()
    (repo / "scripts" / "quality" / "ratchet-baseline.toml").write_text(LEGACY_BASELINE)
    (repo / "pyproject.toml").write_text('[tool.mutmut]\nsource_paths = ["src/lovspor/"]\n')
    module = repo / "src" / "lovspor" / "mod.py"
    module.write_text("def answer(a):\n    x = a + 1\n    return x * 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "legacy base")
    _git(repo, "tag", "-f", "base")
    module.write_text("def answer(a):\n    x = a + 1\n    return x * 3\n", encoding="utf-8")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "touch line 3")
    return repo


def _run_script(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/mutmut-pr.sh", *args, "base"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_harness_env(),
        timeout=180,
        check=False,
    )


class TestLegacyCarveOut:
    """#228: a changed function on the function-lines baseline runs only the
    mutants on its changed lines; the rest is reported, never counted killed."""

    def test_only_the_changed_lines_mutants_are_run(self, tmp_path: Path) -> None:
        repo = _repo_with_a_changed_legacy_function(tmp_path)

        result = _run_script(repo)

        assert result.returncode == 0, result.stdout + result.stderr
        assert (repo / "run-args.txt").read_text(encoding="utf-8").split() == [
            "lovspor.mod.x_answer__mutmut_4",
            "lovspor.mod.x_answer__mutmut_5",
            "--max-children",
            "4",
        ]

    def test_the_remainder_reaches_the_artifact_as_legacy_remainder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _run_script(_repo_with_a_changed_legacy_function(tmp_path))
        monkeypatch.chdir(tmp_path)

        verdict = _mutation_to_json().build_result(
            "0" * 40, result.stdout + result.stderr, result.returncode
        )

        assert verdict["unmeasured_changed_lines"] == [
            "src/lovspor/mod.py:2 (legacy remainder answer: 3 of 5 mutants not run, #228)"
        ]
        assert verdict["mutants"]["total"] == 1
        assert verdict["mutants"]["killed"] == 1

    def test_list_names_the_legacy_function_without_generating_mutants(
        self, tmp_path: Path
    ) -> None:
        repo = _repo_with_a_changed_legacy_function(tmp_path)

        result = _run_script(repo, "--list")

        assert result.returncode == 0, result.stderr
        assert (
            "lovspor.mod.x_answer__mutmut_* (legacy function: only mutants on changed lines 3"
            in (result.stderr)
        )
        assert "scoped to 0 mutation target(s) and 1 legacy function(s)" in result.stdout
        assert not (repo / "mutants").exists()


INHERITED_TOTAL = "3600"  # the nightly lane's kind of value: far above any test's budget
STATS_FAILED_MUTMUT = (
    "#!/bin/sh\n"
    '[ "$1" = run ] || exit 0\n'
    "printf 'failed to collect stats. runner returned 1\\n'\n"
    "exit 1\n"
)


class TestInheritedTotalBudget:
    """#896: an inherited MUTMUT_PR_TOTAL_BUDGET_SECONDS never reaches a harness run.

    Each test sets the variable in this process, as the nightly lane does, and
    expects exactly the result the same test gets without it.
    """

    def test_the_child_environment_drops_it_and_keeps_an_explicit_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TOTAL_BUDGET, INHERITED_TOTAL)

        assert TOTAL_BUDGET not in _harness_env()
        assert _harness_env({TOTAL_BUDGET: "1"})[TOTAL_BUDGET] == "1"
        assert _harness_env({"A": "b"})["A"] == "b"

    @pytest.mark.skipif(shutil.which("timeout") is None, reason="needs timeout(1)")
    @pytest.mark.parametrize("inherited", [None, INHERITED_TOTAL])
    def test_a_budget_kill_is_the_budget_with_or_without_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited: str | None
    ) -> None:
        """Timeout vs missing progress (#365), identical under the nightly environment."""
        if inherited is None:
            monkeypatch.delenv(TOTAL_BUDGET, raising=False)
        else:
            monkeypatch.setenv(TOTAL_BUDGET, inherited)

        result = TestBudgetKillBeforeFirstTally()._run(tmp_path)

        assert "budget: 0s for mutmut's setup + 1s x 1 changed file(s) = 1s" in result.stdout
        assert "mutation budget exceeded: after 1s" in result.stdout
        assert "no progress line" not in result.stderr
        assert result.returncode == 16

    @pytest.mark.skipif(shutil.which("timeout") is None, reason="needs timeout(1)")
    def test_an_explicit_total_still_replaces_the_sum_under_an_inherited_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TOTAL_BUDGET, INHERITED_TOTAL)
        repo = _repo_with_one_changed_function(tmp_path, SILENT_MUTMUT)
        budgets = {
            "MUTMUT_PR_SETUP_BUDGET_SECONDS": "100",
            "MUTMUT_PR_FILE_BUDGET_SECONDS": "100",
            TOTAL_BUDGET: "1",
        }

        result = subprocess.run(
            ["bash", "scripts/mutmut-pr.sh", "base"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=_harness_env(budgets),
            timeout=60,
            check=False,
        )

        assert "budget: 1s wall clock for the whole run" in result.stdout
        assert "mutation budget exceeded: after 1s" in result.stdout
        assert result.returncode == 16

    @pytest.mark.skipif(shutil.which("timeout") is None, reason="needs timeout(1)")
    def test_the_setup_allowance_is_counted_once_under_an_inherited_total(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TOTAL_BUDGET, INHERITED_TOTAL)
        repo = _repo_with_one_changed_function(tmp_path, SILENT_MUTMUT)
        budgets = {"MUTMUT_PR_SETUP_BUDGET_SECONDS": "1", "MUTMUT_PR_FILE_BUDGET_SECONDS": "1"}

        result = subprocess.run(
            ["bash", "scripts/mutmut-pr.sh", "base"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=_harness_env(budgets),
            timeout=60,
            check=False,
        )

        assert "budget: 1s for mutmut's setup + 1s x 1 changed file(s) = 2s" in result.stdout
        assert "mutation budget exceeded: after 2s" in result.stdout

    def test_a_failed_stats_pass_never_reports_a_score(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The shape of the failure #896 hit: mutmut cannot collect stats and exits 1.

        This pins goal item "no score from a failed stats pass"; it is not an isolation
        test (the fake fails either way), which is why it is not parametrized on the
        inherited budget.
        """
        repo = _repo_with_one_changed_function(tmp_path, STATS_FAILED_MUTMUT)

        result = _run_script(repo)
        monkeypatch.chdir(tmp_path)
        verdict = _mutation_to_json().build_result(
            "0" * 40, result.stdout + result.stderr, result.returncode
        )

        assert result.returncode == 3
        assert "no score for this PR — do not report one" in result.stderr
        assert "killed:" not in result.stdout
        assert verdict["gate"] == {"passed": False, "reason": "tool_failed"}
