"""Resuming an LLHB run that died halfway (#806).

Records are appended per case, but a run that died (a crash, Ctrl-C, the
tool-count cross-check) could not be continued: its directory was refused
and a rerun under a new run id repaid every case. A resume continues the
same run only when its stored metadata is the run being asked for, never
re-runs or rewrites a recorded case, and says in the run's notes that it
was interrupted.
"""

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
