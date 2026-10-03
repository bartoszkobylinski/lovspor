"""Splitting extracted lines into identification block and body; pre-filled fields."""

from __future__ import annotations

from datetime import date

import pytest

from lovspor.promotion import ExtractionHoldReason, RegulationFields
from lovspor.promotion.fields import read_regulation
from tests.unit.promotion_fixtures import REGULATION_LINES


def _read(*lines: str) -> RegulationFields:
    result = read_regulation(lines)
    assert not isinstance(result, ExtractionHoldReason), result
    return result[1]


def test_identification_block_runs_from_title_to_first_section() -> None:
    result = read_regulation(("Lokale forskrifter", *REGULATION_LINES))
    assert not isinstance(result, ExtractionHoldReason)
    regulation, fields = result
    assert regulation.identification_block == "\n".join(REGULATION_LINES[:2])
    assert regulation.body == "\n".join(REGULATION_LINES[2:])
    assert regulation.title == fields.title == REGULATION_LINES[0]
    assert regulation.vedtaksdato == fields.vedtatt == date(2019, 12, 12)


def test_no_first_section_is_held_as_no_body() -> None:
    assert read_regulation(REGULATION_LINES[:2]) == ExtractionHoldReason.NO_BODY


def test_section_ten_or_a_mid_line_citation_does_not_start_the_body() -> None:
    lines = ("Forskrift om gebyr", "Endret jf. § 1 i loven", "§ 10 Gebyr")
    assert read_regulation(lines) == ExtractionHoldReason.NO_BODY


def test_no_title_before_the_body_is_held_as_no_title() -> None:
    lines = ("Vedtatt av kommunestyret 12.12.2019", "§ 1 Formål", "Forskrift om noe")
    assert read_regulation(lines) == ExtractionHoldReason.NO_TITLE


@pytest.mark.parametrize(
    "start", ["Kapittel 1. Innledende bestemmelser", "Kap. 1 Formål", "KAPITTEL I Generelt"]
)
def test_first_chapter_starts_the_body(start: str) -> None:
    result = read_regulation(("Forskrift om gebyr", start, "§ 1 Gebyr"))
    assert not isinstance(result, ExtractionHoldReason)
    assert result[0].body.splitlines()[0] == start


@pytest.mark.parametrize(
    "title",
    [
        "Forskrift om renovasjon, Eksempel kommune",
        "Lokal forskrift om snøscooterløyper, Eksempel kommune",
        "Renovasjonsforskrift for Eksempel kommune",
        "FORSKRIFT FOR BRUK AV KOMMUNALE IDRETTSANLEGG",
    ],
)
def test_title_line_shapes(title: str) -> None:
    assert _read("Hjem", title, "§ 1 Formål").title == title


@pytest.mark.parametrize(
    "line",
    [
        "Vedtatt av kommunestyret med hjemmel i forskrift om noe",
        "Dato: FOR-2019-12-12-2077",
        "Forskrift" + " lang" * 80,
    ],
)
def test_lines_that_are_not_a_title(line: str) -> None:
    assert read_regulation((line, "§ 1 Formål")) == ExtractionHoldReason.NO_TITLE


def test_hjemmel_phrase_verbatim_to_sentence_end() -> None:
    assert _read(*REGULATION_LINES).hjemmel == (
        "lov 13. mars 1981 nr. 6 om vern mot forurensninger og om avfall (forurensningsloven) § 30",
    )


def test_hjemmel_header_lists_each_lovdata_reference() -> None:
    fields = _read(
        "Forskrift om gebyr",
        "Hjemmel: LOV-1981-03-13-6-§30, LOV-2018-06-22-83-§8-1",
        "§ 1 Gebyr",
    )
    assert fields.hjemmel == ("LOV-1981-03-13-6-§30", "LOV-2018-06-22-83-§8-1")


def test_hjemmel_header_and_phrase_together_without_duplicates() -> None:
    fields = _read(
        "Forskrift om gebyr",
        "Hjemmel: LOV-1981-03-13-6-§30",
        "Hjemmel: Fastsatt av kommunestyret 1.2.2020 i medhold av LOV-1981-03-13-6-§30.",
        "§ 1 Gebyr",
    )
    assert fields.hjemmel == ("LOV-1981-03-13-6-§30",)


def test_heimel_and_medhald_nynorsk() -> None:
    fields = _read("Forskrift om gebyr", "Vedteke med heimel i opplæringslova § 9 A-10.", "§ 1")
    assert fields.hjemmel == ("opplæringslova § 9 A-10",)


