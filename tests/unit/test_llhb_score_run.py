"""Stage 8 score-run corpus-pin and pair-manifest integrity checks."""

from __future__ import annotations

import argparse
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


def test_the_manifest_argument_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ruling #30(d): there is no manifest-less path into aggregate scoring."""
    monkeypatch.setattr("sys.argv", ["score_run.py", "--corpus-path", "x"])

    with pytest.raises(SystemExit):
        score_run.parse_args()


def test_scoring_never_runs_on_a_manifest_that_fails_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verification must gate scoring, not decorate it — a failing hash
    stops main() before a single record is read."""
    calls: list[str] = []
    args = argparse.Namespace(
        manifest=Path("m.json"), corpus_path=Path("c"), runs_root=Path("r"), out=None
    )
    monkeypatch.setattr(score_run, "parse_args", lambda: args)
    monkeypatch.setattr(
        score_run, "load_pair_manifest", lambda _path: calls.append("load") or object()
    )

    def _refuse(_manifest: object, _repo: Path, _runs: Path) -> None:
        calls.append("verify")
        raise LovsporError("hash mismatch")

    monkeypatch.setattr(score_run, "verify_pair_manifest", _refuse)
    monkeypatch.setattr(
        score_run, "score_pair", lambda *_a: pytest.fail("scored past a failing manifest")
    )

    with pytest.raises(LovsporError, match="hash mismatch"):
        score_run.main()

    assert calls == ["load", "verify"]
