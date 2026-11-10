"""Phase 3 (#744): the act's commencement per legal unit (ADR-0020 Decisions 4 to 6).

The decision sentences are those of barnevernsloven (FOR-2022-10-14-1739) and
straffeloven (FOR-2008-03-07-225, lov 19. juni 2015 nr. 65); the acts are
synthetic.
"""

from datetime import date

from lovspor.legaltime.archive import ArchiveIndex, Evidence
from lovspor.legaltime.baseline import (
    INSTRUMENT_UNDATED,
    NAMES_NOTHING,
    Commencement,
    UnitTiming,
    baseline_commencement,
)
from lovspor.legaltime.commencement import CONFLICT, Decisions, Timing
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.run import events_for
from lovspor.legaltime.units import Unit

ACT = "lov/2021-06-18-97"


def _article(key: str, *ledd: str) -> str:
    body = "".join(f'<article class="legalP">{text}</article>' for text in ledd)
    return (
        f'<article class="legalArticle" data-name="{key}"><h3 class="legalArticleHeader">'
        f'<span class="legalArticleValue">§ {key[1:]}</span></h3>{body}</article>'
    )


def _act(
    refid: str = ACT, date_in_force: str = "Kongen bestemmer", body: str | None = None
) -> Document:
    body = body or (
        '<section class="section" data-name="kap2"><h3>Kapittel 2.</h3>'
        + _article("§2-1", "A.")
        + "</section>"
        '<section class="section" data-name="kap15"><h3>Kapittel 15.</h3>'
        + _article("§15-7", "En.", "To.", "Tre.")
        + "</section>"
    )
    xml = (
        f'<html><body><header><dl><dd class="refid">{refid}</dd>'
        f'<dd class="dateInForce">{date_in_force}</dd>'
        '<dd class="dateOfPublication">2021-06-18 12:50</dd><dd class="title">Lov om barnevern</dd>'
        f"</dl></header><main>{body}</main></body></html>"
    )
    return parse_document(xml.encode(), "lti/2021/act.xml")


def _decision(refid: str, title: str, date_in_force: str, body: str, published: str) -> Document:
    xml = (
        f'<html><body><header><dl><dd class="refid">{refid}</dd>'
        f'<dd class="dateInForce">{date_in_force}</dd>'
        f'<dd class="dateOfPublication">{published}</dd>'
        f'<dd class="title">{title}</dd></dl></header><main>'
        f'<article class="legalP">{body}</article></main></body></html>'
    )
    return parse_document(xml.encode(), f"lti/x/{refid.rsplit('/', maxsplit=1)[-1]}.xml")


STAGED = _decision(
    "forskrift/2022-10-14-1739",
    "Delt ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern (barnevernsloven)",
    "2023-01-01, 2023-07-01",
    "Delt ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern (barnevernsloven). "
    "Loven trer i kraft 1. januar 2023, med unntak av § 2-6 og § 15-7 fjerde ledd. "
    "Loven § 15-7 fjerde ledd trer i kraft 1. juli 2023.",
    "2022-10-14 14:20",
)


def test_a_staged_commencement_dates_the_act_less_its_exceptions_and_each_exception_apart() -> None:
    commencement = baseline_commencement(_act(), (STAGED,), [])
    assert (commencement.default.status, commencement.default.effective) == (
        "dated",
        date(2023, 1, 1),
    )
    assert commencement.default.known_from == date(2022, 10, 14)
    by_unit = {(e.unit, e.part): e.timing for e in commencement.units}
    section = by_unit[(Unit(section="§2-6"), None)]
    assert section.status == "pending" and section.effective is None
    assert [p.refid for p in section.proofs] == [ACT, "forskrift/2022-10-14-1739"]
    ledd = by_unit[(Unit(section="§15-7", ledd=4), None)]
    assert (ledd.status, ledd.effective) == ("dated", date(2023, 7, 1))
    assert commencement.section_timing("§15-7", "kap15", ("del1",)).effective == date(2023, 1, 1)
    assert commencement.section_timing("§2-6", "kap2", ()).status == "pending"
    assert commencement.ledd_timings("§15-7") == {4: ledd}
    assert not commencement.finer_than_ledd("§15-7")


