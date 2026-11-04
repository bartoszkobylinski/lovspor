"""Alternate JSON spellings must not bypass the producer's canonical-byte gate."""

import pytest

from lovspor.crosswalk.canonical import parse_records, serialize_records
from lovspor.crosswalk.check import check
from tests.unit.crosswalk_support import attested


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        (
            b'"record_revision":1',
            b'"record_revision":1,"record_revision":1',
        ),
        (
            b'"record_revision":1',
            b'"record_revision":99,"record_revision":1',
        ),
        (
            b'"record_revision":1',
            b'"record_\\u0072evision":1',
        ),
        (
            b'"release_state":"research_only"',
            b'"release_state":"\\u0072esearch_only"',
        ),
    ],
    ids=["repeated-key", "overwritten-key", "escaped-key", "escaped-value"],
)
def test_check_refuses_alternate_spellings_of_an_otherwise_valid_record(
    original: bytes, replacement: bytes
) -> None:
    # docs/crosswalk-producer.md: check fails on "anything that is not canonical".
    # Keep the parsed record identical so schema validation cannot mask a missing
    # byte-level canonicality check.
    canonical = serialize_records([attested()])
    assert check(canonical).passed
    assert canonical.count(original) == 1
    alternate = canonical.replace(original, replacement, 1)
    assert alternate != canonical
    assert parse_records(alternate) == parse_records(canonical)

    report = check(alternate)

    assert not report.passed
    assert report.problems == ["file is not in canonical form"]
    assert report.unverified == []
    assert report.drifted == []
