"""A release_candidate manifest needs verified provenance; research manifests say they lack it."""

from hashlib import sha256
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.cache import MANIFEST
from lovspor.crosswalk.canonical import parse_records, serialize_records
from lovspor.crosswalk.commands import manifest_impl
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.crosswalk.manifest import UNVERIFIED, build_manifest
from lovspor.crosswalk.release import Approval, promote
from tests.unit.crosswalk_support import PRODUCER, make_capture


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return make_capture(tmp_path, httpx_mock)


@pytest.fixture
def built(capture_dir: Path) -> bytes:
    return build(capture_dir, BuildOptions(producer_commit=PRODUCER))


@pytest.fixture
def promoted(built: bytes, capture_dir: Path) -> bytes:
    approval = Approval.model_validate(
        {
            "input_sha256": sha256(built).hexdigest(),
            "approved_by": "synthetic test approval",
            "approved_on": "2026-11-03",
            "R1": "approved",
            "R2": "approved",
        }
    )
    data, _ = promote(built, approval, capture_dir)
    return data


def test_a_release_candidate_manifest_needs_a_capture(promoted: bytes) -> None:
    with pytest.raises(CrosswalkError) as caught:
        build_manifest(promoted, "x")
    assert str(caught.value) == (
        "a release_candidate manifest needs verified source provenance: give the capture"
    )


def test_a_verified_release_candidate_manifest_names_its_capture(
    promoted: bytes, capture_dir: Path
) -> None:
    manifest = build_manifest(promoted, "x", capture_dir)
    assert manifest["release_state"] == "release_candidate"
    assert manifest["provenance"] == {
        "verified": True,
        "capture_manifest_sha256": sha256((capture_dir / MANIFEST).read_bytes()).hexdigest(),
        "not_reproduced": [],
        "raw_bytes_not_in_capture": [],
    }


def _fabricate_candidate_digest(promoted: bytes) -> bytes:
    records = parse_records(promoted)
    candidate = next(r for r in records if r["release_state"] == "release_candidate")
    candidate["evidence"][0]["body_sha256"] = "f" * 64
    return serialize_records(records)


def test_a_drifted_release_candidate_gets_no_manifest(promoted: bytes, capture_dir: Path) -> None:
    with pytest.raises(CrosswalkError, match="raw bytes not in this capture"):
        build_manifest(_fabricate_candidate_digest(promoted), "x", capture_dir)


def test_a_release_candidate_from_another_capture_gets_no_manifest(
    promoted: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    records = parse_records(promoted)
    candidate = next(r for r in records if r["release_state"] == "release_candidate")
    law_id = candidate["amending_lov_id"]
    candidate["record_state"] = "active"
    narrowed = [r for r in records if r["amending_lov_id"] == law_id]
    manifest_path = capture_dir / MANIFEST
    text = manifest_path.read_text(encoding="utf-8")
    other = next(x for x in parse_records(promoted) if x["amending_lov_id"] != law_id)
    manifest_path.write_text(text.replace(law_id, other["amending_lov_id"]), encoding="utf-8")
    with pytest.raises(CrosswalkError, match="not reproduced from the capture"):
        build_manifest(serialize_records(narrowed), "x", capture_dir)


def test_a_research_manifest_without_a_capture_is_labelled_unverified(built: bytes) -> None:
    manifest = build_manifest(built, "x")
    assert manifest["release_state"] == "research_only"
    assert manifest["provenance"] == UNVERIFIED
    assert UNVERIFIED["verified"] is False
    assert "research_only" in str(UNVERIFIED["note"])


def test_a_research_manifest_with_a_capture_reports_what_was_not_verified(
    built: bytes, capture_dir: Path
) -> None:
    records = parse_records(built)
    records[0]["evidence"][0]["body_sha256"] = "f" * 64
    manifest = build_manifest(serialize_records(records), "x", capture_dir)
    provenance = manifest["provenance"]
    assert provenance["verified"] is True
    assert provenance["raw_bytes_not_in_capture"] == [records[0]["amending_lov_id"]]


def test_manifest_command_refuses_a_candidate_without_capture_and_writes_nothing(
    promoted: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    data, output = tmp_path / "rc.jsonl", tmp_path / "manifest.json"
    data.write_bytes(promoted)
    with pytest.raises(CrosswalkError):
        manifest_impl(data, output)
    assert not output.exists()
    assert manifest_impl(data, output, capture_dir) == (
        f"wrote the manifest of rc.jsonl (release_candidate) to {output}"
    )
