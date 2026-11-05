"""Strict provenance: a check that fails on any gap, and a manifest that names the gap.

The default check stays as it was: research drift and acts the capture does not
cover are reported, not failed. ``--strict`` turns both into failures, and the
manifest says in ``fully_reproduced`` whether every current record was reproduced
from the capture, raw bytes included. ``verified`` keeps its meaning.
"""

from copy import deepcopy
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.cache import MANIFEST
from lovspor.crosswalk.canonical import parse_records, serialize_records
from lovspor.crosswalk.commands import check_impl
from lovspor.crosswalk.errors import CrosswalkContractError, CrosswalkError
from lovspor.crosswalk.manifest import UNVERIFIED, build_manifest
from tests.unit.crosswalk_support import PRODUCER, make_capture


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return make_capture(tmp_path, httpx_mock)


@pytest.fixture
def built(capture_dir: Path) -> bytes:
    return build(capture_dir, BuildOptions(producer_commit=PRODUCER))


def _drifted(built: bytes) -> tuple[bytes, str]:
    records = parse_records(built)
    records[0]["evidence"][0]["body_sha256"] = "f" * 64
    return serialize_records(records), records[0]["amending_lov_id"]


def _uncovered(built: bytes, capture_dir: Path) -> str:
    """Point the capture's scope away from one act, so the capture no longer covers it."""
    records = parse_records(built)
    law_id, other = records[0]["amending_lov_id"], records[1]["amending_lov_id"]
    path = capture_dir / MANIFEST
    path.write_text(path.read_text(encoding="utf-8").replace(law_id, other), encoding="utf-8")
    return law_id


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "crosswalk.jsonl"
    path.write_bytes(data)
    return path


