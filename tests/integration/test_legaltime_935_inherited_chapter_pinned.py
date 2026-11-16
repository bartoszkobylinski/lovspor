"""#935 on the pinned archives: an amending member's own chapter is never the affected act's.

``lti/2011/nl-20110624-030.xml`` (``lov/2011-06-24-30``) amends other acts inside its own
*Kapittel 13 … endringer i andre lover* (``data-name="kap13"``). Eleven plain insertions there
(``Ny § X skal lyde:``) state no chapter of the act they insert into; nine bind to
``lov/1999-07-02-61`` (``#1:I:335.0`` inserts § 2-1 e) and two to ``lov/1999-07-02-64``
(``#1:I:513.0`` inserts § 38 a). Replay-9 stamped every one ``kap13``: ``chapter_map`` placed the
new sections in the affected act's chapter 13, and a ``kapittel 13`` commencement unit
``covered`` them. No source says so. The amending member's chapter stays raw evidence
(``Provision.enclosures``); the affected act's chapter is unknown.

Positive control: ``lti/2009/nl-20090619-074.xml`` inserts straffeloven's chapters 21 to 31,
"Nye kapitler 21 til 31 plasseres i annen del, og skal lyde:", each chapter read from its own
printed title. Those placements are proven and stay.

Runs only where the pinned archives sit in the gitignored cache; a skip is not a pass.
"""

from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime.archive import Evidence, file_sha256, read_members
from lovspor.legaltime.baseline import Commencement, UnitTiming, unit_state
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import Document
from lovspor.legaltime.instructions import Operation, interpret
from lovspor.legaltime.run import chapter_map
from lovspor.legaltime.units import UNPLACED, Unit, inserted_chapter, relation

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/legaltime"
ARCHIVES = [CACHE / "lovtidend-avd1-2001-2025.tar.bz2", CACHE / "lovtidend-avd1-2026.tar.bz2"]
PIN = "d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0"
HELSE = "lti/2011/nl-20110624-030.xml"
STRAFFELOVEN_2009 = "lti/2009/nl-20090619-074.xml"
PASIENT = "lov/1999-07-02-61"
HELSEPERSONELL = "lov/1999-07-02-64"
STRAFFELOVEN = "lov/2005-05-20-28"
PLAIN = {
    "lov/2011-06-24-30#1:I:335.0": (PASIENT, "§2-1e"),
    "lov/2011-06-24-30#1:I:342.0": (PASIENT, "§2-5a"),
    "lov/2011-06-24-30#1:I:344.0": (PASIENT, "§2-5b"),
    "lov/2011-06-24-30#1:I:346.0": (PASIENT, "§2-6"),
    "lov/2011-06-24-30#1:I:350.0": (PASIENT, "§3-3"),
    "lov/2011-06-24-30#1:I:352.0": (PASIENT, "§3-3a"),
    "lov/2011-06-24-30#1:I:354.0": (PASIENT, "§3-4"),
    "lov/2011-06-24-30#1:I:356.0": (PASIENT, "§3-4a"),
    "lov/2011-06-24-30#1:I:384.0": (PASIENT, "§7-3"),
    "lov/2011-06-24-30#1:I:513.0": (HELSEPERSONELL, "§38a"),
    "lov/2011-06-24-30#1:I:527.0": (HELSEPERSONELL, "§67a"),
}
DEFAULT = Timing(status="dated", effective=date(2012, 1, 1))
KAP13 = Timing(status="dated", effective=date(2013, 1, 1))

pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in ARCHIVES), reason="no local Lovdata archives"
)


@pytest.fixture(scope="module")
def members() -> dict[str, Document]:
    if file_sha256(ARCHIVES[0]) != PIN:
        pytest.skip("local archives are not the pinned ones")
    return read_members(ARCHIVES[0], [HELSE, STRAFFELOVEN_2009])


def _insertions(document: Document, act: str) -> dict[str, Operation]:
    ops = interpret(document).operations
    return {o.id: o for o in ops if o.act == act and o.kind == "insert_section"}


