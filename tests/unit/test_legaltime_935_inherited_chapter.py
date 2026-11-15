"""#935: an inserted section is placed in the affected act's chapter only by its own source.

``document._enclosing`` reads the ``kapN``/``delN`` sections around an article. For the
member's own provisions (``legalArticle``) that is the act's structure; for the text it
carries for another act (``futureLegalArticle``) it is the amending member's structure, and
proves nothing about the affected act. Only a structure sentence or a printed chapter title
read before the provision (``Operation.stated``) places it. Without one the placement is
``UNPLACED``: a chapter unit's relation to it is ``unknown``, and a chapter dated apart leaves
its commencement unresolved, never the default and never the amending member's chapter.
"""

import itertools
from datetime import date

import pytest

from lovspor.legaltime.archive import Evidence
from lovspor.legaltime.baseline import UNPLACED_CHAPTER, Commencement, UnitTiming, unit_state
from lovspor.legaltime.commencement import Decisions, Timing
from lovspor.legaltime.document import Document, Ledd, parse_document
from lovspor.legaltime.instructions import Operation, interpret
from lovspor.legaltime.replay import _unit_gate, _Working
from lovspor.legaltime.run import _events_of, chapter_map
from lovspor.legaltime.units import UNPLACED, Unit, inserted_chapter, inserted_where, relation

ACT = "lov/2005-05-20-28"
INTRO = "Lov 20. mai 2005 nr. 28 om straff endres slik:"
DEFAULT = Timing(status="dated", effective=date(2012, 1, 1))
LATER = Timing(status="dated", effective=date(2030, 1, 1))
AT = date(2020, 1, 1)


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _title(text: str) -> str:
    return f'<article class="futuretitle">{text}</article>'


def _future(key: str) -> str:
    return (
        f'<article class="futureLegalArticle" data-name="{key}">'
        '<span class="futureLegalArticleHeader">'
        f'<span class="legalArticleValue">§ {key[1:]}</span>'
        '<span class="legalArticleTitle">Overskrift</span></span>'
        '<article class="legalP">Teksten.</article></article>'
    )


def _sec(name: str | None, body: str) -> str:
    attribute = "" if name is None else f' data-name="{name}"'
    return f'<section class="section"{attribute}>{body}</section>'


def _member(part_one: str, wrap: str | None = None, refid: str = "lov/2008-03-07-4") -> Document:
    one = _sec(None, f"<h2>I</h2>{part_one}")
    if wrap is not None:  # the amending member's own chapter around its instructions
        one = _sec(wrap, one)
    body = one + _sec(None, "<h2>II</h2>" + _p("Loven trer i kraft straks."))
    xml = (
        f'<html><body><header><dl><dd class="refid">{refid}</dd>'
        '<dd class="dateInForce">2008-03-07</dd>'
        '<dd class="dateOfPublication">2008-03-07 14:45</dd>'
        '<dd class="title">Lov om endringer i straffeloven 2005</dd>'
        f'<dd class="changesToDocuments"><ul><li>{ACT}</li></ul></dd>'
        f"</dl></header><main>{body}</main></body></html>"
    )
    return parse_document(xml.encode(), f"lti/{refid[4:8]}/{refid[4:].replace('-', '')}.xml")


def _enacting(sections: str) -> Document:
    xml = (
        f'<html><body><header><dl><dd class="refid">{ACT}</dd>'
        '<dd class="dateInForce">2005-05-20</dd><dd class="dateOfPublication">2005-05-20</dd>'
        f'<dd class="title">Lov om straff</dd></dl></header><main>{sections}</main></body></html>'
    )
    return parse_document(xml.encode(), "lti/2005/nl-20050520-028.xml")


def _article(key: str) -> str:
    return (
        f'<article class="legalArticle" data-name="{key}">'
        f'<h3><span class="legalArticleValue">§ {key[1:]}</span></h3>'
        '<p class="legalP">Tekst.</p></article>'
    )


def _inserts(document: Document) -> list[Operation]:
    return [o for o in interpret(document).operations if o.kind == "insert_section"]


def _one(document: Document) -> Operation:
    (op,) = _inserts(document)
    return op


def _map(*amenders: Document, baseline: Document | None = None) -> dict[str, str]:
    base = baseline or _enacting("")
    evidence = Evidence(act=ACT, archives=(), baseline=base, amenders=amenders, decisions=())
    return chapter_map(evidence)


def _by_chapter(*chapters: tuple[str, Timing]) -> Commencement:
    units = tuple(UnitTiming(unit=Unit(chapter=c), timing=t) for c, t in chapters)
    return Commencement(default=DEFAULT, units=units)


PLAIN = _member(_p(INTRO) + _p("Ny § 5 a skal lyde:") + _future("§5a"), wrap="kap13")
SENTENCE = _member(_p(INTRO) + _p("Nytt kapittel 6A skal lyde:") + _future("§6A-1"), wrap="kap13")
TITLED = _member(
    _p(INTRO)
    + _p("Nye kapitler 21 til 22 plasseres i annen del, og skal lyde:")
    + _title("Kapittel 21 Vern av den enkeltes frihet")
    + _future("§201")
    + _title("Kapittel 22 Vern av den personlige integritet")
    + _future("§210"),
    wrap="kap13",
)


