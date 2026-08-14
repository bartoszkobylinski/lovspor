"""Stability-report aggregation and committed artifact integrity."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lovspor.llhb.metrics import PairReport
from lovspor.llhb.schema import load_schema, validate_case

LLHB_DIR = Path(__file__).parents[2] / "benchmarks" / "llhb"
RUNS_DIR = LLHB_DIR / "results" / "runs"
REPORT_PATH = LLHB_DIR / "results" / "reports" / "llhb-v1-stability30x5.json"
SCRIPT_PATH = LLHB_DIR / "runner" / "stability_report.py"
RUN_IDS = tuple(
    f"llhb-v1-run-20260813-stab{arm}{repeat}" for arm in ("c", "t") for repeat in range(1, 6)
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("llhb_stability_report", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize("run_id", RUN_IDS)
def test_committed_stability_run_is_schema_valid_and_complete(run_id: str) -> None:
    run_dir = RUNS_DIR / run_id
    metadata = _read_json(run_dir / "run-metadata.json")
    records = _read_jsonl(run_dir / "records.jsonl")

    assert (
        validate_case(metadata, load_schema(LLHB_DIR / "schema" / "run_metadata.schema.json")) == []
    )
    assert len(records) == 30
    assert metadata["run_id"] == run_id
    assert metadata["cases_total"] == 30
    assert metadata["cases_completed"] == sum(record["completed"] for record in records)
    assert metadata["errors_total"] == sum(not record["completed"] for record in records)
    assert {record["run_id"] for record in records} == {run_id}
    assert len({record["case_id"] for record in records}) == 30
    record_schema = load_schema(LLHB_DIR / "schema" / "result_record.schema.json")
    assert all(validate_case(record, record_schema) == [] for record in records)


def test_all_stability_repeats_cover_the_same_case_subset() -> None:
    case_sets = [
        {record["case_id"] for record in _read_jsonl(RUNS_DIR / run_id / "records.jsonl")}
        for run_id in RUN_IDS
    ]

    assert all(case_ids == case_sets[0] for case_ids in case_sets[1:])


def test_committed_report_summary_is_recomputed_from_repeat_reports() -> None:
    document = _read_json(REPORT_PATH)
    reports = [PairReport.model_validate(report) for report in document["per_repeat_reports"]]

    assert document["repeats"] == 5
    assert document["control_runs"] == list(RUN_IDS[:5])
    assert document["treatment_runs"] == list(RUN_IDS[5:])
    assert document["metrics"] == script.summarize_metrics(reports)
    assert document["case_stability"]["cases_per_run"] == 30


def test_parse_args_rejects_zero_repeats(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT_PATH),
            "--control",
            ",",
            "--treatment",
            ",",
            "--corpus-path",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit):
        script.parse_args()
