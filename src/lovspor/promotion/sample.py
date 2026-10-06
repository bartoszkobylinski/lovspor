"""The owner's spot-check sample of a promotion batch (ADR-0016 4g, slice S8).

Before a batch writes anything, a sample of its promotable items is drawn for
human review. The draw is a pure function of the population and the seed —
the batch id — so a rerun draws the same items, whatever order they were
listed in: each item is ranked by ``SHA-256(seed ␟ sha256 ␟ source_url)`` and
the lowest ranks are taken. No random number generator is involved, so
nothing about the machine or the run can move the sample.

The rate is **required and explicit**: there is no default. ADR-0016 4g
recommends 100 % (``1``) for the first authority and for every new adapter
family, and only after that a smaller rate (5 %, min 20, max 100, pending
Open Decision 2). A default would decide that policy silently for the owner,
so the operator states the rate on every batch and the report records it.
The size is the rate times the population rounded **up**: a positive rate
never samples nothing from a non-empty batch.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from lovspor.errors import PromotionRefusedError
from lovspor.promotion.decisions import ArtifactKey

_SEPARATOR = "\x1f"


def parse_sample_rate(text: str) -> Decimal:
    """The rate as an exact decimal in ``(0, 1]``; ``1`` is the full, 100 % review."""
    try:
        rate = Decimal(text.strip())
    except InvalidOperation as exc:
        msg = f"the sample rate must be a decimal in (0, 1], got {text!r}"
        raise PromotionRefusedError(msg) from exc
    if not rate.is_finite() or not Decimal(0) < rate <= Decimal(1):
        msg = f"the sample rate must be a decimal in (0, 1], got {text!r}"
        raise PromotionRefusedError(msg)
    return rate


def sample_size(rate: Decimal, population: int) -> int:
    """The rate times the population, rounded up.

    Computed as an exact fraction: a Decimal product is first rounded to the
    context's precision, which can land a rate just above a whole item on it
    and shrink the review sample by one.
    """
    return math.ceil(Fraction(rate) * population)


def draw_sample(
    population: Iterable[ArtifactKey], seed: str, rate: Decimal
) -> tuple[ArtifactKey, ...]:
    """The items to review, drawn by seed and listed by source URL, then hash."""
    if not seed.strip():
        msg = "the sample seed (the batch id) must not be blank"
        raise PromotionRefusedError(msg)
    items = sorted(set(population), key=lambda key: _rank(seed, key))
    drawn = items[: sample_size(rate, len(items))]
    return tuple(sorted(drawn, key=lambda key: (key.source_url, key.sha256)))


def _rank(seed: str, key: ArtifactKey) -> str:
    material = _SEPARATOR.join((seed, key.sha256, key.source_url))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
