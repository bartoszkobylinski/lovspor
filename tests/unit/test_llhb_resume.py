"""Resuming an LLHB run that died halfway (#806).

Records are appended per case, but a run that died (a crash, Ctrl-C, the
tool-count cross-check) could not be continued: its directory was refused
and a rerun under a new run id repaid every case. A resume continues the
same run only when its stored metadata is the run being asked for, never
re-runs or rewrites a recorded case, and says in the run's notes that it
was interrupted.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.orchestrator import run_arm
from lovspor.llhb.results import ResultsStore, ResultsStoreError
from tests.unit.test_llhb_orchestrator import (
    RUN_ID,
    SCHEMA_DIR,
    SUCCESS_STREAM,
    fake_claude,
    make_case,
    make_config,
    make_metadata,
)
from tests.unit.test_llhb_results import make_record

_CASES = [make_case(f"llhb-v1-C1-00{n}") for n in range(1, 5)]


class _InterruptedError(Exception):
    """Stands in for a crash or Ctrl-C between two cases."""


def _counting_cli(tmp_path: Path) -> Path:
    calls = tmp_path / "calls"
    return fake_claude(tmp_path, f'echo x >> "{calls}"; {SUCCESS_STREAM}')


def _calls(tmp_path: Path) -> int:
    path = tmp_path / "calls"
    return len(path.read_text().splitlines()) if path.exists() else 0


def _store(tmp_path: Path) -> ResultsStore:
    return ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)


def _die_after(store: ResultsStore, records: int, monkeypatch: pytest.MonkeyPatch) -> None:
    real = store.append_record
    seen: list[dict[str, Any]] = []

    def append(record: dict[str, Any]) -> None:
        if len(seen) == records:
            raise _InterruptedError
        real(record)
        seen.append(record)

    monkeypatch.setattr(store, "append_record", append)


def _interrupted_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = _counting_cli(tmp_path)
    store = _store(tmp_path)
    _die_after(store, 2, monkeypatch)
    with pytest.raises(_InterruptedError):
        run_arm(make_config(tmp_path, bin_dir), _CASES, make_metadata(), store)
    assert _calls(tmp_path) == 3
    return bin_dir


def test_a_resume_runs_only_the_cases_without_a_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _interrupted_run(tmp_path, monkeypatch)
    store = _store(tmp_path)
    recorded = [record["case_id"] for record in store.read_records(RUN_ID)]

    summary = run_arm(make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), store)

    assert _calls(tmp_path) == 3 + 2
    records = store.read_records(RUN_ID)
    assert [record["case_id"] for record in records][:2] == recorded
    assert sorted(record["case_id"] for record in records) == sorted(c["case_id"] for c in _CASES)
    assert summary["cases_total"] == 4
    assert summary["cases_completed"] == 4
    assert summary["errors_total"] == 0


def test_a_resumed_run_says_so_in_its_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _interrupted_run(tmp_path, monkeypatch)
    store = _store(tmp_path)

    run_arm(make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), store)

    metadata = store.load_metadata(RUN_ID)
    assert "resumed: 2 of 4 cases were already recorded" in metadata["notes"]
    assert metadata["started_at"] == make_metadata()["started_at"]


def test_a_resume_keeps_the_stored_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = _counting_cli(tmp_path)
    store = _store(tmp_path)
    _die_after(store, 1, monkeypatch)
    with pytest.raises(_InterruptedError):
        run_arm(make_config(tmp_path, bin_dir), _CASES, make_metadata(notes="pilot"), store)
    store = _store(tmp_path)

    run_arm(
        make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(notes="pilot"), store
    )

    assert store.load_metadata(RUN_ID)["notes"].startswith("pilot; resumed: 1 of 4")


@pytest.mark.parametrize(
    "changed",
    [
        {"model_id": "claude-sonnet-5"},
        {"lovspor_commit": "89abcde"},
        {"case_order_seed": 7},
        {"dataset_checksum": "c" * 64},
    ],
)
def test_a_resume_of_a_different_run_is_refused_before_any_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: dict[str, Any]
) -> None:
    bin_dir = _interrupted_run(tmp_path, monkeypatch)
    store = _store(tmp_path)

    with pytest.raises(ResultsStoreError, match="does not match the stored run"):
        run_arm(
            make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(**changed), store
        )

    assert _calls(tmp_path) == 3
    assert len(store.read_records(RUN_ID)) == 2


def test_a_resume_may_restart_later_than_the_run_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _interrupted_run(tmp_path, monkeypatch)
    store = _store(tmp_path)
    later = make_metadata(started_at="2026-08-09T01:00:00Z")

    summary = run_arm(make_config(tmp_path, bin_dir, resume=True), _CASES, later, store)

    assert summary["cases_completed"] == 4


def test_a_finished_run_cannot_be_resumed(tmp_path: Path) -> None:
    bin_dir = _counting_cli(tmp_path)
    store = _store(tmp_path)
    run_arm(make_config(tmp_path, bin_dir), _CASES, make_metadata(), store)

    with pytest.raises(ResultsStoreError, match="already finished"):
        run_arm(make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), store)

    assert _calls(tmp_path) == 4


def test_a_run_that_never_started_cannot_be_resumed(tmp_path: Path) -> None:
    bin_dir = _counting_cli(tmp_path)

    with pytest.raises(ResultsStoreError, match="unknown run"):
        run_arm(
            make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), _store(tmp_path)
        )

    assert _calls(tmp_path) == 0


def test_without_resume_an_existing_run_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _interrupted_run(tmp_path, monkeypatch)

    with pytest.raises(ResultsStoreError, match="already exists"):
        run_arm(make_config(tmp_path, bin_dir), _CASES, make_metadata(), _store(tmp_path))

    assert _calls(tmp_path) == 3


@pytest.mark.parametrize("recorded_count", [1, 4])
def test_resume_preserves_failed_records_and_counts_the_whole_run(
    tmp_path: Path, recorded_count: int
) -> None:
    """A recorded failure is still recorded; it must not be retried on resume."""
    bin_dir = _counting_cli(tmp_path)
    store = _store(tmp_path)
    run_dir = store.open_run(make_metadata())
    for index, case in enumerate(_CASES[:recorded_count]):
        store.append_record(
            make_record(
                case_id=case["case_id"],
                completed=index != 0,
                final_answer=None if index == 0 else "Stored answer",
            )
        )
    records_path = run_dir / "records.jsonl"
    original_bytes = records_path.read_bytes()
    original_records = store.read_records(RUN_ID)

    # A fresh store models a different process after the interruption.
    store = _store(tmp_path)
    summary = run_arm(make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), store)

    assert _calls(tmp_path) == 4 - recorded_count
    assert records_path.read_bytes().startswith(original_bytes)
    assert store.read_records(RUN_ID)[:recorded_count] == original_records
    assert len(store.read_records(RUN_ID)) == 4
    assert summary["cases_total"] == 4
    assert summary["cases_completed"] == 3
    assert summary["errors_total"] == 1
    assert store.load_metadata(RUN_ID)["finished_at"] == summary["finished_at"]
    if recorded_count == 4:
        assert records_path.read_bytes() == original_bytes


def test_resume_before_first_record_still_documents_the_interruption(tmp_path: Path) -> None:
    """The runner's --resume contract says the run's notes say it was resumed."""
    bin_dir = _counting_cli(tmp_path)
    store = _store(tmp_path)
    store.open_run(make_metadata(notes="original pilot"))

    run_arm(make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), store)

    assert _calls(tmp_path) == 4
    metadata = store.load_metadata(RUN_ID)
    assert metadata["cases_completed"] == 4
    assert metadata["notes"] == "original pilot; resumed: 0 of 4 cases were already recorded"


@pytest.mark.parametrize(
    ("stored_extra", "requested_extra"),
    [
        ({}, {"max_turns": 5}),
        ({"max_turns": 5}, {}),
        ({}, {"sampling": {"temperature": 0.5}}),
        ({}, {"system_prompt_sha256": "c" * 64}),
    ],
)
def test_resume_rejects_added_removed_or_nested_configuration_changes_without_writes(
    tmp_path: Path, stored_extra: dict[str, Any], requested_extra: dict[str, Any]
) -> None:
    """Every non-completion field must describe the same stored experiment."""
    store = _store(tmp_path)
    run_dir = store.open_run(make_metadata(**stored_extra))
    metadata_path = run_dir / "run-metadata.json"
    original_bytes = metadata_path.read_bytes()

    with pytest.raises(ResultsStoreError, match="does not match the stored run"):
        store.resume_run(make_metadata(**requested_extra))

    assert metadata_path.read_bytes() == original_bytes
    assert not (run_dir / "records.jsonl").exists()


@pytest.mark.parametrize(
    "changed",
    [
        {"transport": "function-bridge"},
        {"tools": ["mcp__lovverk__search"]},
        {"tool_schema_sha256": "d" * 64},
        {"backend": "different checkout"},
    ],
)
def test_resume_rejects_changed_tool_configuration_without_writes(
    tmp_path: Path, changed: dict[str, Any]
) -> None:
    """The runner's resume contract explicitly requires matching tools."""
    tools = {
        "transport": "native-mcp",
        "tools": ["mcp__lovverk__get_section"],
        "tool_schema_sha256": "c" * 64,
        "backend": "local checkout",
    }
    store = _store(tmp_path)
    run_dir = store.open_run(make_metadata(condition="lovspor", tool_config=tools))
    store.append_record(make_record(condition="lovspor"))
    original = {path.name: path.read_bytes() for path in run_dir.iterdir()}

    with pytest.raises(ResultsStoreError, match="tool_config.*does not match"):
        store.resume_run(make_metadata(condition="lovspor", tool_config={**tools, **changed}))

    assert {path.name: path.read_bytes() for path in run_dir.iterdir()} == original


