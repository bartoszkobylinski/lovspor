"""Stage 4 selection of the frozen 250 (SELECTION.md, rulings #23/#24).

Deterministic and performance-independent: inputs are the committed
pool candidates, the owner's merged review decisions, and the frozen
METHODOLOGY §3 targets. Per category, eligible cases walk in ascending
numeric case-id order under the ≤2-per-provision cap; any shortfall
aborts the whole selection — never a silent gap, never cross-category
borrowing.
"""

from typing import Any, Final

from pydantic import BaseModel

from lovspor.errors import LovsporError

FROZEN_TARGETS: Final[dict[str, int]] = {
    "C1": 50,
    "C2": 40,
    "C3": 35,
    "C4": 30,
    "C5": 15,
    "C6": 35,
    "C7": 25,
    "C8": 20,
}

HUNDRED_PCT_CATEGORIES: Final = ("C2", "C4", "C5", "C8")
"""Categories where ONLY an explicit owner keep is eligible (ruling #23:
C5/C8 always were 100%-review; C2/C4 joined after the genericity round).
An undecided case in any other category is eligible — machine-validated
and covered by the stratified sample (FREEZE.md §2.5)."""

_PROVISION_CAP: Final = 2  # FREEZE.md §2.3


class SelectionShortfallError(LovsporError):
    """A category cannot reach its frozen target — selection fails closed."""


class SelectionResult(BaseModel):
    selected: list[dict[str, Any]]
    report: dict[str, dict[str, Any]]


def eligible_cases(
    candidates: list[dict[str, Any]],
    decisions: dict[str, str],
) -> list[dict[str, Any]]:
    """Cases the owner's merged decisions leave in play.

    A decided case is eligible only when the decision is ``keep``; an
    undecided case is eligible only outside the 100%-review categories.
    """
    picked: list[dict[str, Any]] = []
    for case in candidates:
        decision = decisions.get(str(case["case_id"]))
        if decision is not None:
            if decision == "keep":
                picked.append(case)
        elif case["category"] not in HUNDRED_PCT_CATEGORIES:
            picked.append(case)
    return picked


def _numeric_id(case: dict[str, Any]) -> int:
    return int(str(case["case_id"]).rsplit("-", 1)[1])


def _provision(case: dict[str, Any]) -> tuple[str, str] | None:
    """Ground-truth provision key; None (cap-exempt) when there is none —
    C8's ground truth is abstention, not a provision."""
    slug = case.get("expected_act_slug")
    if slug is None:
        return None
    return (str(slug), str(case.get("expected_section_id")))


def select(
    candidates: list[dict[str, Any]],
    decisions: dict[str, str],
    targets: dict[str, int] | None = None,
) -> SelectionResult:
    """Walk each category's eligible cases in id order under the cap."""
    goals = FROZEN_TARGETS if targets is None else targets
    pool = eligible_cases(candidates, decisions)
    selected: list[dict[str, Any]] = []
    report: dict[str, dict[str, Any]] = {}
    for category in sorted(goals):
        rows = sorted(
            (c for c in pool if c["category"] == category),
            key=_numeric_id,
        )
        taken, skipped = _walk(rows, goals[category])
        report[category] = {
            "eligible": len(rows),
            "target": goals[category],
            "selected": [str(c["case_id"]) for c in taken],
            "cap_skipped": skipped,
        }
        if len(taken) < goals[category]:
            raise SelectionShortfallError(
                f"{category}: {len(taken)} selectable of target {goals[category]} "
                f"({len(rows)} eligible, {len(skipped)} cap-skipped)"
            )
        selected.extend(taken)
    return SelectionResult(selected=selected, report=report)


def _walk(
    rows: list[dict[str, Any]],
    target: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    taken: list[dict[str, Any]] = []
    skipped: list[str] = []
    per_provision: dict[tuple[str, str], int] = {}
    for case in rows:
        if len(taken) == target:
            break
        key = _provision(case)
        if key is not None and per_provision.get(key, 0) >= _PROVISION_CAP:
            skipped.append(str(case["case_id"]))
            continue
        if key is not None:
            per_provision[key] = per_provision.get(key, 0) + 1
        taken.append(case)
    return taken, skipped
