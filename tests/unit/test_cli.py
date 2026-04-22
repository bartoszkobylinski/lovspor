from typer.testing import CliRunner

from lovspor import __version__
from lovspor.cli import app

runner = CliRunner()


def test_help_succeeds() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Norwegian law change tracker" in result.stdout


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_short_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_info_command_prints_project_info() -> None:
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
    assert "lovverk" in result.stdout


def test_unknown_command_fails_cleanly() -> None:
    result = runner.invoke(app, ["nonexistent-command"])
    assert result.exit_code != 0
