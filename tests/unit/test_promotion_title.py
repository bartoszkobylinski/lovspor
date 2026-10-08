"""The title a regulation's identification block spells across its lines (issue #592).

Structural fixtures only: the line shapes met in the archive's PDFs, with
invented text ("Eksempel kommune").
"""

from __future__ import annotations

import pytest

from lovspor.promotion.anchors import MAX_TITLE_CHARS
from lovspor.promotion.title import FUNCTION_WORDS, read_title

#: Sykkylven 1528's shape: an uppercase heading broken over three lines.
CAPS_HEADING = (
    "FORSKRIFT OM",
    "SKULEREGLAR",
    "FOR GRUNNSKULEN I EKSEMPEL KOMMUNE",
    "Vedtatt av utvalet 28.05.2024",
)


def test_an_uppercase_heading_broken_over_lines_is_one_title() -> None:
    assert read_title(CAPS_HEADING) == "FORSKRIFT OM SKULEREGLAR FOR GRUNNSKULEN I EKSEMPEL KOMMUNE"


@pytest.mark.parametrize(
    ("block", "title"),
    [
        (
            ("Lokal forskrift for adressering i", "Eksempel kommune (adresseforskrifta)"),
            "Lokal forskrift for adressering i Eksempel kommune (adresseforskrifta)",
        ),
        (
            ("Forskrift om gebyr, Eksempel kommune, Møre og", "Romsdal", "Heimel: lov"),
            "Forskrift om gebyr, Eksempel kommune, Møre og Romsdal",
        ),
        (
            ("FORSKRIFT OM VANN- OG", "AVLØPSGEBYR I EKSEMPEL KOMMUNE", "vedtatt av styret"),
            "FORSKRIFT OM VANN- OG AVLØPSGEBYR I EKSEMPEL KOMMUNE",
        ),
    ],
)
def test_a_title_ending_on_a_function_word_takes_the_next_line(
    block: tuple[str, ...], title: str
) -> None:
    assert read_title(block) == title


@pytest.mark.parametrize(
    "block",
    [
        ("FORSKRIFT OM GEBYR", "EKSEMPEL KOMMUNE"),
        ("FORSKRIFT OM GEBYR", "i medhold av lov om kommunar"),
        ("FORSKRIFT OM GEBYR", "For Eksempel kommune"),
        ("Forskrift om gebyr", "FOR EKSEMPEL KOMMUNE"),
        ("Forskrift om gebyr", "Eksempel kommune"),
    ],
)
def test_a_complete_title_does_not_take_the_next_line(block: tuple[str, ...]) -> None:
    assert read_title(block) == block[0]


@pytest.mark.parametrize("word", sorted(FUNCTION_WORDS))
def test_a_title_left_on_a_function_word_is_truncated(word: str) -> None:
    assert read_title((f"Forskrift om gebyr {word}",)) is None
    assert read_title((f"FORSKRIFT OM GEBYR {word.upper()}",)) is None


@pytest.mark.parametrize(
    "line",
    [
        "Vedtatt av kommunestyret 12.12.2019",
        "Vedteke av kommunestyret 12.12.2019",
        "Vedteken av kommunestyret 12.12.2019",
        "Fastsatt av kommunestyret 12.12.2019",
        "Fastsett av kommunestyret 12.12.2019",
        "Hjemmel: lov om kommunar",
        "Heimel: lov om kommunar",
        "Med hjemmel i lov om kommunar",
        "med heimel i lov om kommunar",
        "I medhold av lov om kommunar",
        "i medhald av lov om kommunar",
    ],
)
def test_an_enactment_or_hjemmel_line_never_completes_a_title(line: str) -> None:
    assert read_title(("Forskrift om gebyr for", line)) is None


@pytest.mark.parametrize(
    "title", ["Forskrift om vern av Hom", "Forskrift om gebyr i Mog", "Forskrift om Navi"]
)
def test_a_last_word_that_merely_ends_like_a_function_word_is_whole(title: str) -> None:
    assert read_title((title, "EKSEMPEL KOMMUNE")) == title


def test_a_joined_title_stops_at_the_title_length() -> None:
    head = "Forskrift om " + "x" * (MAX_TITLE_CHARS - len("Forskrift om  i") - 4) + " i"
    fits = "a" * (MAX_TITLE_CHARS - len(head) - 1)
    assert read_title((head, fits)) == f"{head} {fits}"
    assert read_title((head, fits + "a")) is None
