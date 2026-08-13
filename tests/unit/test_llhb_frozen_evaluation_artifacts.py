"""Integrity checks for the committed 2026-08-12 LLHB evaluation artifacts."""

import json
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.schema import load_schema, validate_case

LLHB_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb"
RUNS_DIR = LLHB_DIR / "results" / "runs"
REPORTS_DIR = LLHB_DIR / "results" / "reports"
SCHEMA_DIR = LLHB_DIR / "schema"

RUN_IDS = (
    "llhb-v1-run-20260812-frozen1",
    "llhb-v1-run-20260812-frozen2",
    "llhb-v1-run-20260812-treatfrozen1",
    "llhb-v1-run-20260812-treatfrozen2",
    "llhb-v1-run-20260812-treatfrozen3",
    "llhb-v1-run-20260812-treatfrozen4",
)
PUBLISHED_CONTROL = "llhb-v1-run-20260812-frozen2"
PUBLISHED_TREATMENT = "llhb-v1-run-20260812-treatfrozen4"
REPORTS = (
    ("llhb-v1-run-20260812-frozen2-vs-llhb-v1-run-20260812-treatfrozen4.json", "v1"),
    ("llhb-v1-run-20260812-frozen2-vs-treatfrozen4-scorev2.json", "v2"),
)


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize("run_id", RUN_IDS)
def test_committed_frozen_run_is_schema_valid_and_self_consistent(run_id: str) -> None:
    """Post-run edits must not bypass ResultsStore's write-time validation."""
    run_dir = RUNS_DIR / run_id
    metadata = _read_json(run_dir / "run-metadata.json")
    records = _read_jsonl(run_dir / "records.jsonl")
    metadata_schema = load_schema(SCHEMA_DIR / "run_metadata.schema.json")
    record_schema = load_schema(SCHEMA_DIR / "result_record.schema.json")

    assert validate_case(metadata, metadata_schema) == []
    assert metadata["run_id"] == run_id
    assert records, "a committed evaluation run must contain at least one record"
    for record in records:
        assert validate_case(record, record_schema) == []
        for field in ("run_id", "provider", "model_id", "condition"):
            assert record[field] == metadata[field]

    keys = [(record["case_id"], record.get("repeat_index")) for record in records]
    assert len(keys) == len(set(keys)), "case/repeat records must be unique within a run"

    if metadata.get("finished_at") is not None:
        assert metadata["cases_total"] == len(records)
        assert metadata["cases_completed"] == sum(record["completed"] for record in records)
        assert metadata["errors_total"] == sum(not record["completed"] for record in records)


@pytest.mark.parametrize("run_id", RUN_IDS)
def test_committed_run_contains_only_frozen_dataset_cases(run_id: str) -> None:
    frozen_ids = {
        case["case_id"] for case in _read_jsonl(LLHB_DIR / "dataset" / "frozen" / "llhb-v1.jsonl")
    }
    records = _read_jsonl(RUNS_DIR / run_id / "records.jsonl")

    assert {record["case_id"] for record in records} <= frozen_ids


@pytest.mark.parametrize(("filename", "version"), REPORTS)
def test_published_report_has_consistent_provenance_and_arithmetic(
    filename: str, version: str
) -> None:
    report = _read_json(REPORTS_DIR / filename)

    assert report["control_run"] == PUBLISHED_CONTROL
    assert report["treatment_run"] == PUBLISHED_TREATMENT
    assert report["cases_scored"] == 250
    assert report["scorer_version"] == f"llhb-score-{version}"
    assert report["metrics_version"] == f"llhb-metrics-{version}"
    assert report["bootstrap"] == {"resamples": 2000, "seed": 42}

    for group in (report["metrics"], report["per_category"]):
        for comparison in group.values():
            control = comparison["control"]
            treatment = comparison["treatment"]
            for estimate in (control, treatment):
                if estimate is None:
                    continue
                assert 0 <= estimate["numerator"] <= estimate["denominator"]
                assert estimate["rate"] == pytest.approx(
                    estimate["numerator"] / estimate["denominator"]
                )
                assert 0 <= estimate["ci_low"] <= estimate["ci_high"] <= 1
            if control is None or treatment is None:
                assert comparison["delta"] is None
                assert comparison["delta_ci_low"] is None
                assert comparison["delta_ci_high"] is None
            else:
                assert comparison["delta"] == pytest.approx(control["rate"] - treatment["rate"])
                assert comparison["delta_ci_low"] <= comparison["delta_ci_high"]
