"""Tests for lovspor.sync.change_detector.

Each output list is asserted on exact contents and order, because
deterministic sorting is part of the contract: callers may rely on the
order for reproducible commit messages, log lines, and reporting.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lovspor.storage.manifest import Manifest, ManifestRecord
from lovspor.sync.change_detector import ChangeSet, detect_changes


def _record(*, xml_hash: str, status: str = "current") -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov",
        xml_hash=xml_hash,
        markdown_path="lover/x.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 22, 1, 31, tzinfo=UTC),
        status=status,
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
