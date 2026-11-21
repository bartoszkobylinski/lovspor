"""Per-mutant nightly artifacts and source-derived kinds (#984)."""

from __future__ import annotations

import gzip
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci/mutation_night.py"
SPEC = importlib.util.spec_from_file_location(
    "mutation_kinds", ROOT / "scripts/ci/mutation_kinds.py"
)
assert SPEC is not None and SPEC.loader is not None
kinds = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kinds)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("return 1", "return 2", "number"),
        ('return "hello"', 'return "XXhelloXX"', "string: XX added"),
        ("return a < b", "return a <= b", "operator: comparison"),
        ("return a and b", "return a or b", "operator: and / or"),
        ("return f(a)", "return f(None)", "argument: replaced by None"),
        ("return True", "return False", "True / False swapped"),
        ("return a", "return b", "other"),
    ],
)
def test_kind_is_read_from_changed_tokens(old: str, new: str, expected: str) -> None:
    result = kinds.describe(
        f"def x_f__mutmut_orig():\n    {old}\n", f"def x_f__mutmut_1():\n    {new}\n"
    )
    assert result.kind == expected
    assert result.removed and result.added


def test_kind_ignores_generated_names_and_layout() -> None:
    result = kinds.describe(
        "def x_f__mutmut_orig():\n    return 1  # original\n",
        "def x_f__mutmut_1():\n    return 1\n",
    )
    assert result.kind == "no change in the tokens"
    assert (result.removed, result.added) == ("", "")


def test_records_and_reconciles_measured_mutants(tmp_path: Path) -> None:
    root = tmp_path / "mutants"
    root.mkdir()
    source = root / "mod.py"
    source.write_text(
        "def x_f__mutmut_orig():\n    return 1\ndef x_f__mutmut_1():\n    return 2\n",
        encoding="utf-8",
    )
    (root / "mod.py.spans").write_text(
        json.dumps({"version": 1, "spans": {"x_f__mutmut_orig": [1, 2], "x_f__mutmut_1": [3, 4]}})
    )
    first, second, unchecked = (f"lovspor.mod.x_f__mutmut_{n}" for n in (1, 2, 3))
    (root / "mod.py.meta").write_text(
        json.dumps(
            {
                "exit_code_by_key": {first: 1, second: 0, unchecked: None},
                "durations_by_key": {first: 1.2345, second: 3.0},
                "estimated_durations_by_key": {first: 2.0, second: 2.0},
            }
        )
    )
    (root / "mutmut-stats.json").write_text(
        json.dumps({"tests_by_mangled_function_name": {"lovspor.mod.x_f": ["test_a", "test_b"]}})
    )
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps(
            {"mutants": {"killed": 1, "survived": 0, "timeout": 0, "no_tests": 0, "skipped": 0}}
        )
    )
    night, records, summary = (
        tmp_path / name for name in ("night.json", "records.gz", "summary.md")
    )
    command = [
        sys.executable,
        str(SCRIPT),
        "--commit",
        "abc",
        "--mutants",
        str(root),
        "--result",
        str(result),
        "--out-night",
        str(night),
        "--out-mutants",
        str(records),
        "--summary",
        str(summary),
    ]
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    data = json.loads(night.read_text())
    rows = [
        json.loads(line) for line in gzip.decompress(records.read_bytes()).decode().splitlines()
    ]
    assert [row["id"] for row in rows] == [first, second]
    assert (rows[0]["status"], rows[0]["kind"], rows[0]["removed"], rows[0]["added"]) == (
        "killed",
        "number",
        "1",
        "2",
    )
    assert rows[1]["status"] == "survived"
    assert rows[1]["kind"] == "unknown: no source in the shadow tree"
    assert rows[0]["seconds"] == 1.234
    assert data["commit"] == "abc"
    assert data["not_checked"] == 1
    assert data["measured"]["by_status"] == {"killed": 1, "survived": 1}
    assert data["by_function"][0]["tests"] == 2
    assert data["result_check"]["differences"] == {"survived": {"result": 0, "night": 1}}
    assert any("no readable source" in problem for problem in data["problems"])
    assert "Counts differ" in summary.read_text()
    original = records.read_bytes()
    subprocess.run(command, capture_output=True, text=True, check=True)
    assert records.read_bytes() == original
