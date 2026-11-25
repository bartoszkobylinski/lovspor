"""Boundary coverage for the owner-change pin file."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.owner_changes import MAX_BYTES, parse_pin_file


def test_file_at_byte_limit_is_accepted() -> None:
    data = b"#" + b"x" * (MAX_BYTES - 2) + b"\n"
    assert len(data) == MAX_BYTES
    assert parse_pin_file(data) == ()


def test_cli_checks_default_file_in_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin_file = tmp_path / "ops" / "lovverk-owner-changes"
    pin_file.parent.mkdir()
    pin_file.write_text("# no pins\n", encoding="ascii")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["ops", "owner-changes", "check"])
    assert result.exit_code == 0
    assert result.stdout == "ok ops/lovverk-owner-changes: 0 pins\n"
    assert pin_file.read_text(encoding="ascii") == "# no pins\n"
