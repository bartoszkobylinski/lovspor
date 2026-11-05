"""The fail-closed check command must diagnose schema-invalid JSON objects."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk.canonical import serialize_records
from lovspor.crosswalk.check import check
from tests.unit.crosswalk_support import attested


@pytest.mark.parametrize(
    "record",
    [
        pytest.param({}, id="missing-sort-fields"),
        pytest.param(
            {"amending_lov_id": "LOV-2022-06-17-63", "record_revision": "invalid"},
            id="non-numeric-revision",
        ),
        pytest.param(
            {"amending_lov_id": "LOV-2022-06-17-63", "record_revision": 1, "evidence": None},
            id="null-evidence",
        ),
        pytest.param(
            {
                "amending_lov_id": "LOV-2022-06-17-63",
                "record_revision": 1,
                "evidence": [{}],
            },
            id="missing-evidence-sort-fields",
        ),
    ],
)
def test_check_reports_schema_invalid_objects_without_crashing(
    tmp_path: Path, record: dict
) -> None:
    # check.py promises schema validation and fail-closed diagnostics. These are
    # valid JSON objects, so they must reach a controlled refusal, not a traceback
    # from canonical sorting before the schema has checked the fields.
    source = tmp_path / "invalid.jsonl"
    data = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    source.write_bytes(data)

    result = CliRunner().invoke(app, ["ops", "crosswalk", "check", "--input", str(source)])

    assert result.exit_code == 1
    assert "problem:" in result.output
    assert isinstance(result.exception, SystemExit)
    assert source.read_bytes() == data
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize(
    "previous_data",
    [
        pytest.param(b"{}\n", id="missing-identity-fields"),
        pytest.param(
            b'{"amending_lov_id":"LOV-2022-06-17-63","record_revision":"invalid"}\n',
            id="non-numeric-revision",
        ),
        pytest.param(b"{oops\n", id="malformed-json"),
        pytest.param(b"\xff\n", id="malformed-utf8"),
    ],
)
def test_check_refuses_invalid_previous_without_a_traceback(
    tmp_path: Path, previous_data: bytes
) -> None:
    # check_command promises "Fail closed: ... append-only history". A broken
    # previous release must produce a diagnostic, just like a broken input,
    # rather than leaking KeyError/ValueError from the history comparison.
    source = tmp_path / "input.jsonl"
    data = serialize_records([attested()])
    source.write_bytes(data)
    previous = tmp_path / "previous.jsonl"
    previous.write_bytes(previous_data)

    result = CliRunner().invoke(
        app,
        ["ops", "crosswalk", "check", "--input", str(source), "--previous", str(previous)],
    )

    assert result.exit_code == 1
    assert "problem:" in result.output
    assert isinstance(result.exception, SystemExit)
    assert source.read_bytes() == data
    assert previous.read_bytes() == previous_data
    assert set(tmp_path.iterdir()) == {source, previous}


@pytest.mark.parametrize("revisions", [(1, 1), (2,), (1, 3)])
def test_check_rejects_invalid_previous_revision_history(
    tmp_path: Path, revisions: tuple[int, ...]
) -> None:
    # check._history_problems promises that the previous release is held to
    # the contract before comparison. All these records are individually valid;
    # only the previous release's cross-record history violates invariant 6.
    record = attested()
    current = serialize_records([{**record, "record_revision": revision} for revision in (1, 2, 3)])
    previous = serialize_records(
        [{**record, "record_revision": revision} for revision in revisions]
    )
    assert check(current).passed
    source = tmp_path / "input.jsonl"
    source.write_bytes(current)
    prior = tmp_path / "previous.jsonl"
    prior.write_bytes(previous)

    result = CliRunner().invoke(
        app,
        ["ops", "crosswalk", "check", "--input", str(source), "--previous", str(prior)],
    )

    expected = (
        f"previous release: {record['amending_lov_id']}: revisions {list(revisions)} "
        f"are not 1..{len(revisions)} without repeats"
    )
    assert result.exit_code == 1
    assert f"problem: {expected}" in result.output  # Plain typer.echo diagnostic.
    assert isinstance(result.exception, SystemExit)
    assert source.read_bytes() == current
    assert prior.read_bytes() == previous
    assert set(tmp_path.iterdir()) == {source, prior}
