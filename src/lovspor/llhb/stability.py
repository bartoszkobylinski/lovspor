"""Stability subset selection (ruling #26, METHODOLOGY "Matrix and repeats").

The 30-case stratified subset is drawn from the frozen 250 before any
result exists: allocation is proportional per category (largest
remainder, deterministic tie-break), the within-category pick is a
seeded sample over id-sorted rows. Same inputs, same seed — same
subset, byte for byte. A category that cannot fill its seats aborts
the whole selection; never a silent gap.
"""

import random
from typing import Any, Final

from pydantic import BaseModel

from lovspor.errors import LovsporError

STABILITY_SUBSET_SIZE: Final = 30
STABILITY_REPEATS: Final = 5
STABILITY_SELECTION_SEED: Final = 42


class StabilityShortfallError(LovsporError):
    """The subset cannot be drawn as specified — selection fails closed."""


class StabilitySubset(BaseModel):
    size: int
    seed: int
    allocation: dict[str, int]
    case_ids: list[str]


def subset_allocation(counts: dict[str, int], size: int) -> dict[str, int]:
    """Proportional seats per category via largest remainder.

    Ties on the fractional part break by category name, ascending —
    no randomness in the allocation itself.
    """
    total = sum(counts.values())
    if size > total:
        raise StabilityShortfallError(f"subset size {size} exceeds pool of {total}")
    seats = {cat: size * n // total for cat, n in counts.items()}
    leftover = size - sum(seats.values())
    by_remainder = sorted(counts, key=lambda cat: (-(size * counts[cat] % total), cat))
    for cat in by_remainder[:leftover]:
        seats[cat] += 1
    return dict(sorted(seats.items()))


def _numeric_id(case: dict[str, Any]) -> int:
    return int(str(case["case_id"]).rsplit("-", 1)[1])


def _draw(
    rows: list[dict[str, Any]],
    seats: int,
    category: str,
    rng: random.Random,
) -> list[str]:
    if len(rows) < seats:
        raise StabilityShortfallError(
            f"{category}: {len(rows)} cases for {seats} seats — cannot fill"
        )
    picked = rng.sample(sorted(rows, key=_numeric_id), seats)
    return [str(case["case_id"]) for case in sorted(picked, key=_numeric_id)]


def select_stability_subset(
    cases: list[dict[str, Any]],
    size: int = STABILITY_SUBSET_SIZE,
    seed: int = STABILITY_SELECTION_SEED,
    allocation: dict[str, int] | None = None,
) -> StabilitySubset:
    """Draw the stratified subset; input order never matters."""
    by_category: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        by_category.setdefault(str(case["category"]), []).append(case)
    if allocation is None:
        allocation = subset_allocation({cat: len(rows) for cat, rows in by_category.items()}, size)
    rng = random.Random(seed)  # noqa: S311 — reproducible sampling, not crypto
    case_ids: list[str] = []
    for category in sorted(allocation):
        case_ids.extend(_draw(by_category.get(category, []), allocation[category], category, rng))
    return StabilitySubset(size=size, seed=seed, allocation=allocation, case_ids=case_ids)