def test_default_check_reports_drift_and_passes(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    data, _ = _drifted(built)
    assert check_impl(_write(tmp_path, data), None, capture_dir) == (
        f"ok: {len(parse_records(data))} records, 0 not reproduced from a capture, "
        "1 with raw bytes not in this capture"
    )


def test_strict_check_fails_on_raw_bytes_not_in_the_capture(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    data, law_id = _drifted(built)
    with pytest.raises(CrosswalkContractError) as caught:
        check_impl(_write(tmp_path, data), None, capture_dir, strict=True)
    assert caught.value.problems == (f"{law_id}: raw bytes not in this capture",)


def test_strict_check_fails_on_an_act_the_capture_does_not_reproduce(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    law_id = _uncovered(built, capture_dir)
    with pytest.raises(CrosswalkContractError) as caught:
        check_impl(_write(tmp_path, built), None, capture_dir, strict=True)
    assert caught.value.problems == (f"{law_id}: not reproduced from the capture",)


def test_strict_check_names_every_gap_in_order(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    records = parse_records(built)
    records[2]["evidence"][0]["body_sha256"] = "f" * 64
    uncovered = _uncovered(built, capture_dir)
    with pytest.raises(CrosswalkContractError) as caught:
        check_impl(_write(tmp_path, serialize_records(records)), None, capture_dir, strict=True)
    assert caught.value.problems == (
        f"{uncovered}: not reproduced from the capture",
        f"{records[2]['amending_lov_id']}: raw bytes not in this capture",
    )


def test_strict_check_needs_a_capture(built: bytes, tmp_path: Path) -> None:
    with pytest.raises(CrosswalkError) as caught:
        check_impl(_write(tmp_path, built), None, None, strict=True)
    assert str(caught.value) == "--strict verifies against a capture: give --cache"


def test_strict_check_passes_a_fully_reproduced_file(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    assert check_impl(_write(tmp_path, built), None, capture_dir, strict=True) == (
        f"ok: {len(parse_records(built))} records, 0 not reproduced from a capture, "
        "0 with raw bytes not in this capture"
    )


def test_strict_check_command_exits_one_and_names_the_gap(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    data, law_id = _drifted(built)
    path = _write(tmp_path, data)
    base = ["ops", "crosswalk", "check", "--input", str(path), "--cache", str(capture_dir)]
    runner = CliRunner()
    default = runner.invoke(app, base)
    strict = runner.invoke(app, [*base, "--strict"])
    assert default.exit_code == 0
    assert strict.exit_code == 1
    assert f"problem: {law_id}: raw bytes not in this capture" in strict.output


def test_strict_check_command_without_a_capture_is_refused(built: bytes, tmp_path: Path) -> None:
    path = _write(tmp_path, built)
    args = ["ops", "crosswalk", "check", "--input", str(path), "--strict"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "--strict verifies against a capture: give --cache" in result.output


def test_manifest_says_a_drifted_research_file_is_not_fully_reproduced(
    built: bytes, capture_dir: Path
) -> None:
    data, law_id = _drifted(built)
    provenance = build_manifest(data, "x", capture_dir)["provenance"]
    assert provenance["verified"] is True
    assert provenance["fully_reproduced"] is False
    assert provenance["raw_bytes_not_in_capture"] == [law_id]


def test_manifest_says_an_uncovered_act_is_not_fully_reproduced(
    built: bytes, capture_dir: Path
) -> None:
    law_id = _uncovered(built, capture_dir)
    provenance = build_manifest(built, "x", capture_dir)["provenance"]
    assert provenance["fully_reproduced"] is False
    assert provenance["not_reproduced"] == [law_id]


def test_manifest_says_a_clean_file_is_fully_reproduced(built: bytes, capture_dir: Path) -> None:
    assert build_manifest(built, "x", capture_dir)["provenance"]["fully_reproduced"] is True


def test_a_manifest_without_a_capture_is_not_fully_reproduced(built: bytes) -> None:
    assert build_manifest(built, "x")["provenance"] == UNVERIFIED
    assert UNVERIFIED["fully_reproduced"] is False


def test_strict_check_and_manifest_include_archive_pin_drift(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    """Exactly this capture's bytes includes archive pins, not just evidence bodies."""
    records = parse_records(built)
    record = records[-1]
    record["source_pins"]["lovdata_archives"][0]["sha256"] = "f" * 64
    data = serialize_records(records)
    law_id = record["amending_lov_id"]

    with pytest.raises(CrosswalkContractError) as caught:
        check_impl(_write(tmp_path, data), None, capture_dir, strict=True)

    assert caught.value.problems == (f"{law_id}: raw bytes not in this capture",)
    provenance = build_manifest(data, "x", capture_dir)["provenance"]
    assert provenance["verified"] is True
    assert provenance["fully_reproduced"] is False
    assert provenance["not_reproduced"] == []
    assert provenance["raw_bytes_not_in_capture"] == [law_id]


def test_strict_check_preserves_append_only_validation(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    """A capture match cannot excuse rewriting the previous release (D6)."""
    previous = _write(tmp_path, built)
    records = parse_records(built)
    records[0]["source_pins"]["producer_commit"] = "c" * 40
    current = tmp_path / "current.jsonl"
    current.write_bytes(serialize_records(records))

    with pytest.raises(CrosswalkContractError) as caught:
        check_impl(current, previous, capture_dir, strict=True)

    assert caught.value.problems == (
        f"{records[0]['amending_lov_id']}@1: revision of the previous release was rewritten",
    )


@pytest.mark.parametrize("field", ["body_sha256", "parsed_identity_sha256"])
def test_strict_provenance_only_judges_current_revisions(
    built: bytes, capture_dir: Path, tmp_path: Path, field: str
) -> None:
    """Old evidence remains in history; only current records must reproduce."""
    records = parse_records(built)
    historical = deepcopy(records[0])
    historical["evidence"][0][field] = "f" * 64
    records[0]["record_revision"] = 2
    data = serialize_records([*records, historical])
    previous = tmp_path / "previous.jsonl"
    previous.write_bytes(serialize_records([historical]))

    assert check_impl(_write(tmp_path, data), previous, capture_dir, strict=True) == (
        f"ok: {len(records) + 1} records, 0 not reproduced from a capture, "
        "0 with raw bytes not in this capture"
    )
    assert build_manifest(data, "x", capture_dir)["provenance"]["fully_reproduced"] is True


def test_strict_cli_reports_all_gaps_without_writing(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    records = parse_records(built)
    records[2]["evidence"][0]["body_sha256"] = "f" * 64
    uncovered = _uncovered(built, capture_dir)
    path = _write(tmp_path, serialize_records(records))
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "crosswalk",
            "check",
            "--input",
            str(path),
            "--cache",
            str(capture_dir),
            "--strict",
        ],
    )

    assert result.exit_code == 1
    assert result.output.splitlines() == [
        f"problem: {uncovered}: not reproduced from the capture",
        f"problem: {records[2]['amending_lov_id']}: raw bytes not in this capture",
    ]
    assert {
        p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before


@pytest.mark.parametrize("invalid", ["noncanonical", "identity"])
def test_strict_cli_preserves_existing_contract_failures(
    built: bytes, capture_dir: Path, tmp_path: Path, invalid: str
) -> None:
    """Strict adds provenance requirements without bypassing the default contract."""
    if invalid == "noncanonical":
        data = built.replace(b"\n", b"\r\n")
        problem = "file is not in canonical form"
    else:
        records = parse_records(built)
        records[0]["evidence"][0]["parsed_identity_sha256"] = "f" * 64
        data = serialize_records(records)
        problem = (
            f"{records[0]['amending_lov_id']}: "
            "evidence identities are not reproduced from the capture"
        )
    path = _write(tmp_path, data)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "crosswalk",
            "check",
            "--input",
            str(path),
            "--cache",
            str(capture_dir),
            "--strict",
        ],
    )

    assert result.exit_code == 1
    assert result.output.splitlines() == [f"problem: {problem}"]
    assert {
        p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before


def test_strict_cli_accepts_reproduced_input_without_writing(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    path = _write(tmp_path, built)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "crosswalk",
            "check",
            "--input",
            str(path),
            "--cache",
            str(capture_dir),
            "--strict",
        ],
    )

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        f"ok: {len(parse_records(built))} records, 0 not reproduced from a capture, "
        "0 with raw bytes not in this capture"
    ]
    assert {
        p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before
