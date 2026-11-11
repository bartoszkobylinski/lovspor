"""#872: a canonical intro citing an act by date alone binds only on the header's exact proof.

``INTRO`` and ``INTRO_SLIK`` accept a citation without ``nr. N``. Lovdata identifies an
unnumbered act by a date-only refid (``lov/1930-02-21``), so such a citation names an act only
when the member's own ``changesToDocuments`` lists exactly that refid and no numbered act of
the same date. Otherwise the intro closes the block before it and what follows stays
unattributed: identity is never inferred from a matching date, a title, the previous block or
a numbered refid of that date.

Every intro sentence is quoted from a pinned archive member (``lovtidend-avd1-2001-2025``,
sha256 ``d55f8a38…``), named beside it. The markup and the headers around them are synthetic
unless a test says it copies the member's header.
"""

import pytest

from lovspor.legaltime.document import parse_document
from lovspor.legaltime.instructions import Operation, interpret

STRAFFELOVEN = "lov/2005-05-20-28"
SKIFTE = "lov/1930-02-21"  # lov 21. februar 1930 om skifte, unnumbered
FORVALTNING = "lov/1967-02-10"  # lov 10. februar 1967 (forvaltningsloven), unnumbered
NUMBERED = "I lov 20. mai 2005 nr. 28 om straff gjøres følgende endringer:"  # lov/2015-06-19-65
INTRO_PROVEN = "2. I lov 21. februar 1930 om skifte gjøres følgende endringer:"  # lov/2017-05-11-26
INTRO_UNLISTED = "14. I lov 21. februar 1930 om skifte gjøres følgende endringer:"  # 2007-01-26-3
INTRO_INLINE = "2. I lov 21. februar 1930 om skifte skal § 86 annet ledd lyde:"  # lov/2014-05-09-16
SLIK = "4. Lov 10. februar 1967 om behandlingsmåten i forvaltningssaker blir endra slik:"
# ``SLIK``: lov/2006-05-19-16 § 34 (its header lists lov/1967-02-10)
NR_WITHOUT_STOP = "I lov 20. mai 2005 nr 28 om straff gjøres følgende endring:"  # 2021-05-07-33
NUMBER_LEFT_OUT = (  # lov/2008-12-19-115, whose header lists only lov/2001-04-20-13
    "I lov 20. april 2001 om erstatning frå staten for personskade valda ved straffbar handling "
    "m.m. (valdsoffererstatningslova) skal § 11 første ledd lyde:"
)
AMENDMENT_OF_AMENDMENT = (  # lov/2007-06-15-27, whose header lists only lov/2006-12-15-81
    "I lov 15. desember 2006 om endringer i lov 26. mars 1999 nr. 14 om skatt av formue og "
    "inntekt (skatteloven) gjøres følgende endringer:"
)


def _law(body: str, changes: tuple[str, ...]) -> bytes:
    items = "".join(f"<li>{refid}</li>" for refid in changes)
    return (
        '<html><body><header><dl><dd class="refid">lov/2017-05-11-26</dd>'
        '<dd class="dateInForce">2017-05-11</dd>'
        '<dd class="dateOfPublication">2017-05-11 15:00</dd>'
        '<dd class="title">Lov om endringar i lovgivinga</dd>'
        f'<dd class="changesToDocuments"><ul>{items}</ul></dd>'
        f"</dl></header><main>{body}</main></body></html>"
    ).encode()


def _ledd(text: str) -> str:
    return f'<article class="legalP">{text}</article>'


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _article(key: str, *children: str) -> str:
    return f'<article class="legalArticle" data-name="{key}">{"".join(children)}</article>'


def _operations(body: str, *changes: str) -> tuple[Operation, ...]:
    document = parse_document(_law(body, changes), "lti/2017/nl-20170511-026.xml")
    return interpret(document).operations


def _acts(body: str, *changes: str) -> list[tuple[str | None, str | None]]:
    return [(op.address.section, op.act) for op in _operations(body, *changes)]


def _after_straffeloven(intro: str) -> str:
    """A straffeloven block, then ``intro`` in the same article, then one instruction."""
    return _article("§1", _ledd(NUMBERED), _p("§ 40 oppheves."), _ledd(intro), _p("§ 41 oppheves."))


# 1. Exact header identity binds -------------------------------------------------------------


