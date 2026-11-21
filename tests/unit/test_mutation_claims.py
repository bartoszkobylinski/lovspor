"""Contracts for claimed survivor remeasurement in the nightly lane (#984)."""

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts/ci"))
    return importlib.import_module("mutation_remeasure"), importlib.import_module(
        "mutation_register"
    )


def test_claims_parse_multiple_keys_and_keep_oldest_commit(modules) -> None:
    remeasure, _ = modules
    log = (
        "new\0Kills-mutant: 0123456789abcdef fedcba9876543210-2\n\x01"
        "old\0kills-mutant: 0123456789abcdef\n"
        "ordinary text 1111111111111111\n\x01"
    )
    assert remeasure.claimed(log) == [
        ("0123456789abcdef", "old"),
        ("fedcba9876543210-2", "new"),
    ]


def test_claim_resolution_writes_unique_patterns_and_reports_missing(
    modules, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    remeasure, _ = modules
    keys = ["0000000000000001", "0000000000000002", "0000000000000003", "0000000000000004"]
    entries = [
        {"key": key, "file": "src/lovspor/example.py", "symbol": symbol}
        for key, symbol in zip(keys, ["present", "present", "gone"], strict=False)
    ]
    register = tmp_path / "register.jsonl"
    register.write_text("\n".join(json.dumps(entry) for entry in entries), encoding="utf-8")
    monkeypatch.setattr(
        remeasure, "range_log", lambda base, head: "abc\0Kills-mutant: " + " ".join(keys) + "\x01"
    )
    monkeypatch.setattr(remeasure, "head_source", lambda file: "def present(): pass\n")
    patterns, claims = tmp_path / "patterns", tmp_path / "claims.json"
    assert (
        remeasure.main(
            [
                "--base",
                "base",
                "--head",
                "head",
                "--register",
                str(register),
                "--out-patterns",
                str(patterns),
                "--out-claims",
                str(claims),
            ]
        )
        == 0
    )
    data = json.loads(claims.read_text(encoding="utf-8"))
    assert [claim["state"] for claim in data["claims"]] == [
        "to measure",
        "to measure",
        "its function is not in the tree",
        "not in the register",
    ]
    assert patterns.read_text(encoding="utf-8") == "lovspor.example.x_present__mutmut_*\n"
    assert "2 function(s)" not in capsys.readouterr().out


def test_claim_outcomes_depend_on_measurement_not_claim(modules) -> None:
    _, register = modules
    entries = [{"key": str(i), "file": "example.py", "symbol": f"f{i}"} for i in range(3)]
    claims = [{"key": str(i), "pattern": "pattern"} for i in range(3)] + [
        {"key": "missing", "pattern": None, "state": "not in the register"},
        {"key": "gone", "pattern": None, "state": "its function is not in the tree"},
    ]
    before = [*entries, {"key": "gone", "file": "example.py", "symbol": "gone"}]
    measured = {register.function_of(entries[0]), register.function_of(entries[1])}
    outcomes = register.claim_outcomes(claims, before, [entries[1], entries[2]], measured)
    assert [item["outcome"] for item in outcomes] == [
        "killed",
        "still alive",
        "not decided: the night did not measure its function",
        "not in the register",
        "its function is not in the tree",
    ]
    incomplete = register.claim_outcomes(claims[:1], before, before, set())
    assert incomplete[0]["outcome"] == "not decided: the night did not measure its function"
