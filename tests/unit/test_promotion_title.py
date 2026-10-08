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


@pytest.mark.parametrize("word", ["om", "for", "i", "ved", "av", "til", "og"])
def test_each_function_word_can_continue_a_title_in_mixed_case(word: str) -> None:
    head = f"Forskrift om gebyr {word.capitalize()}"
    assert read_title((head, "Eksempel kommune")) == f"{head} Eksempel kommune"


def test_repeated_function_word_continuations_stop_at_the_first_complete_title() -> None:
    block = ("Forskrift om", "gebyr for", "tilsyn i", "Eksempel kommune", "Annen overskrift")
    assert read_title(block) == "Forskrift om gebyr for tilsyn i Eksempel kommune"


@pytest.mark.parametrize("line", ["VEDTATT AV STYRET", "MED HEIMEL I LOV", "I MEDHOLD AV LOV"])
def test_uppercase_metadata_never_joins_an_uppercase_title(line: str) -> None:
    assert read_title(("FORSKRIFT OM GEBYR", line)) == "FORSKRIFT OM GEBYR"
    assert read_title(("FORSKRIFT OM", line)) is None


@pytest.mark.parametrize("dangling", [False, True])
def test_length_limit_applies_to_the_accumulated_title(dangling: bool) -> None:
    head = "FORSKRIFT OM"
    second = "X" * (MAX_TITLE_CHARS - len(head) - 4)
    if dangling:
        second = second[:-2] + " I"
    joined = f"{head} {second}"
    assert len(joined) == MAX_TITLE_CHARS - 3
    result = read_title((head, second, "FOR EKSEMPEL KOMMUNE"))
    assert result == (None if dangling else joined)


@pytest.mark.parametrize("line", ["FOR", "FOR EKSEMPEL", "FOR EKSEMPEL KOMMUNE"])
def test_uppercase_continuation_uses_its_first_word(line: str) -> None:
    block = ("FORSKRIFT OM GEBYR", line)
    assert read_title(block) == (None if line == "FOR" else f"{block[0]} {line}")


@pytest.mark.parametrize("head", ["For", "Gebyr for", "Forskrift om gebyr for"])
def test_continuation_uses_the_last_word_even_in_a_single_word_heading(head: str) -> None:
    assert read_title((head, "Eksempel kommune")) == f"{head} Eksempel kommune"


def test_a_complete_single_word_heading_stops_before_another_line() -> None:
    assert read_title(("Renovasjonsforskrift", "Eksempel kommune")) == "Renovasjonsforskrift"


@pytest.mark.parametrize("separator", [" ", "  ", "\t", "\u00a0"])
@pytest.mark.parametrize("word", ["OM", "FOR", "I", "VED", "AV", "TIL", "OG"])
def test_uppercase_continuation_reads_first_word_across_whitespace(
    separator: str, word: str
) -> None:
    head = "FORSKRIFT OM GEBYR"
    line = separator + separator.join((word, "EKSEMPEL", "KOMMUNE")) + separator
    assert read_title((head, line)) == f"{head} {line}"


@pytest.mark.parametrize("separator", [" ", "  ", "\t", "\u00a0"])
@pytest.mark.parametrize("word", ["om", "for", "i", "ved", "av", "til", "og"])
def test_dangling_title_reads_last_word_across_whitespace(separator: str, word: str) -> None:
    head = separator + separator.join(("Forskrift", "om", "gebyr", word)) + separator
    assert read_title((head, "Eksempel kommune")) == f"{head} Eksempel kommune"


@pytest.mark.parametrize("length", [MAX_TITLE_CHARS - 1, MAX_TITLE_CHARS])
def test_a_complete_title_at_the_length_boundary_cannot_take_another_line(length: int) -> None:
    head = "FORSKRIFT OM"
    continuation = "X" * (length - len(head) - 1)
    title = f"{head} {continuation}"
    assert len(title) == length
    assert read_title((head, continuation, "FOR EKSEMPEL KOMMUNE")) == title


@pytest.mark.parametrize("head", ["FORSKRIFT OM", "FORSKRIFT OM GEBYR"])
def test_metadata_ends_title_reading_without_skipping_to_a_later_continuation(head: str) -> None:
    block = (head, "Hjemmel: LOV-1981-03-13-6-§30", "FOR EKSEMPEL KOMMUNE")
    assert read_title(block) == (None if head == "FORSKRIFT OM" else head)
