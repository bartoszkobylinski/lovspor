"""Phase 4A (#857, #861): a commencement sentence is never dropped as informational prose.

``Fra samme tidspunkt trer endringene i § 78 nr. 1 til 16 i kraft, med unntak av nr. 7.``
is sentence 2 of FOR-2009-06-19-704 (naturmangfoldloven, pinned archive member
``lti/2009/sf-20090619-0704.xml``); the markup around it here is synthetic. Before the fix
``_INFORMATIONAL`` matched its opening words and the sentence never reached the grammar.
"""

from datetime import date

import pytest

from lovspor.legaltime.baseline import baseline_commencement
from lovspor.legaltime.commencement import _blocks, _informational, clause_timings
from lovspor.legaltime.document import parse_document
from lovspor.legaltime.instructions import interpret
from tests.unit.test_legaltime_baseline import _act, _decision

NML_1 = (
    "Lov 19. juni 2009 nr. 100 om forvaltning av naturens mangfold (naturmangfoldloven), med "
    "unntak av kapittel IV i loven, trer i kraft fra 1. juli 2009."
)
NML_2 = "Fra samme tidspunkt trer endringene i § 78 nr. 1 til 16 i kraft, med unntak av nr. 7."
NML_TITLE = (
    "Delvis ikraftsetting av lov 19. juni 2009 nr. 100 om forvaltning av naturens mangfold "
    "(naturmangfoldloven)"
)
BV_TITLE = "Delvis ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern (barnevernsloven)"


def test_the_naturmangfoldloven_decision_keeps_its_dated_exception_sentence() -> None:
    decision = _decision(
        "forskrift/2009-06-19-704", NML_TITLE, "2009-07-01", f"{NML_1} {NML_2}", "2009-06-23 15:15"
    )
    sentences = [s for _, body in _blocks(decision) for s in body]
    assert sentences == [NML_1, NML_2]


@pytest.mark.parametrize(
    "sentence",
    [
        NML_2,
        "Fra samme tid trer § 15-7 fjerde ledd i kraft.",
        "Frå same tid tek § 15-7 til å gjelde.",
        "Fra samme tidspunkt trer § 15-7 i kraft, med unntak av fjerde ledd.",
        "Fra den tid loven trer i kraft, trer § 2-6 i kraft 1. juli 2023.",
        "Kongen kan likevel sette § 2-6 i kraft 1. juli 2023.",
        "Dersom § 2-6 ikke er satt i kraft, trer den i kraft 1. januar 2024.",
        "Når loven trer i kraft, trer § 2-6 likevel ikke i kraft.",
        "Dersom § 2-6 ikke er satt i kraft, trer den i kraft.",  # independent review, no date
        "Fra den tid Kongen bestemmer, trer kapittel 3 i kraft.",
        "Når loven trer i kraft, trer del II i kraft.",
        "Dersom fjerde ledd ikke er satt i kraft, trer det i kraft.",  # _NAMES_UNIT: bare "ledd"
        "Fra den tid Kongen bestemmer, trer kap. 3 i kraft.",  # _NAMES_UNIT: abbreviated "kap."
        "Dersom § 2-6 ikke er satt i kraft, tek den til å gjelde 1. januar 2024.",  # _SPLIT_VERB
    ],
)
def test_an_informational_opening_does_not_hide_a_commencement(sentence: str) -> None:
    assert _informational(sentence) is False


