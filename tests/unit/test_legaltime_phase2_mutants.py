"""Phase 2 (#744): boundary tests for the mutants the first mutation run left alive."""

from datetime import date

import pytest

from lovspor.legaltime import commencement as cm
from lovspor.legaltime import replay, scopes
from lovspor.legaltime.archive import MemberRecord, _decision_names, _title_document
from lovspor.legaltime.document import Ledd, parse_document
from lovspor.legaltime.instructions import Address, Operation, classify
from lovspor.legaltime.replay import _Working
from lovspor.legaltime.text import section_sort_key
from tests.unit.test_legaltime_phase2_commencement import LAW, _by_part, _decision, _law
from tests.unit.test_legaltime_phase2_instructions import FIXTURES, _ops, _p, _state, _t

FULL = "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer"
DELT = "Delt ikraftsetting av lov 18. desember 2020 nr. 999 om endringer"
ITEM = '<ol><li data-name="{l}."><article class="listArticle"><article class="legalP">{t}</article></article></li></ol>'  # noqa: E501


# ---------------------------------------------------------------- roman numerals and part runs


@pytest.mark.parametrize(
    "numeral, value",
    [
        ("I", 1),
        ("II", 2),
        ("IV", 4),
        ("V", 5),
        ("IX", 9),
        ("X", 10),
        ("XIV", 14),
        ("XL", 40),
        ("XC", 90),
        ("C", 100),
    ],
)
def test_roman_numerals(numeral: str, value: int) -> None:
    assert scopes._roman(numeral) == value


def test_part_runs_and_lists() -> None:
    assert scopes._part_labels("I til IV") == ["I", "II", "III", "IV"]
    assert scopes._part_labels("IV til VI") == ["IV", "V", "VI"]
    assert scopes._part_labels("I og III") == ["I", "III"]
    assert scopes._part_labels("I, II og IV") == ["I", "II", "IV"]
    assert scopes._part_labels("III til I") == []
    assert scopes._part_labels("II") == ["II"]


# ---------------------------------------------------------------- scope sentences


def test_straks_in_a_comma_delt_sentence_and_in_a_remainder_is_the_resolution_s_day() -> None:
    own = date(2021, 3, 1)
    found = scopes._scopes_of("Endringsloven del I trer i kraft straks.", own)
    assert found is not None and found[0].when == own
    assert scopes._scopes_of("Endringsloven del I trer i kraft straks.", None) is None
    rest = scopes._scopes_of("De andre bestemmelsene trer i kraft straks.", own)
    assert rest is not None and rest[0].remainder and rest[0].when == own
    assert scopes._scopes_of("De andre bestemmelsene trer i kraft seinare.", own) is None


def test_part_scopes_with_and_without_listed_sections() -> None:
    with_secs = scopes._scopes_of("Romertall I § 1 og § 2 trer i kraft 1. mars 2020.", None)
    assert with_secs is not None and [(s.part, s.section) for s in with_secs] == [
        ("I", "§1"),
        ("I", "§2"),
    ]
    bare = scopes._scopes_of("Romertall I trer i kraft 1. mars 2020.", None)
    assert bare is not None and [(s.part, s.section) for s in bare] == [("I", None)]


@pytest.mark.parametrize(
    "rest, ok",
    [
        ("§ 1", True),
        ("§ 1 første ledd", False),
        ("§§ 1 og 5", False),
        ("§ 1 og § 5", False),
        ("§ 1 mv", False),
        ("", True),
    ],
)
def test_representable_amended_act_references(rest: str, ok: bool) -> None:
    sentence = f"Endringsloven del I, endringene i lov 14. juni 2019 nr. 99 {rest}, trer i kraft 1. juli 2021."  # noqa: E501
    assert (scopes._scopes_of(sentence.replace(" ,", ","), None) is not None) is ok


# ---------------------------------------------------------------- decisions


def test_a_full_decision_whose_body_names_two_dates_is_unreadable_with_that_reason() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    body = f"{FULL}.\nLoven trer i kraft 1. januar 2026.\nLoven gjelder fra 1. mars 2026."
    decision = _decision("forskrift/2025-12-12-1", FULL, "2026-01-01", body)
    assert {t.reason for t in _by_part(law, decision).values()} == {
        "full decision body names several dates"
    }
    two = _decision(
        "forskrift/2025-12-12-1",
        FULL,
        "2026-01-01",
        f"{FULL}.\nLoven trer i kraft 1. januar 2026. Loven trer i kraft 1. januar 2026.",
    )
    assert {t.effective for t in _by_part(law, two).values()} == {date(2026, 1, 1)}