# ------------------------------------------------------------------ the provenance boundary


def test_a_plain_insertion_keeps_the_members_chapter_as_raw_evidence_only() -> None:
    op = _one(PLAIN)
    assert op.stated is None and op.new_provision is not None
    assert (op.new_provision.chapter, op.new_provision.part) == (None, None)
    assert [e.name for e in op.new_provision.enclosures if e.tag == "section"] == [None, "kap13"]
    assert inserted_chapter(op) is UNPLACED
    assert inserted_where(op) == (UNPLACED, ("", op.part.upper()))


def test_the_members_own_provisions_keep_their_enclosing_structure() -> None:
    document = _enacting(_sec("del1", _sec("kap2", _article("§5"))) + _article("§9"))
    stamps = {p.key: (p.chapter, p.part) for p in document.provisions}
    assert stamps == {"§5": ("kap2", "del1"), "§9": (None, None)}


def test_a_chapter_named_by_the_inserting_sentence_places_the_section() -> None:
    op = _one(SENTENCE)
    assert op.stated is not None and op.stated.chapter_from == "sentence"
    assert inserted_chapter(op) == "kap6a"
    assert _map(SENTENCE) == {"§6A-1": "kap6a"}
    assert relation(Unit(chapter="kap6a"), op) == "covers"
    assert relation(Unit(chapter="kap13"), op) == "disjoint"  # the member's chapter: beside it


def test_a_chapter_read_from_the_title_before_each_section_places_it() -> None:
    first, second = _inserts(TITLED)
    assert (inserted_chapter(first), inserted_chapter(second)) == ("kap21", "kap22")
    assert inserted_where(first) == ("kap21", ("del2", first.part.upper()))
    assert _map(TITLED) == {"§201": "kap21", "§210": "kap22"}
    assert relation(Unit(chapter="kap22"), first) == "disjoint"
    assert relation(Unit(chapter="kap22"), second) == "covers"


def test_an_inherited_chapter_is_never_mapped_or_related() -> None:
    op = _one(PLAIN)
    assert _map(PLAIN) == {}
    for unit in (Unit(chapter="kap13"), Unit(chapter="kap5")):
        assert relation(unit, op) == "unknown"
        assert relation(unit, op, {"§5a": "kap5"}) == "unknown"  # nor a section map's guess


def test_a_new_chapter_is_only_the_text_its_own_insertion_carries() -> None:
    """``nytt kapittel 6A`` names the chapter insertion's text; a plain ``Ny §`` is not it."""
    new = Unit(chapter="kap6a", new=True)
    assert relation(new, _one(PLAIN)) == "disjoint"
    assert relation(new, _one(SENTENCE)) == "covers"
    assert relation(Unit(chapter="kap13", new=True), _one(SENTENCE)) == "disjoint"


def test_a_replaced_section_never_maps_the_members_chapter() -> None:
    replaced = _member(_p(INTRO) + _p("§ 5 skal lyde:") + _future("§5"), wrap="kap13")
    assert [o.kind for o in interpret(replaced).operations] == ["replace_section"]
    assert _map(replaced) == {}


def test_a_members_chapter_equal_to_the_acts_own_chapter_still_places_nothing() -> None:
    """The coincidence would be the right answer by luck; luck is not a source."""
    baseline = _enacting(_sec("kap5", _article("§5")))
    coincident = _member(_p(INTRO) + _p("Ny § 5 a skal lyde:") + _future("§5a"), wrap="kap5")
    op = _one(coincident)
    assert inserted_chapter(op) is UNPLACED
    assert _map(coincident, baseline=baseline) == {"§5": "kap5"}
    assert relation(Unit(chapter="kap5"), op, {"§5": "kap5"}) == "unknown"


@pytest.mark.parametrize(
    ("sentence", "title"),
    [
        ("Nytt kapittel IV skal lyde:", None),  # a roman chapter the grammar does not read
        ("Nytt kapittel 6A skal lyde:", "Kapittel IV Noe"),  # a printed title it cannot read
        ("Nytt kapittel 6A skal lyde:", "Kapittel 6B Annet"),  # two sources, two chapters
    ],
)
def test_an_unreadable_or_contradicted_chapter_places_nothing(
    sentence: str, title: str | None
) -> None:
    payload = (_title(title) if title else "") + _future("§6A-1")
    op = _one(_member(_p(INTRO) + _p(sentence) + payload))
    assert inserted_chapter(op) is UNPLACED
    assert relation(Unit(chapter="kap6a"), op) == "unknown"
    assert relation(Unit(chapter="kap6b"), op) == "unknown"
    assert relation(Unit(chapter="kap6a", new=True), op) == "unknown"  # a chapter insertion


def test_a_title_agreeing_with_the_sentence_places_the_section() -> None:
    payload = _title("Kapittel 6A Nytt") + _future("§6A-1")
    op = _one(_member(_p(INTRO) + _p("Nytt kapittel 6A skal lyde:") + payload))
    assert op.stated is not None and op.stated.chapter_from == "title"
    assert inserted_chapter(op) == "kap6a"


