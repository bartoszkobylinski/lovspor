"""Behavioral checks for the reported commencement and replay survivors."""

from datetime import date
from hashlib import sha256

import pytest

from lovspor.legaltime.baseline import OUTSIDE, _Fold, _statement_proof, baseline_commencement
from lovspor.legaltime.commencement import _Decision
from lovspor.legaltime.replay import replay
from lovspor.legaltime.scopes import _item_units
from lovspor.legaltime.units import Unit
from tests.unit.test_legaltime_baseline import STAGED, _act, _decision
from tests.unit.test_legaltime_baseline_rules import PROOF_A
from tests.unit.test_legaltime_phase3_replay import (
    ACT_DOC,
    COMMENCEMENT,
    INSERT_LEDD_4,
    _event,
    _op,
)


def test_empty_partial_scopes_are_unreadable_before_folding() -> None:
    act = _act()
    decision = _decision(
        "forskrift/2025-06-10-967",
        "Delt ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern",
        "2026-01-01",
        "Loven trer i kraft om kort tid.",
        "2025-06-10 14:40",
    )
    read = _Decision(decision, act, True)
    assert read.partial and not read.scopes
    assert read.unreadable == OUTSIDE
    result = baseline_commencement(act, (STAGED, decision), [])
    assert result.units
    for timing in (result.default, *(entry.timing for entry in result.units)):
        assert (timing.status, timing.effective, timing.reason) == ("unresolved", None, OUTSIDE)
        assert timing.proofs[-1].refid == decision.header.refid
        assert timing.proofs[-1].sha256 == decision.sha256


def test_quote_hashes_use_utf8_for_norwegian_text() -> None:
    quote = "Loven trer i kraft straks. ÆØÅ æøå."
    event = _event(90, _op(90, "replace_ledd"), date(2023, 1, 1))
    proof = PROOF_A.model_copy(update={"refid": event.law, "member": event.member})
    event = event.model_copy(
        update={"timing": event.timing.model_copy(update={"proofs": (proof,)})}
    )
    expected = sha256(quote.encode("utf-8")).hexdigest()
    statement = _statement_proof(event, quote)
    assert statement is not None and statement.quote_sha256 == expected
    excepted = _Fold(_act(), {})._excepted(STAGED, quote)
    assert excepted.proofs[-1].quote_sha256 == expected


@pytest.mark.parametrize(
    "tail, expected",
    [
        ("lov 6. juni 2014 nr. 19 om prøver § 2 og § 3", "lov/2014-06-06-19"),
        ("lov uten dato § 2 og § 3", None),
        ("lov uten dato § 2 lov 6. juni 2014 nr. 19 § 3", None),
    ],
)
def test_inline_act_citation_and_multiple_sections(tail: str, expected: str | None) -> None:
    item = _item_units(tail)
    if expected is None:
        assert item is None
    else:
        assert item is not None
        assert item.act == expected
        assert item.units == (Unit(section="§2"), Unit(section="§3"))


def test_missing_first_substitution_target_does_not_hide_later_pending_ledd() -> None:
    event = _event(
        91,
        _op(
            91,
            "substitute_term",
            locations="§ 999, § 15-7 fjerde ledd",
            term_old="Fire",
            term_new="Endret",
        ),
        date(2023, 3, 1),
    )
    state = replay(ACT_DOC, COMMENCEMENT, [INSERT_LEDD_4, event], date(2023, 6, 30))
    assert state.withheld == ("op91",)
    assert state.applied == ("op1",)
    assert state.unresolved == ()
    assert {p.key: p for p in state.provisions}["§15-7"].ledd[3].text == "Fire."
