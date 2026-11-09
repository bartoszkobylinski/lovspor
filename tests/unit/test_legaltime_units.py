"""Phase 3 (#744): the legal units a commencement instrument names, and what they reach.

Every listing here is a shape seen in the permitted archives' commencement
decisions (barnevernsloven, tvisteloven, straffeloven, 2022 to 2026); the
operations are synthetic.
"""

import pytest

from lovspor.legaltime.document import Provision
from lovspor.legaltime.instructions import Address, Operation
from lovspor.legaltime.units import Unit, parse_units, relation


def _op(kind: str = "replace_ledd", **address: object) -> Operation:
    return Operation(
        id="x", kind=kind, act="lov/2021-06-18-97", address=Address(**address),  # type: ignore[arg-type]
        instruction="", part="I",
    )  # fmt: skip


@pytest.mark.parametrize(
    "listing, expected",
    [
        ("§ 2-6 og § 15-7 fjerde ledd", [Unit(section="§2-6"), Unit(section="§15-7", ledd=4)]),
        (
            "§ 12-11 annet ledd, § 13-5 og § 13-6 første ledd",
            [Unit(section="§12-11", ledd=2), Unit(section="§13-5"), Unit(section="§13-6", ledd=1)],
        ),
        ("ny § 2-6", [Unit(section="§2-6", new=True)]),
        ("§ 3-8 nytt tredje ledd", [Unit(section="§3-8", ledd=3, new=True)]),
        ("§ 20-5 tredje ledd fjerde punktum", [Unit(section="§20-5", ledd=3, punktum=4)]),
        ("§ 16-3 tredje ledd bokstav c", [Unit(section="§16-3", ledd=3, bokstav="c")]),
        ("§ 3-2 paragrafoverskriften", [Unit(section="§3-2", heading=True)]),
        ("§ 3-2 første punktum", [Unit(section="§3-2", punktum=1)]),
        ("nytt kapittel 9 A", [Unit(chapter="kap9a", new=True)]),
        ("kapittel 16", [Unit(chapter="kap16")]),
        (
            "§ 1 annet ledd og overskriften til kapittel 3",
            [Unit(section="§1", ledd=2), Unit(chapter="kap3", heading=True)],
        ),
        (
            "§§ 7a tredje ledd og 23a andre ledd",
            [Unit(section="§7a", ledd=3), Unit(section="§23a", ledd=2)],
        ),
        ("§§ 103b og 104a", [Unit(section="§103b"), Unit(section="§104a")]),
        (
            "§ 2 annet ledd annet punktum bokstav a",
            [Unit(section="§2", ledd=2, punktum=2, bokstav="a")],
        ),
        ("§ 37 første ledd nr. 3", [Unit(section="§37", ledd=1, bokstav="3")]),
    ],
)
def test_unit_listings_are_read_literally(listing: str, expected: list[Unit]) -> None:
    assert parse_units(listing) == tuple(expected)


@pytest.mark.parametrize(
    "listing",
    [
        "lovendringene om utlegg som i saker om utlegg kommer gradvis til anvendelse",
        "§ 5 første og annet ledd",  # two ledd in one token: not one unit
        "§ 6 nr. 2 om endring i lov 8. april 1981 nr. 7 om barn og foreldre § 44",
        "",
        "§ 3-2 og",
    ],
)
def test_anything_outside_the_unit_grammar_is_refused_whole(listing: str) -> None:
    assert parse_units(listing) is None


def test_a_section_unit_covers_every_operation_inside_the_section() -> None:
    unit = Unit(section="§13-5")
    assert relation(unit, _op(section="§13-5", ledd=1)) == "covers"
    assert relation(unit, _op("replace_section", section="§13-5")) == "covers"
    assert relation(unit, _op("replace_heading", section="§13-5")) == "covers"
    assert relation(unit, _op(section="§13-6", ledd=1)) == "disjoint"


