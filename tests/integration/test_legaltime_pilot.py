"""Replay the #744 pilot acts from the pinned archives and compare with the evidence files.

Raw Lovdata XML is never committed, so this runs only where
`lovspor ops legal-time fetch --dest data/cache/legaltime` (or a manual copy of
the same archives) left the archives in the gitignored cache, and only when
those archives are the pinned ones: a different archive digest is a different
input, not a failure of the replay. A skip here means the pilot was not
replayed on this machine; it is not a pass.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from lovspor.legaltime.archive import collect, dump_json, file_sha256, load_oracle
from lovspor.legaltime.run import build_report

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
EVIDENCE = sorted((ROOT / "docs/research/data").glob("legal-time-replay-2026-11-06-*.json"))
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
ORACLE = CACHE / "gjeldende-lover.tar.bz2"

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in [*ARCHIVES, ORACLE]), reason="no local Lovdata archives"
)


def _pinned(evidence: dict[str, object]) -> bool:
    inputs = evidence["inputs"]
    assert isinstance(inputs, list)
    return all(file_sha256(CACHE / pin["filename"]) == pin["sha256"] for pin in inputs)


@pytest.mark.parametrize("evidence_path", EVIDENCE, ids=[p.stem.split("-")[-1] for p in EVIDENCE])
def test_pilot_report_is_reproduced_byte_for_byte(evidence_path: Path) -> None:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not _pinned(evidence):
        pytest.skip("local archives are not the pinned ones")
    dates = [date.fromisoformat(state["at"]) for state in evidence["states"]]
    collected = collect(evidence["act"], ARCHIVES)
    oracle = load_oracle(ORACLE, evidence["act"])
    report = build_report(collected, dates, oracle, ARCHIVES)
    assert dump_json(report) + "\n" == evidence_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("evidence_path", EVIDENCE, ids=[p.stem.split("-")[-1] for p in EVIDENCE])
def test_pilot_end_state_reconciles_with_no_unresolved_section(evidence_path: Path) -> None:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    final = evidence["verification"][-1]
    assert final["at"] == "2026-11-06"
    assert set(final["counts"]) <= {
        "exact_raw",
        "exact_normalized",
        "editorial_omission",
        "oracle_annex",
    }
    assert final["notes_without_replay_event"] == []
    assert final["replay_events_without_note"] == []
    assert evidence["states"][-1]["unresolved"] == []
    assert evidence["states"][-1]["act_level_problems"] == []


def test_day_before_each_event_differs_only_in_its_targets() -> None:
    """Readback: the day before a dated event, exactly that event's targets are different."""
    evidence = json.loads(
        (ROOT / "docs/research/data/legal-time-replay-2026-11-06-arveloven.json").read_text()
    )
    if not _pinned(evidence):
        pytest.skip("local archives are not the pinned ones")
    collected = collect(evidence["act"], ARCHIVES)
    days = sorted(
        {
            date.fromisoformat(event["timing"]["effective"])
            for event in evidence["events"]
            if event["timing"]["status"] == "dated" and event["timing"]["effective"] > "2021-01-01"
        }
    )
    for day in days:
        before, after = (
            build_report(collected, [when], None, ARCHIVES)["states"][0]
            for when in (day - timedelta(days=1), day)
        )
        newly = set(after["applied"]) - set(before["applied"])
        assert newly, day
        earlier = {p["key"]: p for p in before["provisions"]}
        changed = {p["key"] for p in after["provisions"] if earlier.get(p["key"]) != p}
        touched = {p["key"] for p in after["provisions"] if set(p["applied"]) & newly}
        assert changed == touched, (day, changed ^ touched)
