"""Which fields a tombstone keeps and which it drops (issue #401).

``_tombstone`` builds a fresh ``ManifestRecord`` rather than copying the prior
one, so every field it does not name falls back to its default. The owner
decided (2026-09-26) to keep that behaviour; these tests pin the exact split so
a change to it — or a new ``ManifestRecord`` field nobody classified — fails
here instead of silently changing what a removed law's record carries.
"""

from datetime import UTC, datetime

import pytest

from lovspor.storage.manifest import ManifestRecord
from lovspor.sync.orchestrator import _tombstone

KEPT = frozenset(
    {"doc_type", "xml_hash", "markdown_path", "source_dataset", "last_seen", "slug", "title"}
)
DROPPED = frozenset(
    {
        "total_changes",
        "last_changed",
        "eu_basis",
        "embedding_hash",
        "embedding_space",
        "embedding_space_id",
        "embedding_input_hash",
        "renderer_version",
    }
)
SET_BY_TOMBSTONE = frozenset({"status", "removed_reason"})


def _fully_populated() -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/aksjeloven.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 9, 1, 4, 0, tzinfo=UTC),
        status="current",
        slug="aksjeloven",
        title="Lov om aksjeselskaper",
        total_changes=7,
        last_changed="2026-08-01",
        eu_basis=["32017L1132"],
        embedding_hash="a" * 64,
        embedding_space="openai:text-embedding-3-large:3072",
        embedding_space_id="b" * 32,
        embedding_input_hash="c" * 64,
        renderer_version=5,
        removed_reason=None,
    )


def test_every_manifest_field_is_classified() -> None:
    assert set(ManifestRecord.model_fields) == KEPT | DROPPED | SET_BY_TOMBSTONE
    assert not KEPT & DROPPED


def test_tombstone_keeps_exactly_the_kept_fields() -> None:
    prior = _fully_populated()
    tomb = _tombstone(prior, "upstream_placeholder")
    for name in KEPT:
        assert getattr(tomb, name) == getattr(prior, name), name
    assert tomb.status == "removed"
    assert tomb.removed_reason == "upstream_placeholder"


def test_tombstone_drops_exactly_the_dropped_fields() -> None:
    tomb = _tombstone(_fully_populated())
    assert {name: getattr(tomb, name) for name in DROPPED} == dict.fromkeys(DROPPED)


def _docstring_halves() -> tuple[str, str]:
    kept, _, dropped = (_tombstone.__doc__ or "").partition("Drops ")
    return kept, dropped


@pytest.mark.parametrize("name", sorted(KEPT))
def test_tombstone_docstring_lists_kept_field_as_kept(name: str) -> None:
    kept, dropped = _docstring_halves()
    assert f"``{name}``" in kept
    assert f"``{name}``" not in dropped


@pytest.mark.parametrize("name", sorted(DROPPED))
def test_tombstone_docstring_lists_dropped_field_as_dropped(name: str) -> None:
    kept, dropped = _docstring_halves()
    assert f"``{name}``" in dropped
    assert f"``{name}``" not in kept
