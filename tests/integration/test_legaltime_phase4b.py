"""Phase 4B (#861): proven attribution of intros outside the canonical shapes, on the archives.

Runs only where the pinned publicData archives sit in the gitignored cache (see
test_legaltime_pilot.py); a skip means the evidence was not replayed here, not a pass.
Raw Lovdata XML never enters the repository.
"""

from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime.archive import collect, file_sha256, read_members
from lovspor.legaltime.instructions import interpret
from lovspor.legaltime.run import build_report

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
STRAFFELOVEN = "lov/2005-05-20-28"
MILITAER = "lov/1902-05-22-13"
STRAFFEPROSESS = "lov/1981-05-22-25"
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


def _read(member: str) -> tuple[str, ...]:
    document = read_members(ARCHIVES[0], [member])[member]
    return tuple(op.act or "-" for op in interpret(document).operations)


def test_sections_4_and_5_bind_the_acts_they_cite_and_the_header_lists() -> None:
    document = read_members(ARCHIVES[0], [IKRAFTSETTINGSLOVEN])[IKRAFTSETTINGSLOVEN]
    assert {MILITAER, STRAFFEPROSESS} <= set(document.header.changes_to_documents)
    operations = interpret(document).operations
    by_host = {
        host: {op.act for op in operations if op.host_section == host} for host in ("§4", "§5")
    }
    assert by_host == {"§4": {MILITAER}, "§5": {STRAFFEPROSESS}}
    counts = [sum(op.host_section == host for op in operations) for host in ("§4", "§5")]
    assert counts == [71, 145]
    assert {op.act for op in operations if op.host_section == "§3"} == {STRAFFELOVEN}


def test_the_straffeloven_ladder_still_applies_nothing_from_sections_4_and_5() -> None:
    document = read_members(ARCHIVES[0], [IKRAFTSETTINGSLOVEN])[IKRAFTSETTINGSLOVEN]
    hosted = {op.id for op in interpret(document).operations if op.host_section in ("§4", "§5")}
    report = build_report(collect(STRAFFELOVEN, ARCHIVES), [date(2026, 11, 6)], None, ARCHIVES)
    listed = {e["operation"]["id"] for e in [*report["events"], *report["unattributed_events"]]}
    assert hosted and not hosted & listed  # neither straffeloven's nor unattributed: other acts'
    state = report["states"][0]
    assert not hosted & set(state["applied"])
    assert not any(p.split(": ", 1)[0] in hosted for p in state["act_level_problems"])


@pytest.mark.parametrize(
    ("member", "act", "count"),
    [
        ("lti/2013/nl-20130621-085.xml", "lov/1902-05-22-10", 24),  # title before the citation
        ("lti/2007/nl-20070629-083.xml", "lov/2001-05-18-21", 15),  # short title before it
        ("lti/2007/nl-20070615-021.xml", "lov/1991-11-08-76", 9),  # reordered nynorsk: 4 + 5 bound
    ],
)
def test_a_proven_intro_binds_every_operation_of_its_block(
    member: str, act: str, count: int
) -> None:
    assert _read(member).count(act) == count


def test_a_misprinted_citation_stays_unattributed() -> None:
    """lov/2008-06-20-47 prints ``22. mai 2005 nr. 28`` for straffeloven: not proven, not bound."""
    member = "lti/2008/nl-20080620-047.xml"
    document = read_members(ARCHIVES[0], [member])[member]
    assert "lov/2005-05-22-28" not in document.header.changes_to_documents
    assert "lov/2005-05-22-28" not in _read(member)
