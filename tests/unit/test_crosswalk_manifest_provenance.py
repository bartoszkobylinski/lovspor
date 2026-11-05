"""A release_candidate manifest needs verified provenance; research manifests say they lack it."""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
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
        "fully_reproduced": True,
        "capture_manifest_sha256": sha256((capture_dir / MANIFEST).read_bytes()).hexdigest(),
        "not_reproduced": [],
        "raw_bytes_not_in_capture": [],
    }


def test_capture_provenance_hashes_exact_bytes_not_reserialized_json(
    promoted: bytes, capture_dir: Path
) -> None:
    """P5 names the capture manifest's SHA-256, including its byte representation."""
    path = capture_dir / MANIFEST
    original = path.read_bytes()
    before = build_manifest(promoted, "x", capture_dir)
    reformatted = json.dumps(json.loads(original), separators=(",", ":")).encode() + b"\r\n"
    assert reformatted != original
    assert json.loads(reformatted) == json.loads(original)
    path.write_bytes(reformatted)

    after = build_manifest(promoted, "x", capture_dir)
    assert after["provenance"] == {
        "verified": True,
        "fully_reproduced": True,
        "capture_manifest_sha256": sha256(reformatted).hexdigest(),
        "not_reproduced": [],
        "raw_bytes_not_in_capture": [],
    }
    assert before["provenance"]["capture_manifest_sha256"] == sha256(original).hexdigest()
    assert before["provenance"] != after["provenance"]
    assert {k: v for k, v in before.items() if k != "provenance"} == {
        k: v for k, v in after.items() if k != "provenance"
    }
    assert path.read_bytes() == reformatted


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
    with pytest.raises(CrosswalkError) as caught:
        build_manifest(serialize_records(narrowed), "x", capture_dir)
    assert str(caught.value) == f"release candidates not reproduced from the capture: {[law_id]}"


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


def test_mixed_manifest_reports_research_drift_without_certifying_that_act(
    promoted: bytes, capture_dir: Path
) -> None:
    """The producer contract requires provenance for every current candidate only."""
    records = parse_records(promoted)
    research = next(r for r in records if r["status"] == "unsupported")
    research["evidence"][0]["body_sha256"] = "f" * 64

    manifest = build_manifest(serialize_records(records), "x", capture_dir)

    assert manifest["release_state"] == "release_candidate"
    assert manifest["counts"]["release_state"] == {"release_candidate": 3, "research_only": 5}
    assert manifest["provenance"] == {
        "verified": True,
        "fully_reproduced": False,
        "capture_manifest_sha256": sha256((capture_dir / MANIFEST).read_bytes()).hexdigest(),
        "not_reproduced": [],
        "raw_bytes_not_in_capture": [research["amending_lov_id"]],
    }


@pytest.mark.parametrize("field", ["body_sha256", "parsed_identity_sha256"])
def test_manifest_checks_current_provenance_without_rejudging_superseded_evidence(
    promoted: bytes, capture_dir: Path, field: str
) -> None:
    """A historical revision may describe older bytes or an older parsed identity."""
    records = parse_records(promoted)
    candidate = next(r for r in records if r["release_state"] == "release_candidate")
    historical = next(
        r
        for r in records
        if r["amending_lov_id"] == candidate["amending_lov_id"] and r["record_revision"] == 1
    )
    historical["evidence"][0][field] = "f" * 64
    data = serialize_records(records)

    manifest = build_manifest(data, "x", capture_dir)

    assert manifest["release_state"] == "release_candidate"
    assert manifest["counts"]["revisions"] == len(records)
    assert manifest["files"] == [
        {"name": "x", "sha256": sha256(data).hexdigest(), "size_bytes": len(data)}
    ]
    assert manifest["provenance"]["not_reproduced"] == []
    assert manifest["provenance"]["raw_bytes_not_in_capture"] == []


