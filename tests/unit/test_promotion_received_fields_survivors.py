"""Received text keeps its title and an unnumbered body without enactment lines."""

import pytest

from lovspor.promotion.fields import read_received_regulation


@pytest.mark.parametrize(
    "body",
    [
        ("Alle skal holde området ryddig.",),
        ("Alle skal holde området ryddig.", "Avfall skal leveres til mottaket."),
        ("§ 1. Orden", "Alle skal holde området ryddig."),
    ],
)
def test_received_title_and_entire_body_are_preserved(body: tuple[str, ...]) -> None:
    title = "Forskrift om orden."
    result = read_received_regulation((title, *body))
    assert isinstance(result, tuple)
    regulation, fields = result
    assert regulation.title == title
    assert regulation.identification_block == title
    assert regulation.body == "\n".join(body)
    assert fields.title == title


@pytest.mark.parametrize("anchor", ["§ 1. Orden", "Kapittel 1", "I", "1. Orden"])
def test_received_identification_keeps_first_line_and_all_headers(anchor: str) -> None:
    lines = (
        "Renovasjonsforskrift for Eksempel kommune.",
        "Hjemmel: LOV-2009-06-19-100.",
        "Fastsatt av Eksempel kommunestyre 1. januar 2020.",
        anchor,
        "Alle skal holde området ryddig.",
    )
    result = read_received_regulation(lines)
    assert isinstance(result, tuple)
    regulation, fields = result
    assert regulation.title == lines[0]
    assert regulation.identification_block == "\n".join(lines[:3])
    assert regulation.body == "\n".join(lines[3:])
    assert fields.title == lines[0]
    assert fields.hjemmel == ("LOV-2009-06-19-100",)
