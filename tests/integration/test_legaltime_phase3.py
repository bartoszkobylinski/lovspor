"""Phase 3 (#744): the two target acts' commencement, replayed from the pinned archives.

Runs only where the pinned publicData archives sit in the gitignored cache
(see test_legaltime_pilot.py); a skip means the evidence was not replayed on
this machine, not a pass. Raw Lovdata XML never enters the repository.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime.archive import collect, dump_json, file_sha256, load_oracle
from lovspor.legaltime.run import build_report, commencement_for, events_for
from lovspor.legaltime.units import Unit

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
ORACLE = CACHE / "gjeldende-lover.tar.bz2"
EVIDENCE = ROOT / "docs/research/data/legal-time-replay-2026-11-08-barnevernsloven.json"
BARNEVERNSLOVEN = "lov/2021-06-18-97"
STRAFFELOVEN = "lov/2005-05-20-28"
PINS = {
    "lovtidend-avd1-2001-2025.tar.bz2": (
        "d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0"
    ),
    "lovtidend-avd1-2026.tar.bz2": (
        "600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2"
    ),
}

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in [*ARCHIVES, ORACLE]), reason="no local Lovdata archives"
)


def _pinned() -> bool:
    return all(file_sha256(CACHE / name) == digest for name, digest in PINS.items())


def test_barnevernsloven_evidence_is_reproduced_byte_for_byte() -> None:
    if not _pinned():
        pytest.skip("local archives are not the pinned ones")
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    dates = [date.fromisoformat(state["at"]) for state in evidence["states"]]
    report = build_report(
        collect(BARNEVERNSLOVEN, ARCHIVES), dates, load_oracle(ORACLE, BARNEVERNSLOVEN), ARCHIVES
    )
    assert dump_json(report) + "\n" == EVIDENCE.read_text(encoding="utf-8")


def test_barnevernsloven_staged_commencement_before_and_after_each_date() -> None:
    """Decision 4 on the evidence: the act less two units on 2023-01-01, one unit on 2023-07-01."""
    if not _pinned():
        pytest.skip("local archives are not the pinned ones")
    evidence = collect(BARNEVERNSLOVEN, ARCHIVES)
    events = events_for(evidence)
    commencement = commencement_for(evidence, events)
    default = commencement.default
    assert (default.status, default.effective, default.known_from) == (
        "dated", date(2023, 1, 1), date(2022, 10, 14),
    )  # fmt: skip
    assert [p.refid for p in default.proofs] == ["forskrift/2022-10-14-1739"]
    units = {(e.unit, e.part): e.timing for e in commencement.units}
    assert units[(Unit(section="§2-6"), None)].status == "pending"
    ledd = units[(Unit(section="§15-7", ledd=4), None)]
    assert (ledd.status, ledd.effective) == ("dated", date(2023, 7, 1))
    before, first, june, july = (
        build_report(evidence, [when], None, ARCHIVES)["states"][0]
        for when in (date(2022, 12, 31), date(2023, 1, 1), date(2023, 6, 30), date(2023, 7, 1))
    )
    assert before["status"] == "not_yet_in_force" and before["provisions"] == []
    assert before["applied"] == [] and before["withheld"]
    assert first["status"] == "replayed"
    flagged = {
        p["key"]: p["ledd_not_in_force"] for p in first["provisions"] if "ledd_not_in_force" in p
    }
    assert flagged == {"§15-7": [4]}
    assert {p["key"] for p in first["provisions"]} >= {"§2-1", "§15-7"}
    assert "§2-6" not in {
        p["key"] for p in first["provisions"]
    }  # excepted, pending: never inserted
    assert {
        p["key"]: p["ledd_not_in_force"] for p in june["provisions"] if "ledd_not_in_force" in p
    } == {"§15-7": [4]}
    assert not any("ledd_not_in_force" in p for p in july["provisions"])
    assert set(first["applied"]) <= set(july["applied"])


def test_straffeloven_is_commenced_by_its_ikraftsettingslov_and_chapter_16_contradicts() -> None:
    """Decision 5(d) and Decision 6 on the evidence, with provenance naming both acts."""
    if not _pinned():
        pytest.skip("local archives are not the pinned ones")
    evidence = collect(STRAFFELOVEN, ARCHIVES)
    commencement = commencement_for(evidence, events_for(evidence))
    default = commencement.default
    assert (default.status, default.effective, default.known_from) == (
        "dated", date(2015, 10, 1), date(2015, 6, 19),
    )  # fmt: skip
    assert [(p.refid, p.member) for p in default.proofs] == [
        ("lov/2015-06-19-65", "lti/2015/nl-20150619-065.xml")
    ]
    chapter = next(e for e in commencement.units if e.unit == Unit(chapter="kap16"))
    assert chapter.part == "I"
    assert (chapter.timing.status, chapter.timing.reason) == (
        "unresolved", "conflicting commencement decisions",
    )  # fmt: skip
    assert {p.refid for p in chapter.timing.proofs} == {
        "forskrift/2008-03-07-225",
        "lov/2015-06-19-65",
    }
    ladder = [date(2008, 3, 6), date(2008, 3, 7), date(2015, 9, 30), date(2015, 10, 1)]
    states = build_report(evidence, ladder, None, ARCHIVES)["states"]
    assert [s["status"] for s in states] == ["not_yet_in_force"] * 3 + ["replayed"]
    # The 2008 decision dates chapter 16's operations the day it is published, the act is not in
    # force, so they wait: 4 withheld the day before, 99 the day of; by 2015-09-30 every dated
    # operation waits (391); on 2015-10-01 the text exists (358 provisions) and 335 apply.
    # Phase 4A (#856): 105/614/376 at 51c5d702 counted the militær straffelov and
    # straffeprosessloven blocks of lov/2015-06-19-65 §§ 4-5 (216 ops, 41 applied) and six
    # unsupported sentences of lov/2008-03-07-4 after intros the grammar cannot bind (``I
    # straffeloven 2005 tredje del …``, ``1. I militær straffelov …``) as straffeloven's.
    # Phase 4B (#861): 392 → 391 at 2015-09-30. lov/2009-06-19-74's ``2. Lov 13. desember 1946
    # nr. 14 om straff for utlendske krigsbrotsmenn oppheves.`` repeals another act; Phase 4A
    # credited it to the straffeloven block before it, a numbered item naming an act now closes
    # that block.
    assert [(len(s["withheld"]), len(s["applied"]), len(s["provisions"])) for s in states] == [
        (4, 0, 0),
        (99, 0, 0),
        (391, 0, 0),
        (0, 335, 358),
    ]
    assert all(s["unresolved_commencement"] == [] for s in states)
