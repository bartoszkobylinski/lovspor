"""Resume preserves experimental order and uses an unambiguous publication marker."""

from pathlib import Path

import pytest

from lovspor.llhb.orchestrator import run_arm
from lovspor.llhb.results import was_resumed
from tests.unit.test_llhb_orchestrator import RUN_ID, make_config, make_metadata
from tests.unit.test_llhb_results import make_record
from tests.unit.test_llhb_resume import _CASES, _calls, _counting_cli, _store


def test_resume_skips_nonprefix_records_without_reshuffling_remaining_cases(tmp_path: Path) -> None:
    """The seeded run order remains fixed when already recorded cases are removed."""
    bin_dir = _counting_cli(tmp_path)
    store = _store(tmp_path)
    run_dir = store.open_run(make_metadata())
    # Seed 42 orders these four ids as 003, 002, 004, 001. Record two
    # nonadjacent positions, in a different append order, before resuming.
    recorded_ids = ["llhb-v1-C1-004", "llhb-v1-C1-003"]
    for case_id in recorded_ids:
        store.append_record(make_record(case_id=case_id))
    captured = (run_dir / "records.jsonl").read_bytes()

    run_arm(
        make_config(tmp_path, bin_dir, resume=True),
        list(reversed(_CASES)),
        make_metadata(),
        _store(tmp_path),
    )

    assert _calls(tmp_path) == 2
    assert [r["case_id"] for r in store.read_records(RUN_ID)] == [
        *recorded_ids,
        "llhb-v1-C1-002",
        "llhb-v1-C1-001",
    ]
    assert (run_dir / "records.jsonl").read_bytes().startswith(captured)


@pytest.mark.parametrize(
    "notes",
    [
        "",
        "operator discussed resumed: 2 of 4 cases were already recorded",
        "not resumed: 2 of 4 cases were already recorded",
        "resumed: 2 of 4 cases were already recorded later",
        "resumed: two of four cases were already recorded",
        "resumed: 2 of 4 cases were already recorded\nadditional text",
    ],
)
def test_resume_marker_does_not_match_free_text_mentions(notes: str) -> None:
    """Only the operational marker emitted by the runner identifies a resumed arm."""
    assert was_resumed({"notes": notes}) is False