def test_hjemmel_in_the_body_is_never_read() -> None:
    fields = _read("Forskrift om gebyr", "§ 1 Gebyr", "Gebyr fastsatt med hjemmel i lov § 2.")
    assert fields.hjemmel == ()


def test_vedtatt_and_organ_across_a_wrapped_line() -> None:
    fields = _read(
        "Forskrift om gebyr", "Fastsatt av Eksempel bystyre", "12. desember 2019.", "§ 1"
    )
    assert (fields.vedtatt, fields.vedtatt_av) == (date(2019, 12, 12), "Eksempel bystyre")


def test_vedtatt_den_and_vedteke_i() -> None:
    fields = _read("Forskrift om gebyr", "Vedteke i heradsstyret den 3.4.2021", "§ 1")
    assert (fields.vedtatt, fields.vedtatt_av) == (date(2021, 4, 3), "heradsstyret")


def test_impossible_vedtaksdato_is_none_but_organ_is_kept() -> None:
    fields = _read("Forskrift om gebyr", "Vedtatt av kommunestyret 31.02.2019", "§ 1")
    assert (fields.vedtatt, fields.vedtatt_av) == (None, "kommunestyret")


def test_no_enactment_line_leaves_both_empty() -> None:
    fields = _read("Forskrift om gebyr", "§ 1 Gebyr")
    assert fields == RegulationFields(title="Forskrift om gebyr")


@pytest.mark.parametrize(
    ("clause", "ikraft", "ikraft_text"),
    [
        ("Forskriften trer i kraft 1. januar 2020.", date(2020, 1, 1), None),
        ("Forskrifta trår i kraft frå 1.8.2021.", date(2021, 8, 1), None),
        ("Forskriften trer i kraft fra og med den 1.1.2020", date(2020, 1, 1), None),
        ("Forskriften trer i kraft straks.", None, "straks"),
        (
            "Forskriften trer i kraft fra kunngjøring i Norsk Lovtidend. Samtidig oppheves",
            None,
            "fra kunngjøring i Norsk Lovtidend",
        ),
        ("Forskriften trer i kraft 30.02.2020.", None, "30.02.2020"),
    ],
)
def test_ikraft_date_or_verbatim_phrase(
    clause: str, ikraft: date | None, ikraft_text: str | None
) -> None:
    fields = _read("Forskrift om gebyr", "§ 1 Gebyr", clause)
    assert (fields.ikraft, fields.ikraft_text) == (ikraft, ikraft_text)


def test_ikraft_header_wins_over_the_body_clause() -> None:
    fields = _read(
        "Forskrift om gebyr",
        "Ikrafttredelse: 01.01.2020",
        "§ 1 Gebyr",
        "§ 9 Forskriften trer i kraft 1. juli 2020.",
    )
    assert fields.ikraft == date(2020, 1, 1)


def test_no_ikraft_clause_leaves_both_empty() -> None:
    fields = _read("Forskrift om gebyr", "§ 1 Gebyr")
    assert (fields.ikraft, fields.ikraft_text) == (None, None)


def test_a_title_of_exactly_the_length_limit_is_a_title() -> None:
    title = ("Forskrift om renovasjon og slam for hytter og fritidsboliger " * 5)[:250]
    assert len(title) == 250
    assert _read("Hjem", title, "§ 1 Formål").title == title


def test_hjemmel_header_drops_only_a_sentence_period() -> None:
    fields = _read(
        "Forskrift om gebyr",
        "Hjemmel: LOV-1981-03-13-6-§30, FOR-2008-06-27-71-KAPIX.",
        "§ 1 Gebyr",
    )
    assert fields.hjemmel == ("LOV-1981-03-13-6-§30", "FOR-2008-06-27-71-KAPIX")


def test_hjemmel_phrase_wrapped_across_block_lines() -> None:
    fields = _read(
        "Forskrift om gebyr",
        "Vedtatt av kommunestyret med hjemmel i",
        "forurensningsloven § 30. Kunngjort på nettstedet.",
        "§ 1 Gebyr",
    )
    assert fields.hjemmel == ("forurensningsloven § 30",)


def test_ikraft_phrase_ends_at_its_sentence_when_the_next_starts_a_new_line() -> None:
    fields = _read(
        "Forskrift om gebyr",
        "§ 1 Gebyr",
        "Forskriften trer i kraft straks.",
        "Samtidig oppheves forskrift om gebyr.",
    )
    assert (fields.ikraft, fields.ikraft_text) == (None, "straks")
