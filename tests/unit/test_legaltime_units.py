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


def test_units_describe_themselves_for_reports() -> None:
    assert Unit(section="§15-7", ledd=4).describe() == "§15-7 ledd 4"
    assert Unit(section="§3-2", heading=True).describe() == "§3-2 heading"
    assert Unit(chapter="kap9a", new=True).describe() == "kap9a"
    assert (
        Unit(section="§2", ledd=2, punktum=2, bokstav="a").describe()
        == "§2 ledd 2 punktum 2 bokstav a"
    )
