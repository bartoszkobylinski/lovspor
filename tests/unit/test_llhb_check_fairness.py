"""Stage 7 command-line fairness gate over frozen benchmark artifacts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from lovspor.llhb.fairness import RunArtifacts
from lovspor.llhb.run_setup import RunSetupError, sha256_text
from lovspor.llhb.schema import canonical_jsonl, dataset_checksum

_SCRIPT = Path(__file__).parents[2] / "benchmarks" / "llhb" / "runner" / "check_fairness.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("llhb_check_fairness", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_fairness = _load_script()


def _write_frozen_artifacts(tmp_path: Path) -> tuple[Path, Path, Path, list[dict[str, str]]]:
    cases = [
        {"case_id": "llhb-v1-C1-001", "question": "Første?"},
        {"case_id": "llhb-v1-C2-001", "question": "Andre?"},
    ]
    cases_path = tmp_path / "llhb-v1.jsonl"
    cases_path.write_bytes(canonical_jsonl(cases))
    lock_path = tmp_path / "llhb-v1.lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "case_count": len(cases),
                "dataset_sha256": dataset_checksum(canonical_jsonl(cases)),
                "corpus_pin": {"lovverk_commit": "6" * 40},
            }
        ),
        encoding="utf-8",
    )
    prompt_path = tmp_path / "system-prompt-v1.txt"
    prompt_path.write_text("Fast prompt.\n", encoding="utf-8")
    return cases_path, lock_path, prompt_path, cases


def test_load_frozen_expectation_is_derived_from_committed_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases_path, lock_path, prompt_path, cases = _write_frozen_artifacts(tmp_path)
    monkeypatch.setattr(check_fairness, "FROZEN_CASES_PATH", cases_path)
    monkeypatch.setattr(check_fairness, "FROZEN_LOCK_PATH", lock_path)
    monkeypatch.setattr(check_fairness, "PROMPT_PATH", prompt_path)

    expected = check_fairness.load_frozen_expectation()

    assert expected.dataset_sha256 == dataset_checksum(canonical_jsonl(cases))
    assert expected.case_ids == ("llhb-v1-C1-001", "llhb-v1-C2-001")
    assert expected.lovverk_commit == "6" * 40
    assert expected.system_prompt_sha256 == sha256_text("Fast prompt.\n")
    assert expected.system_prompt_path == check_fairness.PROMPT_REPO_PATH


def test_load_frozen_expectation_rejects_a_dataset_that_no_longer_matches_its_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases_path, lock_path, prompt_path, _ = _write_frozen_artifacts(tmp_path)
    cases_path.write_text(
        json.dumps({"case_id": "llhb-v1-C8-999", "question": "Byttet?"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_fairness, "FROZEN_CASES_PATH", cases_path)
    monkeypatch.setattr(check_fairness, "FROZEN_LOCK_PATH", lock_path)
    monkeypatch.setattr(check_fairness, "PROMPT_PATH", prompt_path)

    with pytest.raises(RunSetupError, match="lock declares 2"):
        check_fairness.load_frozen_expectation()


def test_plain_mode_does_not_load_or_apply_the_frozen_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = check_fairness.argparse.Namespace(
        control="control-run", treatment="treatment-run", runs_root=Path("unused"), frozen=False
    )
    monkeypatch.setattr(check_fairness, "parse_args", lambda: args)
    empty_run = RunArtifacts(metadata={}, records=[])
    monkeypatch.setattr(check_fairness, "load_run", lambda *_: empty_run)
    monkeypatch.setattr(check_fairness, "load_expected_surface", lambda *_: object())
    monkeypatch.setattr(check_fairness, "check_pair", lambda *_: [])
    monkeypatch.setattr(
        check_fairness,
        "load_frozen_expectation",
        lambda: pytest.fail("plain pilot mode must not read frozen artifacts"),
    )

    assert check_fairness.main() == 0
    assert capsys.readouterr().out.startswith("fair pair:")
