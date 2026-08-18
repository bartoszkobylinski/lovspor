"""Stage 5 results store: run dirs, run metadata, per-case JSONL records."""

import json
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.results import ResultsStore, ResultsStoreError, new_run_id

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb" / "schema"

RUN_ID = "llhb-v1-run-20260808-pilot1"


def make_metadata(**overrides: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "run_id": RUN_ID,
        "llhb_version": "1.0",
        "dataset_checksum": "a" * 64,
        "provider": "anthropic",
        "model_id": "claude-opus-5",
        "condition": "control",
        "analysis_plan_sha256": "a" * 64,
        "system_prompt_sha256": "b" * 64,
        "sampling": {"temperature": 0.0},
        "started_at": "2026-08-08T12:00:00Z",
        "lovspor_commit": "0123abc",
        "lovverk_commit": "0" * 40,
        "runner_commit": "4567def",
        "case_order_seed": 42,
    }
    metadata.update(overrides)
    return metadata


def make_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "run_id": RUN_ID,
        "case_id": "llhb-v1-C1-001",
        "provider": "anthropic",
        "model_id": "claude-opus-5",
        "condition": "control",
        "final_answer": "Svar fra modellen.",
        "tool_calls": [],
        "timing": {"started_at": "2026-08-08T12:00:01Z", "total_ms": 1234},
        "errors": [],
        "completed": True,
    }
    record.update(overrides)
    return record


@pytest.fixture
def store(tmp_path: Path) -> ResultsStore:
    return ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)


class TestNewRunId:
    def test_builds_schema_valid_id(self) -> None:
        assert new_run_id("20260808", "pilot1") == RUN_ID

    def test_rejects_bad_date(self) -> None:
        with pytest.raises(ResultsStoreError):
            new_run_id("2026-08-08", "pilot1")

    def test_rejects_bad_suffix(self) -> None:
        with pytest.raises(ResultsStoreError):
            new_run_id("20260808", "PILOT")


class TestOpenRun:
    def test_writes_metadata_file(self, store: ResultsStore, tmp_path: Path) -> None:
        run_dir = store.open_run(make_metadata())

        assert run_dir == tmp_path / "runs" / RUN_ID
        written = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
        assert written == make_metadata()

    def test_rejects_invalid_metadata_before_any_write(
        self, store: ResultsStore, tmp_path: Path
    ) -> None:
        bad = make_metadata()
        del bad["dataset_checksum"]

        with pytest.raises(ResultsStoreError, match="dataset_checksum"):
            store.open_run(bad)
        assert not (tmp_path / "runs" / RUN_ID).exists()

    def test_rejects_lovspor_condition_without_tool_config(self, store: ResultsStore) -> None:
        with pytest.raises(ResultsStoreError, match="tool_config"):
            store.open_run(make_metadata(condition="lovspor"))

    def test_refuses_existing_run(self, store: ResultsStore) -> None:
        store.open_run(make_metadata())

        with pytest.raises(ResultsStoreError, match="exists"):
            store.open_run(make_metadata())


class TestAppendRecord:
    def test_round_trips_records(self, store: ResultsStore) -> None:
        store.open_run(make_metadata())
        first = make_record()
        second = make_record(case_id="llhb-v1-C2-007", final_answer=None, completed=False)

        store.append_record(first)
        store.append_record(second)

        assert store.read_records(RUN_ID) == [first, second]

    def test_rejects_schema_violation(self, store: ResultsStore) -> None:
        store.open_run(make_metadata())
        bad = make_record()
        del bad["final_answer"]

        with pytest.raises(ResultsStoreError, match="final_answer"):
            store.append_record(bad)

    def test_rejects_record_for_unknown_run(self, store: ResultsStore) -> None:
        with pytest.raises(ResultsStoreError, match="unknown run"):
            store.append_record(make_record())

    def test_rejects_metadata_mismatch(self, store: ResultsStore) -> None:
        store.open_run(make_metadata())

        with pytest.raises(ResultsStoreError, match="condition"):
            store.append_record(make_record(condition="lovspor"))

    def test_rejects_duplicate_case(self, store: ResultsStore) -> None:
        store.open_run(make_metadata())
        store.append_record(make_record())

        with pytest.raises(ResultsStoreError, match="duplicate"):
            store.append_record(make_record())

    def test_duplicate_detection_survives_reopen(self, tmp_path: Path) -> None:
        first = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        first.open_run(make_metadata())
        first.append_record(make_record())

        reopened = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        with pytest.raises(ResultsStoreError, match="duplicate"):
            reopened.append_record(make_record())

    def test_allows_stability_repeats(self, store: ResultsStore) -> None:
        store.open_run(make_metadata())
        store.append_record(make_record(repeat_index=1))
        store.append_record(make_record(repeat_index=2))

        assert len(store.read_records(RUN_ID)) == 2


class TestRunIdConfinement:
    def test_finalize_rejects_traversal_run_id(self, store: ResultsStore) -> None:
        with pytest.raises(ResultsStoreError, match="invalid run id"):
            store.finalize_run("../../outside", {"finished_at": "2026-08-08T13:00:00Z"})

    def test_read_records_rejects_invalid_run_id(self, store: ResultsStore) -> None:
        with pytest.raises(ResultsStoreError, match="invalid run id"):
            store.read_records("llhb-v1-run-20260808-..")


class TestSurvivorRegressions:
    def test_records_file_is_named_records_jsonl(self, store: ResultsStore) -> None:
        run_dir = store.open_run(make_metadata())
        store.append_record(make_record())

        assert (run_dir / "records.jsonl").is_file()

    def test_reopen_dedup_distinguishes_repeat_index(self, tmp_path: Path) -> None:
        first = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        first.open_run(make_metadata())
        first.append_record(make_record(repeat_index=1))

        reopened = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        with pytest.raises(ResultsStoreError, match="duplicate"):
            reopened.append_record(make_record(repeat_index=1))
        reopened.append_record(make_record(repeat_index=2))

    def test_finalize_accepts_notes_and_evaluator_version(
        self, store: ResultsStore, tmp_path: Path
    ) -> None:
        store.open_run(make_metadata())

        store.finalize_run(RUN_ID, {"notes": "cap hit twice", "evaluator_version": "scorer-v1"})

        path = tmp_path / "runs" / RUN_ID / "run-metadata.json"
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["notes"] == "cap hit twice"
        assert written["evaluator_version"] == "scorer-v1"


class TestFinalizeRun:
    def test_updates_metadata(self, store: ResultsStore, tmp_path: Path) -> None:
        store.open_run(make_metadata())

        store.finalize_run(
            RUN_ID,
            {
                "finished_at": "2026-08-08T13:00:00Z",
                "cases_total": 250,
                "cases_completed": 250,
                "errors_total": 0,
            },
        )

        path = tmp_path / "runs" / RUN_ID / "run-metadata.json"
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["finished_at"] == "2026-08-08T13:00:00Z"
        assert written["cases_total"] == 250
        assert written["run_id"] == RUN_ID

    def test_rejects_unknown_field(self, store: ResultsStore) -> None:
        store.open_run(make_metadata())

        with pytest.raises(ResultsStoreError, match="not finalizable"):
            store.finalize_run(RUN_ID, {"provider": "openai"})

    def test_rejects_schema_breaking_update(self, store: ResultsStore) -> None:
        store.open_run(make_metadata())

        with pytest.raises(ResultsStoreError, match="cases_total"):
            store.finalize_run(RUN_ID, {"cases_total": -1})