def test_a_dated_header_is_the_whole_answer() -> None:
    commencement = baseline_commencement(_act(date_in_force="2021-07-01"), (STAGED,), [])
    assert commencement.default.effective == date(2021, 7, 1)
    assert commencement.units == ()


def test_an_exception_the_unit_grammar_cannot_read_unsettles_every_unit() -> None:
    muddled = _decision(
        "forskrift/2025-06-10-967",
        "Delt ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern (barnevernsloven)",
        "2026-01-01, 2027-01-01",
        "Loven trer i kraft 1. januar 2026, med unntak av lovendringene om utlegg som i saker om "
        "utlegg kommer gradvis til anvendelse, og § 21 som trer i kraft 1. januar 2027.",
        "2025-06-10 14:40",
    )
    commencement = baseline_commencement(_act(), (STAGED, muddled), [])
    assert commencement.default.status == "unresolved"
    assert commencement.default.reason == "partial decision outside the grammar"
    assert {e.timing.status for e in commencement.units} == {"unresolved"}


def test_a_partial_decision_naming_no_unit_of_the_act_unsettles_it() -> None:
    foreign = _decision(
        "forskrift/2022-10-14-1740",
        "Delt ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern (barnevernsloven)",
        "2023-01-01",
        "Loven § 99-9 trer i kraft 1. januar 2023.",
        "2022-10-14 14:30",
    )
    commencement = baseline_commencement(_act(), (foreign,), [])
    assert (commencement.default.status, commencement.default.reason) == (
        "unresolved",
        NAMES_NOTHING,
    )


def test_two_instruments_naming_different_days_for_one_unit_conflict() -> None:
    again = _decision(
        "forskrift/2023-03-01-100",
        "Ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern (barnevernsloven)",
        "2023-09-01",
        "Loven § 15-7 fjerde ledd trer i kraft 1. september 2023.",
        "2023-03-01 10:00",
    )
    commencement = baseline_commencement(_act(), (STAGED, again), [])
    ledd = next(e.timing for e in commencement.units if e.unit == Unit(section="§15-7", ledd=4))
    assert (ledd.status, ledd.reason) == ("unresolved", CONFLICT)
    assert len(ledd.proofs) == 3  # header, the first decision, the second
    assert commencement.default.effective == date(2023, 1, 1)  # the act itself is untouched


# ------------------------------------------------------------- straffeloven's shape

STRAFFELOVEN = "lov/2005-05-20-28"


def _straffeloven() -> Document:
    body = (
        '<section class="section" data-name="del1"><h2>Første del.</h2>'
        '<section class="section" data-name="kap1"><h3>Kapittel 1.</h3>'
        + _article("§1", "Virkeområde.")
        + "</section></section>"
        '<section class="section" data-name="del3"><h2>Tredje del.</h2>'
        + '<article class="legalArticle" data-name="§101"><h3 class="legalArticleHeader">'
        '<span class="legalArticleValue">§ 101</span>. <span class="legalArticleTitle">'
        'Ikraftsetting</span></h3><article class="legalP">Loven trer i kraft fra den tid Kongen '
        "bestemmer.</article></article></section>"
    )
    return _act(STRAFFELOVEN, "Kongen bestemmer", body)


