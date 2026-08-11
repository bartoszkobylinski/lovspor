"""Gate policy tests for scripts/ci/mutation_to_json.py and mutation_gate.py.

The gate mirrors mutmut 2.5's own exit-code bits (2 survived / 4 timeout /
8 suspicious — mutmut.compute_exit_code): each class alone must fail the gate,
and suspicious mutants must never be folded into the killed count or score.
"""

from __future__ import annotations

import importlib.util
import json
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


def _progress_line(
    *, killed: int = 0, timeout: int = 0, suspicious: int = 0, survived: int = 0
) -> str:
    total = killed + timeout + suspicious + survived
    return f"{total}/{total}  🎉 {killed}  ⏰ {timeout}  🤔 {suspicious}  🙁 {survived}  🔇 0\n"


def _run(tmp_path: Path, raw: str, survivors: str | None = None) -> dict[str, object]:
    raw_file = tmp_path / "raw.log"
    raw_file.write_text(raw)
    out = tmp_path / "result.json"
    argv = ["--commit", FULL_SHA, "--raw", str(raw_file), "--tool-exit-code", "0"]
    argv += ["--out", str(out)]
    if survivors is not None:
        surv = tmp_path / "survivors.txt"
        surv.write_text(survivors)
        argv += ["--survivors-file", str(surv)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("sys.argv", ["mutation_to_json.py", *argv])
        assert mutation_to_json.main() == 0
    return json.loads(out.read_text())  # type: ignore[no-any-return]


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
