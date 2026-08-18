"""Gate policy tests for scripts/ci/mutation_to_json.py and mutation_gate.py.

The gate preserves the existing 2/4/8 verdict bits over Mutmut 3's output:
each unkilled class fails, and suspicious mutants never inflate the score.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest
from mutmut.__main__ import Config as MutmutConfig
from mutmut.__main__ import copy_also_copy_files

_SCRIPTS = Path(__file__).parents[2] / "scripts" / "ci"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mutation_to_json = _load("mutation_to_json")
mutation_gate = _load("mutation_gate")
mutation_survivors = _load("mutation_survivors")
codex_account_failover = _load("codex_account_failover")

FULL_SHA = "a" * 40
SCOPE_GUARD = _SCRIPTS / "assert_codex_scope.sh"


def _progress_line(
    *,
    killed: int = 0,
    no_tests: int = 0,
    timeout: int = 0,
    suspicious: int = 0,
    survived: int = 0,
) -> str:
    total = killed + no_tests + timeout + suspicious + survived
    return (
        f"{total}/999  🎉 {killed} 🫥 {no_tests}  ⏰ {timeout}  "
        f"🤔 {suspicious}  🙁 {survived}  🔇 0  🧙 0\n"
    )


def _run(
    tmp_path: Path, raw: str, survivors: str | None = None, tool_exit_code: int = 0
) -> dict[str, object]:
    raw_file = tmp_path / "raw.log"
    raw_file.write_text(raw)
    out = tmp_path / "result.json"
    argv = ["--commit", FULL_SHA, "--raw", str(raw_file), "--tool-exit-code", str(tool_exit_code)]
    argv += ["--out", str(out)]
    if survivors is not None:
        surv = tmp_path / "survivors.txt"
        surv.write_text(survivors)
        argv += ["--survivors-file", str(surv)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_to_json.py", *argv])
        assert mutation_to_json.main() == 0
    return json.loads(out.read_text())  # type: ignore[no-any-return]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True)


def _scope_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "--quiet", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD").stdout.strip()


def _run_scope_guard(repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCOPE_GUARD), base], cwd=repo, check=False, text=True, capture_output=True
    )


def test_scope_guard_allows_nested_test_changes(tmp_path: Path) -> None:
    repo, base = _scope_repo(tmp_path)
    test_file = repo / "tests" / "unit" / "test_new.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_new():\n    assert True\n")

    result = _run_scope_guard(repo, base)

    assert result.returncode == 0
    assert "scope guard OK (1 changed file(s), all allowed)" in result.stdout


@pytest.mark.parametrize("state", ["committed", "staged", "unstaged", "untracked"])
def test_scope_guard_rejects_non_test_changes(tmp_path: Path, state: str) -> None:
    repo, base = _scope_repo(tmp_path)
    forbidden = repo / "src" / "lovspor" / "forbidden.py"
    forbidden.parent.mkdir(parents=True)
    forbidden.write_text("changed = True\n")
    if state in {"committed", "staged"}:
        _git(repo, "add", str(forbidden.relative_to(repo)))
    if state == "committed":
        _git(repo, "commit", "--quiet", "-m", "forbidden")
    elif state == "unstaged":
        _git(repo, "add", str(forbidden.relative_to(repo)))
        _git(repo, "commit", "--quiet", "-m", "tracked")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        forbidden.write_text("changed = False\n")

    result = _run_scope_guard(repo, base)

    assert result.returncode == 1
    assert "SCOPE VIOLATION" in result.stderr
    assert "src/lovspor/forbidden.py" in result.stderr


def test_all_killed_passes(tmp_path: Path) -> None:
    result = _run(tmp_path, _progress_line(killed=5))
    assert result["gate"] == {"passed": True, "reason": "ok"}
    assert result["score"] == 100.0
    assert result["completed"] is True


def test_not_applicable_passes(tmp_path: Path) -> None:
    result = _run(tmp_path, "mutation not applicable: no src/lovspor logic changed\n")
    assert result["gate"] == {"passed": True, "reason": "not_applicable"}
    assert result["mutants"]["total"] == 0


def test_survivors_fail_the_gate(tmp_path: Path) -> None:
    result = _run(tmp_path, _progress_line(killed=4, survived=1), survivors="7\n")
    assert result["gate"] == {"passed": False, "reason": "surviving_mutants"}
    assert [s["id"] for s in result["survivors"]] == ["7"]


def test_timeout_mutants_fail_the_gate(tmp_path: Path) -> None:
    result = _run(tmp_path, _progress_line(killed=4, timeout=1))
    assert result["gate"] == {"passed": False, "reason": "timeout_mutants"}


def test_suspicious_mutants_fail_the_gate(tmp_path: Path) -> None:
    result = _run(tmp_path, _progress_line(killed=4, suspicious=1))
    assert result["gate"] == {"passed": False, "reason": "suspicious_mutants"}


def test_suspicious_and_timeout_never_inflate_score(tmp_path: Path) -> None:
    result = _run(tmp_path, _progress_line(killed=2, timeout=1, suspicious=1))
    assert result["score"] == 50.0


def test_mutants_without_tests_fail_as_uncovered(tmp_path: Path) -> None:
    result = _run(tmp_path, _progress_line(killed=4, no_tests=1))

    assert result["gate"] == {"passed": False, "reason": "uncovered_mutants"}
    assert result["mutants"]["no_tests"] == 1


def test_incomplete_run_fails_the_gate(tmp_path: Path) -> None:
    result = _run(tmp_path, "mutmut crashed before producing progress\n")
    assert result["gate"] == {"passed": False, "reason": "run_incomplete"}
    assert result["completed"] is False


def test_baseline_failure_fails_the_gate(tmp_path: Path) -> None:
    raw = "Tests failed when run without mutations\n" + _progress_line(killed=1)
    result = _run(tmp_path, raw)
    assert result["gate"] == {"passed": False, "reason": "baseline_tests_failed"}


@pytest.mark.parametrize("sha", ["abc123", "z" * 40, "A" * 40, "a" * 39, "a" * 41, ""])
def test_non_full_hex_sha_is_refused(tmp_path: Path, sha: str) -> None:
    raw_file = tmp_path / "raw.log"
    raw_file.write_text("")
    out = tmp_path / "result.json"
    argv = ["--commit", sha, "--raw", str(raw_file), "--tool-exit-code", "0"]
    argv += ["--out", str(out)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_to_json.py", *argv])
        assert mutation_to_json.main() == 2
    assert not out.exists()


def test_gate_exit_codes(tmp_path: Path) -> None:
    passing = _run(tmp_path, _progress_line(killed=3))
    failing = _run(tmp_path, _progress_line(killed=2, survived=1), survivors="4\n")
    for result, expected in ((passing, 0), (failing, 1)):
        out = tmp_path / "gate-input.json"
        out.write_text(json.dumps(result))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", str(out)])
            assert mutation_gate.main() == expected


def test_gate_rejects_unknown_schema(tmp_path: Path) -> None:
    out = tmp_path / "bad.json"
    out.write_text(json.dumps({"schema_version": 2}))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_gate.py", str(out)])
        assert mutation_gate.main() == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1},
        {"schema_version": 1, "mutants": {"total": 1}},
        {"schema_version": 1, "mutants": None, "gate": None, "commit": "x", "score": 0},
        {"schema_version": 1, "mutants": {}, "gate": {"passed": True}, "commit": "x", "score": 0},
    ],
)
def test_gate_rejects_malformed_result_without_traceback(
    tmp_path: Path, payload: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "mangled.json"
    out.write_text(json.dumps(payload))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_gate.py", str(out)])
        assert mutation_gate.main() == 1
    assert "malformed mutation result" in capsys.readouterr().err


@pytest.mark.parametrize("passed", ["true", "false", 1, 0, None, [True]])
def test_gate_rejects_non_boolean_passed_value(
    tmp_path: Path, passed: object, capsys: pytest.CaptureFixture[str]
) -> None:
    result = {
        "schema_version": 1,
        "commit": FULL_SHA,
        "score": 100.0,
        "mutants": {"total": 1, "killed": 1, "survived": 0, "timeout": 0},
        "gate": {"passed": passed, "reason": "ok"},
        "survivors": [],
    }
    out = tmp_path / "typed.json"
    out.write_text(json.dumps(result))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_gate.py", str(out)])
        assert mutation_gate.main() == 1
    assert "malformed mutation result" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("field", "value"),
    [("commit", 42), ("score", "high"), ("score", True), ("mutants", {"total": "5"})],
)
def test_gate_rejects_wrong_field_types(
    tmp_path: Path, field: str, value: object, capsys: pytest.CaptureFixture[str]
) -> None:
    result: dict[str, object] = {
        "schema_version": 1,
        "commit": FULL_SHA,
        "score": 100.0,
        "mutants": {"total": 1, "killed": 1, "survived": 0, "timeout": 0},
        "gate": {"passed": True, "reason": "ok"},
        "survivors": [],
    }
    if field == "mutants":
        result["mutants"] = {"total": "5", "killed": 1, "survived": 0, "timeout": 0}
    else:
        result[field] = value
    out = tmp_path / "typed.json"
    out.write_text(json.dumps(result))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_gate.py", str(out)])
        assert mutation_gate.main() == 1
    assert "malformed mutation result" in capsys.readouterr().err


@pytest.mark.parametrize("payload", [[], ["x"], "text", 42, None, True])
def test_gate_rejects_non_object_result_without_traceback(
    tmp_path: Path, payload: object, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "non-object.json"
    out.write_text(json.dumps(payload))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_gate.py", str(out)])
        assert mutation_gate.main() == 1
    assert "malformed mutation result" in capsys.readouterr().err


def test_gate_summary_reports_sha_and_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = _run(tmp_path, _progress_line(killed=3))
    out = tmp_path / "gate-input.json"
    out.write_text(json.dumps(result))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out)])
        assert mutation_gate.main() == 0
    captured = capsys.readouterr().out
    assert FULL_SHA in captured
    assert "PASS" in captured


class TestToolExitAuthority:
    """Issue #72: the tool's own exit code outranks whatever the raw log
    says. A fatal mutmut-pr.sh exit (1, 3) after an EARLIER file's completed
    progress line must never read as PASS, and the exit bits are the
    authoritative cross-file aggregate."""

    def test_fatal_tool_exit_cannot_pass_from_stale_completed_progress(
        self, tmp_path: Path
    ) -> None:
        raw = _progress_line(killed=1) + "error: mutmut run failed on a later file (exit 1)\n"
        result = _run(tmp_path, raw, tool_exit_code=3)

        assert result["gate"] == {"passed": False, "reason": "tool_failed"}

    def test_tool_health_outranks_not_applicable(self, tmp_path: Path) -> None:
        raw = "mutation not applicable for this PR\n"
        result = _run(tmp_path, raw, tool_exit_code=1)

        assert result["gate"] == {"passed": False, "reason": "tool_failed"}

    def test_exit_bits_fail_the_gate_even_when_the_last_line_is_clean(self, tmp_path: Path) -> None:
        """Counts come from the LAST progress line; an earlier file's
        survivors live only in the exit bits (mutmut OR-combines 2/4/8)."""
        result = _run(tmp_path, _progress_line(killed=3), tool_exit_code=2)

        assert result["gate"] == {"passed": False, "reason": "surviving_mutants"}

    @pytest.mark.parametrize(
        ("code", "reason"),
        [
            (0, "ok"),
            (2, "surviving_mutants"),
            (4, "timeout_mutants"),
            (6, "surviving_mutants"),
            (8, "suspicious_mutants"),
            (10, "surviving_mutants"),
            (12, "timeout_mutants"),
            (14, "surviving_mutants"),
        ],
    )
    def test_allowed_verdict_codes_apply_each_aggregate_bit(
        self, tmp_path: Path, code: int, reason: str
    ) -> None:
        result = _run(tmp_path, _progress_line(killed=2), tool_exit_code=code)

        assert result["gate"]["reason"] == reason
        assert result["gate"]["passed"] is (code == 0)

    @pytest.mark.parametrize("code", [-1, 1, 3, 15, 17, 127])
    def test_non_verdict_tool_exit_codes_fail_closed(self, tmp_path: Path, code: int) -> None:
        result = _run(tmp_path, _progress_line(killed=2), tool_exit_code=code)

        assert result["tool_exit_code"] == code
        assert result["gate"] == {"passed": False, "reason": "tool_failed"}


class TestBudgetExceeded:
    """Issue #102: a wall-clock budget cut is a verdict about the RUN, not
    the code — it fails the gate under its own reason so the remediation
    workflow knows not to hand it to Codex (tests cannot kill a mutant
    that was never measured)."""

    BUDGET_LINE = "mutation budget exceeded: after 1200s — unmeasured mutants are untested\n"

    def test_budget_marker_fails_the_gate_with_its_own_reason(self, tmp_path: Path) -> None:
        raw = _progress_line(killed=38, survived=2) + self.BUDGET_LINE

        result = _run(tmp_path, raw, tool_exit_code=18)

        assert result["gate"] == {"passed": False, "reason": "budget_exceeded"}

    def test_the_exit_bit_is_authoritative_without_the_marker(self, tmp_path: Path) -> None:
        """The marker is a raw-log line; the exit bit survives even a log
        that lost it. Either alone must be enough."""
        result = _run(tmp_path, _progress_line(killed=5), tool_exit_code=16)

        assert result["gate"] == {"passed": False, "reason": "budget_exceeded"}

    def test_budget_outranks_run_incomplete(self, tmp_path: Path) -> None:
        """The cut is the cause; the incomplete progress line is only its
        symptom — reporting run_incomplete would hide WHY from the human
        the PR escalates to."""
        raw = _progress_line(killed=40) + self.BUDGET_LINE

        result = _run(tmp_path, raw, tool_exit_code=16)

        assert result["gate"] == {"passed": False, "reason": "budget_exceeded"}

    def test_budget_outranks_the_per_bucket_reasons(self, tmp_path: Path) -> None:
        raw = _progress_line(killed=2, survived=3) + self.BUDGET_LINE

        result = _run(tmp_path, raw, tool_exit_code=18)

        assert result["gate"] == {"passed": False, "reason": "budget_exceeded"}

    @pytest.mark.parametrize("code", [16, 18, 20, 22, 24, 26, 28, 30])
    def test_budget_codes_are_legal_verdicts_not_tool_failures(
        self, tmp_path: Path, code: int
    ) -> None:
        result = _run(tmp_path, _progress_line(killed=2), tool_exit_code=code)

        assert result["gate"]["reason"] == "budget_exceeded"

    def test_fatal_tool_exit_outranks_a_budget_marker(self, tmp_path: Path) -> None:
        """A marker already printed before a later tool failure cannot turn
        that fatal run into a valid budget verdict."""
        raw = self.BUDGET_LINE + "error: mutmut results failed after the run\n"

        result = _run(tmp_path, raw, tool_exit_code=17)

        assert result["gate"] == {"passed": False, "reason": "tool_failed"}


class TestFailureHint:
    """A failing gate must carry the decisive raw-log line: three blocked
    runs in a row required digging job logs for one FAILED test line the
    artifact already contained."""

    def test_first_failed_line_lands_in_the_result(self, tmp_path: Path) -> None:
        raw = (
            _progress_line(killed=1)
            + "FAILED tests/unit/test_x.py::test_y - AssertionError: boom\n"
            + "FAILED tests/unit/test_x.py::test_z - later, ignored\n"
        )
        result = _run(tmp_path, raw, tool_exit_code=3)

        assert result["failure_hint"] == (
            "FAILED tests/unit/test_x.py::test_y - AssertionError: boom"
        )

    def test_tool_error_line_is_a_hint_too(self, tmp_path: Path) -> None:
        raw = "error: mutmut run failed on a later file (exit 1)\n"
        result = _run(tmp_path, raw, tool_exit_code=3)

        assert result["failure_hint"] == "error: mutmut run failed on a later file (exit 1)"

    def test_pytest_error_line_is_a_hint_too(self, tmp_path: Path) -> None:
        raw = "ERROR tests/unit/test_x.py - RuntimeError: collection failed\n"
        result = _run(tmp_path, raw, tool_exit_code=3)

        assert result["failure_hint"] == (
            "ERROR tests/unit/test_x.py - RuntimeError: collection failed"
        )

    def test_hint_is_single_line_and_capped_at_300_characters(self, tmp_path: Path) -> None:
        decisive_line = "FAILED " + "x" * 400
        raw = decisive_line + "\ncontinuation that must not be included\n"
        result = _run(tmp_path, raw, tool_exit_code=3)

        assert result["failure_hint"] == decisive_line[:300]
        assert len(result["failure_hint"]) == 300  # type: ignore[arg-type]

    def test_a_clean_run_has_no_hint(self, tmp_path: Path) -> None:
        result = _run(tmp_path, _progress_line(killed=2))

        assert result["failure_hint"] is None

    def test_gate_reader_prints_the_hint_on_fail(self, tmp_path: Path, capsys: object) -> None:
        raw = _progress_line(killed=1) + "FAILED tests/unit/test_x.py::test_y - boom\n"
        _run(tmp_path, raw, tool_exit_code=3)
        out_file = tmp_path / "result.json"

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", str(out_file)])
            assert mutation_gate.main() == 1
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert "FAILED tests/unit/test_x.py::test_y - boom" in captured.out

    def test_summary_includes_the_hint(self, tmp_path: Path, capsys: object) -> None:
        raw = _progress_line(killed=1) + "FAILED tests/unit/test_x.py::test_y - boom\n"
        _run(tmp_path, raw, tool_exit_code=3)
        out_file = tmp_path / "result.json"

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out_file)])
            assert mutation_gate.main() == 0
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert "Hint:" in captured.out

    @pytest.mark.parametrize("failure_hint", [None, "", 42, ["FAILED fake"]])
    def test_gate_accepts_artifacts_without_a_valid_hint(
        self, tmp_path: Path, capsys: object, failure_hint: object
    ) -> None:
        result = _run(tmp_path, _progress_line(killed=1))
        if failure_hint is None:
            result.pop("failure_hint")
        else:
            result["failure_hint"] = failure_hint
        out_file = tmp_path / "result.json"
        out_file.write_text(json.dumps(result))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out_file)])
            assert mutation_gate.main() == 0
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert "Hint:" not in captured.out


class TestClaudeFallback:
    """The tertiary test author: both Codex accounts limited -> a separate,
    non-Fable Claude writes the tests. The refusal paths run for real —
    each one is a way the fallback could silently do the wrong thing
    (wrong model, per-token billing, missing CLI)."""

    SCRIPT = _SCRIPTS / "claude_test_author.sh"

    def _run_script(self, tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        prompt = tmp_path / "prompt.md"
        prompt.write_text("write tests\n")
        # PATH without the real `claude` binary, so the CLI check is testable
        # on a developer machine that has it installed.
        base = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
        return subprocess.run(
            ["bash", str(self.SCRIPT), str(prompt)],
            env=base | env,
            check=False,
            text=True,
            capture_output=True,
        )

    def test_refuses_a_fable_model(self, tmp_path: Path) -> None:
        result = self._run_script(
            tmp_path,
            {"CLAUDE_TESTS_MODEL": "claude-fable-5", "CLAUDE_TESTS_OAUTH_TOKEN": "sk-ant-oat-x"},
        )
        assert result.returncode == 2
        assert "must not be" in result.stderr

    def test_refuses_a_missing_token(self, tmp_path: Path) -> None:
        result = self._run_script(tmp_path, {"CLAUDE_TESTS_MODEL": "claude-sonnet-5"})
        assert result.returncode == 2
        assert "CLAUDE_TESTS_OAUTH_TOKEN is not set" in result.stderr

    def test_refuses_a_per_token_api_key(self, tmp_path: Path) -> None:
        result = self._run_script(
            tmp_path,
            {"CLAUDE_TESTS_MODEL": "claude-sonnet-5", "CLAUDE_TESTS_OAUTH_TOKEN": "sk-ant-api03-x"},
        )
        assert result.returncode == 2
        assert "bill per token" in result.stderr

    def test_refuses_when_the_cli_is_absent(self, tmp_path: Path) -> None:
        result = self._run_script(
            tmp_path,
            {"CLAUDE_TESTS_MODEL": "claude-sonnet-5", "CLAUDE_TESTS_OAUTH_TOKEN": "sk-ant-oat-x"},
        )
        assert result.returncode == 2
        assert "claude CLI is not installed" in result.stderr

    def test_strips_per_token_credentials_from_the_child_environment(self) -> None:
        text = self.SCRIPT.read_text(encoding="utf-8")
        assert "env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN" in text


class TestFailoverExitCode:
    def test_no_available_account_exits_75(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """75 (EX_TEMPFAIL) is the routing signal for the Claude fallback;
        exit 1 would be indistinguishable from the wrapped command failing."""

        def _exhausted(*_args: object, **_kwargs: object) -> object:
            raise codex_account_failover.RateLimitError("all accounts at 100%")

        monkeypatch.setattr(codex_account_failover, "choose_home", _exhausted)

        code = codex_account_failover.main(
            ["--primary-home", "a", "--secondary-home", "b", "--", "echo", "x"]
        )

        assert code == codex_account_failover.NO_ACCOUNT_EXIT == 75


ORIGINAL_SOURCE = """\
class Renderer:
    def render(self, value):
        return value.strip()


