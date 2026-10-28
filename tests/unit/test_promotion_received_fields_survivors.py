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
