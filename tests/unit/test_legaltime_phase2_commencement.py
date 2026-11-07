"""Phase 2 (#744): commencement shapes read from the eight harder acts.

Every sentence here is a shape seen in the permitted archives (research note
2026-11-07, section "failure taxonomy"); none is invented.
"""

from datetime import date

import pytest

from lovspor.legaltime.commencement import (
    _informational,
    baseline_timing,
    header_timing,
    resolve_timings,
)
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.instructions import interpret
from lovspor.legaltime.scopes import Scope, _scopes_of

LAW = "lov/2020-12-18-999"


def _law(clause: str, date_in_force: str = "Kongen bestemmer.") -> Document:
    body = (
        f'<html><body><header><dl><dd class="refid">{LAW}</dd>'
        f'<dd class="dateInForce">{date_in_force}</dd>'
        '<dd class="dateOfPublication">2020-12-18 12:10</dd><dd class="title">Lov om endringer</dd>'
        '</dl></header><main><section class="section"><h2>I</h2><article class="defaultP">'
        "I lov 14. juni 2019 nr. 99 om prøving gjøres følgende endringer:</article>"
        '<article class="defaultP">§ 1 skal lyde:</article>'
        '<article class="futureLegalArticle" data-name="§1"><article class="legalP">A.</article>'
        '</article></section><section class="section"><h2>II</h2><article class="defaultP">'
        "I lov 10. februar 1967 om behandlingsmåten skal § 5 lyde:</article>"
        '<article class="futureLegalArticle" data-name="§5"><article class="legalP">C.</article>'
        f'</article></section><section class="section"><h2>III</h2><article class="legalP">{clause}'
        "</article></section></main></body></html>"
    )
    return parse_document(body.encode(), "lti/2020/law.xml")


def _decision(
    refid: str, title: str, date_in_force: str, body: str, commences: tuple[str, ...] = ()
) -> Document:
    listed = "".join(f"<li>{law}</li>" for law in commences)
    xml = (
        f'<html><body><header><dl><dd class="refid">{refid}</dd>'
        f'<dd class="dateInForce">{date_in_force}</dd>'
        '<dd class="dateOfPublication">2021-01-01 10:00</dd>'
        f'<dd class="title">{title}</dd><dd class="changesToDocuments"><ul>{listed}</ul></dd>'
        "</dl></header><main>"
        + "".join(f'<article class="legalP">{s}</article>' for s in body.split("\n"))
        + "</main></body></html>"
    )
    return parse_document(xml.encode(), f"lti/x/{refid.rsplit('/', maxsplit=1)[-1]}.xml")


def _by_part(law: Document, *decisions: Document):
    reading = interpret(law)
    timings = resolve_timings(law, reading.clauses, reading.operations, decisions)
    return {(op.part, op.address.section): timings[op.id] for op in reading.operations}


# ---------------------------------------------------------------- header shapes


@pytest.mark.parametrize(
    "raw",
    [
        "Kongen bestemmer.",
        "Kongen fastsetter.",
        "Kongen fastset",
        "Departementet bestemmer.",
        "Kongen avgjer.",
    ],
)
def test_delegation_headers_with_or_without_a_period_are_pending(raw: str) -> None:
    assert (
        header_timing(_law("Loven gjelder fra den tid Kongen bestemmer.", raw)).status == "pending"
    )


def test_a_mixed_header_stays_outside_the_grammar() -> None:
    assert header_timing(_law("x", "2007-06-29, Kongen fastset.")).status == "unresolved"
    assert header_timing(_law("x", "Straks")).status == "unresolved"


# ---------------------------------------------------------------- clause sentences


