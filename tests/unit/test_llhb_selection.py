"""Stage 4 selection: eligibility, ordering, provision cap, fail-closed."""

import pytest

from lovspor.llhb.selection import (
    FROZEN_TARGETS,
    HUNDRED_PCT_CATEGORIES,
    SelectionShortfallError,
    eligible_cases,
    select,
)


def _case(cid: str, category: str, act: str | None = None, section: str | None = None) -> dict:
    return {
        "case_id": cid,
        "category": category,
        "expected_act_slug": act,
        "expected_section_id": section,
    }


def test_frozen_targets_match_methodology() -> None:
    assert FROZEN_TARGETS == {
        "C1": 50,
        "C2": 40,
        "C3": 35,
        "C4": 30,
        "C5": 15,
        "C6": 35,
        "C7": 25,
        "C8": 20,
    }
    assert HUNDRED_PCT_CATEGORIES == ("C2", "C4", "C5", "C8")


def test_eligibility_decision_beats_default() -> None:
    cases = [
        _case("llhb-v1-C1-101", "C1"),  # undecided, sampled category -> eligible
        _case("llhb-v1-C1-102", "C1"),  # dropped -> out
        _case("llhb-v1-C2-101", "C2"),  # undecided, 100%-review category -> OUT
        _case("llhb-v1-C2-102", "C2"),  # explicit keep -> eligible
        _case("llhb-v1-C5-101", "C5"),  # needs_fix -> out
    ]
    decisions = {
        "llhb-v1-C1-102": "drop",
        "llhb-v1-C2-102": "keep",
        "llhb-v1-C5-101": "needs_fix",
    }
    ids = [c["case_id"] for c in eligible_cases(cases, decisions)]
    assert ids == ["llhb-v1-C1-101", "llhb-v1-C2-102"]


def test_select_orders_by_numeric_id_and_fills_target() -> None:
    cases = [
        _case("llhb-v1-C1-201", "C1", "beta", "2"),
        _case("llhb-v1-C1-102", "C1", "alfa", "1"),
        _case("llhb-v1-C1-101", "C1", "alfa", "2"),
    ]
    result = select(cases, {}, {"C1": 2})
    assert [c["case_id"] for c in result.selected] == ["llhb-v1-C1-101", "llhb-v1-C1-102"]
    assert result.report["C1"]["eligible"] == 3
    assert result.report["C1"]["selected"] == ["llhb-v1-C1-101", "llhb-v1-C1-102"]


def test_select_caps_two_per_provision_and_records_skips() -> None:
    cases = [
        _case("llhb-v1-C3-101", "C3", "alfa", "1"),
        _case("llhb-v1-C3-102", "C3", "alfa", "1"),
        _case("llhb-v1-C3-103", "C3", "alfa", "1"),  # third same provision -> skipped
        _case("llhb-v1-C3-104", "C3", "beta", "1"),
    ]
    result = select(cases, {}, {"C3": 3})
    assert [c["case_id"] for c in result.selected] == [
        "llhb-v1-C3-101",
        "llhb-v1-C3-102",
        "llhb-v1-C3-104",
    ]
    assert result.report["C3"]["cap_skipped"] == ["llhb-v1-C3-103"]


def test_select_exempts_c8_from_the_provision_cap() -> None:
    cases = [_case(f"llhb-v1-C8-10{i}", "C8") for i in range(1, 5)]
    keeps = {str(c["case_id"]): "keep" for c in cases}
    result = select(cases, keeps, {"C8": 4})
    assert len(result.selected) == 4
    assert result.report["C8"]["cap_skipped"] == []


def test_select_stops_at_target_and_records_no_further_skips() -> None:
    """The walk ends the moment the target is met: a later case that
    WOULD be cap-skipped never enters the report — cap_skipped documents
    only skips that happened while still selecting."""
    cases = [
        _case("llhb-v1-C3-101", "C3", "alfa", "1"),
        _case("llhb-v1-C3-102", "C3", "alfa", "1"),
        _case("llhb-v1-C3-103", "C3", "alfa", "1"),  # after target — never visited
    ]
    result = select(cases, {}, {"C3": 2})
    assert [c["case_id"] for c in result.selected] == ["llhb-v1-C3-101", "llhb-v1-C3-102"]
    assert result.report["C3"]["cap_skipped"] == []


def test_select_fails_closed_on_shortfall() -> None:
    cases = [_case("llhb-v1-C7-101", "C7", "alfa", "1")]
    with pytest.raises(SelectionShortfallError, match="C7"):
        select(cases, {}, {"C7": 2})


def test_select_never_selects_a_dropped_case() -> None:
    cases = [
        _case("llhb-v1-C6-101", "C6", "alfa", "1"),
        _case("llhb-v1-C6-102", "C6", "beta", "1"),
    ]
    result = select(cases, {"llhb-v1-C6-101": "drop"}, {"C6": 1})
    assert [c["case_id"] for c in result.selected] == ["llhb-v1-C6-102"]
