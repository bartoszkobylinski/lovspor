"""Persisted ordering and resume notes through the real run orchestrator."""

from pathlib import Path

import pytest

from lovspor.llhb.orchestrator import run_arm
from lovspor.llhb.results import ResultsStore
from tests.unit.test_llhb_orchestrator import (
    RUN_ID,
    SCHEMA_DIR,
    SUCCESS_STREAM,
    fake_claude,
    make_case,
    make_config,
    make_metadata,
)


def test_run_arm_persists_the_configured_seed_order(tmp_path: Path) -> None:
    """The configured seed determines execution order, independent of input order."""
    bin_dir = fake_claude(tmp_path, SUCCESS_STREAM)
    expected = [f"llhb-v1-C1-{n:03d}" for n in (8, 4, 3, 9, 6, 7, 10, 5, 1, 2)]
    cases = [make_case(f"llhb-v1-C1-{n:03d}") for n in range(1, 11)]

    for name, inputs in (("forward", cases), ("reverse", list(reversed(cases)))):
        store = ResultsStore(runs_root=tmp_path / name, schema_dir=SCHEMA_DIR)
        run_arm(make_config(tmp_path, bin_dir), inputs, make_metadata(), store)

        assert [record["case_id"] for record in store.read_records(RUN_ID)] == expected


@pytest.mark.parametrize("notes", [None, ""])
def test_resume_without_prior_notes_stores_only_the_resume_message(
    tmp_path: Path, notes: str | None
) -> None:
    """Absent or empty operational notes must not introduce a spurious prefix."""
    bin_dir = fake_claude(tmp_path, SUCCESS_STREAM)
    store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
    metadata = make_metadata() if notes is None else make_metadata(notes=notes)
    store.open_run(metadata)

    summary = run_arm(
        make_config(tmp_path, bin_dir, resume=True),
        [make_case("llhb-v1-C1-001")],
        metadata,
        store,
    )

    expected = "resumed: 0 of 1 cases were already recorded"
    assert summary["notes"] == expected
    assert store.load_metadata(RUN_ID)["notes"] == expected