@pytest.mark.parametrize(
    "sentence",
    [
        "De ulike bestemmelsene kan settes i kraft til forskjellig tid.",
        "De ulike endringene kan settes i kraft til ulik tid.",
        "(2) De enkelte bestemmelsene kan settes i kraft til ulik tid.",
        "Det kan fastsettes at bestemmelser skal tre i kraft til ulik tid for ulike skyldnere.",
        "Forskrifter gitt i medhold av straffeloven 1902 gjelder også etter at loven her har trådt i kraft.",  # noqa: E501
        "Endringene i tvisteloven § 28-3 gjelder bare der innførsel finner sted etter at loven har trådt i kraft.",  # noqa: E501
        "Loven §§ 59 til 62 gjelder for inngrep som finner sted etter at loven har trådt i kraft.",
        "Kongen kan sette i kraft og oppheve de enkelte bestemmelsene til forskjellig tid.",
    ],
)
def test_staggering_and_transitional_sentences_state_no_commencement(sentence: str) -> None:
    assert _informational(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "Loven gjelder først fra 1. januar 2010.",
        "Endringene gjelder bare fra 1. juli 2021.",
        "Loven trer i kraft 1. juli 2021.",
    ],
)
def test_dated_sentences_are_never_informational(sentence: str) -> None:
    assert not _informational(sentence)


def test_nynorsk_delegation_clause_is_pending() -> None:
    law = _law("Lova gjeld frå den tida Kongen avgjer.")
    assert {t.status for t in _by_part(law).values()} == {"pending"}


def test_a_numbered_clause_sentence_is_read_without_its_number() -> None:
    law = _law(
        "(1) Loven trer i kraft 1. juli 2021. (2) De enkelte bestemmelsene kan settes i kraft til ulik tid."  # noqa: E501
    )
    assert {t.effective for t in _by_part(law).values()} == {date(2021, 7, 1)}


def test_a_staggering_sentence_beside_a_delegation_leaves_everything_pending() -> None:
    law = _law(
        "Loven gjelder fra den tid Kongen bestemmer. De ulike bestemmelsene kan settes i kraft til forskjellig tid."  # noqa: E501
    )
    assert {t.status for t in _by_part(law).values()} == {"pending"}


# ---------------------------------------------------------------- Delt grammar


@pytest.mark.parametrize(
    "head", ["Lovens del I", "Loven del I", "Lova del I", "Endringslova del I"]
)
def test_a_part_scope_may_name_the_law_as_loven_or_endringsloven(head: str) -> None:
    scopes = _scopes_of(f"{head} trer i kraft 1. januar 2010.", None)
    assert scopes == [
        Scope(part="I", when=date(2010, 1, 1), quote=f"{head} trer i kraft 1. januar 2010.")
    ]


def test_delvis_is_a_partial_decision_title() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    delvis = _decision(
        "forskrift/2021-03-01-2", "Delvis ikraftsetting av lov 18. desember 2020 nr. 999 om endringer",  # noqa: E501
        "2021-07-01", "Delvis ikraftsetting av lov 18. desember 2020 nr. 999 om endringer.\nLovens del I trer i kraft 1. juli 2021.",  # noqa: E501
    )  # fmt: skip
    timings = _by_part(law, delvis)
    assert timings[("I", "§1")].effective == date(2021, 7, 1)
    assert timings[("II", "§5")].status == "pending"


# ---------------------------------------------------------------- multi-law decisions


MULTI = (
    "Ikraftsetting av lov 15. mai 2008 nr. 35 om utlendingers adgang til riket (utlendingsloven).\n"
    "Loven trer i kraft 1. januar 2010.\n"
    "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer.\n"
    "Lovens del I trer i kraft 1. mars 2010.\n"
    "Delvis ikraftsetting av lov 19. juni 2009 nr. 38 om endringer i utlendingsloven.\n"
    "Lovens del II trer i kraft 1. januar 2010."
)


def test_a_multi_law_decision_is_read_block_by_block() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    other = _law("Loven gjelder fra den tid Kongen bestemmer.").model_copy(
        update={"header": _law("x").header.model_copy(update={"refid": "lov/2008-05-15-35"})}
    )
    decision = _decision(
        "forskrift/2009-10-30-1324",
        "Ikraftsetting av lov 15. mai 2008 nr. 35 om utlendingers adgang til riket (utlendingsloven)",  # noqa: E501
        "2010-01-01, 2010-03-01",
        MULTI,
    )
    ours = _by_part(law, decision)
    assert ours[("I", "§1")].effective == date(2010, 3, 1)
    assert ours[("II", "§5")].status == "pending"  # the block for our law dates only part I
    theirs = _by_part(other, decision)
    assert {t.effective for t in theirs.values()} == {date(2010, 1, 1)}


