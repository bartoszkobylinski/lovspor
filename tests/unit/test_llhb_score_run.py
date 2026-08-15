"""Stage 8 score-run corpus-pin and pair-manifest integrity checks."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from lovspor.errors import LovsporError
from lovspor.llhb.pair_manifest import PairManifest

_SCRIPT = Path(__file__).parents[2] / "benchmarks" / "llhb" / "runner" / "score_run.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("llhb_score_run", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


score_run = _load_script()


def _manifest() -> PairManifest:
    return PairManifest(
        schema_version=1,
        benchmark="llhb-v1",
        analysis_plan_path="benchmarks/llhb/plan.md",
        analysis_plan_sha256="1" * 64,
        dataset_path="benchmarks/llhb/cases.jsonl",
        dataset_sha256="2" * 64,
        system_prompt_path="benchmarks/llhb/prompt.txt",
        system_prompt_sha256="3" * 64,
        scorer_commit="4" * 40,
        runner_commit="5" * 40,
        control_run_id="control-from-manifest",
        control_run_sha256="6" * 64,
        treatment_run_id="treatment-from-manifest",
        treatment_run_sha256="7" * 64,
        model_requested="model",
        corpus_snapshot="8" * 40,
    )


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


def test_score_pair_uses_only_manifest_bound_dataset_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest()
    runs_root = tmp_path / "runs"
    corpus_path = tmp_path / "corpus"
    cases = {"case-1": {"case_id": "case-1"}}
    observed: list[object] = []

    monkeypatch.setattr(
        score_run,
        "load_cases",
        lambda path, corpus: observed.append((path, corpus)) or cases,
    )

    class Store:
        def __init__(self, *, runs_root: Path, schema_dir: Path) -> None:
            observed.append((runs_root, schema_dir))

        def read_records(self, run_id: str) -> list[str]:
            observed.append(run_id)
            return [run_id]

    monkeypatch.setattr(score_run, "ResultsStore", Store)
    monkeypatch.setattr(score_run, "CorpusReader", lambda path: observed.append(path) or object())
    monkeypatch.setattr(
        score_run,
        "CaseScorer",
        lambda reader: observed.append(reader) or object(),
    )
    monkeypatch.setattr(
        score_run,
        "score_arm",
        lambda scorer, loaded_cases, records: (
            observed.append((scorer, loaded_cases, records)) or records
        ),
    )
    monkeypatch.setattr(score_run, "compute_pair_report", lambda control, treatment: "report")

    assert score_run.score_pair(manifest, corpus_path, runs_root) == ("report", 1)
    assert observed[0] == (score_run.REPO_ROOT / manifest.dataset_path, corpus_path)
    assert "control-from-manifest" in observed
    assert "treatment-from-manifest" in observed
