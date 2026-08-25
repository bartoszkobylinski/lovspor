"""Integrity checks for the committed ruling #31 single-arm artifacts."""

import json
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.schema import load_schema, validate_case

LLHB_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb"
RUNS_DIR = LLHB_DIR / "results" / "runs"
REPORTS_DIR = LLHB_DIR / "results" / "reports"
SCHEMA_DIR = LLHB_DIR / "schema"
NEW_RUN_ID = "llhb-v1-run-20260825-nmctrl1"
ARM_REPORTS = (
    "llhb-v1-run-20260812-frozen2-arm.json",
    f"{NEW_RUN_ID}-arm.json",
)
MEAN_METRICS = {"valid_citations_per_answer", "invalid_citations_per_answer"}


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_new_control_run_is_schema_valid_and_is_exactly_the_frozen_dataset() -> None:
    run_dir = RUNS_DIR / NEW_RUN_ID
    metadata = _read_json(run_dir / "run-metadata.json")
    records = _read_jsonl(run_dir / "records.jsonl")
    frozen_ids = {
        case["case_id"] for case in _read_jsonl(LLHB_DIR / "dataset" / "frozen" / "llhb-v1.jsonl")
    }
    metadata_schema = load_schema(SCHEMA_DIR / "run_metadata.schema.json")
    record_schema = load_schema(SCHEMA_DIR / "result_record.schema.json")

    assert validate_case(metadata, metadata_schema) == []
    assert metadata["run_id"] == NEW_RUN_ID
    assert metadata["condition"] == "control"
    assert metadata["tool_config"] is None
    assert len(records) == metadata["cases_total"] == metadata["cases_completed"] == 250
    assert metadata["errors_total"] == 0
    assert {record["case_id"] for record in records} == frozen_ids
    assert len({record["case_id"] for record in records}) == len(records)
    for record in records:
        assert validate_case(record, record_schema) == []
        for field in ("run_id", "provider", "model_id", "condition"):
            assert record[field] == metadata[field]


@pytest.mark.parametrize("filename", ARM_REPORTS)
def test_single_arm_report_matches_its_run_and_has_consistent_arithmetic(filename: str) -> None:
    report = _read_json(REPORTS_DIR / filename)
    metadata = _read_json(RUNS_DIR / report["run_id"] / "run-metadata.json")

    assert report["report_kind"] == "single-arm"
    assert "no pair manifest, no delta, no verdict" in report["epistemic_status"]
    assert report["cases_scored"] == metadata["cases_completed"] == 250
    assert report["scorer_version"] == "llhb-score-v2"
    assert report["metrics_version"] == "llhb-metrics-v3"
    for field in (
        "provider",
        "model_id",
        "condition",
        "dataset_checksum",
        "lovverk_commit",
        "lovspor_commit",
        "system_prompt_sha256",
        "sampling",
    ):
        assert report[field] == metadata[field]

    assert "primary" not in report and "delta" not in report
    for name, estimate in report["metrics"].items():
        assert "delta" not in estimate
        numerator = estimate["numerator"]
        denominator = estimate["denominator"]
        if denominator == 0:
            assert (numerator, estimate["rate"], estimate["ci_low"], estimate["ci_high"]) == (
                0.0,
                None,
                None,
                None,
            )
            continue
        assert numerator >= 0
        if name not in MEAN_METRICS:
            assert numerator <= denominator
            assert 0 <= estimate["ci_low"] <= estimate["ci_high"] <= 1
        assert estimate["rate"] == pytest.approx(numerator / denominator)

    for counts_name in ("outcomes", "c8_outcomes"):
        counts = report[counts_name]
        assert counts["total"] == sum(
            counts[name]
            for name in ("PASS", "FAIL", "UNRESOLVED_SEMANTIC", "SCORER_ERROR", "MODEL_ERROR")
        )
