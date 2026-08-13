"""Stability subset (ruling #26): proportional allocation, seeded pick, fail-closed."""

import pytest

from lovspor.llhb.selection import FROZEN_TARGETS
from lovspor.llhb.stability import (
    STABILITY_REPEATS,
    STABILITY_SELECTION_SEED,
    STABILITY_SUBSET_SIZE,
    StabilityShortfallError,
    select_stability_subset,
    subset_allocation,
)


def _case(cid: str, category: str) -> dict:
    return {"case_id": cid, "category": category}


def _pool(counts: dict[str, int]) -> list[dict]:
    return [
        _case(f"llhb-v1-{cat}-{100 + i}", cat) for cat, n in counts.items() for i in range(1, n + 1)
    ]


def test_ruling_26_constants() -> None:
    assert STABILITY_SUBSET_SIZE == 30
    assert STABILITY_REPEATS == 5
    assert STABILITY_SELECTION_SEED == 42


def test_allocation_over_frozen_targets_is_the_published_split() -> None:
    assert subset_allocation(FROZEN_TARGETS, 30) == {
        "C1": 6,
        "C2": 5,
        "C3": 4,
        "C4": 4,
        "C5": 2,
        "C6": 4,
        "C7": 3,
        "C8": 2,
    }


def test_allocation_sums_to_size_and_preserves_proportions() -> None:
    allocation = subset_allocation(FROZEN_TARGETS, 30)
    assert sum(allocation.values()) == 30
    for category, count in FROZEN_TARGETS.items():
        exact = 30 * count / 250
        assert abs(allocation[category] - exact) < 1


def test_allocation_ties_break_deterministically() -> None:
    # Two categories with identical counts and one leftover seat: the
    # lexicographically first category takes it, every time.
    assert subset_allocation({"C1": 10, "C2": 10}, 3) == {"C1": 2, "C2": 1}


def test_select_is_deterministic_and_stratified() -> None:
    pool = _pool(FROZEN_TARGETS)
    first = select_stability_subset(pool)
    second = select_stability_subset(pool)
    assert first == second
    assert first.size == 30
    assert first.seed == STABILITY_SELECTION_SEED
    assert len(first.case_ids) == 30
    assert len(set(first.case_ids)) == 30
    per_category: dict[str, int] = {}
    for cid in first.case_ids:
        per_category[cid.split("-")[2]] = per_category.get(cid.split("-")[2], 0) + 1
    assert per_category == first.allocation


def test_select_output_order_is_by_category_then_numeric_id() -> None:
    subset = select_stability_subset(_pool(FROZEN_TARGETS))
    keys = [(cid.split("-")[2], int(cid.rsplit("-", 1)[1])) for cid in subset.case_ids]
    assert keys == sorted(keys)


def test_select_ignores_input_order() -> None:
    pool = _pool(FROZEN_TARGETS)
    assert select_stability_subset(pool) == select_stability_subset(list(reversed(pool)))


def test_seed_changes_the_pick() -> None:
    pool = _pool(FROZEN_TARGETS)
    assert (
        select_stability_subset(pool).case_ids
        != select_stability_subset(pool, seed=STABILITY_SELECTION_SEED + 1).case_ids
    )


def test_select_fails_closed_when_a_category_cannot_fill_its_seats() -> None:
    counts = dict(FROZEN_TARGETS)
    counts["C5"] = 1  # allocation wants 2 seats from a 15-case stratum
    pool = _pool(counts)
    with pytest.raises(StabilityShortfallError, match="C5"):
        select_stability_subset(pool, allocation=subset_allocation(FROZEN_TARGETS, 30))


def test_allocation_rejects_size_above_pool() -> None:
    with pytest.raises(StabilityShortfallError):
        subset_allocation({"C1": 2}, 3)


def test_allocation_can_select_the_entire_pool() -> None:
    assert subset_allocation({"C1": 2, "C2": 1}, 3) == {"C1": 2, "C2": 1}


def test_select_accepts_a_category_with_exactly_enough_cases() -> None:
    pool = _pool({"C1": 2})

    subset = select_stability_subset(pool, size=2, allocation={"C1": 2})

    assert subset.case_ids == ["llhb-v1-C1-101", "llhb-v1-C1-102"]


def test_explicit_allocation_must_match_declared_subset_size() -> None:
    """Returned metadata must never claim a size different from the selected IDs."""
    pool = _pool({"C1": 3})

    with pytest.raises(StabilityShortfallError, match=r"allocation|size"):
        select_stability_subset(pool, size=2, allocation={"C1": 1})
