"""Behavioral boundaries for the supplied 72-survivor artifact."""

from datetime import date

import pytest
from lxml import etree

from lovspor.legaltime import commencement, document, instructions, replay, scopes, units
from tests.unit.test_legaltime_mutants import _op
from tests.unit.test_legaltime_remediation import xml_doc


def test_statement_lists_and_plain_nodes():
    ledd = document.Ledd(xml="Tiltak:<ol><li>A.</li><li>B.</li></ol>", text="Tiltak: A. B.")
    node = document.Node(kind="ledd", css_class="legalP", text=ledd.text, ledd=ledd)
    assert commencement._statements(node) == ["Tiltak: A.", "Tiltak: B."]
    assert commencement._statements(
        document.Node(kind="paragraph", css_class="", text="A. B.")
    ) == ["A.", "B."]


@pytest.mark.parametrize(
    "sentence,expected",
    [("Ingen instruks.", None), ("ukjent del skal lyde:", None), ("annen del skal lyde:", "del2")],
)
def test_structure_part_names(sentence, expected):
    assert instructions._structure_part(sentence) == expected


def test_enclosing_sections_only_and_nearest():
    root = etree.fromstring(
        '<section data-name="del1"><section><div data-name="kap9">'
        '<section data-name="kap2"><article/></section></div></section></section>'
    )
    assert document._enclosing(root.find(".//article")) == ("kap2", "del1")
    assert document._enclosing(etree.fromstring("<section><article/></section>")[0]) == (None, None)
    assert document._enclosing(etree.fromstring('<div data-name="kap9"><article/></div>')[0]) == (
        None,
        None,
    )


@pytest.mark.parametrize(
    "xml,expected",
    [
        ("<ol><li>A.</li></ol>", ["", "A."]),
        ("Tiltak:<ol><li>A.</li><li>B.</li></ol>", ["Tiltak: A.", "Tiltak: B."]),
        ("<ul><li>A<!--editorial--> B.</li></ul>", ["", "A B."]),
    ],
)
def test_list_statements_markup(xml, expected):
    assert document.list_statements(document.Ledd(xml=xml, text="")) == expected


def test_unit_description_and_token_boundaries():
    assert units.Unit().describe() == "?"
    assert units.parse_units("1") is None
    assert units.parse_units("") is None
    assert units.parse_units("§§1, 2a") == (units.Unit(section="§1"), units.Unit(section="§2a"))
    assert units.parse_units("§§ 1, kapittel 2") == (
        units.Unit(section="§1"),
        units.Unit(chapter="kap2"),
    )
    assert units._tokens("§§   1") == ["§ 1"]


@pytest.mark.parametrize(
    "number,expected", [(1, "disjoint"), (2, "spans"), (4, "spans"), (5, "disjoint")]
)
def test_range_endpoints(number, expected):
    op = _op(
        "replace_punktum", instructions.Address(section="§1", ledd=1, punktum=2, punktum_end=4)
    )
    assert units.relation(units.Unit(section="§1", ledd=1, punktum=number), op) == expected


@pytest.mark.parametrize(
    "letter,address_letter,expected",
    [(None, "b", "covers"), ("a", "a", "covers"), ("a", "b", "disjoint")],
)
def test_item_relation(letter, address_letter, expected):
    op = _op("replace_bokstav", instructions.Address(section="§1", ledd=1, bokstav=address_letter))
    assert units.relation(units.Unit(section="§1", ledd=1, bokstav=letter), op) == expected


def test_heading_repeal_spans():
    op = _op("repeal_section", instructions.Address(section="§1"))
    assert units.relation(units.Unit(section="§1", heading=True), op) == "spans"


def test_chapter_lookup_without_section():
    op = _op("replace_section", instructions.Address())
    assert units.relation(units.Unit(chapter="kap2"), op, {"": "kap2"}) == "covers"


@pytest.mark.parametrize(
    "unit,address,mapping,expected",
    [
        (units.Unit(chapter="kap2"), instructions.Address(section="§1"), {"§1": "kap2"}, "covers"),
        (units.Unit(section="§1", ledd=1), instructions.Address(section="§1"), {}, "spans"),
        (units.Unit(chapter="kap2"), instructions.Address(section="§1"), {}, "unknown"),
        (units.Unit(section="§2"), instructions.Address(section="§1"), {}, "disjoint"),
    ],
)
def test_scope_narrowing(unit, address, mapping, expected):
    scope = scopes.Scope(part="I", units=(unit,), when=date(2022, 1, 1), quote="test")
    assert scope.reach(_op("replace_section", address), mapping) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("(navn (kort))§ 1", "§ 1"), ("(navn)", ""), ("(§ 3)§ 1", None), ("(navn", None)],
)
def test_parenthetical_balance(text, expected):
    assert scopes._strip_parenthetical(text) == expected


@pytest.mark.parametrize("text", ["lov uten paragraf", "ukjent § 1", "§ ukjent"])
def test_unreadable_item_units(text):
    assert scopes._item_units(text) is None


@pytest.mark.parametrize(
    "sentence",
    [
        "Loven trer i kraft straks med unntak av § 2.",
        "Loven § 1 trer i kraft straks.",
        "Loven kapittel 2 trer i kraft straks.",
    ],
)
def test_act_scope_resolution_day(sentence):
    found = scopes._act_scopes(sentence, date(2022, 1, 1))
    assert found and found[0].when == date(2022, 1, 1)


@pytest.mark.parametrize(
    "sentence", ["Loven § ukjent trer i kraft 1. juli 2022.", "Loven § 1 trer i kraft senere."]
)
def test_act_scope_refuses_unknown_units_or_day(sentence):
    assert scopes._act_scopes(sentence, None) == []


def test_named_act_and_bare_unit_scope():
    law = xml_doc("")
    assert scopes._named_in(law, scopes.Scope(whole=True, when=date(2022, 1, 1), quote=""))
    assert not scopes._named_in(
        law, scopes.Scope(units=(units.Unit(section="§1"),), when=date(2022, 1, 1), quote="")
    )
    assert not scopes._named_in(law, scopes.Scope(remainder=True, when=date(2022, 1, 1), quote=""))


def test_replay_location_spacing():
    assert replay._locations("§ 6 A-7 andre ledd og § 2") == [
        ("§6A-7", frozenset({2})),
        ("§2", frozenset()),
    ]


def test_whole_scope_reaches_an_operation():
    scope = scopes.Scope(whole=True, when=date(2022, 1, 1), quote="whole act")
    assert scope.reach(_op("replace_section", instructions.Address(section="§1"))) == "covers"