def _instrument(
    clause: str = "Loven her trer i kraft straks.", date_in_force: str = "2015-06-19"
) -> Document:
    xml = (
        '<html><body><header><dl><dd class="refid">lov/2015-06-19-65</dd>'
        f'<dd class="dateInForce">{date_in_force}</dd>'
        '<dd class="dateOfPublication">2015-06-19 15:20</dd>'
        '<dd class="title">Lov om ikraftsetting av straffeloven 2005</dd>'
        '<dd class="changesToDocuments"><ul><li>lov/2005-05-20-28</li></ul></dd>'
        "</dl></header><main>"
        '<article class="legalArticle" data-name="§1"><h3 class="legalArticleHeader">'
        '<span class="legalArticleValue">§ 1</span>. <span class="legalArticleTitle">'
        "Ikraftsetting av straffeloven 2005</span></h3>"
        '<article class="legalP">I lov 20. mai 2005 nr. 28 om straff skal § 411 første ledd lyde:'
        "</article>"
        '<article class="legalP">Loven trer i kraft 1. oktober 2015. Fra samme tid oppheves '
        "straffeloven 22. mai 1902 nr. 10.</article></article>"
        '<article class="legalArticle" data-name="§8"><h3 class="legalArticleHeader">'
        '<span class="legalArticleValue">§ 8</span>. <span class="legalArticleTitle">'
        f'Ikrafttredelse</span></h3><article class="legalP">{clause}</article></article>'
        "</main></body></html>"
    )
    return parse_document(xml.encode(), "lti/2015/nl-20150619-065.xml")


CHAPTER_16 = _decision(
    "forskrift/2008-03-07-225",
    "Delvis ikraftsetting av lov 20. mai 2005 nr. 28 om straff (straffeloven)",
    "2008-03-07",
    "Lovvedtaket del I kapittel 16 trer i kraft straks.",
    "2008-03-11 15:35",
)


def _evidence(
    act: Document, amenders: tuple[Document, ...], decisions: tuple[Document, ...]
) -> Evidence:
    index = ArchiveIndex(
        archive="x.tar.bz2", sha256="0" * 64, index_version="legaltime-index-2", members=()
    )
    return Evidence(
        act=act.header.refid,
        archives=(index,),
        baseline=act,
        amenders=amenders,
        decisions=decisions,
    )


def test_an_instrument_writing_a_commencement_sentence_into_the_act_dates_the_act() -> None:
    act, instrument = _straffeloven(), _instrument()
    evidence = _evidence(act, (instrument,), ())
    events = events_for(evidence)
    commencement = baseline_commencement(act, (), events)
    default = commencement.default
    assert (default.status, default.effective, default.known_from) == (
        "dated", date(2015, 10, 1), date(2015, 6, 19),
    )  # fmt: skip
    assert [(p.refid, p.member) for p in default.proofs] == [
        ("lov/2015-06-19-65", "lti/2015/nl-20150619-065.xml")
    ]
    assert default.proofs[0].sha256 == instrument.sha256
    assert not default.retroactive


def test_an_instrument_whose_own_commencement_is_unproven_leaves_the_act_unresolved() -> None:
    act = _straffeloven()
    instrument = _instrument("Loven gjelder fra den tid Kongen bestemmer.", "Kongen bestemmer")
    events = events_for(_evidence(act, (instrument,), ()))
    commencement = baseline_commencement(act, (), events)
    assert (commencement.default.status, commencement.default.reason) == (
        "unresolved", INSTRUMENT_UNDATED,
    )  # fmt: skip


def test_a_chapter_commenced_earlier_than_the_act_wide_date_is_a_contradiction_not_a_stage() -> (
    None
):
    act, instrument = _straffeloven(), _instrument()
    inserted = Decisions(documents=(CHAPTER_16,), chapter_of={"§101": "kap16"})
    events = events_for(_evidence(act, (instrument,), (CHAPTER_16,)))
    commencement = baseline_commencement(act, inserted, events)
    assert commencement.default.effective == date(2015, 10, 1)
    chapter = next(e for e in commencement.units if e.unit == Unit(chapter="kap16"))
    assert chapter.part == "I"
    assert (chapter.timing.status, chapter.timing.reason) == ("unresolved", CONFLICT)
    assert {p.refid for p in chapter.timing.proofs} >= {
        "forskrift/2008-03-07-225",
        "lov/2015-06-19-65",
    }


