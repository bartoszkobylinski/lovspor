"""A record's decision is held to the capture, not only its evidence (#1047).

Strict provenance compared evidence identities and raw bytes, so a record whose
``decision`` (the Lovvedtak number) was changed, with its evidence untouched,
passed ``check --strict`` and the release-candidate gate. The decision is now
compared with the record rebuilt from the capture. A decision of another
session is already refused by the contract; the number is what slipped through.
"""

from hashlib import sha256
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.canonical import Record, parse_records, serialize_records
from lovspor.crosswalk.check import check
from lovspor.crosswalk.commands import check_impl
from lovspor.crosswalk.errors import CrosswalkContractError
from lovspor.crosswalk.release import Approval, promote
from tests.unit.crosswalk_support import PRODUCER, make_capture

LAW = "LOV-2022-06-17-63"
PROBLEM = f"{LAW}: decision is not reproduced from the capture"


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return make_capture(tmp_path, httpx_mock)


@pytest.fixture
def built(capture_dir: Path) -> bytes:
    return build(capture_dir, BuildOptions(producer_commit=PRODUCER))


def _record(records: list[Record], law_id: str) -> Record:
    return next(r for r in records if r["amending_lov_id"] == law_id)


def _forged(built: bytes, number: int) -> bytes:
    records = parse_records(built)
    _record(records, LAW)["decision"]["number"] = number
    return serialize_records(records)


def _approval(data: bytes) -> Approval:
    return Approval.model_validate(
        {
            "input_sha256": sha256(data).hexdigest(),
            "approved_by": "synthetic test approval",
            "approved_on": "2026-11-27",
            "R1": "approved",
            "R2": "approved",
        }
    )


def _write(tmp_path: Path, data: bytes, name: str = "crosswalk.jsonl") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("number", [9999, 86, 84])
def test_strict_check_fails_on_a_decision_number_not_in_the_capture(
    built: bytes, capture_dir: Path, tmp_path: Path, number: int
) -> None:
    data = _forged(built, number)
    with pytest.raises(CrosswalkContractError) as caught:
        check_impl(_write(tmp_path, data), None, capture_dir, strict=True)
    assert caught.value.problems == (PROBLEM,)


def test_a_forged_decision_fails_the_check_given_a_capture(built: bytes, capture_dir: Path) -> None:
    report = check(_forged(built, 9999), cache=capture_dir)
    assert report.problems == [PROBLEM]
    assert not report.passed


def test_strict_check_command_exits_one_on_a_forged_decision(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    path = _write(tmp_path, _forged(built, 9999))
    args = ["ops", "crosswalk", "check", "--input", str(path), "--cache", str(capture_dir)]
    result = CliRunner().invoke(app, [*args, "--strict"])
    assert result.exit_code == 1
    assert f"problem: {PROBLEM}" in result.output


def test_the_release_gate_refuses_a_forged_decision(built: bytes, capture_dir: Path) -> None:
    data = _forged(built, 9999)
    with pytest.raises(CrosswalkContractError) as caught:
        promote(data, _approval(data), capture_dir)
    assert caught.value.problems == (PROBLEM,)


def test_a_release_candidate_shaped_file_still_passes_strict(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    """The P6 shape: research revisions, promoted revisions, an unsupported Besl. O. act."""
    promoted, _ = promote(built, _approval(built), capture_dir)
    states = {(r["record_revision"], r["release_state"]) for r in parse_records(promoted)}
    assert states == {(1, "research_only"), (2, "release_candidate")}
    assert _record(parse_records(promoted), "LOV-2009-06-19-107")["decision"]["kind"] == "besl_o"
    previous = _write(tmp_path, built, "previous.jsonl")
    current = _write(tmp_path, promoted)
    assert check_impl(current, previous, capture_dir, strict=True).startswith("ok: ")
