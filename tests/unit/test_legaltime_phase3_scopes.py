"""Phase 3 (#744): the scope sentences of commencement decisions with units and exceptions.

Every sentence is a shape read from a decision in the permitted archives
(barnevernsloven FOR-2022-10-14-1739, FOR-2022-03-25-465, FOR-2023-06-16-928,
FOR-2023-05-11-683, FOR-2025-12-19-2711, FOR-2026-01-23-68; straffeloven
FOR-2008-03-07-225; innkrevingsloven FOR-2025-06-10-967).
"""

from datetime import date

import pytest

from lovspor.legaltime.document import Ledd, list_statements
from lovspor.legaltime.instructions import Address, Operation
from lovspor.legaltime.scopes import Scope, _scopes_for, _scopes_of
from lovspor.legaltime.units import Unit

OWN = date(2023, 6, 16)


def _one(sentence: str) -> Scope:
    found = _scopes_of(sentence, OWN)
    assert found is not None and len(found) == 1, found
    return found[0]


def _op(part: str, kind: str = "replace_ledd", **address: object) -> Operation:
    return Operation(
        id=f"{part}:{address}", kind=kind, act="lov/2021-06-18-97",  # type: ignore[arg-type]
        address=Address(**address), instruction="", part=part,  # type: ignore[arg-type]
    )  # fmt: skip


def test_an_act_wide_date_with_exceptions_excludes_exactly_the_units_named() -> None:
    scope = _one("Loven trer i kraft 1. januar 2023, med unntak av § 2-6 og § 15-7 fjerde ledd.")
    assert scope.whole and scope.when == date(2023, 1, 1)
    assert scope.excluded == (Unit(section="§2-6"), Unit(section="§15-7", ledd=4))


def test_a_unit_of_the_act_dated_on_its_own() -> None:
    scope = _one("Loven § 15-7 fjerde ledd trer i kraft 1. juli 2023.")
    assert scope.units == (Unit(section="§15-7", ledd=4),)
    assert scope.when == date(2023, 7, 1)
    assert not scope.whole and scope.part is None


def test_a_chapter_under_a_part_of_the_enactment() -> None:
    scope = _one("Lovvedtaket del I kapittel 16 trer i kraft straks.")
    assert (scope.part, scope.units, scope.when) == ("I", (Unit(chapter="kap16"),), OWN)


@pytest.mark.parametrize(
    "sentence, part, excluded, units",
    [
        (
            "Følgende trer i kraft straks: Endringsloven del I (barnevernsloven) med unntak av "
            "§ 12-11 annet ledd, § 13-5 og § 13-6 første ledd.",
            "I",
            (Unit(section="§12-11", ledd=2), Unit(section="§13-5"), Unit(section="§13-6", ledd=1)),
            (),
        ),
        (
            "Følgende trer i kraft 1. januar 2024: Endringsloven del I § 12-11 annet ledd, § 13-5 "
            "og § 13-6 første ledd.",
            "I",
            (),
            (Unit(section="§12-11", ledd=2), Unit(section="§13-5"), Unit(section="§13-6", ledd=1)),
        ),
        (
            "Følgende trer i kraft 25. mars 2022: Endringsloven del III (lov 18. juni 2021 nr. 97 "
            "om barnevern (barnevernsloven)) med unntak av ny § 2-6.",
            "III",
            (Unit(section="§2-6", new=True),),
            (),
        ),
        (
            "Følgende trer i kraft 1. juli 2023: Endringsloven del I (tvisteloven) § 3-8 nytt "
            "tredje ledd, § 8-3, § 20-5 tredje ledd fjerde punktum.",
            "I",
            (),
            (
                Unit(section="§3-8", ledd=3, new=True),
                Unit(section="§8-3"),
                Unit(section="§20-5", ledd=3, punktum=4),
            ),
        ),
        (
            "Følgende endringer trer i kraft 15. april 2026 Endringsloven del II (straffeloven)",
            "II",
            (),
            (),
        ),
        (
            "Følgende trer i kraft straks: Endringsloven del IV lov 6. juni 2014 nr. 19 om stans i "
            "utbetalinga av offentlege ytingar og barnebidrag når ein av foreldra har bortført eit "
            "barn til utlandet § 2 annet ledd annet punktum bokstav a, § 3 og § 4.",
            "IV",
            (),
            (
                Unit(section="§2", ledd=2, punktum=2, bokstav="a"),
                Unit(section="§3"),
                Unit(section="§4"),
            ),
        ),
    ],
)
def test_listed_part_items_with_units_or_exceptions(
    sentence: str, part: str, excluded: tuple[Unit, ...], units: tuple[Unit, ...]
) -> None:
    scope = _one(sentence)
    assert (scope.part, scope.excluded, scope.units) == (part, excluded, units)


def test_nine_exceptions_of_every_kind_in_one_item() -> None:
    scope = _one(
        "Følgende trer i kraft 1. januar 2026: Endringsloven del I (barnevernsloven) med unntak av "
        "§ 3-2 paragrafoverskriften, § 3-2 første punktum, nytt kapittel 9 A, § 12-11 andre ledd, "
        "§ 16-3 tredje ledd bokstav c, § 17-2 første ledd første punktum, § 17-3 andre ledd første "
        "punktum, § 17-4 første punktum og § 17-5 andre ledd"
    )
    assert scope.part == "I" and scope.when == date(2026, 1, 1)
    assert len(scope.excluded) == 9
    assert Unit(chapter="kap9a", new=True) in scope.excluded
    assert Unit(section="§3-2", heading=True) in scope.excluded