def test_a_chapter_decision_alone_dates_the_chapter_and_leaves_the_act_pending() -> None:
    act = _straffeloven()
    inserted = Decisions(documents=(CHAPTER_16,), chapter_of={"§101": "kap16"})
    commencement = baseline_commencement(act, inserted, [])
    assert commencement.default.status == "pending"
    chapter = next(e for e in commencement.units if e.unit == Unit(chapter="kap16"))
    assert (chapter.timing.status, chapter.timing.effective) == ("dated", date(2008, 3, 7))
    assert commencement.section_timing("§101", "kap16", ("I",)).effective == date(2008, 3, 7)
    assert commencement.section_timing("§101", "kap16", ("del3",)).status == "pending"
    assert commencement.section_timing("§1", "kap1", ("del1",)).status == "pending"


def test_a_chapter_the_act_does_not_hold_names_nothing() -> None:
    act = _straffeloven()
    commencement = baseline_commencement(act, (CHAPTER_16,), [])
    assert (commencement.default.status, commencement.default.reason) == (
        "unresolved",
        NAMES_NOTHING,
    )


def test_a_section_s_own_unit_precedes_its_chapter_s_whatever_the_list_order() -> None:
    """An excepted section keeps its state inside a dated chapter; the rest of the chapter dates."""
    chapter = UnitTiming(unit=Unit(chapter="kap2"), timing=_dated_timing(date(2023, 1, 1)))
    section = UnitTiming(unit=Unit(section="§2-6"), timing=Timing(status="pending"))
    for units in ((section, chapter), (chapter, section)):
        commencement = Commencement(default=_dated_timing(date(2022, 1, 1)), units=units)
        assert commencement.section_timing("§2-6", "kap2", ()) == section.timing
        assert commencement.section_timing("§2-1", "kap2", ()) == chapter.timing


def test_a_section_and_its_chapter_dated_on_different_days_conflict() -> None:
    chapter = _decision(
        "forskrift/2022-01-01-1",
        "Delt ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern (barnevernsloven)",
        "2023-01-01",
        "Loven kapittel 2 trer i kraft 1. januar 2023.",
        "2022-01-01 10:00",
    )
    section = _decision(
        "forskrift/2022-02-01-2",
        "Delt ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern (barnevernsloven)",
        "2023-03-01",
        "Loven § 2-1 trer i kraft 1. mars 2023.",
        "2022-02-01 10:00",
    )
    commencement = baseline_commencement(_act(), (chapter, section), [])
    by_unit = {e.unit: e.timing for e in commencement.units}
    assert by_unit[Unit(chapter="kap2")].effective == date(2023, 1, 1)
    conflicted = by_unit[Unit(section="§2-1")]
    assert (conflicted.status, conflicted.reason) == ("unresolved", CONFLICT)
    assert {p.refid for p in conflicted.proofs} >= {
        "forskrift/2022-01-01-1",
        "forskrift/2022-02-01-2",
    }
    same_day = _decision(
        "forskrift/2022-02-01-2",
        "Delt ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern (barnevernsloven)",
        "2023-01-01",
        "Loven § 2-1 trer i kraft 1. januar 2023.",
        "2022-02-01 10:00",
    )
    settled = baseline_commencement(_act(), (chapter, same_day), [])
    assert {e.unit: e.timing.status for e in settled.units} == {
        Unit(chapter="kap2"): "dated",
        Unit(section="§2-1"): "dated",
    }


def _dated_timing(when: date) -> Timing:
    return Timing(status="dated", effective=when)


def test_a_commencement_is_a_timing_plus_units() -> None:
    plain = Commencement(default=Timing(status="dated", effective=date(2020, 1, 1)))
    assert plain.section_timing("§1", None, ()).effective == date(2020, 1, 1)
    assert plain.ledd_timings("§1") == {}
