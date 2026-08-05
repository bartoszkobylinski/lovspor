"""Frozen abbreviation table: every entry tested, no fuzzy expansion."""

from lovspor.llhb.abbreviations import (
    ABBREVIATIONS,
    ABBREVIATIONS_VERSION,
    expand_abbreviation,
)

_EXPECTED_TABLE = {
    "aml.": "arbeidsmiljøloven",
    "avtl.": "avtaleloven",
    "fvl.": "forvaltningsloven",
    "ftrl.": "folketrygdloven",
    "grl.": "grunnloven",
    "sktl.": "skatteloven",
    "strl.": "straffeloven",
    "tvl.": "tvisteloven",
}


def test_table_is_exactly_the_frozen_v1_set() -> None:
    """The table is part of the evaluator freeze surface — a golden copy.

    Any entry added, removed or changed must fail here first, forcing a
    deliberate version decision instead of a silent freeze-surface drift.
    """
    assert ABBREVIATIONS == _EXPECTED_TABLE
    assert ABBREVIATIONS_VERSION == "llhb-abbrev-v1"


def test_every_entry_expands_to_its_act_name() -> None:
    for token, name in _EXPECTED_TABLE.items():
        assert expand_abbreviation(token) == name


def test_expansion_is_case_insensitive_but_otherwise_exact() -> None:
    assert expand_abbreviation("AML.") == "arbeidsmiljøloven"
    assert expand_abbreviation("Sktl.") == "skatteloven"


def test_unknown_and_near_miss_tokens_do_not_expand() -> None:
    for token in ("aml", "aml..", "amls.", "xx.", "", " aml.", "arbml."):
        assert expand_abbreviation(token) is None
