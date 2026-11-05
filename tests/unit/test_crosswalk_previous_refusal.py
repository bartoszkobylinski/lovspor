"""The build validates previous releases before reading a capture or publishing."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk.canonical import serialize_records
from tests.unit.cli_output import said
from tests.unit.crosswalk_support import PRODUCER, attested


def _wrong_case() -> bytes:
    record = attested()
    record["sak_id"] += 1
    return serialize_records([record])


@pytest.mark.parametrize(
    ("data", "diagnostic"),
    [
        (b"\xff\n", "file is not UTF-8"),
        (b"{}", "file does not end with a newline"),
        (b"[]\n", "line 1: not a JSON object"),
        (b'{"value":NaN}\n', "non-finite number NaN is not JSON"),
        (_wrong_case(), "decision_case evidence"),
    ],
    ids=["utf8", "newline", "object", "nonfinite", "case-invariant"],
)
def test_cli_refuses_invalid_previous_before_reading_capture(
    tmp_path: Path, data: bytes, diagnostic: str
) -> None:
    previous = tmp_path / "previous.jsonl"
    previous.write_bytes(data)
    output = tmp_path / "release.jsonl"
    # A missing capture ensures the refusal actually describes the previous
    # release, rather than a later cache error masking an unchecked input.
    cache = tmp_path / "missing-capture"

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "crosswalk",
            "build",
            "--cache",
            str(cache),
            "--output",
            str(output),
            "--previous",
            str(previous),
            "--producer-commit",
            PRODUCER,
        ],
    )

    assert result.exit_code == 1, result.output
    assert said(diagnostic, result.output), result.output
    assert previous.read_bytes() == data
    assert not cache.exists()
    assert not output.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["previous.jsonl"]
