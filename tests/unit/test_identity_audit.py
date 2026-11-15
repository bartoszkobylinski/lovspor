"""Outcome sentences and counts of the restored-tombstone collision audit (#871)."""

import pytest

from lovspor.identity.audit import CollisionAudit, _outcome, outcome_counts
from lovspor.identity.migration import Rename, suffix_note
from lovspor.identity.registry import SlugOwner, SlugRegistry


def _audit(side: str, status: str = "current", owner: str = "sf-1") -> CollisionAudit:
    return CollisionAudit(
        directory="forskrifter",
        slug="x",
        restored_doc_id="sf-1",
        last_seen_commit="c1",
        missing_since_commit="c2",
        holder_at_base="sf-2",
        holder_status=status,
        owner=owner,
        owning_side=side,
        outcome="",
    )


@pytest.mark.parametrize(
    ("audit", "renamed_to", "recorded", "expected"),
    [
        (
            _audit("restored"),
            "x-2026",
            False,
            "the holder moves to x-2026; the slug stays with sf-1",
        ),
        (
            _audit("holder", owner="sf-2"),
            None,
            True,
            "the holder keeps the slug; the restored document is recorded as a prior holder",
        ),
        (
            _audit("holder", owner="sf-2"),
            None,
            False,
            "the holder keeps the slug; the restored document held it later, a tombstone",
        ),
        (
            _audit("other", "removed", "sf-3"),
            None,
            False,
            "no file moves: both are tombstones; the slug stays with sf-3",
        ),
        (
            _audit("other", owner="sf-3"),
            None,
            False,
            "the current holder stays; the slug stays with sf-3",
        ),
    ],
)
def test_outcome_names_what_happens_at_the_address(
    audit: CollisionAudit, renamed_to: str | None, recorded: bool, expected: str
) -> None:
    assert _outcome(audit, renamed_to, recorded) == expected


def test_outcome_counts_per_side_and_moves() -> None:
    audits = [
        _audit("restored").model_copy(update={"outcome": "the holder moves to x-2026"}),
        _audit("holder").model_copy(update={"outcome": "the holder keeps the slug"}),
        _audit("holder").model_copy(update={"outcome": "the holder keeps the slug"}),
    ]

    assert outcome_counts(audits) == {
        "holder_moves": 1,
        "owned_by_holder": 2,
        "owned_by_restored": 1,
    }


def test_suffix_note_explains_a_doc_id_fallback() -> None:
    owner = SlugOwner(owner="sf-9", assigned_at="t")
    registry = (
        SlugRegistry()
        .assign("forskrifter", "x-2026", owner)
        .assign("forskrifter", "x-2026-06-01", owner)
    )
    fallback = Rename(
        doc_id="sf-20260601-0002",
        directory="forskrifter",
        from_slug="x",
        to_slug="x-sf-20260601-0002",
    )
    readable = fallback.model_copy(update={"to_slug": "x-2026"})

    note = suffix_note(registry, "forskrifter", fallback)

    assert note == (
        "readable suffixes unavailable: x-2026 (owned by sf-9), x-2026-06-01 (owned by sf-9)"
    )
    assert suffix_note(registry, "forskrifter", readable) is None
