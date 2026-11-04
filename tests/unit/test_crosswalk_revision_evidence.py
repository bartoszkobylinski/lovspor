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
