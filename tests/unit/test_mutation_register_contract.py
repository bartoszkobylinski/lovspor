"""Contract tests for the persistent nightly mutation register (#984)."""

import gzip
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/mutation_register.py"


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _run(tmp_path: Path, result: dict, mutants: list[dict], *, complete: bool, run: str) -> Path:
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result))
    mutants_path = tmp_path / "mutants.jsonl.gz"
    with gzip.open(mutants_path, "wt", encoding="utf-8") as stream:
        for mutant in mutants:
            stream.write(json.dumps(mutant) + "\n")
    out = tmp_path / f"out-{run}"
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "merge",
            "--result",
            str(result_path),
            "--mutants",
            str(mutants_path),
            "--register-in",
            str(tmp_path / "register.jsonl"),
            "--history-in",
            str(tmp_path / "history.jsonl"),
            "--out",
            str(out),
            "--date",
            f"2026-11-{run}",
            "--run-id",
            run,
            "--run-url",
            "https://example.test/run",
            "--base",
            "base",
            "--head",
            f"head{run}abcdef",
            "--verdict",
            "PASS",
            "--complete",
            str(complete).lower(),
            "--repo",
            "owner/repo",
            "--root",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out


def _record(symbol: str, mutant_id: str, diff: str) -> dict:
    return {"file": "module.py", "symbol": symbol, "id": mutant_id, "diff": diff}


def test_completed_nights_replace_measured_keep_unmeasured_and_drop_deleted(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text(
        "def measured(): pass\ndef kept(): pass\ndef deleted(): pass\n"
    )
    a = _record("measured", "old-id", "-return 1\n+return 2")
    b = _record("measured", "killed-id", "-return 3\n+return 4")
    kept = _record("kept", "kept-id", "-x\n+y")
    deleted = _record("deleted", "deleted-id", "-a\n+b")
    first = _run(
        tmp_path,
        {"survivors": [a, b, kept, deleted]},
        [a, b, kept, deleted],
        complete=True,
        run="01",
    )
    first_register = _lines(first / "branch/register.jsonl")
    (tmp_path / "register.jsonl").write_text((first / "branch/register.jsonl").read_text())
    (tmp_path / "history.jsonl").write_text((first / "branch/history.jsonl").read_text())
    (tmp_path / "module.py").write_text("def measured(): pass\ndef kept(): pass\n")
    renumbered = {**a, "id": "new-id"}
    new = _record("measured", "fresh-id", "-return 5\n+return 6")
    second = _run(
        tmp_path, {"survivors": [renumbered, new]}, [renumbered, new, b], complete=True, run="02"
    )
    entries = _lines(second / "branch/register.jsonl")
    assert {e["mutant"] for e in entries} == {"new-id", "fresh-id", "kept-id"}
    old = next(e for e in first_register if e["mutant"] == "old-id")
    current = next(e for e in entries if e["mutant"] == "new-id")
    assert current["key"] == old["key"]
    assert current["first_seen"] == old["first_seen"]
    assert current["last_measured"]["run"] == "02"
    assert next(e for e in entries if e["mutant"] == "kept-id")["last_measured"]["run"] == "01"
    assert _lines(second / "branch/history.jsonl")[-1]["register"] == {
        "open": 3,
        "files": 1,
        "new": 1,
        "left": 1,
        "gone": 1,
    }


def test_incomplete_night_preserves_register_and_records_history(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def measured(): pass\n")
    a = _record("measured", "a", "-x\n+y")
    first = _run(tmp_path, {"survivors": [a]}, [a], complete=True, run="01")
    register = first / "branch/register.jsonl"
    (tmp_path / "register.jsonl").write_bytes(register.read_bytes())
    (tmp_path / "history.jsonl").write_bytes((first / "branch/history.jsonl").read_bytes())
    second = _run(tmp_path, {"survivors": []}, [a], complete=False, run="02")
    assert (second / "branch/register.jsonl").read_bytes() == register.read_bytes()
    history = _lines(second / "branch/history.jsonl")
    assert len(history) == 2
    assert history[-1]["complete"] is False
    assert history[-1]["register"] == {"open": 1, "files": 1, "new": 0, "left": 0, "gone": 0}
    report = (second / "branch/nights/2026-11-02-head02ab/report.md").read_text()
    assert "only a completed night changes it" in report
