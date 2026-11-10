"""Phase 4A (#856-#859, #861): the four engine defects, checked on the pinned archives.

Runs only where the pinned publicData archives sit in the gitignored cache (see
test_legaltime_pilot.py); a skip means the evidence was not replayed here, not a pass.
Raw Lovdata XML never enters the repository.
"""

from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime.archive import collect, file_sha256, read_members
from lovspor.legaltime.commencement import _blocks
from lovspor.legaltime.instructions import interpret
from lovspor.legaltime.run import build_report, commencement_for, events_for

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
STRAFFELOVEN = "lov/2005-05-20-28"
BARNEVERNSLOVEN = "lov/2021-06-18-97"
IKRAFTSETTINGSLOVEN = "lti/2015/nl-20150619-065.xml"
PINS = {
    "lovtidend-avd1-2001-2025.tar.bz2": (
        "d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0"
    ),
    "lovtidend-avd1-2026.tar.bz2": (
        "600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2"
    ),
}

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in ARCHIVES), reason="no local Lovdata archives"
)


@pytest.fixture(autouse=True)
def _pinned() -> None:
    if not all(file_sha256(CACHE / name) == digest for name, digest in PINS.items()):
        pytest.skip("local archives are not the pinned ones")


def test_no_militaer_straffelov_or_straffeprosessloven_operation_is_straffeloven_s() -> None:
    """#856: § 4 (71 operations) and § 5 (145) of the ikraftsettingslov are unattributed."""
    document = read_members(ARCHIVES[0], [IKRAFTSETTINGSLOVEN])[IKRAFTSETTINGSLOVEN]
    operations = interpret(document).operations
    hosted = [op for op in operations if op.host_section in ("§4", "§5")]
    assert (len([o for o in hosted if o.host_section == "§4"]), len(hosted)) == (71, 216)
    assert {op.act for op in hosted} == {None}
    assert {op.act for op in operations if op.host_section == "§3"} == {STRAFFELOVEN}


def test_the_straffeloven_ladder_applies_nothing_from_the_military_or_procedure_blocks() -> None:
    evidence = collect(STRAFFELOVEN, ARCHIVES)
    report = build_report(evidence, [date(2015, 10, 1), date(2026, 11, 6)], None, ARCHIVES)
    hosted = {
        e["operation"]["id"]
        for e in report["events"]
        if e["law"] == "lov/2015-06-19-65" and e["operation"]["host_section"] in ("§4", "§5")
    }
    assert hosted == set()
    unattributed = {
        e["operation"]["id"]: e["operation"]
        for e in report["unattributed_events"]
        if e["law"] == "lov/2015-06-19-65"
    }
    assert sum(op["host_section"] in ("§4", "§5") for op in unattributed.values()) == 216
    for state in report["states"]:  # 51c5d702 applied 41 of them (§ 4: 11, § 5: 30) at both
        assert not any(i in unattributed for i in state["applied"])
        assert not any(i in unattributed for p in state["provisions"] for i in p["applied"])
        blanket = [p for p in state["act_level_problems"] if "unattributed operation" in p]
        assert any(IKRAFTSETTINGSLOVEN in p for p in blanket), state["at"]


def test_the_naturmangfoldloven_decision_keeps_sentence_two() -> None:
    """#857: FOR-2009-06-19-704's ``Fra samme tidspunkt …`` sentence reaches the grammar."""
    member = "lti/2009/sf-20090619-0704.xml"
    decision = read_members(ARCHIVES[0], [member])[member]
    sentences = [s for _, body in _blocks(decision) for s in body]
    assert any(s.startswith("Fra samme tidspunkt trer endringene i § 78") for s in sentences)
    evidence = collect("lov/2009-06-19-100", ARCHIVES)
    default = commencement_for(evidence, events_for(evidence)).default
    assert (default.status, default.effective) == ("unresolved", None)
    assert "forskrift/2009-06-19-704" in {p.refid for p in default.proofs}


def test_barnevernsloven_unknown_target_unresolved_ops_blanket_every_state() -> None:
    """#858: lov/2024-06-25-53 XI:12.0 / XI:14.0 (unsupported, no address, timing unresolved)."""
    evidence = collect(BARNEVERNSLOVEN, ARCHIVES)
    dates = [date(2022, 12, 31), date(2023, 1, 1), date(2026, 11, 6)]
    report = build_report(evidence, dates, None, ARCHIVES)
    for state in report["states"]:
        for op_id in ("lov/2024-06-25-53#11:XI:12.0", "lov/2024-06-25-53#11:XI:14.0"):
            assert any(
                p.startswith(f"{op_id}: target unknown") for p in state["act_level_problems"]
            )
