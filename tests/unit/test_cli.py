import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor import __version__
from lovspor.cli import app
from lovspor.errors import ConfigError
from lovspor.sync.orchestrator import SyncReport

runner = CliRunner()

# Typer >= 0.12 renders --help through Rich panels, which interleave the
# usage text with ANSI escape sequences and box-drawing characters. The
# literal "Usage: lovspor [OPTIONS] COMMAND [ARGS]..." chunk only appears
# contiguously after the ANSI is stripped — Rich does not honour NO_COLOR
# for panel rendering. Local terminals with TERM=dumb don't trigger Rich,
# which is why the gap reached CI rather than failing pre-push.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("LOVSPOR_"):
            monkeypatch.delenv(key, raising=False)


def test_help_succeeds() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    plain = _strip_ansi(result.stdout)
    assert "Usage: lovspor [OPTIONS] COMMAND [ARGS]..." in plain
    assert "Norwegian law change tracker" in plain
    assert "XXNorwegian law change tracker" not in plain


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "Usage: lovspor [OPTIONS] COMMAND [ARGS]..." in _strip_ansi(result.stdout)


def test_completion_options_are_not_registered() -> None:
    result = runner.invoke(app, ["--help"])
    assert "--install-completion" not in result.stdout
    assert "--show-completion" not in result.stdout


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == f"lovspor {__version__}\n"


def test_short_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert result.stdout == f"lovspor {__version__}\n"


def test_info_command_prints_project_info() -> None:
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert result.stdout == (
        f"lovspor {__version__}\n"
        "Engine producing the lovverk Norwegian law corpus.\n"
        "Repo:   https://github.com/bartoszkobylinski/lovspor\n"
        "Corpus: https://github.com/bartoszkobylinski/lovverk\n"
    )


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


def test_mcp_help_mentions_fifteen_tools_and_optional_semantic_search_key() -> None:
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "Fifteen read-only tools" in result.stdout
    assert "OPENAI_API_KEY" in result.stdout
    assert "semantic_search" in result.stdout
    assert "other fourteen" in result.stdout
    assert "tools work normally" in result.stdout


def test_mcp_command_reads_corpus_path_supplied_via_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """LOVVERK_CORPUS_PATH provided only via .env (not exported) must still
    reach the mcp command. Typer resolves the option's envvar during arg
    parsing, so .env has to be loaded in the app callback — loading it in
    serve() is too late (the command already exited on the missing option)."""
    monkeypatch.setattr("lovspor.settings._ENV_LOADED", False)
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)

    def fake_load_dotenv(**_kwargs: object) -> bool:
        monkeypatch.setenv("LOVVERK_CORPUS_PATH", str(tmp_path))
        return True

    monkeypatch.setattr("lovspor.settings.load_dotenv", fake_load_dotenv)

    captured: dict[str, Path] = {}
    monkeypatch.setattr("lovspor.cli._mcp_serve", lambda path: captured.update(path=path))

    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 0, result.output
    assert captured["path"] == tmp_path.resolve()


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
    assert result.stdout == f"Seeded corpus at {tmp_path}: 3 documents added.\n"


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
    assert result.stdout == (
        f"Sync complete at {tmp_path}: 2 new, 5 changed, 1 removed, 774 unchanged.\n"
    )


def test_sync_surfaces_config_error_on_missing_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)
