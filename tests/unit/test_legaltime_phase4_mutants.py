"""Phase 4A (#861): each disjunct of the new guards is pinned on its own.

The CI mutation gate on PR #865 (513 of 531 killed at 1f8be75f) showed that the
earlier tests reached most guards through a sentence that also names a unit, so
one disjunct could be dropped without a failure. Each case here triggers exactly
one reason a sentence reaches the grammar, or exactly one way a block keeps or
loses its act.
"""

import pytest

from lovspor.legaltime.commencement import _informational
from tests.unit.test_legaltime_phase4_attribution import (
    INTRO_3,
    STRAFFELOVEN,
    _article,
    _ledd,
    _operations,
    _p,
)


@pytest.mark.parametrize(
    "sentence",
    [
        "Kongen kan sette endringene i kraft 1. juli 2023.",  # a date alone
        "Kongen kan sette endringene i kraft straks.",  # straks alone
        "Kongen kan likevel sette endringene i kraft.",  # a reservation alone
        "Fra samme tid trer endringene i kraft.",  # the anaphora alone
    ],
)
def test_one_reason_alone_brings_an_informational_opening_to_the_grammar(sentence: str) -> None:
    assert _informational(sentence) is False


def test_a_cited_act_is_cut_out_to_nothing_before_a_date_is_looked_for() -> None:
    """``1. lov 2. mai 2000 nr. 3 juli 2021``: the citation goes, the date around it is read."""
    sentence = "Kongen kan sette endringene i kraft 1. lov 2. mai 2000 nr. 3 juli 2021."
    assert _informational(sentence) is False
    assert _informational("Kongen kan sette endringene i kraft etter lov 2. mai 2000 nr. 3.")


def test_cut_references_and_citations_leave_no_letters_to_read_as_a_part() -> None:
    """A removed reference or citation must not leave text a ``del I``-style unit could match."""
    assert _informational("Kongen kan sette i kraft hele del før loven trer i kraft.")
    assert _informational("Kongen kan sette i kraft hele del lov 2. mai 2000 nr. 3.")


def test_only_the_first_leading_condition_is_set_aside() -> None:
    """The main clause follows the first condition; a second condition is part of it."""
    assert (
        _informational(
            "Dersom § 2-6 er vedtatt, dersom den er satt i kraft, gjelder overgangsreglene."
        )
        is False
    )


def test_the_set_aside_condition_leaves_the_main_clause_s_first_word_intact() -> None:
    """``…, i kraft trer den …``: the phrase opens the main clause and must still be read."""
    assert _informational("Er § 2-6 vedtatt, i kraft trer den samtidig.") is False


def test_a_substitution_intro_whose_term_is_an_act_word_keeps_the_block() -> None:
    """``«lov»`` would make the colon sentence look like an act-naming intro; it is not one."""
    body = _article(
        "§3", _ledd(INTRO_3), _p("I følgende bestemmelser skal ordet «lov» endres til «lova»:"),
        _p("§ 40 oppheves."),
    )  # fmt: skip
    assert [(op.address.section, op.act) for op in _operations(body)] == [("§40", STRAFFELOVEN)]


def test_a_payload_is_measured_from_the_instruction_s_own_article() -> None:
    """The first node of a new article may be an instruction; its payload is in that article."""
    body = _article("§3", _ledd(INTRO_3), _p("§ 40 oppheves."))
    body += _article("§4", _p("§ 13 første ledd skal lyde:"), _ledd("Ny tekst."))
    replacement = next(op for op in _operations(body) if op.address.section == "§13")
    assert [ledd.text for ledd in replacement.ledd] == ["Ny tekst."]
