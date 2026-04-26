import os

import pytest
from typer.testing import CliRunner

from lovspor import __version__
from lovspor.cli import app
from lovspor.errors import ConfigError
from lovspor.sync.orchestrator import SyncReport

runner = CliRunner()


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("LOVSPOR_"):
            monkeypatch.delenv(key, raising=False)


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


def test_seed_help_mentions_empty_corpus() -> None:
    result = runner.invoke(app, ["seed", "--help"])
    assert result.exit_code == 0
    assert "Initial population" in result.stdout


def test_sync_help_mentions_incremental() -> None:
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "Incremental sync" in result.stdout


def test_seed_invokes_run_sync_and_reports_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path))
    canned = SyncReport(
        new_count=3,
        changed_count=0,
        removed_count=0,
        unchanged_count=0,
    )
    monkeypatch.setattr("lovspor.cli.run_sync", lambda _settings: canned)
    result = runner.invoke(app, ["seed"])
    assert result.exit_code == 0
    assert "3 documents added" in result.stdout


def test_sync_invokes_run_sync_and_reports_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path))
    canned = SyncReport(
        new_count=2,
        changed_count=5,
        removed_count=1,
        unchanged_count=774,
    )
    monkeypatch.setattr("lovspor.cli.run_sync", lambda _settings: canned)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "2 new" in result.stdout
    assert "5 changed" in result.stdout
    assert "1 removed" in result.stdout
    assert "774 unchanged" in result.stdout


def test_sync_surfaces_config_error_on_missing_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)
