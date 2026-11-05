"""Raw evidence integrity is checked apart from semantic identity (ADR-0017 D5, D8).

A record whose parsed identities are reproduced but whose claimed raw bytes or
archive pins are not in this capture is byte-only drift: not a contract failure,
but never certified and never promoted.
"""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.canonical import parse_records, serialize_records
from lovspor.crosswalk.check import check
from lovspor.crosswalk.commands import check_impl
from lovspor.crosswalk.release import Approval, promote
from tests.unit.crosswalk_support import PRODUCER, make_capture

LAW = "LOV-2022-06-17-63"


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return make_capture(tmp_path, httpx_mock)


@pytest.fixture
def built(capture_dir: Path) -> bytes:
    return build(capture_dir, BuildOptions(producer_commit=PRODUCER))


def _edited(built: bytes, edit: object) -> bytes:
    records = parse_records(built)
    record = next(r for r in records if r["amending_lov_id"] == LAW)
    edit(record)  # type: ignore[operator]
    return serialize_records(records)


def _approval(data: bytes) -> Approval:
    return Approval.model_validate(
        {
            "input_sha256": sha256(data).hexdigest(),
            "approved_by": "synthetic test approval",
            "approved_on": "2026-11-03",
            "R1": "approved",
            "R2": "approved",
        }
    )


def test_a_fresh_build_has_no_drift(built: bytes, capture_dir: Path) -> None:
    report = check(built, cache=capture_dir)
    assert (report.problems, report.unverified, report.drifted) == ([], [], [])


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_a_fabricated_body_digest_is_drift_never_certified(
    built: bytes, capture_dir: Path, index: int
) -> None:
    def fabricate(record: dict) -> None:
        record["evidence"][index]["body_sha256"] = "f" * 64

    report = check(_edited(built, fabricate), cache=capture_dir)
    assert report.passed
    assert report.drifted == [LAW]
    assert LAW not in report.unverified


@pytest.mark.parametrize(
    ("field", "value"),
    [("sha256", "e" * 64), ("size_bytes", 1), ("last_modified", "2020-01-01T00:00:00Z")],
)
def test_an_archive_pin_not_in_the_capture_is_drift(
    built: bytes, capture_dir: Path, field: str, value: object
) -> None:
    def repin(record: dict) -> None:
        record["source_pins"]["lovdata_archives"][0][field] = value

    report = check(_edited(built, repin), cache=capture_dir)
    assert report.drifted == [LAW]


def test_a_dropped_archive_pin_is_drift(built: bytes, capture_dir: Path) -> None:
    def drop(record: dict) -> None:
        pins = record["source_pins"]["lovdata_archives"]
        record["source_pins"]["lovdata_archives"] = [
            p for p in pins if "2026" not in p["source_url"]
        ]

    report = check(_edited(built, drop), cache=capture_dir)
    assert report.drifted == [LAW]


@pytest.mark.parametrize("field", ["retrieved_at", "licence_basis"])
def test_accepted_archive_metadata_cannot_bypass_capture_provenance(
    built: bytes, capture_dir: Path, field: str
) -> None:
    # check._raw claims the entire archive pin, not just its body digest.
    # Both licence bases are accepted under R2 approved; neither that approval
    # nor a schema-valid timestamp may certify metadata absent from the capture.
    records = parse_records(built)
    record = next(r for r in records if r["amending_lov_id"] == LAW)
    pin = record["source_pins"]["lovdata_archives"][0]
    if field == "retrieved_at":
        # Keep the UTC day so observed_on remains valid independently of drift.
        value = pin[field][:11] + "23:59:59Z"
    else:
        value = "stated_for_endpoint" if pin[field] == "stated_for_file" else "stated_for_file"
    assert value != pin[field]
    pin[field] = value
    data = serialize_records(records)

    report = check(data, cache=capture_dir)

    assert report.problems == []
    assert report.unverified == []
    assert report.drifted == [LAW]
    promoted, counts = promote(data, _approval(data), capture_dir)
    assert counts == {
        "promoted": 2,
        "not an active attested record": 5,
        "raw bytes are not those of this capture": 1,
    }
    assert [r for r in parse_records(promoted) if r["amending_lov_id"] == LAW] == [record]
    assert set(data.splitlines()) <= set(promoted.splitlines())


def test_a_changed_identity_is_a_problem_not_drift(built: bytes, capture_dir: Path) -> None:
    def tamper(record: dict) -> None:
        record["evidence"][0]["parsed_identity_sha256"] = "f" * 64
        record["evidence"][0]["body_sha256"] = "f" * 64

    report = check(_edited(built, tamper), cache=capture_dir)
    assert report.problems == [f"{LAW}: evidence identities are not reproduced from the capture"]
    assert report.drifted == []


def test_drifted_records_are_never_promoted(built: bytes, capture_dir: Path) -> None:
    def fabricate(record: dict) -> None:
        record["evidence"][0]["body_sha256"] = "f" * 64

    data = _edited(built, fabricate)
    promoted, counts = promote(data, _approval(data), capture_dir)
    assert counts["raw bytes are not those of this capture"] == 1
    candidates = {
        r["amending_lov_id"]
        for r in parse_records(promoted)
        if r["release_state"] == "release_candidate"
    }
    assert LAW not in candidates
    assert counts["promoted"] == 2


def test_check_command_reports_drift(built: bytes, capture_dir: Path, tmp_path: Path) -> None:
    def fabricate(record: dict) -> None:
        record["evidence"][0]["body_sha256"] = "f" * 64

    path = tmp_path / "in.jsonl"
    path.write_bytes(_edited(built, fabricate))
    assert check_impl(path, None, capture_dir) == (
        "ok: 8 records, 0 not reproduced from a capture, 1 with raw bytes not in this capture"
    )
    assert check_impl(path, None, None) == "ok: 8 records"


@pytest.mark.parametrize("drift_revision", [1, 2])
def test_promotion_judges_current_raw_bytes_and_preserves_history(
    built: bytes, capture_dir: Path, drift_revision: int
) -> None:
    # check.current_records and release._promotions explicitly judge the highest
    # revision. D6 still requires every earlier revision to remain byte-identical.
    records = parse_records(built)
    historical = next(record for record in records if record["amending_lov_id"] == LAW)
    current = deepcopy(historical)
    current["record_revision"] = 2
    drifted = historical if drift_revision == 1 else current
    drifted["evidence"][0]["body_sha256"] = "f" * 64
    previous = serialize_records(records)
    data = serialize_records([*records, current])

    report = check(data, previous=previous, cache=capture_dir)

    assert report.passed, report.problems
    assert report.unverified == []
    assert report.drifted == ([LAW] if drift_revision == 2 else [])

    promoted, counts = promote(data, _approval(data), capture_dir)

    assert check(promoted, previous=data, cache=capture_dir).passed
    assert set(data.splitlines()) <= set(promoted.splitlines())
    law_history = [r for r in parse_records(promoted) if r["amending_lov_id"] == LAW]
    assert law_history[:2] == [historical, current]
    if drift_revision == 1:
        assert counts == {"promoted": 3, "not an active attested record": 5}
        assert law_history[2:] == [
            {**current, "record_revision": 3, "release_state": "release_candidate"}
        ]
    else:
        assert counts == {
            "promoted": 2,
            "not an active attested record": 5,
            "raw bytes are not those of this capture": 1,
        }
        assert len(law_history) == 2
