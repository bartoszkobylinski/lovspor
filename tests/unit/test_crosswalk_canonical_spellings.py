"""Alternate JSON spellings must not bypass the producer's canonical-byte gate."""

import pytest

from lovspor.crosswalk.canonical import canonical_json, parse_records, serialize_records
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


@pytest.mark.parametrize("nested", ["evidence", "archive-pin"])
@pytest.mark.parametrize("overwritten", [False, True], ids=["repeated", "overwritten"])
def test_check_refuses_duplicate_digest_keys_inside_provenance(
    nested: str, overwritten: bool
) -> None:
    # docs/crosswalk-producer.md requires rejection of anything noncanonical,
    # including duplicate keys hidden inside evidence and source pins.
    record = attested()
    if nested == "evidence":
        item = record["evidence"][0]
        field = "body_sha256"
    else:
        item = record["source_pins"]["lovdata_archives"][0]
        field = "sha256"
    canonical = serialize_records([record])
    assert check(canonical).passed
    original = canonical_json(field).encode() + b":" + canonical_json(item[field]).encode()
    earlier = "0" * 64 if overwritten else item[field]
    if overwritten:
        assert earlier != item[field]
    duplicate = canonical_json(field).encode() + b":" + canonical_json(earlier).encode()
    alternate = canonical.replace(original, duplicate + b"," + original, 1)
    assert alternate != canonical
    assert parse_records(alternate) == parse_records(canonical)

    report = check(alternate)

    assert not report.passed
    assert report.problems == ["file is not in canonical form"]
    assert report.unverified == []
    assert report.drifted == []