def label(value):
    return value.rstrip(".")
"""

# The shape mutmut 3 writes into mutants/: the unmutated copy beside the
# mutant, both named after the function they were generated from.
MUTATED_SOURCE = """\
class Renderer:
    def xǁRendererǁrender__mutmut_orig(self, value):
        return value.strip()

    def xǁRendererǁrender__mutmut_1(self, value):
        return value.lstrip()


def x_label__mutmut_orig(value):
    return value.rstrip(".")


def x_label__mutmut_1(value):
    return value.rstrip("XX.XX")
"""

RENDER_MUTANT = "pkg.mod.xǁRendererǁrender__mutmut_1"
LABEL_MUTANT = "pkg.mod.x_label__mutmut_1"


def _shadow_tree(tmp_path: Path) -> Path:
    """A minimal repo carrying a real mutmut 3 shadow tree, mutants/ and all."""
    (tmp_path / "pyproject.toml").write_text('[tool.mutmut]\nsource_paths = ["src/pkg/"]\n')
    source = tmp_path / "src" / "pkg"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    (source / "mod.py").write_text(ORIGINAL_SOURCE)
    mutants = tmp_path / "mutants" / "src" / "pkg"
    mutants.mkdir(parents=True)
    (mutants / "__init__.py").write_text("")
    (mutants / "mod.py").write_text(MUTATED_SOURCE)
    meta = {
        "exit_code_by_key": {RENDER_MUTANT: 0, LABEL_MUTANT: 0},
        "durations_by_key": {},
        "estimated_durations_by_key": {},
    }
    (mutants / "mod.py.meta").write_text(json.dumps(meta))
    return tmp_path


def _survivor_records(tmp_path: Path, ids: str) -> list[dict[str, object]]:
    """Run the collector the way the workflow does, from the repo root."""
    (tmp_path / "survivors.txt").write_text(ids)
    out = tmp_path / "survivors.jsonl"
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(tmp_path)
        mp.setattr(
            "sys.argv",
            [
                "mutation_survivors.py",
                "--survivors-file",
                "survivors.txt",
                "--out",
                str(out),
            ],
        )
        _reset_mutmut_config()
        assert mutation_survivors.main() == 0
    return [json.loads(line) for line in out.read_text().splitlines() if line.strip()]


def _reset_mutmut_config() -> None:
    """mutmut caches its config globally; a second tmp repo must not inherit it."""
    MutmutConfig.reset()


class TestSurvivorIdentity:
    """Issue #119: what a mutant id proves on its own, with no shadow tree."""

    def test_a_plain_function_id_splits_into_module_and_symbol(self) -> None:
        assert mutation_survivors.split_id(LABEL_MUTANT) == ("pkg.mod", "label", None)

    def test_a_method_id_carries_its_class(self) -> None:
        assert mutation_survivors.split_id(RENDER_MUTANT) == ("pkg.mod", "render", "Renderer")

    def test_a_module_file_is_only_reported_when_it_is_on_disk(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "pkg").mkdir(parents=True)
        (tmp_path / "src" / "pkg" / "mod.py").write_text(ORIGINAL_SOURCE)
        root = tmp_path / "src"
        assert mutation_survivors.module_file("pkg.mod", root) == root / "pkg" / "mod.py"
        assert mutation_survivors.module_file("pkg.absent", root) is None

    def test_a_package_resolves_to_its_init(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "pkg").mkdir(parents=True)
        (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
        root = tmp_path / "src"
        assert mutation_survivors.module_file("pkg", root) == root / "pkg" / "__init__.py"

    def test_a_method_line_is_found_inside_its_class(self, tmp_path: Path) -> None:
        path = tmp_path / "mod.py"
        path.write_text(ORIGINAL_SOURCE)
        assert mutation_survivors.symbol_line(path, "render", "Renderer") == 2
        assert mutation_survivors.symbol_line(path, "label", None) == 6
        assert mutation_survivors.symbol_line(path, "absent", None) is None


class TestSurvivorDetail:
    """The artifact must answer 'what changed' without a second mutmut run."""

    def test_the_mutation_diff_is_recovered_from_the_shadow_tree(self, tmp_path: Path) -> None:
        _shadow_tree(tmp_path)

        [record] = _survivor_records(tmp_path, f"{LABEL_MUTANT}\n")

        assert record["file"] == "src/pkg/mod.py"
        assert record["symbol"] == "label"
        assert record["symbol_line"] == 6
        assert record["detail_source"] == "mutants_shadow_tree"
        assert '-    return value.rstrip(".")' in record["diff"]
        assert '+    return value.rstrip("XX.XX")' in record["diff"]

    def test_a_method_mutant_reports_its_class(self, tmp_path: Path) -> None:
        _shadow_tree(tmp_path)

        [record] = _survivor_records(tmp_path, f"{RENDER_MUTANT}\n")

        assert record["class"] == "Renderer"
        assert record["symbol_line"] == 2
        # mutmut renders the method standalone, so the diff arrives dedented.
        assert "+    return value.lstrip()" in record["diff"]
        assert "def render(self, value):" in record["diff"]

    def test_without_a_shadow_tree_the_id_is_still_resolved_and_labelled(
        self, tmp_path: Path
    ) -> None:
        """A downloaded artifact or a fresh checkout: no mutants/, so no diff.

        Emitting bare nulls there is what made the old report unreadable — a
        null must say whether it means 'absent' or 'never recovered'.
        """
        _shadow_tree(tmp_path)
        for leftover in (tmp_path / "mutants").rglob("*"):
            if leftover.is_file():
                leftover.unlink()

        [record] = _survivor_records(tmp_path, f"{LABEL_MUTANT}\n")

        assert record["id"] == LABEL_MUTANT
        assert record["file"] == "src/pkg/mod.py"
        assert record["symbol"] == "label"
        assert record["diff"] is None
        assert str(record["detail_source"]).startswith("id_only:")

    def test_every_survivor_gets_a_record(self, tmp_path: Path) -> None:
        _shadow_tree(tmp_path)

        records = _survivor_records(tmp_path, f"{LABEL_MUTANT}\n{RENDER_MUTANT}\n")

        assert [r["id"] for r in records] == [LABEL_MUTANT, RENDER_MUTANT]

    def test_the_line_field_stays_null_because_mutmut_3_has_no_position(
        self, tmp_path: Path
    ) -> None:
        _shadow_tree(tmp_path)

        [record] = _survivor_records(tmp_path, f"{LABEL_MUTANT}\n")

        assert record["line"] is None

    def test_a_renumbered_mutant_id_is_distinguished_from_a_missing_shadow_tree(
        self, tmp_path: Path
    ) -> None:
        """Mutant ids renumber when the file changes upstream of the mutant.

        The shadow tree is intact and readable here — unlike the
        no-shadow-tree case above — so the report must say the id itself
        wasn't found, not blame a missing or unreadable shadow tree.
        """
        _shadow_tree(tmp_path)
        stale_id = "pkg.mod.x_label__mutmut_99"

        [record] = _survivor_records(tmp_path, f"{stale_id}\n")

        assert record["id"] == stale_id
        assert record["file"] == "src/pkg/mod.py"
        assert record["symbol"] == "label"
        assert record["diff"] is None
        assert record["detail_source"] == "id_only: this mutant is not in the shadow tree"

    def test_an_id_for_an_unknown_module_still_returns_a_record(self, tmp_path: Path) -> None:
        _shadow_tree(tmp_path)
        bogus_id = "totally.unknown.x_thing__mutmut_1"

        [record] = _survivor_records(tmp_path, f"{bogus_id}\n")

        assert record["id"] == bogus_id
        assert record["file"] is None
        assert record["symbol"] == "thing"
        assert record["symbol_line"] is None
        assert record["diff"] is None
        assert str(record["detail_source"]).startswith("id_only:")

    def test_a_missing_survivors_file_produces_an_empty_report(self, tmp_path: Path) -> None:
        """No survivors file at all — e.g. a run with nothing to triage — must
        not crash the collector; it should just write an empty artifact."""
        _shadow_tree(tmp_path)
        out = tmp_path / "survivors.jsonl"

        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(tmp_path)
            mp.setattr(
                "sys.argv",
                [
                    "mutation_survivors.py",
                    "--survivors-file",
                    "does-not-exist.txt",
                    "--out",
                    str(out),
                ],
            )
            _reset_mutmut_config()
            assert mutation_survivors.main() == 0

        assert out.read_text() == ""


class TestSurvivorsInTheReport:
    """mutation-result.json carries the detail, and stays readable without it."""

    def test_detail_lines_reach_the_report(self, tmp_path: Path) -> None:
        detail = {
            "id": LABEL_MUTANT,
            "file": "src/pkg/mod.py",
            "line": None,
            "symbol": "label",
            "class": None,
            "symbol_line": 6,
            "operator": None,
            "diff": '-    return value.rstrip(".")\n+    return value.rstrip("XX.XX")',
            "detail_source": "mutants_shadow_tree",
        }
        result = _run(
            tmp_path,
            _progress_line(survived=1),
            survivors=json.dumps(detail) + "\n",
            tool_exit_code=2,
        )
        # The register annotates every survivor (issue #122); the detail the
        # collector recovered must survive that pass untouched.
        assert result["survivors"] == [{**detail, "equivalent": None}]

    def test_a_bare_id_list_is_still_legal_input(self, tmp_path: Path) -> None:
        result = _run(
            tmp_path, _progress_line(survived=1), survivors=f"{LABEL_MUTANT}\n", tool_exit_code=2
        )
        [survivor] = result["survivors"]  # type: ignore[misc]
        assert survivor["id"] == LABEL_MUTANT
        assert survivor["diff"] is None
        assert survivor["detail_source"] == "id_only: no detail collected"

    def test_a_malformed_detail_line_never_loses_the_survivor(self, tmp_path: Path) -> None:
        """The survivor list is the gate's evidence; a parse slip must not
        quietly shrink a red run."""
        broken = '{"id": "' + LABEL_MUTANT + '", "diff": \n'
        result = _run(tmp_path, _progress_line(survived=1), survivors=broken, tool_exit_code=2)
        [survivor] = result["survivors"]  # type: ignore[misc]
        assert survivor["detail_source"] == "id_only: malformed detail line"

    def test_the_summary_points_at_the_file_and_the_replacement(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        detail = {
            "id": LABEL_MUTANT,
            "file": "src/pkg/mod.py",
            "symbol_line": 6,
            "diff": '--- src/pkg/mod.py\n+++ src/pkg/mod.py\n-    return value.rstrip(".")\n'
            '+    return value.rstrip("XX.XX")',
        }
        result = _run(
            tmp_path,
            _progress_line(survived=1),
            survivors=json.dumps(detail) + "\n",
            tool_exit_code=2,
        )
        out = tmp_path / "gate-input.json"
        out.write_text(json.dumps(result))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out)])
            assert mutation_gate.main() == 0

        captured = capsys.readouterr().out
        assert "src/pkg/mod.py:6" in captured
        assert 'return value.rstrip("XX.XX")' in captured
        assert "+++" not in captured

    def test_the_summary_caps_the_list_and_says_how_many_it_dropped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        survivors = [
            {"id": f"pkg.mod.x_f{n}__mutmut_1", "file": "src/pkg/mod.py"} for n in range(12)
        ]
        out = tmp_path / "many.json"
        out.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "commit": FULL_SHA,
                    "score": 90.0,
                    "mutants": {"total": 12, "killed": 0, "survived": 12, "timeout": 0},
                    "gate": {"passed": False, "reason": "surviving_mutants"},
                    "survivors": survivors,
                }
            )
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out)])
            assert mutation_gate.main() == 0

        captured = capsys.readouterr().out
        assert "… and 2 more (see the artifact)" in captured

    def test_the_summary_survives_a_mangled_survivor_list(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """mutation_gate never tracebacks on artifact data — survivors included."""
        out = tmp_path / "mangled.json"
        out.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "commit": FULL_SHA,
                    "score": 90.0,
                    "mutants": {"total": 1, "killed": 0, "survived": 1, "timeout": 0},
                    "gate": {"passed": False, "reason": "surviving_mutants"},
                    "survivors": "not-a-list",
                }
            )
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out)])
            assert mutation_gate.main() == 0

        assert "Survivors:" not in capsys.readouterr().out

    def test_the_summary_marks_a_survivor_with_no_known_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A degraded record — id resolved, source file not — still reads as
        a triage bullet instead of a blank or a crash."""
        out = tmp_path / "no-file.json"
        out.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "commit": FULL_SHA,
                    "score": 90.0,
                    "mutants": {"total": 1, "killed": 0, "survived": 1, "timeout": 0},
                    "gate": {"passed": False, "reason": "surviving_mutants"},
                    "survivors": [{"id": "totally.unknown.x_thing__mutmut_1"}],
                }
            )
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out)])
            assert mutation_gate.main() == 0

        captured = capsys.readouterr().out
        assert "`totally.unknown.x_thing__mutmut_1` — file unknown" in captured


MODEL_FILE = "src/lovspor/observatory/model.py"
REGISTRY_FILE = "src/lovspor/observatory/registry.py"
# The real mutation from PR #118 run 32118697389, the one issue #122 was filed on.
MODEL_DIFF = (
    f"--- {MODEL_FILE}\n"
    f"+++ {MODEL_FILE}\n"
    "@@ -1,3 +1,3 @@\n"
    " def record_to_json_line(record):\n"
    '-    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))\n'
    '+    return json.dumps(data, sort_keys=True, ensure_ascii=None, separators=(",", ":"))'
)
MODEL_ENTRY = f"""
[[equivalent]]
file = "{MODEL_FILE}"
symbol = "record_to_json_line"
registered = "2026-08-18, PR #127"
mutation = '''
-    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
+    return json.dumps(data, sort_keys=True, ensure_ascii=None, separators=(",", ":"))
'''
justification = "None is falsy, so it selects the same json encoder as False."
"""


@contextmanager
def _register(tmp_path: Path, toml: str) -> Iterator[None]:
    """Point the gate at a register written for this test."""
    path = tmp_path / "mutation-equivalents.toml"
    path.write_text(toml)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mutation_to_json, "EQUIVALENTS_FILE", path)
        yield


def _survivor_line(mutant_id: str, file: str, diff: str) -> str:
    record = {
        "id": mutant_id,
        "file": file,
        "line": None,
        "symbol": "record_to_json_line",
        "operator": None,
        "diff": diff,
        "detail_source": "mutants_shadow_tree",
    }
    return json.dumps(record) + "\n"


class TestEquivalentRegister:
    """Issue #122: a survivor no test can ever kill must be able to go green,
    without the register becoming a way to silence real gaps."""

    def test_a_registered_survivor_passes_the_gate_without_moving_the_numbers(
        self, tmp_path: Path
    ) -> None:
        """decisions.md §9c: registered, not chased — the measurement stands,
        only the verdict moves."""
        survivors = _survivor_line("m.x_record_to_json_line__mutmut_7", MODEL_FILE, MODEL_DIFF)
        with _register(tmp_path, MODEL_ENTRY):
            result = _run(
                tmp_path, _progress_line(killed=297, survived=1), survivors, tool_exit_code=2
            )

        assert result["gate"] == {"passed": True, "reason": "equivalent_mutants_only"}
        assert result["mutants"]["survived"] == 1  # type: ignore[index]
        assert result["score"] == 99.66
        assert result["equivalents"] == {"registered": 1, "refused": []}
        [survivor] = result["survivors"]  # type: ignore[misc]
        assert survivor["equivalent"]["registered"] == "2026-08-18, PR #127"

    def test_one_unregistered_survivor_keeps_the_gate_red(self, tmp_path: Path) -> None:
        survivors = _survivor_line(
            "m.x_record_to_json_line__mutmut_7", MODEL_FILE, MODEL_DIFF
        ) + _survivor_line("r.x_other__mutmut_1", REGISTRY_FILE, MODEL_DIFF.replace("None", "True"))
        with _register(tmp_path, MODEL_ENTRY):
            result = _run(
                tmp_path, _progress_line(killed=297, survived=2), survivors, tool_exit_code=2
            )

        assert result["gate"] == {"passed": False, "reason": "surviving_mutants"}
        assert result["equivalents"]["registered"] == 1  # type: ignore[index]

    def test_the_same_mutation_in_another_file_is_not_covered(self, tmp_path: Path) -> None:
        """A waiver written about one function is not evidence about another."""
        survivors = _survivor_line(
            "r.x_write_registry__mutmut_5",
            REGISTRY_FILE,
            MODEL_DIFF.replace(MODEL_FILE, REGISTRY_FILE),
        )
        with _register(tmp_path, MODEL_ENTRY):
            result = _run(
                tmp_path, _progress_line(killed=1, survived=1), survivors, tool_exit_code=2
            )

        assert result["gate"] == {"passed": False, "reason": "surviving_mutants"}

    def test_a_renumbered_id_still_matches_because_the_key_is_the_mutation(
        self, tmp_path: Path
    ) -> None:
        survivors = _survivor_line("m.x_record_to_json_line__mutmut_41", MODEL_FILE, MODEL_DIFF)
        with _register(tmp_path, MODEL_ENTRY):
            result = _run(
                tmp_path, _progress_line(killed=1, survived=1), survivors, tool_exit_code=2
            )

        assert result["gate"]["passed"] is True  # type: ignore[index]

    def test_reindented_code_still_matches(self, tmp_path: Path) -> None:
        """Wrapping the line in a new block changes its indentation, not the
        mutation — re-reviewing that is busywork, so leading space is ignored."""
        reindented = MODEL_DIFF.replace("-    return", "-        return").replace(
            "+    return", "+        return"
        )
        survivors = _survivor_line("m.x_record_to_json_line__mutmut_7", MODEL_FILE, reindented)
        with _register(tmp_path, MODEL_ENTRY):
            result = _run(
                tmp_path, _progress_line(killed=1, survived=1), survivors, tool_exit_code=2
            )

        assert result["gate"]["passed"] is True  # type: ignore[index]

    def test_a_changed_replacement_line_does_not_match(self, tmp_path: Path) -> None:
        """Equivalence was argued about one specific replacement. A different
        one is a different claim and has to be argued again."""
        other = MODEL_DIFF.replace("ensure_ascii=None", "ensure_ascii=True")
        survivors = _survivor_line("m.x_record_to_json_line__mutmut_9", MODEL_FILE, other)
        with _register(tmp_path, MODEL_ENTRY):
            result = _run(
                tmp_path, _progress_line(killed=1, survived=1), survivors, tool_exit_code=2
            )

        assert result["gate"] == {"passed": False, "reason": "surviving_mutants"}

    def test_a_survivor_with_no_recovered_diff_never_matches(self, tmp_path: Path) -> None:
        """Fail closed: a waiver the gate cannot verify is not a waiver."""
        with _register(tmp_path, MODEL_ENTRY):
            result = _run(
                tmp_path,
                _progress_line(killed=1, survived=1),
                survivors="m.x_record_to_json_line__mutmut_7\n",
                tool_exit_code=2,
            )

        assert result["gate"] == {"passed": False, "reason": "surviving_mutants"}

    def test_the_survived_exit_bit_without_survivor_records_still_fails(
        self, tmp_path: Path
    ) -> None:
        """The bit is the authoritative cross-file aggregate; with nothing to
        check it against, there is nothing to excuse it with."""
        with _register(tmp_path, MODEL_ENTRY):
            result = _run(tmp_path, _progress_line(killed=5), tool_exit_code=2)

        assert result["gate"] == {"passed": False, "reason": "surviving_mutants"}

    @pytest.mark.parametrize("field", ["justification", "symbol", "mutation", "file"])
    def test_an_entry_missing_a_required_field_is_refused_not_applied(
        self, tmp_path: Path, field: str
    ) -> None:
        """Silencing a survivor is a decision; an undocumented one does not count."""
        broken = "\n".join(
            line for line in MODEL_ENTRY.splitlines() if not line.startswith(f"{field} =")
        )
        if field == "mutation":
            broken = MODEL_ENTRY.replace("-    return", "     return").replace(
                "+    return", "     return"
            )
        survivors = _survivor_line("m.x_record_to_json_line__mutmut_7", MODEL_FILE, MODEL_DIFF)
        with _register(tmp_path, broken):
            result = _run(
                tmp_path, _progress_line(killed=1, survived=1), survivors, tool_exit_code=2
            )

        assert result["gate"] == {"passed": False, "reason": "surviving_mutants"}
        assert result["equivalents"]["registered"] == 0  # type: ignore[index]
        assert len(result["equivalents"]["refused"]) == 1  # type: ignore[index]

    def test_an_unparseable_register_costs_the_entries_not_the_run(self, tmp_path: Path) -> None:
        with _register(tmp_path, "[[equivalent]\nfile = broken"):
            result = _run(tmp_path, _progress_line(killed=3))

        assert result["gate"] == {"passed": True, "reason": "ok"}
        assert "unreadable register" in result["equivalents"]["refused"][0]  # type: ignore[index]

    def test_no_register_file_changes_nothing(self, tmp_path: Path) -> None:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(mutation_to_json, "EQUIVALENTS_FILE", tmp_path / "absent.toml")
            result = _run(
                tmp_path,
                _progress_line(killed=1, survived=1),
                survivors="x\n",
                tool_exit_code=2,
            )

        assert result["gate"] == {"passed": False, "reason": "surviving_mutants"}
        assert result["equivalents"] == {"registered": 0, "refused": []}

    def test_check_equivalents_exits_nonzero_on_a_refused_entry(self, tmp_path: Path) -> None:
        with (
            _register(tmp_path, MODEL_ENTRY.replace("justification =", "note =")),
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setattr("sys.argv", ["mutation_to_json.py", "--check-equivalents"])
            assert mutation_to_json.main() == 1

    def test_check_equivalents_accepts_the_register_this_repo_ships(self) -> None:
        """The register in the repo root must always parse — a refused entry
        there means the gate is silently applying fewer waivers than it reads."""
        equivalents, refused = mutation_to_json.load_equivalents(
            Path(__file__).parents[2] / "mutation-equivalents.toml"
        )

        assert refused == []
        assert equivalents

    def test_the_summary_shows_what_the_register_did(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        survivors = _survivor_line("m.x_record_to_json_line__mutmut_7", MODEL_FILE, MODEL_DIFF)
        with _register(tmp_path, MODEL_ENTRY):
            result = _run(
                tmp_path, _progress_line(killed=1, survived=1), survivors, tool_exit_code=2
            )
        out = tmp_path / "gate-input.json"
        out.write_text(json.dumps(result))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out)])
            assert mutation_gate.main() == 0

        captured = capsys.readouterr().out
        assert "- Registered equivalents: 1 (`mutation-equivalents.toml`)" in captured
        assert "**equivalent**, 2026-08-18, PR #127" in captured

    def test_the_summary_shouts_about_a_refused_entry(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "refused.json"
        out.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "commit": FULL_SHA,
                    "score": 90.0,
                    "mutants": {"total": 1, "killed": 0, "survived": 1, "timeout": 0},
                    "gate": {"passed": False, "reason": "surviving_mutants"},
                    "equivalents": {"registered": 0, "refused": ["m.py: missing justification"]},
                }
            )
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out)])
            assert mutation_gate.main() == 0

        assert "⚠ Refused register entry: m.py: missing justification" in capsys.readouterr().out

    def test_a_zero_registered_count_prints_no_register_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The common case — no register, or one that matches nothing — must
        stay silent rather than claim "Registered equivalents: 0"."""
        result = _run(
            tmp_path, _progress_line(killed=1, survived=1), survivors="x\n", tool_exit_code=2
        )
        assert result["equivalents"] == {"registered": 0, "refused": []}
        out = tmp_path / "gate-input.json"
        out.write_text(json.dumps(result))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out)])
            assert mutation_gate.main() == 0

        assert "Registered equivalents" not in capsys.readouterr().out

    def test_a_missing_registered_field_still_applies_the_entry(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`registered` provenance is not in EQUIVALENT_FIELDS, so an entry
        that omits it is still a valid waiver — it just renders with no
        stamp, instead of being refused for a field the rules never require."""
        entry = "\n".join(
            line for line in MODEL_ENTRY.splitlines() if not line.startswith("registered =")
        )
        survivors = _survivor_line("m.x_record_to_json_line__mutmut_7", MODEL_FILE, MODEL_DIFF)
        with _register(tmp_path, entry):
            result = _run(
                tmp_path, _progress_line(killed=1, survived=1), survivors, tool_exit_code=2
            )

        assert result["gate"] == {"passed": True, "reason": "equivalent_mutants_only"}
        assert result["equivalents"] == {"registered": 1, "refused": []}
        [survivor] = result["survivors"]  # type: ignore[misc]
        assert survivor["equivalent"]["registered"] == ""

        out = tmp_path / "gate-input.json"
        out.write_text(json.dumps(result))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_gate.py", "--summary", str(out)])
            assert mutation_gate.main() == 0

        captured = capsys.readouterr().out
        assert "**equivalent**" in captured
        assert "**equivalent**," not in captured

    def test_check_equivalents_exits_zero_and_lists_each_entry(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The success path of `--check-equivalents`, not just its refusal path."""
        with (
            _register(tmp_path, MODEL_ENTRY),
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setattr("sys.argv", ["mutation_to_json.py", "--check-equivalents"])
            assert mutation_to_json.main() == 0

        out = capsys.readouterr().out
        assert f"registered: {MODEL_FILE} record_to_json_line" in out
        assert "1 registered, 0 refused" in out

    def test_missing_required_flags_without_check_equivalents_exits_two(self) -> None:
        """`--commit`/`--raw`/`--tool-exit-code`/`--out` became optional flags
        so `--check-equivalents` could stand alone; without that flag they are
        still mandatory, enforced by hand now instead of by argparse."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.argv", ["mutation_to_json.py"])
            with pytest.raises(SystemExit) as exc_info:
                mutation_to_json.main()
        assert exc_info.value.code == 2


class TestShadowTreeCarriesTheRegister:
    """Issue #129: mutmut's clean baseline runs
    test_check_equivalents_accepts_the_register_this_repo_ships against the
    *shadow* tree, not the checkout — if mutation-equivalents.toml isn't
    also_copy'd in there, that test fails, the baseline aborts, and every PR
    touching src/lovspor/ reports tool_failed on a missing file rather than on
    a real mutant."""

    def test_the_repos_mutmut_config_also_copies_the_register(self) -> None:
        """Locks pyproject.toml's [tool.mutmut] also_copy list: dropping
        "mutation-equivalents.toml" from it reproduces issue #129."""
        repo_root = Path(__file__).parents[2]
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(repo_root)
            MutmutConfig.reset()
            try:
                also_copy = MutmutConfig.get().also_copy
            finally:
                MutmutConfig.reset()

        assert Path("mutation-equivalents.toml") in also_copy

    def test_also_copy_lands_the_register_inside_mutants(self, tmp_path: Path) -> None:
        """Exercises mutmut's own copy_also_copy_files() the way the mutation
        run does: also_copy naming a file is only useful if that file
        actually ends up under mutants/."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.mutmut]\nsource_paths = ["src/pkg/"]\n'
            'also_copy = ["mutation-equivalents.toml"]\n'
        )
        (tmp_path / "mutation-equivalents.toml").write_text("[[equivalent]]\n")
        (tmp_path / "mutants").mkdir()

        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(tmp_path)
            MutmutConfig.reset()
            try:
                copy_also_copy_files()
            finally:
                MutmutConfig.reset()

        copied = tmp_path / "mutants" / "mutation-equivalents.toml"
        assert copied.read_text() == "[[equivalent]]\n"

    def test_also_copy_skips_a_register_that_does_not_exist(self, tmp_path: Path) -> None:
        """copy_also_copy_files() silently no-ops on a missing source path —
        confirms the fix relies on the file being present in the checkout,
        not on mutmut inventing it."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.mutmut]\nsource_paths = ["src/pkg/"]\n'
            'also_copy = ["mutation-equivalents.toml"]\n'
        )
        (tmp_path / "mutants").mkdir()

        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(tmp_path)
            MutmutConfig.reset()
            try:
                copy_also_copy_files()
            finally:
                MutmutConfig.reset()

        assert not (tmp_path / "mutants" / "mutation-equivalents.toml").exists()
