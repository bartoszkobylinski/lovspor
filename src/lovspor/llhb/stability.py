"""Stability subset selection (ruling #26, METHODOLOGY "Matrix and repeats").

The 30-case stratified subset is drawn from the frozen 250 before any
result exists: allocation is proportional per category (largest
remainder, deterministic tie-break), the within-category pick is a
seeded sample over id-sorted rows. Same inputs, same seed — same
subset, byte for byte. A category that cannot fill its seats aborts
the whole selection; never a silent gap.
"""

import random
from collections.abc import Hashable, Mapping, Sequence
from typing import Any, Final

from pydantic import BaseModel

from lovspor.errors import LovsporError

STABILITY_SUBSET_SIZE: Final = 30
STABILITY_REPEATS: Final = 5
STABILITY_SELECTION_SEED: Final = 42
_MIN_SAMPLES_FOR_SD: Final = 2


class StabilityShortfallError(LovsporError):
    """The subset cannot be drawn as specified — selection fails closed."""


class StabilitySubset(BaseModel):
    size: int
    seed: int
    allocation: dict[str, int]
    case_ids: list[str]


class RateSummary(BaseModel):
    """Exact statistics of one metric's rate across the 5 repeats."""

    values: list[float | None]
    defined: int
    mean: float | None
    minimum: float | None
    maximum: float | None
    sd: float | None
    """Sample SD (n-1); None below two defined values — never a fake 0."""


def summarize_rates(values: list[float | None]) -> RateSummary:
    """Mean / min / max / sample SD over the defined rates, Nones kept visible."""
    defined = [value for value in values if value is not None]
    n = len(defined)
    mean = sum(defined) / n if n else None
    sd: float | None = None
    if mean is not None and n >= _MIN_SAMPLES_FOR_SD:
        sd = (sum((value - mean) ** 2 for value in defined) / (n - 1)) ** 0.5
    return RateSummary(
        values=list(values),
        defined=n,
        mean=mean,
        minimum=min(defined) if n else None,
        maximum=max(defined) if n else None,
        sd=sd,
    )


def flipped_cases(outcomes_by_repeat: Sequence[Mapping[str, Hashable]]) -> list[str]:
    """Case ids whose outcome is not identical across every repeat.

    A case absent from any repeat is unstable by definition — silence
    is not the same outcome as a verdict. The outcome is whatever the caller
    compares by equality: a tri-state pass since v1, a five-way reason code
    since ruling #30(c).
    """
    outcomes: dict[str, list[Hashable]] = {}
    for repeat in outcomes_by_repeat:
        for case_id, outcome in repeat.items():
            outcomes.setdefault(case_id, []).append(outcome)
    total = len(outcomes_by_repeat)
    return sorted(
        case_id for case_id, seen in outcomes.items() if len(seen) != total or len(set(seen)) > 1
    )


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
    if sum(allocation.values()) != size:
        raise StabilityShortfallError(
            f"allocation grants {sum(allocation.values())} seats but the subset size is {size}"
        )
    rng = random.Random(seed)  # noqa: S311 — reproducible sampling, not crypto
    case_ids: list[str] = []
    for category in sorted(allocation):
        case_ids.extend(_draw(by_category.get(category, []), allocation[category], category, rng))
    return StabilitySubset(size=size, seed=seed, allocation=allocation, case_ids=case_ids)