def test_resume_validates_requested_metadata_before_reopening(tmp_path: Path) -> None:
    """Completion fields may differ, but still must satisfy the stored schema."""
    store = _store(tmp_path)
    run_dir = store.open_run(make_metadata())
    metadata_path = run_dir / "run-metadata.json"
    original = metadata_path.read_bytes()

    with pytest.raises(ResultsStoreError, match="invalid run metadata.*cases_total"):
        store.resume_run(make_metadata(cases_total=-1))

    assert metadata_path.read_bytes() == original
    assert not (run_dir / "records.jsonl").exists()


def test_resume_accepts_null_finish_and_completion_changes_without_rewriting(
    tmp_path: Path,
) -> None:
    """resume_run exempts start/completion fields and preserves captured metadata."""
    store = _store(tmp_path)
    run_dir = store.open_run(make_metadata(finished_at=None, notes="captured notes"))
    store.append_record(make_record())
    original = {path.name: path.read_bytes() for path in run_dir.iterdir()}

    reopened = _store(tmp_path).resume_run(
        make_metadata(
            started_at="2026-08-09T01:00:00Z",
            cases_total=4,
            cases_completed=1,
            errors_total=0,
            notes="new invocation",
            evaluator_version="scorer-v1",
        )
    )

    assert reopened == run_dir
    assert {path.name: path.read_bytes() for path in run_dir.iterdir()} == original