def test_intro_date_only_citation_binds_when_the_header_lists_that_exact_refid() -> None:
    body = _article("§1", _ledd(INTRO_PROVEN), _p("§ 12 a første ledd skal lyde:"), _ledd("Ny."))
    assert _acts(body, SKIFTE, STRAFFELOVEN) == [("§12a", SKIFTE)]


def test_intro_slik_date_only_citation_binds_when_the_header_lists_that_exact_refid() -> None:
    body = _article("§34", _p(SLIK), _p("§ 24 oppheves."))
    assert _acts(body, FORVALTNING) == [("§24", FORVALTNING)]


def test_inline_intro_date_only_citation_binds_its_instruction_on_header_proof() -> None:
    body = _article("§1", _p(INTRO_INLINE), _ledd("Ny tekst."))
    assert _acts(body, SKIFTE) == [("§86", SKIFTE)]


# 2. Absent from the header: unattributed, and the previous block is closed ------------------


def test_intro_date_only_citation_absent_from_the_header_closes_the_block() -> None:
    """lov/2007-01-26-3's header does not list lov/1930-02-21: nothing proves the act."""
    assert _acts(_after_straffeloven(INTRO_UNLISTED), STRAFFELOVEN) == [
        ("§40", STRAFFELOVEN),
        ("§41", None),
    ]


def test_intro_slik_date_only_citation_absent_from_the_header_closes_the_block() -> None:
    assert _acts(_after_straffeloven(SLIK), STRAFFELOVEN) == [
        ("§40", STRAFFELOVEN),
        ("§41", None),
    ]


def test_inline_intro_date_only_citation_absent_from_the_header_is_unattributed() -> None:
    body = _article("§1", _ledd(NUMBERED), _p("§ 40 oppheves."), _p(INTRO_INLINE), _ledd("Ny."))
    assert _acts(body, STRAFFELOVEN) == [("§40", STRAFFELOVEN), ("§86", None)]


def test_unsupported_sentence_under_an_unproven_date_only_intro_is_counted_not_dropped() -> None:
    """An unproven intro opens a block of an unknown act: its prose is an unattributed operation."""
    body = _article("§1", _ledd(INTRO_UNLISTED), _p("Slutningsbestemmelsen oppheves."))
    assert [(op.kind, op.act) for op in _operations(body, STRAFFELOVEN)] == [("unsupported", None)]


# 3. The header lists a numbered act of that date: no binding --------------------------------


@pytest.mark.parametrize(
    ("intro", "changes"),
    [
        (NR_WITHOUT_STOP, (STRAFFELOVEN, "lov/2018-04-20-7")),  # lov/2021-05-07-33's header
        (AMENDMENT_OF_AMENDMENT, ("lov/2006-12-15-81",)),  # lov/2007-06-15-27's header
        (SLIK, ("lov/1967-02-10-3",)),
    ],
)
def test_a_numbered_refid_of_the_same_date_proves_no_date_only_citation(
    intro: str, changes: tuple[str, ...]
) -> None:
    assert _acts(_after_straffeloven(intro), *changes) == [("§40", STRAFFELOVEN), ("§41", None)]


def test_an_intro_leaving_out_the_number_binds_neither_the_date_nor_the_numbered_act() -> None:
    """lov/2008-12-19-115: its header lists lov/2001-04-20-13, the sentence prints no number."""
    body = _article("§1", _p(NUMBER_LEFT_OUT), _ledd("Ny tekst."))
    assert _acts(body, "lov/2001-04-20-13") == [("§11", None)]


# 4. Several acts of one date: no guessed identity -------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        (SKIFTE, "lov/1930-02-21-3"),  # the unnumbered act and a numbered one of that date
        ("lov/1930-02-21-3", "lov/1930-02-21-4"),  # two numbered acts of that date
    ],
)
@pytest.mark.parametrize("intro", [INTRO_UNLISTED, "Lov 21. februar 1930 om skifte endres slik:"])
def test_several_acts_of_the_cited_date_leave_the_block_unattributed(
    intro: str, changes: tuple[str, ...]
) -> None:
    assert _acts(_after_straffeloven(intro), STRAFFELOVEN, *changes) == [
        ("§40", STRAFFELOVEN),
        ("§41", None),
    ]


# 5. An unbound intro after a valid date-only block clears it --------------------------------