def _evidence(act: str, amender: Document) -> Evidence:
    """The affected act's own structure proves nothing here: only the amender can place."""
    empty = amender.model_copy(update={"provisions": ()})
    return Evidence(act=act, archives=(), baseline=empty, amenders=(amender,), decisions=())


def test_every_plain_insertion_is_found_and_states_no_placement(
    members: dict[str, Document],
) -> None:
    found = {**_insertions(members[HELSE], PASIENT), **_insertions(members[HELSE], HELSEPERSONELL)}
    assert {k: (o.act, o.address.section) for k, o in found.items()} == PLAIN
    for op in found.values():
        assert op.stated is None
        assert op.new_provision is not None
        assert (op.new_provision.chapter, op.new_provision.part) == (None, None)
        assert "section:kap13" in {f"{e.tag}:{e.name}" for e in op.new_provision.enclosures}
        assert inserted_chapter(op) is UNPLACED


@pytest.mark.parametrize("act", [PASIENT, HELSEPERSONELL])
def test_chapter_map_never_places_a_section_in_the_amenders_chapter(
    members: dict[str, Document], act: str
) -> None:
    assert chapter_map(_evidence(act, members[HELSE])) == {}


@pytest.mark.parametrize("chapter", ["kap13", "kap2", "kap7"])
@pytest.mark.parametrize("op_id", ["lov/2011-06-24-30#1:I:335.0", "lov/2011-06-24-30#1:I:513.0"])
def test_a_chapter_unit_neither_covers_nor_misses_an_unplaced_insertion(
    members: dict[str, Document], op_id: str, chapter: str
) -> None:
    act, _ = PLAIN[op_id]
    op = _insertions(members[HELSE], act)[op_id]
    chapters = chapter_map(_evidence(act, members[HELSE]))
    assert relation(Unit(chapter=chapter), op, chapters) == "unknown"
    assert relation(Unit(chapter=chapter, new=True), op, chapters) == "disjoint"  # not its text


def test_a_chapter_dated_apart_leaves_an_unplaced_insertion_unresolved(
    members: dict[str, Document],
) -> None:
    op = _insertions(members[HELSE], PASIENT)["lov/2011-06-24-30#1:I:335.0"]
    commencement = Commencement(
        default=DEFAULT, units=(UnitTiming(unit=Unit(chapter="kap13"), timing=KAP13),)
    )
    state = unit_state(commencement, "§2-1e", (inserted_chapter(op), ("", "I")), date(2026, 1, 1))
    assert state.in_force == "unresolved"
    assert state.effective is None


def test_stated_chapters_of_a_new_part_still_place_their_sections(
    members: dict[str, Document],
) -> None:
    ops = _insertions(members[STRAFFELOVEN_2009], STRAFFELOVEN)
    assert len(ops) == 174
    first, last = ops["lov/2009-06-19-74#1:I:53.0-0"], ops["lov/2009-06-19-74#1:I:53.0-173"]
    assert (inserted_chapter(first), inserted_chapter(last)) == ("kap21", "kap31")
    chapters = chapter_map(_evidence(STRAFFELOVEN, members[STRAFFELOVEN_2009]))
    assert len(chapters) == 174
    assert (chapters["§201"], chapters["§410"]) == ("kap21", "kap31")
    assert relation(Unit(chapter="kap21"), first, chapters) == "covers"
    assert relation(Unit(chapter="kap31"), first, chapters) == "disjoint"
    commencement = Commencement(
        default=DEFAULT, units=(UnitTiming(unit=Unit(chapter="kap21"), timing=KAP13),)
    )
    parts = ("del2", "I")
    state = unit_state(commencement, "§201", (inserted_chapter(first), parts), date(2014, 1, 1))
    assert (state.in_force, state.effective) == ("in_force", date(2013, 1, 1))
    state = unit_state(commencement, "§410", (inserted_chapter(last), parts), date(2012, 6, 1))
    assert (state.in_force, state.effective) == ("in_force", date(2012, 1, 1))
