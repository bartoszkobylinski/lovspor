"""The fail-closed check command must diagnose schema-invalid JSON objects."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app


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