def test_a_ledd_unit_covers_within_spans_above_and_is_disjoint_beside() -> None:
    unit = Unit(section="§15-7", ledd=4)
    assert relation(unit, _op(section="§15-7", ledd=4)) == "covers"
    assert relation(unit, _op("replace_punktum", section="§15-7", ledd=4, punktum=2)) == "covers"
    assert relation(unit, _op("replace_section", section="§15-7")) == "spans"
    assert relation(unit, _op(section="§15-7", ledd=3, ledd_end=5)) == "spans"
    assert relation(unit, _op(section="§15-7", ledd=3)) == "disjoint"
    assert relation(unit, _op("replace_heading", section="§15-7")) == "disjoint"


def test_a_punktum_unit_without_a_ledd_cannot_place_a_ledd_addressed_operation() -> None:
    unit = Unit(section="§3-2", punktum=1)
    assert relation(unit, _op("replace_punktum", section="§3-2", punktum=1)) == "covers"
    assert relation(unit, _op("replace_punktum", section="§3-2", ledd=1, punktum=1)) == "unknown"
    assert relation(unit, _op("replace_punktum", section="§3-2", punktum=2)) == "disjoint"
    assert relation(unit, _op("replace_section", section="§3-2")) == "spans"


def test_a_heading_unit_reaches_the_heading_and_is_spanned_by_a_section_rewrite() -> None:
    unit = Unit(section="§3-2", heading=True)
    assert relation(unit, _op("replace_heading", section="§3-2")) == "covers"
    assert relation(unit, _op("replace_section", section="§3-2")) == "spans"
    assert relation(unit, _op(section="§3-2", ledd=1)) == "disjoint"


def test_a_chapter_unit_reaches_insertions_stamped_with_it_and_nothing_it_cannot_place() -> None:
    placed = Provision(key="§9A-1", label="§ 9A-1", heading="", ledd=(), chapter="kap9a")
    insert = _op("insert_section", section="§9A-1").model_copy(update={"new_provision": placed})
    other = _op("insert_section", section="§9-1").model_copy(
        update={"new_provision": placed.model_copy(update={"key": "§9-1", "chapter": "kap9"})}
    )
    unit = Unit(chapter="kap9a")
    assert relation(unit, insert) == "covers"
    assert relation(unit, other) == "disjoint"
    assert relation(unit, _op(section="§9A-1", ledd=1)) == "unknown"
    assert relation(Unit(chapter="kap3", heading=True), insert) == "disjoint"
    plain = _op("insert_section", section="§5-12").model_copy(
        update={"new_provision": placed.model_copy(update={"key": "§5-12", "chapter": None})}
    )
    assert relation(Unit(chapter="kap9a", new=True), plain) == "disjoint"  # not the new chapter
    assert relation(unit, plain) == "unknown"  # an existing chapter: only the act can place it
    chapters = {"§9A-1": "kap9a", "§3-1": "kap3"}
    assert relation(unit, _op(section="§9A-1", ledd=1), chapters) == "covers"
    assert relation(unit, _op(section="§3-1", ledd=1), chapters) == "disjoint"
    assert relation(unit, _op(section="§12-1", ledd=1), chapters) == "unknown"


def test_units_describe_themselves_for_reports() -> None:
    assert Unit(section="§15-7", ledd=4).describe() == "§15-7 ledd 4"
    assert Unit(section="§3-2", heading=True).describe() == "§3-2 heading"
    assert Unit(chapter="kap9a", new=True).describe() == "kap9a"
    assert (
        Unit(section="§2", ledd=2, punktum=2, bokstav="a").describe()
        == "§2 ledd 2 punktum 2 bokstav a"
    )


def test_an_item_unit_covers_its_item_is_disjoint_beside_and_spanned_above() -> None:
    unit = Unit(section="§16-3", ledd=3, bokstav="c")
    assert relation(unit, _op("replace_bokstav", section="§16-3", ledd=3, bokstav="c")) == "covers"
    assert (
        relation(unit, _op("replace_bokstav", section="§16-3", ledd=3, bokstav="d")) == "disjoint"
    )
    assert relation(unit, _op("replace_ledd", section="§16-3", ledd=3)) == "spans"
    assert relation(unit, _op("replace_ledd", section="§16-3", ledd=2)) == "disjoint"
    numbered = Unit(section="§37", ledd=1, bokstav="3")
    assert (
        relation(numbered, _op("replace_bokstav", section="§37", ledd=1, bokstav="3")) == "covers"
    )
    assert (
        relation(numbered, _op("replace_bokstav", section="§37", ledd=1, bokstav="4")) == "disjoint"
    )


