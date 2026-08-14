"""Gate policy tests for scripts/ci/mutation_to_json.py and mutation_gate.py.

The gate mirrors mutmut 2.5's own exit-code bits (2 survived / 4 timeout /
8 suspicious — mutmut.compute_exit_code): each class alone must fail the gate,
and suspicious mutants must never be folded into the killed count or score.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

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

FULL_SHA = "a" * 40
SCOPE_GUARD = _SCRIPTS / "assert_codex_scope.sh"


def _progress_line(
    *, killed: int = 0, timeout: int = 0, suspicious: int = 0, survived: int = 0
) -> str:
    total = killed + timeout + suspicious + survived
    return f"{total}/{total}  🎉 {killed}  ⏰ {timeout}  🤔 {suspicious}  🙁 {survived}  🔇 0\n"


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


def test_incomplete_run_fails_the_gate(tmp_path: Path) -> None:
    result = _run(tmp_path, "3/10  🎉 3  ⏰ 0  🤔 0  🙁 0  🔇 0\n")
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

    BUDGET_LINE = (
        "mutation budget exceeded: src/lovspor/mcp.py after 1200s — "
        "unmeasured mutants are untested, never killed\n"
    )

    def test_budget_marker_fails_the_gate_with_its_own_reason(self, tmp_path: Path) -> None:
        raw = "40/4000  🎉 38  ⏰ 0  🤔 0  🙁 2  🔇 0\n" + self.BUDGET_LINE

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
        raw = "40/4000  🎉 40  ⏰ 0  🤔 0  🙁 0  🔇 0\n" + self.BUDGET_LINE

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
        raw = self.BUDGET_LINE + "error: mutmut result-ids failed on a later file\n"

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