def test_an_insertion_naming_no_act_places_nothing_and_maps_nothing() -> None:
    bare = _member(_p("Ny § 5 a skal lyde:") + _future("§5a"), wrap="kap13")
    ops = _inserts(bare)
    assert all(o.act is None and inserted_chapter(o) is UNPLACED for o in ops)
    assert _map(bare) == {}


@pytest.mark.parametrize("order", list(itertools.permutations([PLAIN, SENTENCE, TITLED])))
def test_the_chapter_map_does_not_depend_on_the_order_of_amenders(
    order: tuple[Document, ...],
) -> None:
    assert _map(*order) == {"§6A-1": "kap6a", "§201": "kap21", "§210": "kap22"}


def test_a_payload_nested_in_a_hosts_list_is_the_hosts_text_never_its_chapter() -> None:
    """#893/#922: the host's own article keeps its chapter; the text it lists does not."""
    nested = (
        '<article class="legalArticle" data-name="§102">'
        '<h3><span class="legalArticleValue">§ 102</span></h3>'
        f"<ol><li>{_p(INTRO)}{_p('Ny § 5 a skal lyde:')}{_future('§5a')}</li></ol></article>"
    )
    host = _enacting(_sec("kap13", nested))
    assert {p.key: p.chapter for p in host.provisions} == {"§102": "kap13"}
    events = _events_of(host, ACT, Decisions(documents=()), 0)
    inserts = [e.operation for e in events if e.operation.kind == "insert_section"]
    assert inserts and all(o.new_provision is not None for o in inserts)
    assert all(inserted_chapter(o) is UNPLACED for o in inserts)
    assert all(o.new_provision.chapter is None for o in inserts if o.new_provision)


# ------------------------------------------------------------------- commencement by chapter


def test_a_chapter_dated_apart_leaves_an_unplaced_section_unresolved() -> None:
    commencement = _by_chapter(("kap13", LATER))
    state = unit_state(commencement, "§5a", inserted_where(_one(PLAIN)), AT)
    assert (state.in_force, state.effective) == ("unresolved", None)
    assert state.problems == (f"commencement {UNPLACED_CHAPTER}",)
    timing = commencement.section_timing("§5a", UNPLACED, ("", "I"))
    assert (timing.status, timing.reason) == ("unresolved", UNPLACED_CHAPTER)


def test_chapters_dated_with_the_default_leave_an_unplaced_section_on_the_default() -> None:
    commencement = _by_chapter(("kap13", DEFAULT), ("kap2", DEFAULT))
    state = unit_state(commencement, "§5a", inserted_where(_one(PLAIN)), AT)
    assert (state.in_force, state.effective) == ("in_force", date(2012, 1, 1))


def test_a_heading_unit_is_no_chapter_dated_apart() -> None:
    units = (UnitTiming(unit=Unit(chapter="kap13", heading=True), timing=LATER),)
    commencement = Commencement(default=DEFAULT, units=units)
    assert commencement.section_timing("§5a", UNPLACED, ()) == DEFAULT


def test_the_sections_own_unit_still_dates_an_unplaced_section() -> None:
    units = (
        UnitTiming(unit=Unit(section="§5a"), timing=DEFAULT),
        UnitTiming(unit=Unit(chapter="kap13"), timing=LATER),
    )
    assert Commencement(default=DEFAULT, units=units).section_timing("§5a", UNPLACED, ()) == DEFAULT


def test_a_stated_chapter_is_still_dated_by_its_chapter_commencement() -> None:
    first, second = _inserts(TITLED)
    commencement = _by_chapter(("kap21", LATER))
    early = unit_state(commencement, "§201", inserted_where(first), AT)
    assert (early.in_force, early.effective) == ("not_yet_in_force", date(2030, 1, 1))
    other = unit_state(commencement, "§210", inserted_where(second), AT)
    assert (other.in_force, other.effective) == ("in_force", date(2012, 1, 1))


def test_a_proven_chapterless_section_is_not_an_unplaced_one() -> None:
    commencement = _by_chapter(("kap13", LATER))
    assert commencement.section_timing("§9", None, ("",)) == DEFAULT


def test_replay_withholds_nothing_and_applies_nothing_on_an_unplaced_guess() -> None:
    working = _Working(_enacting(_article("§5")), _by_chapter(("kap13", LATER)), AT)
    op = _one(PLAIN)
    assert _unit_gate(op, working) == f"commencement {UNPLACED_CHAPTER}"
    assert working.apply_insert_section(op) is None
    assert working.map["§5a"].unit.in_force == "unresolved"
    placed = _Working(_enacting(_article("§5")), _by_chapter(("kap6a", LATER)), AT)
    assert _unit_gate(_one(SENTENCE), placed) == "withheld"


def test_ledd_text_of_an_unplaced_section_is_kept() -> None:
    op = _one(PLAIN)
    assert op.new_provision is not None
    assert op.new_provision.ledd == (Ledd(xml="Teksten.", text="Teksten."),)