def test_an_unbound_intro_after_a_proven_date_only_block_clears_the_binding() -> None:
    body = _article(
        "§1", _ledd(INTRO_PROVEN), _p("§ 40 oppheves."),
        _ledd("Med virkning fra 1. januar 2016 skal det gjøres endringer i annen lovgivning:"),
        _p("§ 41 oppheves."),
    )  # fmt: skip
    assert _acts(body, SKIFTE) == [("§40", SKIFTE), ("§41", None)]


def test_an_unproven_date_only_intro_after_a_proven_one_clears_the_binding() -> None:
    body = _article(
        "§1", _ledd(INTRO_PROVEN), _p("§ 40 oppheves."), _ledd(SLIK), _p("§ 41 oppheves.")
    )
    assert _acts(body, SKIFTE) == [("§40", SKIFTE), ("§41", None)]


def test_a_unit_only_sub_intro_keeps_a_proven_date_only_block() -> None:
    body = _article(
        "§1", _ledd(INTRO_PROVEN), _p("§ 40 oppheves."), _p("§ 4-2 endres slik:"),
        _p("§ 41 oppheves."),
    )  # fmt: skip
    operations = _operations(body, SKIFTE)  # the sub-intro itself stays an unsupported sentence
    assert {op.act for op in operations} == {SKIFTE}
    assert [op.address.section for op in operations if op.kind != "unsupported"] == ["§40", "§41"]


# 6. Fully numbered citations keep their behaviour -------------------------------------------


@pytest.mark.parametrize("changes", [(), (STRAFFELOVEN,), ("lov/2005-05-20",)])
def test_a_fully_numbered_canonical_citation_binds_as_before(changes: tuple[str, ...]) -> None:
    body = _article("§1", _ledd(NUMBERED), _p("§ 40 oppheves."))
    assert _acts(body, *changes) == [("§40", STRAFFELOVEN)]


def test_a_fully_numbered_intro_slik_binds_as_before() -> None:
    intro = "Lov 20. mai 2005 nr. 28 om straff endres slik:"
    body = _article("§1", _p(intro), _p("§ 40 oppheves."))
    assert _acts(body) == [("§40", STRAFFELOVEN)]


# 7. Several acts and article boundaries stay isolated ---------------------------------------


def test_proven_unproven_and_numbered_blocks_in_one_article_stay_apart() -> None:
    body = _article(
        "§1", _ledd(INTRO_PROVEN), _p("§ 40 oppheves."), _ledd(SLIK), _p("§ 41 oppheves."),
        _ledd(NUMBERED), _p("§ 42 oppheves."),
    )  # fmt: skip
    assert _acts(body, SKIFTE, STRAFFELOVEN) == [
        ("§40", SKIFTE),
        ("§41", None),
        ("§42", STRAFFELOVEN),
    ]


def test_a_proven_date_only_block_ends_at_the_next_article() -> None:
    body = _article("§1", _ledd(INTRO_PROVEN), _p("§ 40 oppheves.")) + _article(
        "§2", _p("§ 41 oppheves.")
    )
    operations = _operations(body, SKIFTE)
    assert [(op.host_section, op.act) for op in operations] == [("§1", SKIFTE), ("§2", None)]


def test_two_proven_date_only_acts_in_two_articles_bind_their_own_act() -> None:
    body = _article("§1", _ledd(INTRO_PROVEN), _p("§ 40 oppheves.")) + _article(
        "§2", _p(SLIK), _p("§ 24 oppheves.")
    )
    assert _acts(body, SKIFTE, FORVALTNING) == [("§40", SKIFTE), ("§24", FORVALTNING)]


# 8. #856 stays fixed ------------------------------------------------------------------------


def test_856_militaer_straffelov_never_inherits_straffeloven_beside_date_only_intros() -> None:
    """lov/2015-06-19-65 § 4 (militær straffelov) after § 3, with an unproven date-only § 6."""
    intro_4 = (
        "Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i "
        "lov 22. mai 1902 nr. 13 militær straffelov:"
    )
    body = (
        _article("§3", _ledd(NUMBERED), _p("§ 40 oppheves."))
        + _article("§4", _ledd(intro_4), _p("§ 13 oppheves."))
        + _article("§6", _ledd(INTRO_UNLISTED), _p("§ 14 oppheves."))
    )
    assert _acts(body, STRAFFELOVEN, "lov/1902-05-22-13") == [
        ("§40", STRAFFELOVEN),
        ("§13", "lov/1902-05-22-13"),
        ("§14", None),
    ]
    assert _acts(body, STRAFFELOVEN) == [("§40", STRAFFELOVEN), ("§13", None), ("§14", None)]
