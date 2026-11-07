"""Phase 2 (#744): the paths the independent review of the phase-2 diff found, each closed."""

from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime import commencement as cm
from lovspor.legaltime import replay, scopes
from lovspor.legaltime.document import Ledd, parse_document
from lovspor.legaltime.instructions import Address, classify, interpret
from lovspor.legaltime.replay import _Working
from tests.unit.test_legaltime_phase2_commencement import _by_part, _decision, _law
from tests.unit.test_legaltime_phase2_instructions import _amending, _ops, _p, _state, _t

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"
FULL = "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer"
DELT = "Delt ikraftsetting av lov 18. desember 2020 nr. 999 om endringer"


def _ledd(state, key: str) -> list[str]:
    return [ledd.text for p in state.provisions if p.key == key for ledd in p.ledd]


# 1. nytt siste punktum inserts after the last sentence
def test_a_new_last_punktum_goes_after_the_last_sentence() -> None:
    state = _state(_p("§ 1 første ledd nytt siste punktum skal lyde:"), _t("Tredje."))
    assert state.unresolved == ()
    assert (
        _ledd(state, "§1")[0]
        == "Loven gjelder prøving av fylkesmannen. Den gjelder også retting. Tredje."
    )


# 2. a § inside a parenthesis is not an act name
@pytest.mark.parametrize(
    "sentence",
    [
        "Endringsloven del I (§ 5) trer i kraft 1. mars 2020.",
        "Romertall I (§§ 1 til 4) trer i kraft 1. mars 2020.",
        "Følgende trer i kraft 1. mars 2020: Endringsloven del I (§ 3).",
    ],
)
def test_a_section_inside_a_parenthesis_makes_the_scope_foreign(sentence: str) -> None:
    assert scopes._scopes_of(sentence, None) is None


def test_an_act_name_inside_a_parenthesis_is_still_read() -> None:
    found = scopes._scopes_of(
        "Endringsloven del I (lov 14. juni 2019 nr. 99 om prøving) trer i kraft 1. mars 2020.", None
    )
    assert found is not None and [s.part for s in found] == ["I"]


# 3. a single titled block citing another law is not ours
def test_a_single_block_citing_another_law_is_not_this_law_s_decision() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    body = "Ikraftsetting av lov 5. mai 2010 nr. 7 om noe annet.\nLoven trer i kraft 1. mars 2021."
    decision = _decision("forskrift/2021-02-01-1", FULL, "", body)
    timings = _by_part(law, decision)
    assert {t.reason for t in timings.values()} == {
        "decision body has no commencement sentence for this law"
    }


# 4. the header's Delt is not forgotten when the body block is plain
def test_a_delt_header_title_keeps_the_decision_partial() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    body = "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer.\nLoven trer i kraft 1. mars 2021."  # noqa: E501
    decision = _decision("forskrift/2021-02-01-1", DELT, "2021-03-01", body)
    timings = _by_part(law, decision)
    assert {t.reason for t in timings.values()} == {"partial decision outside the grammar"}


# 5. a staggered or empty header on a single-block decision is still outside the grammar
@pytest.mark.parametrize("header", ["2021-03-01, 2021-07-01", "snart"])
def test_a_single_block_decision_with_an_unreadable_header_is_unreadable(header: str) -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    decision = _decision(
        "forskrift/2021-02-01-1", FULL, header, f"{FULL}.\nLoven trer i kraft 1. mars 2021."
    )
    assert {t.reason for t in _by_part(law, decision).values()} == {
        "dateInForce outside the grammar"
    }


def test_an_empty_header_lets_the_body_speak() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    decision = _decision(
        "forskrift/2021-02-01-1", FULL, "", f"{FULL}.\nLoven trer i kraft 1. mars 2021."
    )
    assert {t.effective for t in _by_part(law, decision).values()} == {date(2021, 3, 1)}


# 6. straks beside an explicit date is a contradiction
def test_straks_beside_an_explicit_date_is_unresolved_everywhere() -> None:
    assert scopes._when("straks med virkning fra 1. januar 2020", date(2021, 2, 1)) is None
    law = _law("Loven trer i kraft straks med virkning fra 1. januar 2020.", "2020-12-18")
    assert {t.status for t in _by_part(law).values()} == {"unresolved"}
    decision = _decision(
        "forskrift/2021-02-01-1",
        FULL,
        "2021-02-01",
        f"{FULL}.\nLoven trer i kraft straks med virkning fra 1. januar 2020.",
    )
    delegated = _law("Loven gjelder fra den tid Kongen bestemmer.")
    assert {t.status for t in _by_part(delegated, decision).values()} == {"unresolved"}


# 7. 6A keys everywhere a section is matched
def test_lettered_section_keys_in_substitution_locations_and_listings() -> None:
    assert replay._locations("§ 6A-4 første ledd og § 7") == [
        ("§6A-4", frozenset({1})),
        ("§7", frozenset()),
    ]
    assert scopes._own_sections("§ 6A-4") == frozenset({"§6A-4"})
    assert cm._exception_scope("Ny § 6A-4 trer likevel i kraft straks.") == ("section", "§6A-4")