def test_a_full_header_with_a_delt_body_block_is_partial() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    body = f"{DELT}.\nEndringsloven del I trer i kraft 1. juli 2021."
    decision = _decision("forskrift/2021-03-01-2", FULL, "2021-07-01", body)
    timings = _by_part(law, decision)
    assert timings[("I", "§1")].effective == date(2021, 7, 1)
    assert timings[("II", "§5")].status == "pending"


def test_a_full_decision_with_a_title_only_body_is_dated_by_its_header() -> None:
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    decision = _decision("forskrift/2021-03-01-2", FULL, "2021-07-01", f"{FULL}.")
    assert {t.effective for t in _by_part(law, decision).values()} == {date(2021, 7, 1)}


def test_own_date_needs_a_dated_refid_and_decisions_need_a_decision_title() -> None:
    assert cm._own_date(_decision("forskrift/x", FULL, "", "")) is None
    assert cm._own_date(_decision("forskrift/2021-03-01-2", FULL, "", "")) == date(2021, 3, 1)
    law = _law("Loven gjelder fra den tid Kongen bestemmer.")
    assert cm.is_decision_for(law, LAW) is False


def test_an_intro_or_instruction_inside_the_clause_is_skipped_even_when_dated() -> None:
    law = _law(
        "Loven trer i kraft 1. juli 2021. I lov 14. juni 2019 nr. 99 om prøving gjøres følgende endringer: § 5 skal lyde:"  # noqa: E501
    )
    assert {t.effective for t in _by_part(law).values()} == {date(2021, 7, 1)}


# ---------------------------------------------------------------- archive stand-ins


def _record(title: str, changes: tuple[str, ...]) -> MemberRecord:
    return MemberRecord(
        name="lti/2021/sf-1.xml", sha256="0" * 64, refid="forskrift/2021-03-01-2", title=title,
        date_in_force="2021-07-01", published_at=None, changes_to_documents=changes,
    )  # fmt: skip


def test_the_stand_in_carries_the_changed_documents_and_selection_uses_either_signal() -> None:
    record = _record(
        "Ikraftsetting av lov 15. mai 2008 nr. 35 om utlendinger", (LAW, "lov/2008-05-15-35")
    )
    assert _title_document(record).header.changes_to_documents == (LAW, "lov/2008-05-15-35")
    by_title = _record(FULL, ())
    index = type("Index", (), {"members": [by_title]})()
    assert _decision_names(index, {LAW}) == ["lti/2021/sf-1.xml"]  # type: ignore[arg-type]
    by_list = type("Index", (), {"members": [record]})()
    assert _decision_names(by_list, {LAW}) == ["lti/2021/sf-1.xml"]  # type: ignore[arg-type]
    neither = type(
        "Index", (), {"members": [_record("Ikraftsetting av lov 15. mai 2008 nr. 35", ())]}
    )()
    assert _decision_names(neither, {LAW}) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------- instructions


def test_numbered_ledd_pairs_that_are_not_adjacent_have_no_range() -> None:
    kind, _groups = classify("§ 10-4 (2) og (4) skal lyde:")
    assert kind == "replace_ledd"
    ops = _ops(_p("§ 10-4 (2) og (4) skal lyde:"), _t("x."))
    assert ops[0].address.ledd is None and ops[0].address.ledd_end is None


def test_a_degenerate_section_range_is_one_repeal() -> None:
    ops = _ops(_p("§§ 14 til 14 oppheves."))
    assert [(op.kind, op.address.section) for op in ops] == [("repeal_section", "§14")]


def test_section_sort_key_orders_capital_letters_inside_the_number() -> None:
    assert section_sort_key("§6A-4") == (6, 4, "A")
    assert section_sort_key("§6") < section_sort_key("§6A-1") < section_sort_key("§7")


# ---------------------------------------------------------------- list items


def _listed_working() -> _Working:
    working = _Working(parse_document((FIXTURES / "enacting.xml").read_bytes(), "x"))
    xml = (
        "Intro:"
        + ITEM.format(l="1", t="en").replace("</ol>", "")
        + '<li data-name="2."><article class="listArticle"><article class="legalP">to</article></article></li></ol>'  # noqa: E501
    )
    working.map["§1"].ledd[1] = Ledd(xml=xml, text="Intro: en to")
    return working


def _item_op(kind: str, section: str, ledd: int | None, label: str, payload: str = "ny") -> object:
    return Operation(
        id="x", kind=kind, act="lov/2019-06-14-99", address=Address(section=section, ledd=ledd, bokstav=label),  # type: ignore[arg-type]  # noqa: E501
        instruction="i", part="I", ledd=(Ledd(xml=payload, text=payload),),
    )  # fmt: skip