def test_a_multi_law_decision_without_a_block_for_the_law_is_unreadable() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    body = (
        "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer.\n"
        "Ikraftsetting av lov 15. mai 2008 nr. 35 om utlendingers adgang til riket.\n"
        "Loven trer i kraft 1. januar 2010."
    )
    decision = _decision(
        "forskrift/2009-10-30-1324",
        "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer",
        "2010-01-01",
        body,
    )
    timings = _by_part(law, decision)
    assert {t.reason for t in timings.values()} == {
        "decision body has no commencement sentence for this law"
    }


def test_straks_in_a_decision_block_means_the_decision_s_own_date() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    body = (
        "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer.\nLoven trer i kraft straks.\n"
        "Ikraftsetting av lov 15. mai 2008 nr. 35 om utlendingers adgang til riket.\nLoven trer i kraft 1. mars 2010."  # noqa: E501
    )
    decision = _decision(
        "forskrift/2010-01-08-3",
        "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer",
        "2010-01-08, 2010-03-01",
        body,
    )
    assert {t.effective for t in _by_part(law, decision).values()} == {date(2010, 1, 8)}


def test_a_single_block_decision_still_checks_its_header_against_its_body() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    decision = _decision(
        "forskrift/2021-01-01-1",
        "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer",
        "2021-07-01",
        "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer.\nLoven trer i kraft 1. oktober 2021.",  # noqa: E501
    )
    assert {t.reason for t in _by_part(law, decision).values()} == {
        "full decision body disagrees with its header"
    }


def test_baseline_reads_its_own_block_of_a_multi_law_decision() -> None:
    act = _law("Loven gjelder fra den tid Kongen bestemmer.").model_copy(
        update={"header": _law("x").header.model_copy(update={"refid": "lov/2008-05-15-35"})}
    )
    decision = _decision(
        "forskrift/2009-10-30-1324",
        "Ikraftsetting av lov 15. mai 2008 nr. 35 om utlendingers adgang til riket (utlendingsloven)",  # noqa: E501
        "2010-01-01, 2010-03-01",
        MULTI,
    )
    timing = baseline_timing(act, (decision,))
    assert timing.status == "dated" and timing.effective == date(2010, 1, 1)


def test_a_decision_listed_as_touching_the_law_but_with_no_block_for_it_is_not_its_decision() -> (
    None
):
    """A decision commencing another law lists every act that law amends; that is not commencement."""  # noqa: E501
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    body = (
        "Ikraftsetting av lov 15. mai 2008 nr. 35 om utlendingers adgang til riket.\n"
        "Loven trer i kraft 1. januar 2010."
    )
    decision = _decision(
        "forskrift/2009-10-30-1324",
        "Ikraftsetting av lov 15. mai 2008 nr. 35 om utlendingers adgang til riket",
        "2010-01-01",
        body,
        ("lov/2008-05-15-35", LAW),
    )
    assert {t.status for t in _by_part(law, decision).values()} == {"pending"}


def test_a_multi_law_decision_whose_block_title_cites_the_law_is_its_decision() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    decision = _decision(
        "forskrift/2009-10-30-1324",
        "Ikraftsetting av lov 15. mai 2008 nr. 35 om utlendingers adgang til riket (utlendingsloven)",  # noqa: E501
        "2010-01-01, 2010-03-01",
        MULTI,
    )
    ours = _by_part(law, decision)
    assert ours[("I", "§1")].effective == date(2010, 3, 1)
    assert ours[("II", "§5")].status == "pending"


# ---------------------------------------------------------------- part lists and remainders


def _delt(body: str, when: str = "2021-07-01", refid: str = "forskrift/2021-03-01-2") -> Document:
    title = "Delt ikraftsetting av lov 18. desember 2020 nr. 999 om endringer"
    return _decision(refid, title, when, f"{title}.\n{body}")


@pytest.mark.parametrize(
    "body",
    [
        "Romertall I trer i kraft 1. juli 2021.",
        "Lovens del I trer i kraft 1. juli 2021.",
        "Følgende trer i kraft 1. juli 2021: Endringsloven del I.",
        "Følgende trer i kraft 1. juli 2021: Endringsloven del I (prøveloven).",
        "Følgende endringer trer i kraft 1. juli 2021: Endringsloven del I (lov 14. juni 2019 nr. 99 om prøving).",  # noqa: E501
        "Endringsloven del I (prøveloven) trer i kraft 1. juli 2021.",
    ],
)
def test_a_single_part_scope_in_its_several_spellings(body: str) -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    timings = _by_part(law, _delt(body))
    assert timings[("I", "§1")].effective == date(2021, 7, 1), body
    assert timings[("II", "§5")].status == "pending", body


