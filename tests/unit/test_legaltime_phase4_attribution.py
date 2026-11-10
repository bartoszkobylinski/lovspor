"""Phase 4A (#856, #861): an amendment block's act is bound only by an intro that names it.

The sentences are the ones lov 19. juni 2015 nr. 65 (straffelovens ikraftsettingslov)
prints in its §§ 3-6 (pinned archive member ``lti/2015/nl-20150619-065.xml``); the
markup around them is synthetic. Before the fix, § 4's and § 5's operations (militær
straffelov, straffeprosessloven) were attributed to straffeloven 2005, the act § 3 names.
"""

import pytest

from lovspor.legaltime.document import parse_document
from lovspor.legaltime.instructions import Operation, interpret

STRAFFELOVEN = "lov/2005-05-20-28"
INTRO_3 = "I lov 20. mai 2005 nr. 28 om straff gjøres følgende endringer:"
INTRO_4 = (
    "Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i "
    "lov 22. mai 1902 nr. 13 militær straffelov:"
)
INTRO_5 = (
    "Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i "
    "lov 22. mai 1981 nr. 25 om rettergangsmåten i straffesaker:"
)
INTRO_6 = (
    "Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i "
    "annen lovgivning:"
)


def _law(body: str) -> bytes:
    return (
        '<html><body><header><dl><dd class="refid">lov/2015-06-19-65</dd>'
        '<dd class="dateInForce">2015-06-19</dd>'
        '<dd class="dateOfPublication">2015-06-19 15:00</dd>'
        '<dd class="title">Lov om ikraftsetting av straffeloven 2005</dd>'
        f"</dl></header><main>{body}</main></body></html>"
    ).encode()


def _ledd(text: str) -> str:
    return f'<article class="legalP">{text}</article>'


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _article(key: str, *children: str) -> str:
    return f'<article class="legalArticle" data-name="{key}">{"".join(children)}</article>'


def _operations(body: str) -> tuple[Operation, ...]:
    return interpret(parse_document(_law(body), "lti/2015/nl-20150619-065.xml")).operations


def _by_host(operations: tuple[Operation, ...]) -> dict[str | None, set[str | None]]:
    hosts: dict[str | None, set[str | None]] = {}
    for op in operations:
        hosts.setdefault(op.host_section, set()).add(op.act)
    return hosts


SECTION_3 = _article(
    "§3", _ledd(INTRO_3), _p("§ 13 første ledd skal lyde:"), _ledd("Ny tekst."),
    _p("§ 40 oppheves."),
)  # fmt: skip
SECTION_4 = _article(
    "§4", _ledd(INTRO_4), _p("§ 13 første ledd skal lyde:"),
    _ledd("Straffelovens første Del finder Anvendelse."), _p("§ 40 oppheves."),
    _p("§ 103 skal lyde:"), _ledd("Den, som uden Nødvendighed ødelegger."),
)  # fmt: skip
SECTION_5 = _article(
    "§5", _ledd(INTRO_5), _p("§ 3 annet ledd skal lyde:"), _ledd("Ny tekst."),
    _p("§ 273 oppheves."),
)  # fmt: skip


def test_militaer_straffelov_operations_are_never_attributed_to_straffeloven() -> None:
    hosts = _by_host(_operations(SECTION_3 + SECTION_4))
    assert hosts["§3"] == {STRAFFELOVEN}
    assert hosts["§4"] == {None}  # unattributed: the intro names an act the grammar cannot bind


def test_straffeprosessloven_operations_are_never_attributed_to_straffeloven() -> None:
    hosts = _by_host(_operations(SECTION_3 + SECTION_4 + SECTION_5))
    assert hosts["§5"] == {None}


def test_an_unrecognised_intro_in_the_same_article_closes_the_open_block() -> None:
    """Positive evidence or nothing: the block § 3 opened ends at the next intro, wherever it is."""
    body = _article(
        "§3", _ledd(INTRO_3), _p("§ 40 oppheves."), _ledd(INTRO_4), _p("§ 41 oppheves.")
    )
    operations = _operations(body)
    assert [(op.address.section, op.act) for op in operations] == [
        ("§40", STRAFFELOVEN),
        ("§41", None),
    ]


def test_a_new_legal_article_without_an_intro_does_not_inherit_the_act() -> None:
    body = SECTION_3 + _article("§4", _p("§ 41 oppheves."))
    assert [(op.host_section, op.act) for op in _operations(body)] == [
        ("§3", STRAFFELOVEN),
        ("§3", STRAFFELOVEN),
        ("§4", None),
    ]