def test_cli_writes_a_verified_release_candidate_manifest(
    promoted: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    """The ``--cache`` option actually reaches ``build_manifest`` through the CLI."""
    data, output = tmp_path / "rc.jsonl", tmp_path / "manifest.json"
    data.write_bytes(promoted)
    args = [
        "ops",
        "crosswalk",
        "manifest",
        "--input",
        str(data),
        "--cache",
        str(capture_dir),
        "--output",
        str(output),
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0
    assert f"wrote the manifest of rc.jsonl (release_candidate) to {output}" in result.output
    written = json.loads(output.read_text())
    assert written["release_state"] == "release_candidate"
    assert written["provenance"]["verified"] is True


def test_a_withdrawn_release_candidate_is_never_verified_even_with_its_own_capture(
    promoted: bytes, capture_dir: Path
) -> None:
    """check's `_judge` reports anything but an active record_state as unverified (check.py),
    regardless of whether the capture reproduces it. A release_candidate withdrawn after
    promotion therefore blocks the manifest forever, the same as one the capture never
    reproduced -- there is no path back to a manifest for that law without demoting it."""
    records = parse_records(promoted)
    candidate = next(r for r in records if r["release_state"] == "release_candidate")
    law_id = candidate["amending_lov_id"]
    withdrawn = {
        **candidate,
        "record_revision": candidate["record_revision"] + 1,
        "record_state": "withdrawn",
        "status": "unresolved",
        "edge_status": {**candidate["edge_status"], "decision_case": "unresolved"},
        "reason": "producer edge was wrong",
        "reason_code": "withdrawn_by_producer",
        "withdrawal": {"withdrawn_on": "2026-11-03", "reason": "producer edge was wrong"},
    }
    data = serialize_records([*records, withdrawn])

    with pytest.raises(CrosswalkError) as caught:
        build_manifest(data, "x", capture_dir)
    assert str(caught.value) == f"release candidates not reproduced from the capture: {[law_id]}"


def test_manifest_with_a_cache_still_demands_it_be_readable_for_a_research_only_file(
    built: bytes, tmp_path: Path
) -> None:
    """check() runs provenance whenever a cache is given, even with no release candidate to
    verify (#610 P5): a research-only file given a bad --cache still gets no manifest."""
    missing_cache = tmp_path / "no-such-cache"
    with pytest.raises(CrosswalkError, match="capture manifest unreadable"):
        build_manifest(built, "x", missing_cache)


def test_manifest_refusal_names_every_blocking_candidate_not_only_the_first_reason(
    promoted: bytes, capture_dir: Path
) -> None:
    records = parse_records(promoted)
    candidates = [r for r in records if r["release_state"] == "release_candidate"]
    assert len(candidates) > 1
    withdrawn_law = candidates[0]["amending_lov_id"]
    drifted_law = candidates[1]["amending_lov_id"]
    withdrawn = {
        **candidates[0],
        "record_revision": candidates[0]["record_revision"] + 1,
        "record_state": "withdrawn",
        "status": "unresolved",
        "edge_status": {**candidates[0]["edge_status"], "decision_case": "unresolved"},
        "reason": "producer edge was wrong",
        "reason_code": "withdrawn_by_producer",
        "withdrawal": {"withdrawn_on": "2026-11-03", "reason": "producer edge was wrong"},
    }
    records.append(withdrawn)
    # The current candidate itself: superseded revisions are never re-judged.
    candidates[1]["evidence"][0]["body_sha256"] = "f" * 64
    data = serialize_records(records)

    with pytest.raises(CrosswalkError) as caught:
        build_manifest(data, "x", capture_dir)
    assert withdrawn_law in str(caught.value)
    assert drifted_law in str(caught.value)
    assert str(caught.value) == (
        f"release candidates not reproduced from the capture: {[withdrawn_law]}; "
        f"release candidates raw bytes not in this capture: {[drifted_law]}"
    )


def test_cli_refuses_a_release_candidate_manifest_without_capture(
    promoted: bytes, tmp_path: Path
) -> None:
    """Exercises ``manifest_command``'s ``except LovsporError`` branch, not its contract one."""
    data, output = tmp_path / "rc.jsonl", tmp_path / "manifest.json"
    data.write_bytes(promoted)
    args = ["ops", "crosswalk", "manifest", "--input", str(data), "--output", str(output)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "refused: a release_candidate manifest needs verified source provenance" in (
        result.output
    )
    assert not output.exists()


@pytest.mark.parametrize("drift", ["body", "archive"])
def test_one_verified_candidate_does_not_certify_another_drifted_candidate(
    promoted: bytes, capture_dir: Path, tmp_path: Path, drift: str
) -> None:
    """Every current candidate must match, including candidates after the first."""
    records = parse_records(promoted)
    candidates = [r for r in records if r["release_state"] == "release_candidate"]
    assert len(candidates) > 1
    candidate = candidates[-1]
    if drift == "body":
        candidate["evidence"][0]["body_sha256"] = "f" * 64
    else:
        candidate["source_pins"]["lovdata_archives"][0]["sha256"] = "f" * 64
    data, output = tmp_path / "rc.jsonl", tmp_path / "manifest.json"
    data.write_bytes(serialize_records(records))

    with pytest.raises(CrosswalkError, match="raw bytes not in this capture") as caught:
        manifest_impl(data, output, capture_dir)

    assert str(caught.value) == (
        f"release candidates raw bytes not in this capture: {[candidate['amending_lov_id']]}"
    )
    assert not output.exists()
    assert not (tmp_path / ".manifest.json.partial").exists()