@pytest.mark.parametrize(
    "body",
    [
        "Romertall I og II trer i kraft 1. juli 2021.",
        "Romertall I, II og III trer i kraft 1. juli 2021.",
        "Lovens del I og del II trer i kraft 1. juli 2021.",
        "Følgende trer i kraft 1. juli 2021: Endringsloven del I og II.",
        "Følgjande blir sett i kraft 1. juli 2021: Endringslova del I og II.",
    ],
)
def test_a_list_of_parts_dates_each_part(body: str) -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    timings = _by_part(law, _delt(body))
    assert {t.effective for t in timings.values()} == {date(2021, 7, 1)}, body


def test_straks_in_a_part_scope_is_the_resolution_s_own_date() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    timings = _by_part(law, _delt("Romertall I trer i kraft straks.", when="2021-03-01"))
    assert timings[("I", "§1")].effective == date(2021, 3, 1)
    assert timings[("II", "§5")].status == "pending"


def test_the_remainder_covers_what_no_other_scope_of_the_decision_covers() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    body = (
        "Romertall I trer i kraft 1. juli 2021.\n"
        "De andre bestemmelsene i endringsloven trer i kraft 1. oktober 2021."
    )
    timings = _by_part(law, _delt(body))
    assert timings[("I", "§1")].effective == date(2021, 7, 1)
    assert timings[("II", "§5")].effective == date(2021, 10, 1)
    alone = _by_part(law, _delt("Loven for øvrig trer i kraft 1. oktober 2021."))
    assert {t.reason for t in alone.values()} == {"partial decision outside the grammar"}


@pytest.mark.parametrize(
    "body",
    [
        "Romertall I trer i kraft 1. juli 2021, med unntak av endringen i § 1.",
        "Følgende trer i kraft 1. juli 2021: Endringsloven del I med unntak av ny § 2 a.",
        "Følgende trer i kraft 1. juli 2021: Endringsloven del I § 1 annet ledd.",
        "Romertall I § 1 første ledd og II trer i kraft 1. juli 2021.",
    ],
)
def test_part_scopes_with_exceptions_or_ledd_qualifiers_stay_outside_the_grammar(body: str) -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    timings = _by_part(law, _delt(body))
    assert {t.reason for t in timings.values()} == {"partial decision outside the grammar"}, body


def test_a_part_scope_listing_bare_sections_dates_only_those_sections() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    timings = _by_part(law, _delt("Følgende trer i kraft 1. juli 2021: Endringsloven del I § 1."))
    assert timings[("I", "§1")].effective == date(2021, 7, 1)
    assert timings[("II", "§5")].status == "pending"
    other = _by_part(law, _delt("Følgende trer i kraft 1. juli 2021: Endringsloven del II § 7."))
    assert {t.status for t in other.values()} == {"pending"}


# ---------------------------------------------------------------- commencement references


@pytest.mark.parametrize(
    "sentence",
    [
        "Loven har virkning for søksmål eller anke som tas ut etter at loven har trådt i kraft.",
        "Overtredelsesgebyr etter § 15-4 kan bare ilegges for lovovertredelser begått etter at loven trådte i kraft.",  # noqa: E501
        "Lavere bedriftsinterne aldersgrenser enn 70 år kan beholdes inntil ett år etter at loven trer i kraft, dersom de er etablert før dette tidspunktet.",  # noqa: E501
        "For arbeidsforhold som eksisterer på det tidspunkt loven trer i kraft, skal den skriftlige arbeidsavtalen suppleres.",  # noqa: E501
        "Etter nytt kapittel 28 A i tvisteloven kan det bare begjæres informasjon når stevningen er inngitt etter at loven har trådt i kraft.",  # noqa: E501
        "Barnelova § 43 gjelder når kontaktforbud er fastsatt etter at lovendringen trer i kraft.",
        "Forskrifter gitt med heimel i lova held fram å gjelde til dei blir oppheva.",
    ],
)
def test_a_sentence_that_only_refers_to_the_commencement_states_none(sentence: str) -> None:
    assert _informational(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "Loven trer i kraft straks.",
        "Endring under I trer i kraft straks.",
        "Loven trer i kraft 1. juli 2021 og gjelder for saker som kommer inn etter at loven har trådt i kraft.",  # noqa: E501
        "Kongen bestemmer når loven trer i kraft.",
    ],
)
def test_a_sentence_that_states_or_delegates_a_commencement_is_not_informational(
    sentence: str,
) -> None:
    assert not _informational(sentence)