def test_an_instruction_payload_ends_at_the_next_article_and_at_an_unbound_intro() -> None:
    """The last § 3 instruction's text is § 3's; § 4's intro is never read as its payload."""
    body = _article("§3", _ledd(INTRO_3), _p("§ 13 første ledd skal lyde:"), _ledd("Ny tekst."))
    body += _article("§4", _ledd("Tekst i § 4."), _p("§ 40 oppheves."))
    first = _operations(body)[0]
    assert [ledd.text for ledd in first.ledd] == ["Ny tekst."]
    same = _article(
        "§3", _ledd(INTRO_3), _p("§ 13 første ledd skal lyde:"), _ledd("Ny tekst."),
        _ledd(INTRO_4),
    )  # fmt: skip
    assert [ledd.text for ledd in _operations(same)[0].ledd] == ["Ny tekst."]


def test_a_recognised_intro_after_an_unrecognised_one_binds_its_own_act() -> None:
    body = _article(
        "§6", _ledd(INTRO_6),
        _ledd("5. I lov 13. august 1915 nr. 5 om domstolene gjøres følgende endringer:"),
        _p("§ 63 a annet ledd oppheves."),
    )  # fmt: skip
    operations = _operations(SECTION_3 + body)
    assert [(op.address.section, op.act) for op in operations if op.host_section == "§6"] == [
        ("§63a", "lov/1915-08-13-5")
    ]


def test_a_block_spanning_several_paragraphs_of_one_article_keeps_its_act() -> None:
    body = _article(
        "§3", _ledd(INTRO_3), _p("§ 13 første ledd skal lyde:"), _ledd("A."), _ledd("B."),
        _p("§ 37 nytt annet ledd skal lyde:"), _ledd("C."), _p("§ 40 oppheves."),
        _p("§ 41 oppheves."),
    )  # fmt: skip
    assert {op.act for op in _operations(body)} == {STRAFFELOVEN}


def test_a_part_without_legal_articles_keeps_its_intro_across_paragraphs() -> None:
    body = (
        '<section class="section"><h2>I</h2>'
        + _ledd("I lov 20. mai 2005 nr. 28 om straff gjøres følgende endringer:")
        + _p("§ 40 oppheves.")
        + _p("§ 41 oppheves.")
        + "</section>"
    )
    assert [op.act for op in _operations(body)] == [STRAFFELOVEN, STRAFFELOVEN]


def test_an_unrecognised_intro_produces_no_operation_and_no_clause() -> None:
    reading = interpret(parse_document(_law(SECTION_3 + SECTION_4), "lti/2015/nl-20150619-065.xml"))
    assert all(op.instruction != INTRO_4 for op in reading.operations)
    assert INTRO_4 not in reading.clauses


def test_an_unsupported_sentence_in_an_unbound_block_is_counted_unattributed_not_dropped() -> None:
    """``Slutningsbestemmelsen oppheves.`` (§ 4) instructs an act we cannot name: it is counted."""
    unbound = _operations(
        SECTION_3 + _article("§4", _ledd(INTRO_4), _p("Slutningsbestemmelsen oppheves."))
    )
    assert [(op.kind, op.act, op.host_section) for op in unbound][-1] == ("unsupported", None, "§4")
    prose = _operations(SECTION_3 + _article("§4", _p("Slutningsbestemmelsen oppheves.")))
    assert [op.host_section for op in prose] == ["§3", "§3"]  # outside any block: prose


@pytest.mark.parametrize(
    "sub_intro",
    [
        "§ 4-2 endres slik:",  # lov 26. november 2004 nr. 74 (friskolelova)
        "I § 15-11 gjøres følgende endringer:",  # lov 21. desember 2005 nr. 121 (aml)
        "§ 102 nr. 1 vert endra slik:",  # lov 21. desember 2005 nr. 131
        "Kapittel 6 endres slik:",
        "Tredje ledd endres slik:",
    ],
)
def test_an_intro_to_a_unit_of_the_same_act_keeps_the_block(sub_intro: str) -> None:
    body = _article("§3", _ledd(INTRO_3), _p(sub_intro), _p("§ 40 oppheves."))
    assert {op.act for op in _operations(body) if op.kind != "unsupported"} == {STRAFFELOVEN}