@pytest.mark.parametrize("bad", ["ukjent enhet", "§ 5 første og annet ledd", ""])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_one_unreadable_unit_refuses_even_a_partly_readable_listing(
    bad: str, position: int
) -> None:
    """units.py's contract refuses the whole listing, never just its unreadable token."""
    tokens = ["§ 1", "kapittel 2"]
    tokens.insert(position, bad)
    assert parse_units(", ".join(tokens)) is None


@pytest.mark.parametrize(
    "ledd, punktum, expected",
    [
        (2, 1, "disjoint"),
        (2, 2, "spans"),
        (2, 4, "spans"),
        (2, 5, "disjoint"),
        (1, 3, "disjoint"),
        (3, 3, "disjoint"),
    ],
)
def test_punktum_range_is_compared_only_inside_the_same_ledd(
    ledd: int, punktum: int, expected: str
) -> None:
    operation = _op("replace_punktum", section="§1", ledd=2, punktum=2, punktum_end=4)
    assert relation(Unit(section="§1", ledd=ledd, punktum=punktum), operation) == expected


def test_inserted_chapter_placement_takes_precedence_over_existing_section_map() -> None:
    """A stamped insertion is placed by its payload, as units.py documents."""
    provision = Provision(key="§1", label="§ 1", heading="", ledd=(), chapter="kap9")
    operation = _op("insert_section", section="§1").model_copy(update={"new_provision": provision})
    chapters = {"§1": "kap1"}
    assert relation(Unit(chapter="kap9"), operation, chapters) == "covers"
    assert relation(Unit(chapter="kap1"), operation, chapters) == "disjoint"
    assert chapters == {"§1": "kap1"}


@pytest.mark.parametrize("letter_spacing", ["A", " A"])
def test_unit_listings_normalise_chapter_letter_spacing(letter_spacing: str) -> None:
    """The new section grammar accepts the spaced chapter letter in every listed unit."""
    assert parse_units(f"§§ 9{letter_spacing}-1 andre ledd, 9{letter_spacing}-2 og § 10") == (
        Unit(section="§9A-1", ledd=2),
        Unit(section="§9A-2"),
        Unit(section="§10"),
    )


@pytest.mark.parametrize(
    "ledd, expected",
    [(1, "disjoint"), (2, "spans"), (3, "spans"), (4, "spans"), (5, "disjoint")],
)
def test_ledd_range_includes_both_endpoints_and_contains_finer_units(
    ledd: int, expected: str
) -> None:
    """A rewrite of several ledd spans each contained unit, including its finer units."""
    operation = _op("replace_ledd", section="§1", ledd=2, ledd_end=4)
    assert relation(Unit(section="§1", ledd=ledd), operation) == expected
    assert relation(Unit(section="§1", ledd=ledd, punktum=1, bokstav="a"), operation) == expected


@pytest.mark.parametrize("listing", ["§ 1 bokstav a", "§ 1 nr. 3", "§ 1 første ledd bokstav a"])
def test_a_heading_replacement_is_disjoint_from_every_body_unit(listing: str) -> None:
    """A heading rewrite touches no ledd, punktum, bokstav or nummer of the section: a body unit
    is disjoint from it, however the unit is addressed, and only the bare section or the heading
    unit covers it."""
    (unit,) = parse_units(listing) or ()
    assert unit.ledd is not None or unit.bokstav is not None
    assert relation(unit, _op("replace_heading", section="§1")) == "disjoint"
    assert relation(Unit(section="§1"), _op("replace_heading", section="§1")) == "covers"
    assert relation(Unit(section="§1", heading=True), _op("replace_heading", section="§1")) == (
        "covers"
    )