def test_items_in_the_second_ledd_are_edited_there_and_the_first_ledd_is_untouched() -> None:
    working = _listed_working()
    first = working.map["§1"].ledd[0].xml
    assert working.apply_insert_bokstav(_item_op("insert_bokstav", "§1", 2, "3", "tre")) is None  # type: ignore[arg-type]
    assert working.map["§1"].ledd[0].xml == first
    assert working.map["§1"].ledd[1].text == "Intro: en to tre"
    assert working.apply_repeal_bokstav(_item_op("repeal_bokstav", "§1", 2, "1")) is None  # type: ignore[arg-type]
    assert working.map["§1"].ledd[1].text == "Intro: to tre"
    assert working.map["§1"].ledd[0].xml == first


def test_items_without_a_ledd_on_a_two_ledd_provision_name_the_item_unit() -> None:
    working = _listed_working()
    assert (
        working.apply_insert_bokstav(_item_op("insert_bokstav", "§1", None, "3"))
        == "item without ledd on a provision with several ledd"
    )  # type: ignore[arg-type]
    assert (
        working.apply_repeal_bokstav(_item_op("repeal_bokstav", "§1", None, "1"))
        == "item without ledd on a provision with several ledd"
    )  # type: ignore[arg-type]
    assert (
        working.apply_repeal_bokstav(_item_op("repeal_bokstav", "§1", 2, "9"))
        == "bokstav 9 matched 0 list items"
    )  # type: ignore[arg-type]


def test_predecessor_labels() -> None:
    assert replay._previous_label("a") is None
    assert replay._previous_label("1") is None
    assert replay._previous_label("b") == "a"
    assert replay._previous_label("2") == "1"
    assert replay._previous_label("3") == "2"
    assert replay._previous_label("å") == "ø"
    assert replay._previous_label("æ") == "z"


def test_a_first_item_goes_first_in_the_only_list_and_a_later_one_after_its_anchor() -> None:
    one_list = "Intro:" + ITEM.format(l="b", t="bee")
    edited = replay._insert_item(one_list, "a", "ay")
    assert isinstance(edited, Ledd) and edited.text == "Intro: ay bee"
    assert edited.xml.startswith("Intro:<ol>") and "XXXX" not in edited.xml
    ul = one_list.replace("<ol>", '<ul class="x">').replace("</ol>", "</ul>")
    edited_ul = replay._insert_item(ul, "a", "ay")
    assert isinstance(edited_ul, Ledd) and edited_ul.text == "Intro: ay bee"
    two_lists = ITEM.format(l="a", t="a1") + ITEM.format(l="b", t="b1")
    after_b = replay._insert_item(two_lists, "c", "see")
    assert isinstance(after_b, Ledd) and after_b.text == "a1 b1 see"


def test_items_are_only_li_elements_and_entities_are_not_resolved() -> None:
    root = replay._ledd_root(
        '<article data-name="a."><article class="legalP">x</article></article>'
        + ITEM.format(l="b", t="y")
    )
    assert root is not None and [li.get("data-name") for li in replay._items(root, "a")] == []
    assert replay._insert_item("<b>unclosed", "a", "x") == "ledd markup does not parse"
    assert replay._remove_item("<b>unclosed", "a") == "ledd markup does not parse"
    entity = ITEM.format(l="a", t="&amp;lt;x")
    edited = replay._insert_item(entity, "b", "y")
    assert isinstance(edited, Ledd) and "&amp;lt;x" in edited.xml


def test_insert_an_item_by_number_two_after_one() -> None:
    state = _state(_p("§ 2 første ledd ny nr. 1 skal lyde:"), _t("første punkt"))
    assert [problem for _, problem in state.unresolved] == []


def test_item_edits_keep_the_space_between_inline_elements() -> None:
    xml = "Intro:" + ITEM.format(l="a", t="<b>one</b> <i>two</i>")
    edited = replay._insert_item(xml, "b", "<b>three</b> <i>four</i>")
    assert isinstance(edited, Ledd) and edited.text == "Intro: one two three four"
    root = replay._ledd_root(
        '<ol> <li data-name="a."><article class="legalP">x</article></li> </ol>'
    )
    assert root is not None and root.find(".//ol").text == " "


def test_a_consumed_payload_is_not_read_again_as_a_substitution_item() -> None:
    """With a substitution header open, a loose ledd naming an act and a section would become a
    substitution item; an instruction's payload must never be offered to that path."""
    ops = _ops(
        _p("I følgende bestemmelser skal ordet «fylkesmannen» endres til «statsforvalteren»:"),
        _p("§ 3 første ledd skal lyde:"),
        _t("I lov 14. juni 2019 nr. 99 om prøving § 2 gjelder."),
    )
    assert [op.kind for op in ops] == ["replace_ledd"]