@pytest.mark.parametrize(
    "intro",
    [
        "I § 3 i lov 22. mai 1902 nr. 13 gjøres følgende endringer:",
        "I § 3 i straffeloven gjøres følgende endringer:",  # independent review, round 2
        "I kapittel 10 i arbeidsmiljøloven gjøres følgende endringer:",
        "§ 5 i lov om domstolene endres slik:",
        "I del II i forskrift om straffegjennomføring gjøres følgende endringer:",
        "Følgende endringer gjøres i lov 22. mai 1902 nr. 13:",
        "Lov 22. mai 1902 nr. 13 militær straffelov får følgende endringer:",
        "Med virkning fra 1. januar 2016 skal det gjøres endringer i annen lovgivning:",
        "I straffeloven 2005 tredje del gjøres følgende endringer:",  # lov 7. mars 2008 nr. 4
        "1. I militær straffelov 22. mai 1902 nr. 13 gjøres følgende endringer:",
        "(2) Brot på teieplikta kan straffast med bøter. 196. I lov 27. juni 2003 nr. 58 om "
        "særlige tiltak mot Republikken Zimbabwe gjøres følgende endringer:",
    ],
)
def test_an_intro_naming_another_act_or_none_the_grammar_reads_closes_the_block(
    intro: str,
) -> None:
    body = _article("§3", _ledd(INTRO_3), _p("§ 40 oppheves."), _ledd(intro), _p("§ 41 oppheves."))
    assert [(op.address.section, op.act) for op in _operations(body)] == [
        ("§40", STRAFFELOVEN),
        ("§41", None),
    ]


def test_a_substitution_intro_and_a_payload_without_an_act_keep_the_block() -> None:
    """Neither names an act it would open: the open block and its act stay as they were."""
    body = _article(
        "§3", _ledd(INTRO_3), _p("§ 13 første ledd skal lyde:"),
        _ledd("Ved endringer i vilkårene gjelder følgende:"), _p("§ 40 oppheves."),
    )  # fmt: skip
    operations = _operations(body)
    assert {op.act for op in operations} == {STRAFFELOVEN}
    assert [ledd.text for ledd in operations[0].ledd] == [
        "Ved endringer i vilkårene gjelder følgende:"
    ]


def test_a_reordered_intro_the_grammar_reads_binds_its_own_act_not_the_previous_one() -> None:
    """Review round 3's example: ``_INTRO`` reads it (verb ``skal``), so it is positive evidence."""
    body = _article(
        "§3", _ledd(INTRO_3), _p("§ 40 oppheves."),
        _ledd("I lov 1. januar 2000 nr. 1 skal følgende endringer gjøres:"), _p("§ 41 oppheves."),
    )  # fmt: skip
    assert [(op.address.section, op.act) for op in _operations(body) if op.address.section] == [
        ("§40", STRAFFELOVEN),
        ("§41", "lov/2000-01-01-1"),
    ]


def test_an_instruction_citing_an_earlier_amending_act_keeps_the_block() -> None:
    """lov 20. juni 2014 nr. 49: the act named is where § 52 a's wording came from, not a target."""
    body = _article(
        "§3", _ledd(INTRO_3),
        _p("I § 52 a, slik ny § 52 a lyder etter endringer ved lov 20. januar 2012 nr. 6 del X, "
           "skal første ledd innledningen lyde:"),
        _ledd("Ungdomsstraff kan idømmes."), _p("§ 83 nytt åttende ledd skal lyde:"), _ledd("Ny."),
    )  # fmt: skip
    assert [op.act for op in _operations(body)] == [STRAFFELOVEN, STRAFFELOVEN]


def test_an_addition_to_an_embedded_amendment_of_another_act_closes_the_block() -> None:
    """lov 13. april 2007 nr. 14 IV: straffeprosessloven text inside straffeloven § 102 nr. 4."""
    body = _article(
        "§3", _ledd("Lov 20. mai 2005 nr. 28 om straff endres slik:"),
        _ledd("I § 102 nr. 4 (endringer i lov 22. mai 1981 nr. 25 om rettegangsmåten i "
              "straffesaker) tilføyes:"),
        _p("§ 68 sjette ledd skal lyde:"), _ledd("Anke over tingrettens avgjørelse."),
    )  # fmt: skip
    assert [op.act for op in _operations(body)] == [None]
