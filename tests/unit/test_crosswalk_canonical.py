"""Canonical JSONL of crosswalk records (#610, proposal item l)."""

from copy import deepcopy
from itertools import permutations

import pytest

from lovspor.crosswalk.canonical import (
    canonical_json,
    canonical_record,
    is_canonical,
    parse_records,
    serialize_records,
)
from lovspor.crosswalk.errors import CrosswalkContractError
from tests.unit.crosswalk_support import archive_pin, attested, besl_o


def _shuffled(record: dict) -> dict:
    out = dict(reversed(list(record.items())))
    out["evidence"] = list(reversed(record["evidence"]))
    pins = dict(record["source_pins"])
    pins["lovdata_archives"] = [archive_pin("lovtidend-avd1-2026.tar.bz2"), archive_pin()]
    out["source_pins"] = pins
    return out


def test_canonical_json_is_compact_sorted_and_utf8() -> None:
    assert canonical_json({"b": 1, "a": "ø"}) == '{"a":"ø","b":1}'


def test_serialization_ignores_production_order() -> None:
    records = [attested(), besl_o(), _shuffled(attested()) | {"record_revision": 2}]
    expected = serialize_records(records)
    for order in permutations(records):
        assert serialize_records(order) == expected


def test_records_sort_by_law_then_revision_and_lists_inside_records() -> None:
    second = attested() | {"record_revision": 2}
    lines = serialize_records([second, attested(), besl_o()]).decode().splitlines()
    keys = [
        (r["amending_lov_id"], r["record_revision"])
        for r in parse_records("\n".join(lines).encode() + b"\n")
    ]
    assert keys == [("LOV-2009-06-19-107", 1), ("LOV-2022-06-17-63", 1), ("LOV-2022-06-17-63", 2)]
    record = canonical_record(_shuffled(attested()))
    roles = [item["role"] for item in record["evidence"]]
    assert roles == sorted(roles)
    urls = [pin["source_url"] for pin in record["source_pins"]["lovdata_archives"]]
    assert urls == sorted(urls)


def test_canonical_record_adds_no_keys() -> None:
    assert canonical_record({"amending_lov_id": "x"}) == {"amending_lov_id": "x"}


def test_round_trip_is_byte_identical_and_canonical() -> None:
    data = serialize_records([attested(), besl_o()])
    assert data.endswith(b"\n")
    assert serialize_records(parse_records(data)) == data
    assert is_canonical(data)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.replace(b'":', b'": ', 1),
        lambda d: d.replace(b"\n", b"\r\n", 1),
        lambda d: b"\n".join(reversed(d.splitlines())) + b"\n",
    ],
    ids=["spaced", "crlf", "reordered"],
)
def test_non_canonical_bytes_are_detected(mutate) -> None:
    data = serialize_records([attested(), besl_o()])
    assert not is_canonical(mutate(data))


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"\xff\n", "not UTF-8"),
        (b"{}", "newline"),
        (b"{}\n\n", "blank line"),
        (b"[]\n", "not a JSON object"),
        (b"{\n", "not JSON"),
        (b'{"a":NaN}\n', "non-finite"),
    ],
)
def test_parse_refuses_malformed_files(data: bytes, message: str) -> None:
    with pytest.raises(CrosswalkContractError, match=message):
        parse_records(data)


def test_empty_file_is_zero_records() -> None:
    assert parse_records(b"") == []
    assert serialize_records([]) == b""


def test_nested_lists_sort_by_all_documented_keys_without_changing_input() -> None:
    record = attested()
    base = record["evidence"][0]
    evidence_items = [
        {**base, "upstream_id": upstream, "source_url": url}
        for upstream, url in [("b", "a"), ("a", "b"), ("a", "a")]
    ]
    record["evidence"] = evidence_items
    first = record["sanction_events"][0]
    later = {**first, "law_id": "LOV-2022-06-17-64"}
    record["sanction_events"] = [later, first]
    record["source_pins"]["lovdata_archives"] = [
        archive_pin("lovtidend-avd1-2026.tar.bz2"),
        archive_pin(),
    ]
    original = deepcopy(record)

    ordered = canonical_record(record)

    assert ordered["evidence"] == [evidence_items[2], evidence_items[1], evidence_items[0]]
    assert ordered["sanction_events"] == [first, later]
    reversed_lists = deepcopy(record)
    for field in ("evidence", "sanction_events"):
        reversed_lists[field].reverse()
    reversed_lists["source_pins"]["lovdata_archives"].reverse()
    assert serialize_records([record]) == serialize_records([reversed_lists])
    assert record == original


def test_parse_reports_the_line_of_a_later_json_error() -> None:
    with pytest.raises(CrosswalkContractError, match="line 2: not JSON"):
        parse_records(b'{}\n{"broken":}\n')
