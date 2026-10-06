"""Lovdata's metadata block in a copy pasted without print chrome (#527).

The header lines below are synthetic: they keep the layout of the block the
Høylandet verneforskrifter carried (authority 5046, artifacts 73db2824a768,
e9f5662ec509, d9401565294e) with invented ids and titles, so no Lovdata text
enters the repository.
"""

from __future__ import annotations

import pytest

from lovspor.promotion import ExtractedDocument, ExtractionHoldReason, HeldExtraction
from lovspor.promotion.extract import extract_regulation
from lovspor.promotion.lovdata_header import has_lovdata_header
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page, pdf_pages

HEADER = (
    "Dato: FOR-2010-12-17-9001",
    "Publisert: II 2010 hefte 5",
    "Ikrafttredelse: 17.12.2010",
    "Gjelder for: Eksempel kommune, Eksempel fylke",
    "Hjemmel: LOV-2009-06-19-100-§34",
    "Korttittel: Forskrift om Eksempel naturreservat",
)


def _text(*lines: str) -> str:
    return "\n".join((*lines, *REGULATION_LINES))


def test_the_full_metadata_block_is_a_lovdata_header() -> None:
    assert has_lovdata_header(_text(*HEADER))


@pytest.mark.parametrize(
    "block",
    [
        ("Dato: FOR-2010-12-17-9001", "Ikrafttredelse: 17.12.2010", "Korttittel: Eksempel"),
        ("Dato FOR-2010-02-11-9002", "Publisert II 2010 hefte 1", "Korttittel Eksempel"),
        ("Departement: Eksempeldepartementet", "Dato: LOV-2009-06-19-100", "Endrer: x"),
    ],
)
def test_a_dato_carrying_a_lovdata_id_and_two_more_fields_are_a_header(
    block: tuple[str, ...],
) -> None:
    assert has_lovdata_header(_text(*block))


@pytest.mark.parametrize(
    "block",
    [
        ("Dato: FOR-2010-12-17-9001", "Korttittel: Eksempel"),
        ("Dato: 12.12.2019", "Hjemmel: lov 19. juni 2009 nr. 100", "Ikrafttredelse: 1.1.2020"),
        ("Hjemmel: FOR-2004-06-01-931 § 2", "Saken gjelder vann og avløp", "Korttittel: x"),
        ("dato: FOR-2010-12-17-9001", "ikrafttredelse: 17.12.2010", "korttittel: Eksempel"),
        ("Vedtatt etter FOR-2010-12-17-9001", "Ikrafttredelse: 17.12.2010", "Dato: 1.1.2011"),
        ("Hjemmel: LOV-2009-06-19-100-§34", "Kunngjort: 21.12.2010 kl. 11.45", "Endrer: x"),
        ("Dato: se FOR-2010-12-17-9001", "Ikrafttredelse: 17.12.2010", "Korttittel: x"),
        (
            "Dato",
            "Ikrafttredelse",
            "Gjelder for Eksempel kommune, Eksempel fylke",
            "Hjemmel FOR-2004-06-01-9004-§1-2, LOV-1981-03-13-6-§9",
            "Kunngjort",
            "Korttittel Forskrift om nedgravde oljetanker, Eksempel",
        ),
    ],
)
def test_a_municipal_header_or_a_cited_id_is_not_a_lovdata_header(block: tuple[str, ...]) -> None:
    assert not has_lovdata_header(_text(*block))


def test_fields_further_apart_than_the_window_are_not_one_block() -> None:
    filler = tuple(f"Linje {number} i forskriften." for number in range(10))
    spread = (HEADER[0], *filler, HEADER[2], *filler, HEADER[5])
    assert not has_lovdata_header(_text(*spread))


def test_fields_just_inside_the_window_are_one_block() -> None:
    filler = tuple(f"Linje {number} i forskriften." for number in range(8))
    spread = (HEADER[0], *filler, HEADER[2], HEADER[5])
    assert has_lovdata_header(_text(*spread))


def test_a_pasted_copy_without_print_chrome_is_held_as_a_lovdata_copy() -> None:
    pages = (("Forvaltningsplan for Eksempel naturreservat", *HEADER), REGULATION_LINES)
    held = extract_regulation(pdf_pages(*pages), "application/pdf")
    assert isinstance(held, HeldExtraction)
    assert (held.reason, held.detail) == (
        ExtractionHoldReason.LOVDATA_COPY,
        "the text carries Lovdata's metadata block",
    )


def test_a_regulation_whose_preamble_names_its_hjemmel_still_extracts() -> None:
    lines = ("Hjemmel: lov 19. juni 2009 nr. 100 § 34", *REGULATION_LINES)
    result = extract_regulation(html_page(lines), "text/html; charset=utf-8")
    assert isinstance(result, ExtractedDocument)