@pytest.mark.parametrize(
    "sentence",
    [
        "Fra samme tid oppheves lov 13. juni 1980 nr. 35 om barnevern.",
        "Frå same tid blir forskrift 1. januar 2000 nr. 1 oppheva.",
        "Fra den tid loven trer i kraft, oppheves lov 17. juli 1992 nr. 100 om barneverntjenester.",
        "Kongen kan sette de enkelte bestemmelsene i kraft til forskjellig tid.",
        "Departementet kan gi overgangsregler.",
        "Når loven trer i kraft, gjelder overgangsreglene i kapittel 18.",
        "Kongen kan bestemme at de enkelte bestemmelsene i loven skal tre i kraft til ulik tid.",
        # lov 18. desember 2020 nr. 149 (arveloven pilot): a transitional reference, not a date
        "Er arvelateren død før ny § 99 a i arveloven trer i kraft, regnes fristen etter § 99 a "
        "første ledd første punktum fra tidspunktet for ikrafttredelsen av bestemmelsen.",
        # lov 19. juni 2026 nr. 48: the unit sits in the reference, the main clause repeals
        "Fra den tid endringene av konkurslovens første del i del I i loven her trer i kraft, "
        "oppheves midlertidig lov 7. mai 2020 nr. 38 om rekonstruksjon.",
    ],
)
def test_truly_informational_prose_stays_informational(sentence: str) -> None:
    assert _informational(sentence) is True


def test_a_dropped_exception_no_longer_dates_the_unit_it_excepts() -> None:
    """The fail-open #857 named: the first sentence parses, the excepting second one was lost."""
    decision = _decision(
        "forskrift/2022-10-14-1739", BV_TITLE, "2023-01-01",
        "Loven trer i kraft 1. januar 2023, med unntak av § 2-6. "
        "Fra samme tidspunkt trer § 15-7 i kraft, med unntak av fjerde ledd.",
        "2022-10-14 14:20",
    )  # fmt: skip
    commencement = baseline_commencement(_act(), (decision,), [])
    assert commencement.default.status == "unresolved"
    assert commencement.default.effective is None
    assert commencement.default.reason == "partial decision outside the grammar"


def test_the_same_decision_without_the_second_sentence_still_dates_the_act() -> None:
    decision = _decision(
        "forskrift/2022-10-14-1739", BV_TITLE, "2023-01-01",
        "Loven trer i kraft 1. januar 2023, med unntak av § 2-6. "
        "Fra samme tid oppheves lov 17. juli 1992 nr. 100 om barneverntjenester.",
        "2022-10-14 14:20",
    )  # fmt: skip
    commencement = baseline_commencement(_act(), (decision,), [])
    assert (commencement.default.status, commencement.default.effective) == (
        "dated", date(2023, 1, 1),
    )  # fmt: skip


def _amending_law(clause: str) -> bytes:
    return (
        '<html><body><header><dl><dd class="refid">lov/2020-06-19-1</dd>'
        '<dd class="dateInForce">Kongen bestemmer</dd>'
        '<dd class="dateOfPublication">2020-06-19 12:00</dd><dd class="title">Lov om endring</dd>'
        "</dl></header><main>"
        '<section class="section"><h2>I</h2>'
        '<article class="legalP">I lov 18. juni 2021 nr. 97 om barnevern gjøres følgende '
        "endringer:</article>"
        '<article class="defaultP">§ 2-1 oppheves.</article></section>'
        f'<section class="section"><h2>II</h2><article class="legalP">{clause}</article>'
        "</section></main></body></html>"
    ).encode()


def test_an_amending_law_s_dated_same_time_clause_is_refused_not_dropped() -> None:
    document = parse_document(
        _amending_law(
            "Loven trer i kraft 1. juli 2020. Fra samme tid trer endringen i § 2-1 i kraft, "
            "med unntak av annet ledd."
        ),
        "lti/2020/x.xml",
    )
    reading = interpret(document)
    timings = clause_timings(document, reading.clauses, reading.operations)
    (timing,) = timings.values()
    assert (timing.status, timing.reason) == (
        "unresolved", "commencement sentence outside the grammar",
    )  # fmt: skip


def test_an_amending_law_s_informational_same_time_sentence_keeps_its_date() -> None:
    document = parse_document(
        _amending_law("Loven trer i kraft 1. juli 2020. Fra samme tid oppheves lov 1. mai 1990."),
        "lti/2020/x.xml",
    )
    reading = interpret(document)
    timings = clause_timings(document, reading.clauses, reading.operations)
    (timing,) = timings.values()
    assert (timing.status, timing.effective) == ("dated", date(2020, 7, 1))