# 8 + 9. delegations are statements; transitional "fastsetter" sentences are not
@pytest.mark.parametrize(
    "sentence, informational",
    [
        ("Departementet fastsetter nærmere overgangsregler.", True),
        ("Kongen fastsetter overgangsregler.", True),
        ("Departementet gir forskrift om når loven trer i kraft.", False),
        ("Kongen bestemmer når loven trer i kraft.", False),
        ("Loven gjelder fra den tid Kongen bestemmer.", False),
    ],
)
def test_delegation_versus_transitional_fastsetter(sentence: str, informational: bool) -> None:
    assert cm._informational(sentence) is informational


# 10. item insertion refuses ambiguity
def test_item_insertion_refuses_several_lists_without_an_anchor_and_duplicate_anchors() -> None:
    two_lists = (
        'A<ol><li data-name="a."><article class="listArticle"><article class="legalP">a1</article></article></li></ol>'  # noqa: E501
        'B<ol><li data-name="a."><article class="listArticle"><article class="legalP">a2</article></article></li></ol>'  # noqa: E501
    )
    assert replay._insert_item(two_lists, "b", "x") == "item a, the one before b, is in 2 places"
    assert replay._insert_item(two_lists, "1", "x") == "the ledd carries 2 lists"


# 11. a remainder cannot carry a block whose explicit scopes name nothing in the law
def test_a_remainder_does_not_rescue_a_block_naming_nothing_in_the_law() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    body = f"{DELT}.\nParagrafane 1 til 10 gjeld frå 1. mars 2021.\nDe andre bestemmelsene trer i kraft 1. juli 2021."  # noqa: E501
    decision = _decision("forskrift/2021-02-01-1", DELT, "2021-03-01, 2021-07-01", body)
    assert {t.reason for t in _by_part(law, decision).values()} == {
        "partial decision names no part or section of the law"
    }


# 12. a chapter provision without a label is unsupported, not a crash
def test_a_chapter_provision_without_a_label_is_unsupported() -> None:
    ops = _ops(
        _p("Nytt kapittel 6A skal lyde:"),
        '<article class="futureLegalArticle"><article class="legalP">En.</article></article>',
    )
    assert [op.kind for op in ops] == ["unsupported"]


# 14. the report quotes the member, not a synthesized sentence
def test_expanded_and_inline_operations_quote_the_announced_sentence() -> None:
    ops = _ops(_p("§§ 2 og 4 oppheves."))
    assert [op.instruction for op in ops] == ["§§ 2 og 4 oppheves.", "§§ 2 og 4 oppheves."]
    assert [op.address.section for op in ops] == ["§2", "§4"]
    inline = interpret(
        _amending(
            intro="3. I lov 21. februar 1930 om skifte oppheves § 12 annet ledd annet punktum."
        )
    ).operations
    assert (
        inline[0].instruction
        == "3. I lov 21. februar 1930 om skifte oppheves § 12 annet ledd annet punktum."
    )
    assert inline[0].address == Address(section="§12", ledd=2, punktum=2)


# 15. a renumbering needs a ledd or punktum before "blir"
def test_tidligere_prose_is_not_a_renumbering() -> None:
    assert classify("Tidligere vedtak blir stående.")[0] == "unsupported"
    assert classify("Tidlegare tredje ledd blir nytt fjerde ledd.")[0] == "renumber"


# 16. a Delt sentence naming no section is still a whole-part scope
def test_a_delt_sentence_without_a_named_section_scopes_the_whole_part() -> None:
    found = scopes._scopes_of(
        "Endringsloven del II, endringene i lov 1. januar 2000 nr. 1, trer i kraft 1. mars 2020.",
        None,
    )
    assert found is not None and [(s.part, s.section) for s in found] == [("II", None)]


# 17. a block title needs an act citation
def test_a_decision_word_without_a_citation_opens_no_block() -> None:
    decision = _decision(
        "forskrift/2021-02-01-1",
        FULL,
        "2021-03-01",
        "Ikraftsetting av loven skjer samlet.\nLoven trer i kraft 1. mars 2021.",
    )
    assert [title for title, _ in cm._blocks(decision)] == [None]


# 18. an inserted item keeps the payload's markup
def test_an_inserted_item_keeps_its_markup() -> None:
    working = _Working(parse_document((FIXTURES / "enacting.xml").read_bytes(), "x"))
    edited = replay._insert_item(working.map["§2"].ledd[0].xml, "c", "bokstav <i>c</i>")
    assert isinstance(edited, Ledd) and "<i>c</i>" in edited.xml and "bokstav c" in edited.text
    assert (
        replay._insert_item(working.map["§2"].ledd[0].xml, "c", "<b>broken")
        == "item payload markup does not parse"
    )


# 19. boundaries
def test_reversed_section_range_and_sections_under_several_parts_stay_foreign() -> None:
    assert [op.kind for op in _ops(_p("§§ 16 til 14 oppheves."))] == ["unsupported"]
    assert scopes._scopes_of("Romertall I og II § 5 trer i kraft 1. mars 2020.", None) is None


# 20. a chapter with a stray ledd outside any provision is unsupported
def test_a_chapter_with_text_outside_its_provisions_is_unsupported() -> None:
    ops = _ops(
        _p("Nytt kapittel 6A skal lyde:"),
        _t("Stray."),
        '<article class="futureLegalArticle" data-name="§6A-1"><article class="legalP">En.</article></article>',  # noqa: E501
    )
    assert [op.kind for op in ops] == ["unsupported"]