def test_a_referring_sentence_beside_the_clause_does_not_unsettle_the_law() -> None:
    law = _law(
        "Loven trer i kraft 1. juli 2021. "
        "Loven har virkning for søksmål som tas ut etter at loven har trådt i kraft."
    )
    assert {t.effective for t in _by_part(law).values()} == {date(2021, 7, 1)}


# ---------------------------------------------------------------- clause shapes of the harder acts


def _law3(clause: str, date_in_force: str = "Kongen bestemmer.") -> Document:
    """Parts I, II and III each amend a provision; part IV is the commencement clause."""
    body = (
        f'<html><body><header><dl><dd class="refid">{LAW}</dd>'
        f'<dd class="dateInForce">{date_in_force}</dd>'
        '<dd class="dateOfPublication">2020-12-18 12:10</dd><dd class="title">Lov om endringer</dd>'
        "</dl></header><main>"
        + "".join(
            f'<section class="section"><h2>{part}</h2><article class="defaultP">'
            f"I lov {cite} gjøres følgende endringer:</article>"
            f'<article class="defaultP">§ {n} skal lyde:</article>'
            f'<article class="futureLegalArticle" data-name="§{n}"><article class="legalP">T.</article>'  # noqa: E501
            "</article></section>"
            for part, cite, n in (
                ("I", "14. juni 2019 nr. 99 om prøving", 1),
                ("II", "10. februar 1967 om behandlingsmåten", 5),
                ("III", "15. juni 2001 nr. 59 om stiftelser", 9),
            )
        )
        + f'<section class="section"><h2>IV</h2><article class="legalP">{clause}</article></section>'  # noqa: E501
        "</main></body></html>"
    )
    return parse_document(body.encode(), "lti/2020/law.xml")


def _parts(law: Document, *decisions: Document) -> dict[str, tuple[str, date | None]]:
    reading = interpret(law)
    timings = resolve_timings(law, reading.clauses, reading.operations, decisions)
    return {op.part: (timings[op.id].status, timings[op.id].effective) for op in reading.operations}


@pytest.mark.parametrize(
    "clause, expected",
    [
        (
            "Endringene under I trer i kraft straks. Endringene under II trer i kraft 1. januar 2015.",  # noqa: E501
            {
                "I": ("dated", date(2020, 12, 18)),
                "II": ("dated", date(2015, 1, 1)),
                "III": ("unresolved", None),
            },
        ),
        (
            "Del I og III trer i kraft straks. Del II gjeld frå den tida Kongen fastset.",
            {
                "I": ("dated", date(2020, 12, 18)),
                "II": ("pending", None),
                "III": ("dated", date(2020, 12, 18)),
            },
        ),
        (
            "Loven del III trer i kraft straks. Loven del I til II gjelder fra den tiden Kongen bestemmer.",  # noqa: E501
            {"I": ("pending", None), "II": ("pending", None), "III": ("dated", date(2020, 12, 18))},
        ),
        (
            "Endringene i del I gjelder fra den tid Kongen bestemmer. Endringen i del II trer i kraft straks.",  # noqa: E501
            {
                "I": ("pending", None),
                "II": ("dated", date(2020, 12, 18)),
                "III": ("unresolved", None),
            },
        ),
        (
            "Loven trer i kraft 1. juli 2021. Endringene under del III trer likevel i kraft straks.",  # noqa: E501
            {
                "I": ("dated", date(2021, 7, 1)),
                "II": ("dated", date(2021, 7, 1)),
                "III": ("dated", date(2020, 12, 18)),
            },
        ),
        (
            "Lovens del II gjelder fra den tiden Kongen fastsetter. Loven trer ellers i kraft 1. januar 2025.",  # noqa: E501
            {
                "I": ("dated", date(2025, 1, 1)),
                "II": ("pending", None),
                "III": ("dated", date(2025, 1, 1)),
            },
        ),
    ],
)
def test_part_scoped_clauses_date_each_part_on_its_own(clause: str, expected: dict) -> None:
    assert _parts(_law3(clause, "2020-12-18, Kongen bestemmer.")) == expected


