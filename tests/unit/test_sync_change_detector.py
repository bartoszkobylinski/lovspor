"""Tests for lovspor.sync.change_detector.

Each output list is asserted on exact contents and order, because
deterministic sorting is part of the contract: callers may rely on the
order for reproducible commit messages, log lines, and reporting.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lovspor.storage.manifest import Manifest, ManifestRecord
from lovspor.sync.change_detector import (
    ChangeSet,
    detect_changes,
    force_rerender_changeset,
    stale_render_changeset,
)


def _record(
    *,
    xml_hash: str,
    status: str = "current",
    renderer_version: int | None = None,
) -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov",
        xml_hash=xml_hash,
        markdown_path="lover/x.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 22, 1, 31, tzinfo=UTC),
        status=status,
        renderer_version=renderer_version,
    )


def _manifest(documents: dict[str, ManifestRecord]) -> Manifest:
    return Manifest(
        generated_at=datetime(2026, 4, 22, 6, 0, tzinfo=UTC),
        documents=documents,
    )


def test_change_set_is_frozen() -> None:
    cs = ChangeSet(new=[], changed=[], removed=[], unchanged=[])
    with pytest.raises(ValidationError):
        cs.new = ["x"]  # type: ignore[misc]


def test_change_set_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ChangeSet.model_validate(
            {
                "new": [],
                "changed": [],
                "removed": [],
                "unchanged": [],
                "future_bucket": [],
            },
        )


def test_empty_inputs_yield_empty_buckets() -> None:
    cs = detect_changes({}, _manifest({}))
    assert cs == ChangeSet(new=[], changed=[], removed=[], unchanged=[])


def test_all_upstream_new_when_manifest_empty() -> None:
    upstream = {"b": "h2", "a": "h1"}
    cs = detect_changes(upstream, _manifest({}))
    assert cs.new == ["a", "b"]
    assert cs.changed == []
    assert cs.removed == []
    assert cs.unchanged == []


def test_all_manifest_removed_when_upstream_empty() -> None:
    manifest = _manifest(
        {"b": _record(xml_hash="h2"), "a": _record(xml_hash="h1")},
    )
    cs = detect_changes({}, manifest)
    assert cs.new == []
    assert cs.changed == []
    assert cs.removed == ["a", "b"]
    assert cs.unchanged == []


def test_unchanged_when_hashes_match() -> None:
    upstream = {"x": "abc"}
    manifest = _manifest({"x": _record(xml_hash="abc")})
    cs = detect_changes(upstream, manifest)
    assert cs.unchanged == ["x"]
    assert cs.new == []
    assert cs.changed == []
    assert cs.removed == []


def test_changed_when_hashes_differ() -> None:
    upstream = {"x": "new"}
    manifest = _manifest({"x": _record(xml_hash="old")})
    cs = detect_changes(upstream, manifest)
    assert cs.changed == ["x"]
    assert cs.new == []
    assert cs.removed == []
    assert cs.unchanged == []


def test_mixed_scenario_with_one_of_each_category() -> None:
    upstream = {
        "newone": "h_new",
        "changedone": "h_now",
        "unchangedone": "h_same",
    }
    manifest = _manifest(
        {
            "changedone": _record(xml_hash="h_then"),
            "unchangedone": _record(xml_hash="h_same"),
            "removedone": _record(xml_hash="h_gone"),
        },
    )
    cs = detect_changes(upstream, manifest)
    assert cs.new == ["newone"]
    assert cs.changed == ["changedone"]
    assert cs.removed == ["removedone"]
    assert cs.unchanged == ["unchangedone"]


def test_each_bucket_is_sorted_alphabetically() -> None:
    upstream = {
        "z_new": "h",
        "a_new": "h",
        "m_changed": "h_now",
        "b_changed": "h_now",
        "k_unchanged": "h_same",
        "c_unchanged": "h_same",
    }
    manifest = _manifest(
        {
            "m_changed": _record(xml_hash="h_then"),
            "b_changed": _record(xml_hash="h_then"),
            "k_unchanged": _record(xml_hash="h_same"),
            "c_unchanged": _record(xml_hash="h_same"),
            "z_removed": _record(xml_hash="h"),
            "a_removed": _record(xml_hash="h"),
        },
    )
    cs = detect_changes(upstream, manifest)
    assert cs.new == ["a_new", "z_new"]
    assert cs.changed == ["b_changed", "m_changed"]
    assert cs.removed == ["a_removed", "z_removed"]
    assert cs.unchanged == ["c_unchanged", "k_unchanged"]


def test_manifest_record_with_removed_status_is_not_a_match() -> None:
    """A doc that was previously removed and reappears upstream is 'new',
    not 'changed' against its stale removed-state hash."""
    upstream = {"backagain": "h_now"}
    manifest = _manifest(
        {"backagain": _record(xml_hash="h_then", status="removed")},
    )
    cs = detect_changes(upstream, manifest)
    assert cs.new == ["backagain"]
    assert cs.changed == []
    assert cs.removed == []
    assert cs.unchanged == []


def test_manifest_record_with_removed_status_does_not_appear_in_removed() -> None:
    """An already-removed doc still missing upstream stays out of the
    'removed' bucket (it's already in that state)."""
    manifest = _manifest(
        {"oldgone": _record(xml_hash="h", status="removed")},
    )
    cs = detect_changes({}, manifest)
    assert cs.removed == []
    assert cs.new == []
    assert cs.changed == []
    assert cs.unchanged == []


def test_buckets_are_disjoint() -> None:
    """No doc_id may appear in more than one bucket. Invariant of the
    classification."""
    upstream = {
        "a": "h1",
        "b": "h2_new",
        "c": "h3",
    }
    manifest = _manifest(
        {
            "a": _record(xml_hash="h1"),
            "b": _record(xml_hash="h2_old"),
            "d": _record(xml_hash="h4"),
        },
    )
    cs = detect_changes(upstream, manifest)
    seen: set[str] = set()
    for bucket in (cs.new, cs.changed, cs.removed, cs.unchanged):
        for doc_id in bucket:
            assert doc_id not in seen
            seen.add(doc_id)


def test_detect_changes_is_deterministic_across_calls() -> None:
    upstream = {"x": "h", "y": "h"}
    manifest = _manifest(
        {
            "x": _record(xml_hash="h_old"),
            "z": _record(xml_hash="h"),
        },
    )
    cs_a = detect_changes(upstream, manifest)
    cs_b = detect_changes(upstream, manifest)
    assert cs_a == cs_b


def test_detect_changes_does_not_mutate_inputs() -> None:
    upstream = {"x": "h"}
    manifest = _manifest({"y": _record(xml_hash="h_old")})
    upstream_before = dict(upstream)
    docs_before = dict(manifest.documents)
    detect_changes(upstream, manifest)
    assert upstream == upstream_before
    assert manifest.documents == docs_before


def test_force_rerender_promotes_unchanged_to_changed() -> None:
    """A renderer fix leaves upstream XML untouched, so every hash still matches
    and the corpus never picks the fix up. Promotion is what makes it reach disk."""
    changes = ChangeSet(new=["n"], changed=["c"], removed=["r"], unchanged=["u1", "u2"])

    forced = force_rerender_changeset(changes, exclude=set())

    assert forced.changed == ["c", "u1", "u2"]
    assert forced.unchanged == []
    assert forced.new == ["n"]
    assert forced.removed == ["r"]


def test_force_rerender_leaves_renamed_docs_in_unchanged() -> None:
    """The rename loop already re-renders renamed docs at their new path.
    Promoting them too would write the same document from two plans."""
    changes = ChangeSet(new=[], changed=[], removed=[], unchanged=["renamed", "plain"])

    forced = force_rerender_changeset(changes, exclude={"renamed"})

    assert forced.changed == ["plain"]
    assert forced.unchanged == ["renamed"]


def test_force_rerender_does_not_touch_new_or_removed() -> None:
    """Forcing a re-render must not resurrect a tombstone, nor make a
    corpus-wide deletion look like a corpus-wide rewrite."""
    changes = ChangeSet(new=["fresh"], changed=[], removed=["gone"], unchanged=["u"])

    forced = force_rerender_changeset(changes, exclude=set())

    assert forced.new == ["fresh"]
    assert forced.removed == ["gone"]


def test_force_rerender_output_is_sorted_and_disjoint() -> None:
    """Sorted order is part of ChangeSet's contract; the four lists stay disjoint."""
    changes = ChangeSet(new=[], changed=["z"], removed=[], unchanged=["a", "m"])

    forced = force_rerender_changeset(changes, exclude=set())

    assert forced.changed == ["a", "m", "z"]
    assert not set(forced.changed) & set(forced.unchanged)


def test_force_rerender_is_idempotent() -> None:
    changes = ChangeSet(new=[], changed=[], removed=[], unchanged=["a", "b"])

    once = force_rerender_changeset(changes, exclude=set())
    twice = force_rerender_changeset(once, exclude=set())

    assert once == twice


def test_stale_render_promotes_only_stale_stamped_unchanged() -> None:
    """A renderer bump makes docs stale without touching their XML. Only the
    unchanged docs whose stamp differs from the current version are promoted;
    docs already on the current version stay unchanged (nothing to re-render)."""
    changes = ChangeSet(new=["n"], changed=["c"], removed=["r"], unchanged=["old", "cur"])
    manifest = _manifest(
        {
            "old": _record(xml_hash="a" * 64, renderer_version=1),
            "cur": _record(xml_hash="b" * 64, renderer_version=2),
        },
    )

    stale = stale_render_changeset(changes, manifest, current_version=2, exclude=set())

    assert stale.changed == ["c", "old"]
    assert stale.unchanged == ["cur"]
    assert stale.new == ["n"]
    assert stale.removed == ["r"]


def test_stale_render_treats_none_stamp_as_stale() -> None:
    """A record predating the stamp (renderer_version is None) is stale and
    re-rendered once."""
    changes = ChangeSet(new=[], changed=[], removed=[], unchanged=["legacy"])
    manifest = _manifest({"legacy": _record(xml_hash="a" * 64, renderer_version=None)})

    stale = stale_render_changeset(changes, manifest, current_version=1, exclude=set())

    assert stale.changed == ["legacy"]
    assert stale.unchanged == []


def test_stale_render_excludes_renamed() -> None:
    """Renamed docs are re-rendered at their new path by the rename loop, so
    they must not be promoted here even when their stamp is stale."""
    changes = ChangeSet(new=[], changed=[], removed=[], unchanged=["renamed", "plain"])
    manifest = _manifest(
        {
            "renamed": _record(xml_hash="a" * 64, renderer_version=None),
            "plain": _record(xml_hash="b" * 64, renderer_version=None),
        },
    )

    stale = stale_render_changeset(changes, manifest, current_version=1, exclude={"renamed"})

    assert stale.changed == ["plain"]
    assert stale.unchanged == ["renamed"]


def test_stale_render_does_not_touch_new_or_removed() -> None:
    changes = ChangeSet(new=["fresh"], changed=[], removed=["gone"], unchanged=["u"])
    manifest = _manifest({"u": _record(xml_hash="a" * 64, renderer_version=1)})

    stale = stale_render_changeset(changes, manifest, current_version=1, exclude=set())

    assert stale.new == ["fresh"]
    assert stale.removed == ["gone"]
    assert stale.changed == []
    assert stale.unchanged == ["u"]


def test_stale_render_output_is_sorted_and_disjoint() -> None:
    changes = ChangeSet(new=[], changed=["z"], removed=[], unchanged=["m", "a"])
    manifest = _manifest(
        {
            "m": _record(xml_hash="a" * 64, renderer_version=None),
            "a": _record(xml_hash="b" * 64, renderer_version=None),
        },
    )

    stale = stale_render_changeset(changes, manifest, current_version=1, exclude=set())

    assert stale.changed == ["a", "m", "z"]
    assert not set(stale.changed) & set(stale.unchanged)


def test_stale_render_is_idempotent_after_promotion() -> None:
    """Once promoted and (conceptually) re-rendered to the current version, a
    second pass with everything current promotes nothing."""
    changes = ChangeSet(new=[], changed=[], removed=[], unchanged=["a", "b"])
    all_current = _manifest(
        {
            "a": _record(xml_hash="a" * 64, renderer_version=3),
            "b": _record(xml_hash="b" * 64, renderer_version=3),
        },
    )

    stale = stale_render_changeset(changes, all_current, current_version=3, exclude=set())

    assert stale.changed == []
    assert stale.unchanged == ["a", "b"]


def test_stale_render_missing_manifest_record_is_not_promoted() -> None:
    """A doc in unchanged but absent from the manifest cannot be judged stale;
    leave it unchanged rather than guessing (defensive — detect_changes keeps
    unchanged and manifest.current in lockstep, but the transform must not
    KeyError if that ever drifts)."""
    changes = ChangeSet(new=[], changed=[], removed=[], unchanged=["ghost"])
    manifest = _manifest({})

    stale = stale_render_changeset(changes, manifest, current_version=1, exclude=set())

    assert stale.changed == []
    assert stale.unchanged == ["ghost"]
