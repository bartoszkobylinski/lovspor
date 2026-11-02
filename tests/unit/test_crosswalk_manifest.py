"""The tool-written crosswalk manifest (#610, P5)."""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.canonical import parse_records, serialize_records
from lovspor.crosswalk.commands import manifest_impl
from lovspor.crosswalk.errors import CrosswalkContractError, CrosswalkError
from lovspor.crosswalk.manifest import BLOCKERS, build_manifest, render_manifest
from lovspor.crosswalk.release import Approval, promote
from tests.unit.crosswalk_support import COMMIT, PRODUCER, make_capture

OPTIONS = BuildOptions(producer_commit=PRODUCER)


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return make_capture(tmp_path, httpx_mock)


@pytest.fixture
def built(capture_dir: Path) -> bytes:
    return build(capture_dir, OPTIONS)


def test_manifest_describes_the_file(built: bytes) -> None:
    manifest = build_manifest(built, "crosswalk.jsonl")
    assert manifest["schema_version"] == "1.0.0-rc"
    assert manifest["release_state"] == "research_only"
    assert manifest["files"] == [
        {"name": "crosswalk.jsonl", "sha256": sha256(built).hexdigest(), "size_bytes": len(built)}
    ]
    assert manifest["counts"] == {
        "revisions": 8,
        "acts": 8,
        "status": {
            "ambiguous": 1,
            "attested": 3,
            "contradicted": 1,
            "unresolved": 2,
            "unsupported": 1,
        },
        "release_state": {"research_only": 8},
        "record_state": {"active": 8},
    }
    assert manifest["producer_commits"] == [PRODUCER]
    assert manifest["lovverk_commits"] == [COMMIT]
    assert manifest["parser_versions"] == [
        "besl-o-header-1",
        "lovtidend-identity-1",
        "lovvedtak-discovery-2",
        "sak-xml-2",
    ]


def test_archive_pins_are_listed_once_with_their_basis(built: bytes) -> None:
    pins = build_manifest(built, "x")["lovdata_archives"]
    assert [(p["source_url"].rsplit("/", 1)[1], p["licence_basis"]) for p in pins] == [
        ("lovtidend-avd1-2001-2025.tar.bz2", "stated_for_file"),
        ("lovtidend-avd1-2026.tar.bz2", "stated_for_endpoint"),
    ]


def test_blockers_and_attribution_stay_until_resolved(built: bytes) -> None:
    manifest = build_manifest(built, "x")
    assert manifest["unresolved_blockers"] == BLOCKERS
    assert any(b.startswith("R3:") for b in BLOCKERS)
    assert any(b.startswith("R4:") for b in BLOCKERS)
    assert manifest["attribution"]["status"] == "provisional"
    assert "Stortinget" in manifest["attribution"]["nb"]
    assert "Stiftelsen Lovdata" in manifest["attribution"]["en"]
    assert "released" not in json.dumps(manifest)
    assert "no claim beyond them" in manifest["coverage"]


def test_manifest_is_deterministic(built: bytes) -> None:
    first = render_manifest(build_manifest(built, "x"))
    assert render_manifest(build_manifest(built, "x")) == first
    assert first.endswith(b"}\n")


def test_release_candidate_file_level_state(built: bytes, capture_dir: Path) -> None:
    approval = Approval.model_validate(
        {
            "input_sha256": sha256(built).hexdigest(),
            "approved_by": "synthetic test approval",
            "approved_on": "2026-11-03",
            "R1": "approved",
            "R2": "approved",
        }
    )
    promoted, _ = promote(built, approval, capture_dir)
    manifest = build_manifest(promoted, "x")
    assert manifest["release_state"] == "release_candidate"
    assert manifest["counts"]["release_state"] == {"release_candidate": 3, "research_only": 5}
    assert manifest["counts"]["revisions"] == 11


def test_a_file_that_fails_check_gets_no_manifest(built: bytes) -> None:
    records = parse_records(built)
    records[0]["status"] = "attested"
    with pytest.raises(CrosswalkContractError):
        build_manifest(serialize_records(records), "x")


def test_manifest_impl_writes_once_and_cli_refuses(built: bytes, tmp_path: Path) -> None:
    data, output = tmp_path / "crosswalk.jsonl", tmp_path / "manifest.json"
    data.write_bytes(built)
    assert manifest_impl(data, output).startswith("wrote the manifest of crosswalk.jsonl")
    assert json.loads(output.read_text())["files"][0]["name"] == "crosswalk.jsonl"
    with pytest.raises(CrosswalkError, match="overwrite"):
        manifest_impl(data, output)
    data.write_bytes(built.replace(b'":', b'": ', 1))
    args = ["ops", "crosswalk", "manifest", "--input", str(data)]
    result = CliRunner().invoke(app, [*args, "--output", str(tmp_path / "m2.json")])
    assert result.exit_code == 1
    assert "problem: file is not in canonical form" in result.output
