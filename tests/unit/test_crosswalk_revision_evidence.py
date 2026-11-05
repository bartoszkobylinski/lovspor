"""Evidence identity changes remain substantive under the D6/D8 revision rules."""

from copy import deepcopy

import pytest

from lovspor.crosswalk.build import append_revisions
from lovspor.crosswalk.canonical import serialize_records
from tests.unit.crosswalk_support import attested


@pytest.mark.parametrize("role", ["law_identity", "decision_publication", "decision_case"])
@pytest.mark.parametrize(
    ("field", "value"),
    [("parsed_identity_sha256", "f" * 64), ("parser_version", "next-parser-version")],
)
def test_changed_evidence_appends_once_and_preserves_inputs(
    role: str, field: str, value: str
) -> None:
    previous = [attested()]
    original = deepcopy(previous)
    old_bytes = serialize_records(previous)
    fresh = deepcopy(previous)
    evidence = next(item for item in fresh[0]["evidence"] if item["role"] == role)
    evidence[field] = value
    fresh_before = deepcopy(fresh)

    history = append_revisions(fresh, previous)

    assert history == [original[0], {**fresh_before[0], "record_revision": 2}]
    assert serialize_records(history[:1]) == old_bytes
    assert previous == original
    assert fresh == fresh_before
    assert append_revisions(fresh, history) == history


@pytest.mark.parametrize("role", ["law_identity", "decision_publication", "decision_case"])
@pytest.mark.parametrize(
    ("field", "value"),
    [("body_sha256", "f" * 64), ("retrieved_at", "2026-11-04T12:00:00Z")],
)
def test_capture_only_evidence_drift_preserves_history_without_mutating_inputs(
    role: str, field: str, value: str
) -> None:
    # D8 compares parsed identities; build.substance explicitly excludes these
    # two capture-specific fields. Exercise each independently, so one exclusion
    # cannot hide a missing exclusion of the other.
    previous = [attested()]
    original = deepcopy(previous)
    old_bytes = serialize_records(previous)
    fresh = deepcopy(previous)
    evidence = next(item for item in fresh[0]["evidence"] if item["role"] == role)
    assert evidence[field] != value
    evidence[field] = value
    fresh_before = deepcopy(fresh)

    history = append_revisions(fresh, previous)

    assert history == original
    assert serialize_records(history) == old_bytes
    assert previous == original
    assert fresh == fresh_before
    assert append_revisions(fresh, history) == history


def test_return_to_earlier_evidence_appends_a_new_revision() -> None:
    """D6 compares against the latest revision, even when old evidence returns."""
    first = attested()
    second = deepcopy(first)
    second["record_revision"] = 2
    second["evidence"][0]["parsed_identity_sha256"] = "f" * 64
    previous = [second, first]  # Input order does not determine the latest revision.
    original = deepcopy(previous)
    fresh = deepcopy(first)

    history = append_revisions([fresh], previous)

    assert history == [*original, {**first, "record_revision": 3}]
    assert serialize_records(history[:2]) == serialize_records(original)
    assert previous == original
    assert fresh == first
    assert append_revisions([fresh], history) == history


def test_capture_only_drift_preserves_every_revision_of_latest_evidence() -> None:
    """D6/D8 must retain history without appending for new capture bytes or times."""
    first = attested()
    second = deepcopy(first)
    second["record_revision"] = 2
    second["evidence"][0]["parsed_identity_sha256"] = "f" * 64
    previous = [second, first]
    original = deepcopy(previous)
    fresh = deepcopy(second)
    fresh["record_revision"] = 1
    fresh["observed_on"] = "2026-11-04"
    fresh["source_pins"]["producer_commit"] = "b" * 40
    for evidence in fresh["evidence"]:
        evidence["body_sha256"] = "e" * 64
        evidence["retrieved_at"] = "2026-11-04T00:00:00Z"
    fresh_before = deepcopy(fresh)

    history = append_revisions([fresh], previous)

    assert history == original
    assert serialize_records(history) == serialize_records(original)
    assert previous == original
    assert fresh == fresh_before


@pytest.mark.parametrize("reverse_previous", [False, True])
@pytest.mark.parametrize("reverse_fresh", [False, True])
def test_mixed_batch_tracks_revisions_per_law_and_keeps_absent_history(
    reverse_previous: bool, reverse_fresh: bool
) -> None:
    """D6: each act advances independently; absent acts retain all revisions."""
    # Minimal records isolate history bookkeeping from schema/evidence validation.
    unchanged = {"amending_lov_id": "LOV-2022-06-17-63", "record_revision": 9, "status": "a"}
    changed = {"amending_lov_id": "LOV-2022-06-17-64", "record_revision": 2, "status": "a"}
    absent = {"amending_lov_id": "LOV-2022-06-17-65", "record_revision": 20, "status": "a"}
    previous = [
        unchanged,
        changed,
        absent,
        {**unchanged, "record_revision": 1, "status": "old"},
        {**changed, "record_revision": 1, "status": "old"},
        {**absent, "record_revision": 1, "status": "old"},
    ]
    new = {"amending_lov_id": "LOV-2022-06-17-66", "record_revision": 1, "status": "a"}
    fresh = [
        {**unchanged, "record_revision": 1},
        {**changed, "record_revision": 1, "status": "b"},
        new,
    ]
    if reverse_previous:
        previous.reverse()
    if reverse_fresh:
        fresh.reverse()
    original_previous, original_fresh = deepcopy(previous), deepcopy(fresh)

    history = append_revisions(fresh, previous)

    assert history[: len(previous)] == original_previous
    assert sorted(history[len(previous) :], key=lambda r: r["amending_lov_id"]) == [
        {**changed, "record_revision": 3, "status": "b"},
        new,
    ]
    assert previous == original_previous
    assert fresh == original_fresh
    assert append_revisions(fresh, history) == history
