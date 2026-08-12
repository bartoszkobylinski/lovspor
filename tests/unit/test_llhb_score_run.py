"""Stage 8 score-run corpus-pin integrity checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from lovspor.errors import LovsporError

_SCRIPT = Path(__file__).parents[2] / "benchmarks" / "llhb" / "runner" / "score_run.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("llhb_score_run", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


score_run = _load_script()


def _case(case_id: str, commit: str = "a" * 40) -> dict[str, object]:
    return {
        "case_id": case_id,
        "corpus_pin": {
            "lovverk_commit": commit,
            "manifest_generated_at": "2026-08-12T00:00:00Z",
        },
    }


def test_load_cases_verifies_the_shared_pin_before_returning_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = [_case("llhb-v1-C1-101"), _case("llhb-v1-C2-101")]
    observed: list[tuple[Path, object]] = []
    monkeypatch.setattr(score_run, "load_cases_jsonl", lambda _: cases)
    monkeypatch.setattr(score_run, "verify_pin", lambda path, pin: observed.append((path, pin)))

    result = score_run.load_cases(tmp_path / "cases.jsonl", tmp_path / "lovverk")

    assert result == {case["case_id"]: case for case in cases}
    assert len(observed) == 1
    assert observed[0][0] == tmp_path / "lovverk"
    assert observed[0][1].lovverk_commit == "a" * 40


def test_load_cases_refuses_mixed_pins_without_verifying_either_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = [_case("llhb-v1-C1-101"), _case("llhb-v1-C2-101", "b" * 40)]
    monkeypatch.setattr(score_run, "load_cases_jsonl", lambda _: cases)
    verify_calls: list[object] = []
    monkeypatch.setattr(score_run, "verify_pin", lambda *_: verify_calls.append(object()))

    with pytest.raises(LovsporError, match=r"carries 2 distinct corpus pins; refusing to score$"):
        score_run.load_cases(tmp_path / "cases.jsonl", tmp_path / "lovverk")

    assert verify_calls == []
