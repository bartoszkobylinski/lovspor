"""Where the planned owner-change steps must sit in ``sync.yml``.

The rule is checked here against synthetic step lists, and against the real
``sync.yml`` as a strict xfail: the steps are not wired yet (the change to the
production workflow needs its own approval). When they are, the xfail turns
into a failure until its mark is removed, so the order is proven on the first
wiring commit.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]

EPOCH = "Record the temporal gate epoch before any corpus work"
APPLY = "Apply pinned owner changes"
REPAIR = "Flag under-embedded documents for re-embed"
SYNC = "Run lovspor sync"
CHECK = "Check pinned owner changes are unchanged"
AUDIT = "Audit corpus integrity before push"
SEAL_CHECK = "Verify starting head seal"
REPORT = "Report pinned owner changes not applied"


def order_problems(names: list[str]) -> list[str]:
    """Why a sync job's step names break the owner-change order; empty when they keep it.

    APPLY runs after the starting-head seal check and the epoch note (whose
    anchor must already be on origin) and before any corpus work, so the
    merged head is the one the sync attests. CHECK runs after the sync and
    before the audit, so nothing is pushed if the sync touched a pinned file.
    The refusal report runs after the audit."""
    required = [SEAL_CHECK, EPOCH, APPLY, REPAIR, SYNC, CHECK, AUDIT, REPORT]
    missing = [name for name in required if names.count(name) != 1]
    if missing:
        return [f"step missing or repeated: {name}" for name in missing]
    positions = [names.index(name) for name in required]
    return [
        f"{required[i]} must come before {required[i + 1]}"
        for i in range(len(required) - 1)
        if positions[i] > positions[i + 1]
    ]


GOOD = [
    "Clone lovverk corpus",
    SEAL_CHECK,
    "Configure git author for sync commits",
    EPOCH,
    APPLY,
    REPAIR,
    SYNC,
    CHECK,
    AUDIT,
    REPORT,
    "Compose publication seal",
]


def test_planned_order_is_accepted() -> None:
    assert order_problems(GOOD) == []


def _moved(name: str, before: str) -> list[str]:
    names = [step for step in GOOD if step != name]
    names.insert(names.index(before), name)
    return names


@pytest.mark.parametrize(
    ("names", "problem"),
    [
        (_moved(APPLY, SEAL_CHECK), f"{EPOCH} must come before {APPLY}"),
        (_moved(APPLY, EPOCH), f"{EPOCH} must come before {APPLY}"),
        (_moved(APPLY, AUDIT), f"{APPLY} must come before {REPAIR}"),
        (_moved(CHECK, SYNC), f"{SYNC} must come before {CHECK}"),
        (_moved(AUDIT, CHECK), f"{CHECK} must come before {AUDIT}"),
        (_moved(REPORT, AUDIT), f"{AUDIT} must come before {REPORT}"),
    ],
)
def test_misplaced_step_is_named(names: list[str], problem: str) -> None:
    assert problem in order_problems(names)


def test_missing_or_repeated_step_is_named() -> None:
    assert order_problems([n for n in GOOD if n != CHECK]) == [f"step missing or repeated: {CHECK}"]
    assert f"step missing or repeated: {APPLY}" in order_problems([*GOOD, APPLY])


def _sync_steps() -> list[str]:
    workflow: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github/workflows/sync.yml").read_text(encoding="utf-8")
    )
    return [step.get("name", "") for step in workflow["jobs"]["sync"]["steps"]]


def test_existing_sync_steps_keep_their_order() -> None:
    """The anchors the new steps go between exist today, in this order."""
    names = _sync_steps()
    anchors = [SEAL_CHECK, EPOCH, REPAIR, SYNC, AUDIT]
    assert [names.index(name) for name in anchors] == sorted(names.index(name) for name in anchors)


@pytest.mark.xfail(strict=True, reason="owner-change steps are not wired into sync.yml yet")
def test_sync_workflow_runs_owner_change_steps_in_order() -> None:
    assert order_problems(_sync_steps()) == []
