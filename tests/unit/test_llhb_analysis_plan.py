"""Integrity checks for the frozen Fable confirmatory analysis plan."""

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from lovspor.llhb.schema import canonical_jsonl, dataset_checksum

LLHB_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb"
PLAN_PATH = LLHB_DIR / "ANALYSIS-PLAN-fable5-v1.md"
FROZEN_DIR = LLHB_DIR / "dataset" / "frozen"
SCHEMA_PATH = LLHB_DIR / "schema" / "run_metadata.schema.json"
SCORE_RUN_PATH = LLHB_DIR / "runner" / "score_run.py"


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_confirmatory_plan_pins_the_actual_frozen_dataset_and_corpus() -> None:
    """The preregistration must describe the experiment its frozen inputs define."""
    plan = PLAN_PATH.read_text(encoding="utf-8")
    lock = _read_json(FROZEN_DIR / "llhb-v1.lock.json")
    cases = _read_jsonl(FROZEN_DIR / "llhb-v1.jsonl")

    checksum_match = re.search(r"canonical JSONL checksum\s+`([0-9a-f]{64})`", plan)
    case_count_match = re.search(r"LLHB v1 frozen set, (\d+) cases", plan)
    corpus_pin_match = re.search(r"lovverk `([0-9a-f]+)`", plan)

    assert checksum_match is not None
    assert case_count_match is not None
    assert corpus_pin_match is not None
    assert checksum_match.group(1) == lock["dataset_sha256"]
    assert checksum_match.group(1) == dataset_checksum(canonical_jsonl(cases))
    assert int(case_count_match.group(1)) == lock["case_count"] == len(cases)
    assert lock["corpus_pin"]["lovverk_commit"].startswith(corpus_pin_match.group(1))


def test_run_metadata_requires_the_confirmatory_analysis_plan_hash() -> None:
    schema = _read_json(SCHEMA_PATH)

    assert "analysis_plan_sha256" in schema["required"]
    assert schema["properties"]["analysis_plan_sha256"]["pattern"] == "^[0-9a-f]{64}$"


def test_aggregate_scoring_cli_requires_a_pair_manifest() -> None:
    result = subprocess.run(
        ["uv", "run", "python", str(SCORE_RUN_PATH), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--manifest" in result.stdout