def test_resume_after_a_second_interruption_preserves_every_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same unfinished-run contract applies when a resume itself crashes."""
    bin_dir = _interrupted_run(tmp_path, monkeypatch)
    store = _store(tmp_path)
    _die_after(store, 1, monkeypatch)
    with pytest.raises(_InterruptedError):
        run_arm(make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), store)

    records_path = tmp_path / "runs" / RUN_ID / "records.jsonl"
    captured = records_path.read_bytes()
    assert len(store.read_records(RUN_ID)) == 3
    assert store.load_metadata(RUN_ID).get("finished_at") is None
    calls_before = _calls(tmp_path)
    store = _store(tmp_path)

    summary = run_arm(make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), store)

    assert _calls(tmp_path) == calls_before + 1
    assert records_path.read_bytes().startswith(captured)
    assert sorted(r["case_id"] for r in store.read_records(RUN_ID)) == sorted(
        case["case_id"] for case in _CASES
    )
    assert summary["cases_completed"] == 4
    assert summary["errors_total"] == 0
    assert store.load_metadata(RUN_ID)["notes"] == ("resumed: 3 of 4 cases were already recorded")


def test_resume_refuses_a_partial_record_before_execution_or_writes(tmp_path: Path) -> None:
    """Append-only capture must not overwrite or silently discard a torn JSONL tail."""
    bin_dir = _counting_cli(tmp_path)
    store = _store(tmp_path)
    run_dir = store.open_run(make_metadata())
    store.append_record(make_record())
    with (run_dir / "records.jsonl").open("ab") as handle:
        handle.write(b'{"case_id":')
    captured = {path.name: path.read_bytes() for path in run_dir.iterdir()}

    with pytest.raises(json.JSONDecodeError):
        run_arm(
            make_config(tmp_path, bin_dir, resume=True), _CASES, make_metadata(), _store(tmp_path)
        )

    assert _calls(tmp_path) == 0
    assert {path.name: path.read_bytes() for path in run_dir.iterdir()} == captured