@pytest.mark.parametrize(
    "clause, status, when",
    [
        ("Denne loven trer i kraft 1. desember 2004.", "dated", date(2004, 12, 1)),
        ("Denne lova trer i kraft på det tidspunktet Kongen bestemmer.", "pending", None),
        ("Lova blir sett i verk på det tidspunkt Kongen bestemmer.", "pending", None),
        (
            "Loven her trer i kraft straks. Fra samme tid oppheves straffeloven 22. mai 1902 nr. 10.",  # noqa: E501
            "dated",
            date(2020, 12, 18),
        ),
        (
            "Elles tek lova til å gjelde på det tidspunkt Kongen fastset, og dei enkelte føresegnene kan setjast i verk til ulik tid.",  # noqa: E501
            "pending",
            None,
        ),
    ],
)
def test_default_clause_spellings(clause: str, status: str, when: date | None) -> None:
    parts = _parts(_law3(clause, "2020-12-18"))
    assert set(parts.values()) == {(status, when)}, clause


def test_an_amendment_intro_inside_the_commencement_provision_is_not_a_clause() -> None:
    body = (
        f'<html><body><header><dl><dd class="refid">{LAW}</dd><dd class="dateInForce">2020-12-18</dd>'  # noqa: E501
        '<dd class="dateOfPublication">2020-12-18 12:10</dd><dd class="title">Lov om noe</dd></dl></header>'  # noqa: E501
        '<main><section class="section"><h2>Kapittel 9. Ikrafttredelse og endringer i andre lover</h2>'  # noqa: E501
        '<article class="legalArticle" data-name="§40"><h4><span class="legalArticleTitle">Ikrafttredelse</span></h4>'  # noqa: E501
        '<article class="legalP">Loven trer i kraft straks.</article>'
        '<article class="legalP">I Almindelig borgerlig Straffelov 22. mai 1902 nr. 10 gjøres følgende endringer:</article>'  # noqa: E501
        '<article class="legalP">§ 262 skal lyde:</article>'
        '<article class="futureLegalArticle" data-name="§262"><article class="legalP">Ny.</article></article>'  # noqa: E501
        "</article></section></main></body></html>"
    )
    law = parse_document(body.encode(), "lti/2020/act.xml")
    reading = interpret(law)
    assert [op.kind for op in reading.operations] == ["replace_section"]
    timings = resolve_timings(law, reading.clauses, reading.operations, ())
    assert {t.effective for t in timings.values()} == {date(2020, 12, 18)}


def test_short_title_citations_hide_no_dates_and_references_know_denne_lov() -> None:
    assert _informational("Fra samme tid oppheves straffeloven 22. mai 1902 nr. 10.")
    assert _informational(
        "Ikraftsetting av straffeloven 20. mai 2005 nr. 28 reguleres av § 411 i lovvedtaket del I."
    )
    assert _informational(
        "Domfelte skal tilbakeføres senest to år etter at denne lov har trådt i kraft."
    )
    assert not _informational("Straffeloven 20. mai 2005 nr. 28 trer i kraft 1. oktober 2015.")


def test_a_section_scoped_dated_exception_under_a_part() -> None:
    law = _law3(
        "Loven trer i kraft 1. juli 2025. Opphevingen av utlendingsloven §§ 1 og 9 under del I gjelder fra 15. mars 2027."  # noqa: E501
    )
    reading = interpret(law)
    timings = resolve_timings(law, reading.clauses, reading.operations, ())
    by = {(op.part, op.address.section): timings[op.id].effective for op in reading.operations}
    assert by[("I", "§1")] == date(2027, 3, 15)
    assert by[("II", "§5")] == date(2025, 7, 1)
    assert by[("III", "§9")] == date(2025, 7, 1)  # § 9 is named under del I, not del III