@pytest.mark.parametrize(
    "sentence",
    [
        "Loven trer i kraft 1. januar 2026, med unntak av lovendringene om utlegg som i saker om "
        "utlegg kommer gradvis til anvendelse, og innkrevingsloven § 21 som trer i kraft "
        "1. januar 2027.",
        "Følgende trer i kraft straks: Endringsloven del I (barnevernsloven) med unntak av det "
        "meste.",
        "Loven § 15-7 fjerde ledd trer i kraft.",
    ],
)
def test_an_exception_or_unit_the_grammar_cannot_scope_refuses_the_sentence(sentence: str) -> None:
    assert _scopes_of(sentence, OWN) is None


def test_a_part_scope_with_exceptions_reaches_what_it_does_not_except() -> None:
    scope = _one(
        "Følgende trer i kraft straks: Endringsloven del I (barnevernsloven) med unntak av "
        "§ 12-11 annet ledd, § 13-5 og § 13-6 første ledd."
    )
    assert scope.reach(_op("I", section="§12-9", ledd=1, punktum=3)) == "covers"
    assert scope.reach(_op("I", section="§13-5", ledd=1)) == "excluded"
    assert scope.reach(_op("I", "replace_section", section="§13-6")) == "spans"
    assert scope.reach(_op("II", section="§12-9", ledd=1)) == "disjoint"


def test_scopes_for_an_operation_name_the_conflict_when_a_scope_spans_or_cannot_place_it() -> None:
    first = _one(
        "Følgende trer i kraft straks: Endringsloven del I (barnevernsloven) med unntak av "
        "§ 12-11 annet ledd, § 13-5 og § 13-6 første ledd."
    )
    second = _one(
        "Følgende trer i kraft 1. januar 2024: Endringsloven del I § 12-11 annet ledd, § 13-5 "
        "og § 13-6 første ledd."
    )
    scopes = [first, second]
    assert [s.when for s in _scopes_for(_op("I", section="§13-5", ledd=1), scopes)] == [
        date(2024, 1, 1)
    ]
    assert [s.when for s in _scopes_for(_op("I", section="§12-9", ledd=1), scopes)] == [OWN]
    assert _scopes_for(_op("I", "replace_section", section="§13-6"), scopes) == (
        "operation spans a unit the decision excepts"
    )


@pytest.mark.parametrize(
    "sentence, excluded, units",
    [
        (
            "Følgende trer i kraft 1. juli 2021: Endringsloven del I med unntak av ny § 2 a.",
            (Unit(section="§2a", new=True),),
            (),
        ),
        (
            "Følgende trer i kraft 1. juli 2021: Endringsloven del I § 1 annet ledd.",
            (),
            (Unit(section="§1", ledd=2),),
        ),
    ],
)
def test_shapes_phase_two_refused_are_units_now(
    sentence: str, excluded: tuple[Unit, ...], units: tuple[Unit, ...]
) -> None:
    """Phase 2 refused these two shapes; ADR-0020 Decisions 4 and 6 model them as units."""
    scope = _one(sentence)
    assert (scope.part, scope.excluded, scope.units, scope.when) == (
        "I", excluded, units, date(2021, 7, 1),
    )  # fmt: skip


def test_a_list_carrying_ledd_yields_one_statement_per_item() -> None:
    xml = (
        'Følgende trer i kraft straks:<ul class="defaultList"><li data-name="•">'
        '<article class="listArticle"><article class="legalP">Endringsloven del I '
        "(barnevernsloven) med unntak av § 13-5.</article></article></li>"
        '<li data-name="•"><article class="listArticle"><article class="legalP">Endringsloven '
        "del II (barnelova) § 69 første punktum.</article></article></li></ul>"
    )
    ledd = Ledd(xml=xml, text="irrelevant here")
    assert list_statements(ledd) == [
        "Følgende trer i kraft straks: Endringsloven del I (barnevernsloven) med unntak av § 13-5.",
        "Følgende trer i kraft straks: Endringsloven del II (barnelova) § 69 første punktum.",
    ]
    assert (
        list_statements(Ledd(xml="Loven trer i kraft straks.", text="Loven trer i kraft straks."))
        is None
    )


def test_lead_sentences_before_the_colon_stand_alone_and_a_lead_without_a_colon_joins_nothing() -> (
    None
):
    item = '<ul><li><article class="legalP">{}</article></li></ul>'
    titled = Ledd(
        xml="Delt ikraftsetting av Stortingets vedtak 2. mai 2023 til lov om endringer. "
        "Følgende trer i kraft 1. juli 2023:"
        + item.format("Endringsloven del III (jordskiftelova)."),
        text="",
    )
    assert list_statements(titled) == [
        "Delt ikraftsetting av Stortingets vedtak 2. mai 2023 til lov om endringer.",
        "Følgende trer i kraft 1. juli 2023: Endringsloven del III (jordskiftelova).",
    ]
    numbered = Ledd(
        xml="Delt iverksetjing av lov 20. juni 2025 nr. 96 om dokumentasjon og arkiv (arkivlova)."
        + item.format("Paragrafane 1 til 10 og §§ 12 til 25 gjeld frå 1. januar 2026."),
        text="",
    )
    assert list_statements(numbered) == [
        "Delt iverksetjing av lov 20. juni 2025 nr. 96 om dokumentasjon og arkiv (arkivlova).",
        "Paragrafane 1 til 10 og §§ 12 til 25 gjeld frå 1. januar 2026.",
    ]
