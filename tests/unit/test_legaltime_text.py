"""Grammar of the legal-time replay: ordinals, dates, citations, sentences."""

from datetime import date

import pytest

from lovspor.legaltime.text import (
    act_refid,
    collapse,
    ordinal,
    parse_date,
    section_key,
    section_sort_key,
    split_sentences,
)


@pytest.mark.parametrize(
    ("word", "value"),
    [("første", 1), ("Annet", 2), ("andre", 2), ("syvende", 7), ("sjuende", 7), ("tolvte", 12)],
)
def test_ordinal_words(word: str, value: int) -> None:
    assert ordinal(word) == value


def test_ordinal_outside_table_is_none() -> None:
    assert ordinal("mange") is None


def test_parse_date_reads_norwegian_month_names() -> None:
    assert parse_date("trer i kraft 1. januar 2022.") == date(2022, 1, 1)
    assert parse_date("Lova gjeld frå 1. januar 2021.") == date(2021, 1, 1)
    assert parse_date("gjelder fra den tid Kongen bestemmer") is None


def test_act_refid_with_and_without_number() -> None:
    assert act_refid("I lov 14. juni 2019 nr. 21 om arv og dødsboskifte") == "lov/2019-06-14-21"
    assert act_refid("I lov 10. februar 1967 om behandlingsmåten") == "lov/1967-02-10"
    assert act_refid("Ikraftsetjing av lov 7. mai 2021 nr. 34 om endringar") == "lov/2021-05-07-34"
    assert act_refid("ingen lov her") is None


def test_section_key_folds_label_forms() -> None:
    assert section_key("§ 88 a") == "§88a"
    assert section_key("§ 99a") == "§99a"
    assert section_key("§ 8-7 b") == "§8-7b"
    assert section_key("ny § 12 a skal lyde") == "§12a"
    assert section_key("kapittel 3") is None


def test_section_sort_key_orders_letters_after_numbers() -> None:
    keys = ["§89", "§88a", "§88", "§9", "§8-7", "§8-7a"]
    assert sorted(keys, key=section_sort_key) == ["§8-7", "§8-7a", "§9", "§88", "§88a", "§89"]


def test_split_sentences_respects_abbreviations_and_ordinal_numbers() -> None:
    text = (
        "Dødsfall skal straks meldes til tingretten, jf. § 84. Meldingen skal gis av legen. "
        "Fristen regnes fra 1. januar 2022. Den meldepliktige kan gi en annen i oppdrag."
    )
    assert split_sentences(text) == [
        "Dødsfall skal straks meldes til tingretten, jf. § 84.",
        "Meldingen skal gis av legen.",
        "Fristen regnes fra 1. januar 2022.",
        "Den meldepliktige kan gi en annen i oppdrag.",
    ]


def test_split_sentences_keeps_nr_and_quotes_together() -> None:
    assert split_sentences("Se lov nr. 21. «Det gjelder», sa hun. Punktum tre.") == [
        "Se lov nr. 21.",
        "«Det gjelder», sa hun.",
        "Punktum tre.",
    ]


def test_collapse_normalises_whitespace() -> None:
    assert collapse("  a \n b\t c ") == "a b c"
