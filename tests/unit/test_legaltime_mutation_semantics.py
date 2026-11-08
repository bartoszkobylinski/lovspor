"""Observable boundaries for the eight-survivor remediation artifact."""

from datetime import date
from pathlib import Path

import pytest
from lxml import etree

from lovspor.legaltime.document import _enclosing, parse_document
from lovspor.legaltime.instructions import Address, Operation
from lovspor.legaltime.scopes import Scope, _named_in, _strip_parenthetical
from lovspor.legaltime.units import Unit, parse_units, relation


@pytest.mark.parametrize("attribute", ["", ' data-name=""'])
def test_unnamed_sections_preserve_nearest_named_ancestors(attribute: str) -> None:
    root = etree.fromstring(
        f'<section data-name="del2"><section data-name="kap3">'
        f"<section{attribute}><article/></section></section></section>"
    )
    assert _enclosing(root.find(".//article")) == ("kap3", "del2")
    assert _enclosing(etree.fromstring(f"<section{attribute}><article/></section>")[0]) == (
        None,
        None,
    )


@pytest.mark.parametrize(
    "listing, expected",
    [
        ("1 og 2", None),
        ("§ 1 og 2", None),
        ("§§ 1 og 2", (Unit(section="§1"), Unit(section="§2"))),
        ("", None),
        ("  ", None),
        (",", None),
        ("§ 1,", None),
        ("§ 1", (Unit(section="§1"),)),
    ],
)
def test_unit_listing_carry_and_empty_boundaries(
    listing: str, expected: tuple[Unit, ...] | None
) -> None:
    assert parse_units(listing) == expected


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("replace_heading", "covers"),
        ("replace_section", "spans"),
        ("repeal_section", "spans"),
        ("replace_ledd", "disjoint"),
    ],
)
def test_heading_relation_depends_on_operation_kind(kind: str, expected: str) -> None:
    operation = Operation(
        id="heading", kind=kind, act="lov/2020-01-01-1",  # type: ignore[arg-type]
        address=Address(section="§1"), instruction="", part="I",
    )  # fmt: skip
    assert relation(Unit(section="§1", heading=True), operation) == expected


@pytest.mark.parametrize("item", [None, "a", "b", "3"])
def test_item_relation_distinguishes_equal_and_unequal_items(item: str | None) -> None:
    operation = Operation(
        id="item", kind="replace_bokstav", act="lov/2020-01-01-1",
        address=Address(section="§1", ledd=1, bokstav=item), instruction="", part="I",
    )  # fmt: skip
    assert relation(Unit(section="§1", ledd=1, bokstav="a"), operation) == (
        "covers" if item == "a" else "disjoint"
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        ("(Navn (kortnavn)) § 1", "§ 1"),
        ("(Navn) § 1", "§ 1"),
        ("(Navn", None),
        ("(Navn (kortnavn)", None),
        ("(§ 3) § 1", None),
        ("§ 1", "§ 1"),
    ],
)
def test_parenthetical_names_end_at_first_balanced_prefix(text: str, expected: str | None) -> None:
    assert _strip_parenthetical(text) == expected


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"remainder": True}, False),
        ({}, False),
        ({"units": (Unit(section="§1"),)}, False),
        ({"part": "KAPITTEL 1. INNLEDNING"}, True),
        ({"part": "MISSING"}, False),
        ({"whole": True}, True),
        ({"own_sections": frozenset({"§1"})}, True),
        ({"own_sections": frozenset({"§999"})}, False),
    ],
)
def test_only_scopes_naming_law_structure_are_named_in_it(
    fields: dict[str, object], expected: bool
) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures/legaltime/enacting.xml"
    law = parse_document(fixture.read_bytes(), "enacting.xml")
    scope = Scope(when=date(2026, 1, 1), quote="test", **fields)  # type: ignore[arg-type]
    assert _named_in(law, scope) is expected
