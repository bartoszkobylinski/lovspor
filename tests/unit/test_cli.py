import asyncio
import json
import os
import re
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor import __version__
from lovspor.access import CredentialStore, hash_token
from lovspor.cli import app
from lovspor.corpus_fetch import FetchResult
from lovspor.errors import ConfigError
from lovspor.mcp import HttpConfig
from lovspor.rendering.markdown_renderer import RENDERER_VERSION
from lovspor.storage.manifest import MANIFEST_VERSION
from lovspor.sync.orchestrator import SyncReport

runner = CliRunner()

_AUTHKIT_DOMAIN = "https://vigilant-beacon-78-staging.authkit.app"
_PUBLIC_URL = "https://lovspor.example.com/mcp"

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


def test_mcp_help_mentions_sixteen_tools_and_optional_semantic_search_key() -> None:
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "Sixteen read-only tools" in result.stdout
    assert "OPENAI_API_KEY" in result.stdout
    assert "semantic_search" in result.stdout
    assert "other fifteen" in result.stdout
    assert "tools work normally" in result.stdout


def test_repair_embeddings_command_is_registered() -> None:
    result = runner.invoke(app, ["repair-embeddings", "--help"])
    assert result.exit_code == 0
    assert "repair-embeddings" in _strip_ansi(result.stdout)


def test_repair_embeddings_invokes_mark_and_reports_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path))
    monkeypatch.setattr("lovspor.cli.mark_undersized_embeddings_stale", lambda _settings: 2326)
    result = runner.invoke(app, ["repair-embeddings"])
    assert result.exit_code == 0
    assert "Flagged 2326 document(s) for re-embed." in result.stdout
    assert "lovspor sync" in result.stdout


def test_mcp_command_reads_corpus_path_from_real_dotenv_in_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A real .env in the working directory (not an exported var) must reach
    the mcp command. Two things have to line up: the app callback loads .env
    BEFORE Typer resolves the --corpus-path envvar, and the loader discovers
    .env from the cwd (usecwd=True) rather than the installed module dir.
    No load_dotenv mock, so this exercises real dotenv discovery."""
    monkeypatch.setattr("lovspor.settings._ENV_LOADED", False)
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"LOVVERK_CORPUS_PATH={tmp_path}\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")

    captured: dict[str, Path] = {}
    monkeypatch.setattr("lovspor.cli._mcp_serve", lambda path: captured.update(path=path))

    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 0, result.output
    assert captured["path"] == tmp_path.resolve()


def test_mcp_http_passes_bind_address_and_resolved_corpus_to_the_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The hosted transport is only safe behind a proxy today, so the bind
    address the operator asked for must reach serve_http verbatim."""
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)
    monkeypatch.delenv("LOVSPOR_CREDENTIALS", raising=False)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    creds = tmp_path / "creds.json"
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "lovspor.cli._mcp_serve_http",
        lambda path, http: captured.update(path=path, http=http),
    )

    result = runner.invoke(
        app,
        [
            "mcp-http",
            "--host",
            "127.0.0.1",
            "--port",
            "9123",
            "--corpus-path",
            str(tmp_path),
            "--credentials",
            str(creds),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["path"] == tmp_path.resolve()
    assert captured["http"] == HttpConfig(host="127.0.0.1", port=9123, credentials_path=creds)


def test_mcp_http_defaults_to_the_credential_store_not_to_open_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Omitting --credentials must resolve the default store, never silently
    serve unauthenticated."""
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)
    monkeypatch.delenv("LOVSPOR_CREDENTIALS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "lovspor.cli._mcp_serve_http",
        lambda path, http: captured.update(path=path, http=http),
    )

    result = runner.invoke(app, ["mcp-http", "--corpus-path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    http = captured["http"]
    assert isinstance(http, HttpConfig)
    assert http.allow_insecure is False
    assert http.credentials_path == tmp_path / "cfg" / "lovspor" / "credentials.json"


def test_mcp_http_binds_localhost_when_no_host_is_given(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The --host/--port defaults are what the systemd unit relies on: the
    droplet runs `lovspor mcp-http` behind Caddy on 127.0.0.1:8000. Every
    other CLI test passes --host explicitly, so a default that drifted to a
    public interface would reach production unopposed."""
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)
    monkeypatch.delenv("LOVSPOR_CREDENTIALS", raising=False)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    creds = tmp_path / "creds.json"
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "lovspor.cli._mcp_serve_http",
        lambda path, http: captured.update(path=path, http=http),
    )

    result = runner.invoke(
        app,
        ["mcp-http", "--corpus-path", str(tmp_path), "--credentials", str(creds)],
    )

    assert result.exit_code == 0, result.output
    http = captured["http"]
    assert isinstance(http, HttpConfig)
    assert http.host == "127.0.0.1"
    assert http.port == 8000


def test_mcp_http_insecure_flag_clears_the_credential_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)
    monkeypatch.delenv("LOVSPOR_CREDENTIALS", raising=False)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "lovspor.cli._mcp_serve_http",
        lambda path, http: captured.update(path=path, http=http),
    )

    result = runner.invoke(
        app,
        ["mcp-http", "--corpus-path", str(tmp_path), "--insecure-no-auth"],
    )

    assert result.exit_code == 0, result.output
    http = captured["http"]
    assert isinstance(http, HttpConfig)
    assert http.credentials_path is None
    assert http.allow_insecure is True


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--authkit-domain", _AUTHKIT_DOMAIN),
        ("--public-url", _PUBLIC_URL),
    ],
)
def test_mcp_http_half_configured_oauth_flag_surfaces_config_error_before_serve(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    option: str,
    value: str,
) -> None:
    _clear_env(monkeypatch)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    served: list[object] = []
    monkeypatch.setattr("lovspor.cli._mcp_serve_http", lambda *args: served.append(args))

    result = runner.invoke(
        app,
        ["mcp-http", "--corpus-path", str(tmp_path), option, value],
    )

    assert result.exit_code == 1
    assert type(result.exception) is ConfigError
    assert "needs --authkit-domain and --public-url together" in str(result.exception)
    assert served == []


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("LOVSPOR_AUTHKIT_DOMAIN", _AUTHKIT_DOMAIN),
        ("LOVSPOR_PUBLIC_URL", _PUBLIC_URL),
        ("LOVSPOR_AUTHKIT_DOMAIN", "   "),
    ],
)
def test_mcp_http_half_configured_oauth_env_surfaces_config_error_before_serve(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_name: str,
    value: str,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(env_name, value)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    served: list[object] = []
    monkeypatch.setattr("lovspor.cli._mcp_serve_http", lambda *args: served.append(args))

    result = runner.invoke(app, ["mcp-http", "--corpus-path", str(tmp_path)])

    assert result.exit_code == 1
    assert type(result.exception) is ConfigError
    assert "needs --authkit-domain and --public-url together" in str(result.exception)
    assert served == []


def test_mcp_http_empty_oauth_env_is_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_AUTHKIT_DOMAIN", "")
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "lovspor.cli._mcp_serve_http",
        lambda path, http: captured.update(path=path, http=http),
    )

    result = runner.invoke(app, ["mcp-http", "--corpus-path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    http = captured["http"]
    assert isinstance(http, HttpConfig)
    assert http.oauth_pair() is None


def test_mcp_http_reads_complete_oauth_pair_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_AUTHKIT_DOMAIN", _AUTHKIT_DOMAIN)
    monkeypatch.setenv("LOVSPOR_PUBLIC_URL", _PUBLIC_URL)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "lovspor.cli._mcp_serve_http",
        lambda path, http: captured.update(path=path, http=http),
    )

    result = runner.invoke(app, ["mcp-http", "--corpus-path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    http = captured["http"]
    assert isinstance(http, HttpConfig)
    assert http.oauth_pair() == (_AUTHKIT_DOMAIN, _PUBLIC_URL)


def test_tokens_issue_prints_the_token_once_and_stores_only_its_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("LOVSPOR_CREDENTIALS", raising=False)
    creds = tmp_path / "credentials.json"

    result = runner.invoke(
        app,
        ["tokens", "issue", "--label", "Beta tester", "--credentials", str(creds)],
    )

    assert result.exit_code == 0, result.output
    token = next(w for w in result.stdout.split() if w.startswith("lsp_"))
    stored = creds.read_text(encoding="utf-8")
    assert token not in stored
    assert hash_token(token) in stored
    # 0600: the store is a set of live credentials.
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600


def test_tokens_revoke_then_the_store_rejects_that_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End-to-end through the real files: issue, verify, revoke, verify again."""
    monkeypatch.delenv("LOVSPOR_CREDENTIALS", raising=False)
    creds = tmp_path / "credentials.json"
    issued = runner.invoke(
        app,
        ["tokens", "issue", "--label", "Beta tester", "--credentials", str(creds)],
    )
    token = next(w for w in issued.stdout.split() if w.startswith("lsp_"))
    assert asyncio.run(CredentialStore(creds).verify_token(token)) is not None

    result = runner.invoke(app, ["tokens", "revoke", "beta-001", "--credentials", str(creds)])

    assert result.exit_code == 0, result.output
    assert asyncio.run(CredentialStore(creds).verify_token(token)) is None


def test_tokens_list_never_prints_a_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("LOVSPOR_CREDENTIALS", raising=False)
    creds = tmp_path / "credentials.json"
    issued = runner.invoke(
        app,
        ["tokens", "issue", "--label", "Beta tester", "--credentials", str(creds)],
    )
    token = next(w for w in issued.stdout.split() if w.startswith("lsp_"))

    result = runner.invoke(app, ["tokens", "list", "--credentials", str(creds)])

    assert result.exit_code == 0, result.output
    assert "beta-001" in result.stdout
    assert "Beta tester" in result.stdout
    assert token not in result.stdout


def test_tokens_issue_list_revoke_round_trip_tracks_ids_and_statuses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("LOVSPOR_CREDENTIALS", raising=False)
    creds = tmp_path / "credentials.json"

    first = runner.invoke(
        app,
        ["tokens", "issue", "--label", "Alpha", "--credentials", str(creds)],
    )
    second = runner.invoke(
        app,
        ["tokens", "issue", "--label", "Beta", "--credentials", str(creds)],
    )
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output

    revoked = runner.invoke(app, ["tokens", "revoke", "beta-001", "--credentials", str(creds)])
    listed = runner.invoke(app, ["tokens", "list", "--credentials", str(creds)])

    assert revoked.exit_code == 0, revoked.output
    assert listed.exit_code == 0, listed.output
    assert "beta-001  revoked  Alpha" in listed.stdout
    assert "beta-002  active until " in listed.stdout
    assert "Beta" in listed.stdout


def test_mcp_http_refuses_to_start_without_a_corpus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fail at startup with a fixable message rather than serving an empty
    corpus over the network."""
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)

    result = runner.invoke(app, ["mcp-http", "--corpus-path", str(tmp_path / "nope")])

    assert result.exit_code != 0


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
    seen: dict[str, object] = {}

    def _fake(settings: object, *, force_rerender: bool = False) -> SyncReport:
        seen["force_rerender"] = force_rerender
        return canned

    monkeypatch.setattr("lovspor.cli.run_sync", _fake)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert seen["force_rerender"] is False
    assert result.stdout == (
        f"Sync complete at {tmp_path}: 2 new, 5 changed, 1 removed, 774 unchanged.\n"
    )


def test_sync_force_rerender_flag_reaches_run_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """The corpus-wide rewrite must be opt-in per invocation. It is a CLI flag,
    not a Settings/env field, so a stray env var can never make the scheduled
    workflow rewrite every document."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path))
    monkeypatch.setenv("LOVSPOR_FORCE_RERENDER", "1")
    canned = SyncReport(new_count=0, changed_count=1, removed_count=0, unchanged_count=0)
    seen: dict[str, object] = {}

    def _fake(settings: object, *, force_rerender: bool = False) -> SyncReport:
        seen["force_rerender"] = force_rerender
        return canned

    monkeypatch.setattr("lovspor.cli.run_sync", _fake)

    assert runner.invoke(app, ["sync"]).exit_code == 0
    assert seen["force_rerender"] is False, "env var must not enable a corpus-wide rewrite"

    assert runner.invoke(app, ["sync", "--force-rerender"]).exit_code == 0
    assert seen["force_rerender"] is True


def _drift_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path))
    gh_output = tmp_path / "gh_output"
    gh_summary = tmp_path / "gh_summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(gh_summary))
    return gh_output, gh_summary


def test_sync_signals_upstream_schema_drift_to_the_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unknown upstream fields must not fail the sync, but they must reach the
    workflow: a step output the issue-filing step keys on, plus a summary note."""
    gh_output, gh_summary = _drift_env(monkeypatch, tmp_path)
    canned = SyncReport(
        new_count=0,
        changed_count=0,
        removed_count=0,
        unchanged_count=1,
        unknown_archive_fields=("checksum", "mimeType"),
    )
    monkeypatch.setattr("lovspor.cli.run_sync", lambda *_a, **_k: canned)

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    assert (
        gh_output.read_text(encoding="utf-8")
        == "schema_drift_fields<<__EOF__\nchecksum,mimeType\n__EOF__\n"
    )
    assert "Lovdata schema drift" in gh_summary.read_text(encoding="utf-8")


def test_sync_emits_no_drift_signal_when_schema_matches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gh_output, gh_summary = _drift_env(monkeypatch, tmp_path)
    canned = SyncReport(new_count=0, changed_count=0, removed_count=0, unchanged_count=1)
    monkeypatch.setattr("lovspor.cli.run_sync", lambda *_a, **_k: canned)

    assert runner.invoke(app, ["sync"]).exit_code == 0
    assert not gh_output.exists()
    assert not gh_summary.exists()


def test_sync_surfaces_config_error_on_missing_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)


def test_fetch_corpus_command_is_registered() -> None:
    result = runner.invoke(app, ["fetch-corpus", "--help"])
    assert result.exit_code == 0
    assert "fetch-corpus" in _strip_ansi(result.stdout)


def test_fetch_corpus_reports_action_and_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "lovverk"
    canned = FetchResult(path=target, action="cloned")
    monkeypatch.setattr("lovspor.cli.fetch_corpus", lambda _dest, **_kwargs: canned)
    result = runner.invoke(app, ["fetch-corpus", "--dest", str(target)])
    assert result.exit_code == 0, result.output
    assert "cloned" in result.stdout
    assert str(target) in result.stdout


def test_fetch_corpus_passes_full_history_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """--full-history reaches fetch_corpus as full_history=True; the default
    invocation stays shallow (full_history=False) — ADR-0003 keeps shallow
    clones supported where limited temporal reach is acceptable."""
    target = tmp_path / "lovverk"
    seen: list[bool] = []

    def fake_fetch(_dest: Path, *, full_history: bool = False) -> FetchResult:
        seen.append(full_history)
        return FetchResult(path=target, action="cloned")

    monkeypatch.setattr("lovspor.cli.fetch_corpus", fake_fetch)

    assert runner.invoke(app, ["fetch-corpus", "--dest", str(target)]).exit_code == 0
    assert (
        runner.invoke(app, ["fetch-corpus", "--dest", str(target), "--full-history"]).exit_code == 0
    )
    assert seen == [False, True]


def test_mcp_falls_back_to_default_corpus_path_when_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Silence .env discovery so the LOVVERK_CORPUS_PATH envvar stays truly unset.
    monkeypatch.setattr("lovspor.cli.load_env", lambda: None)
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)
    monkeypatch.setattr("lovspor.cli.default_corpus_path", lambda: tmp_path)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    captured: dict[str, Path] = {}
    monkeypatch.setattr("lovspor.cli._mcp_serve", lambda path: captured.update(path=path))

    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 0, result.output
    assert captured["path"] == tmp_path.resolve()


def test_mcp_path_precedence_prefers_cli_then_env_then_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("lovspor.cli.load_env", lambda: None)
    cli_path = tmp_path / "cli"
    env_path = tmp_path / "env"
    default_path = tmp_path / "default"
    for path in (cli_path, env_path, default_path):
        path.mkdir()
        (path / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LOVVERK_CORPUS_PATH", str(env_path))
    monkeypatch.setattr("lovspor.cli.default_corpus_path", lambda: default_path)
    captured: dict[str, Path] = {}
    monkeypatch.setattr("lovspor.cli._mcp_serve", lambda path: captured.update(path=path))

    result = runner.invoke(app, ["mcp", "--corpus-path", str(cli_path)])

    assert result.exit_code == 0, result.output
    assert captured["path"] == cli_path.resolve()

    captured.clear()
    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 0, result.output
    assert captured["path"] == env_path.resolve()

    captured.clear()
    monkeypatch.delenv("LOVVERK_CORPUS_PATH")
    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 0, result.output
    assert captured["path"] == default_path.resolve()


def test_mcp_missing_corpus_points_to_fetch_corpus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("lovspor.cli.load_env", lambda: None)
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)
    missing = tmp_path / "absent"
    monkeypatch.setattr("lovspor.cli.default_corpus_path", lambda: missing)

    result = runner.invoke(app, ["mcp"])

    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)
    assert "fetch-corpus" in str(result.exception)


def test_mcp_existing_non_corpus_path_points_to_fetch_corpus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("lovspor.cli.load_env", lambda: None)
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)
    monkeypatch.setattr("lovspor.cli.default_corpus_path", lambda: tmp_path)
    monkeypatch.setattr(
        "lovspor.cli._mcp_serve",
        lambda _path: pytest.fail("MCP server should not start without a corpus manifest"),
    )

    result = runner.invoke(app, ["mcp"])

    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)
    assert "fetch-corpus" in str(result.exception)


# --- audit: reconcile the corpus on disk against the manifest ---


def _audit_corpus_fixture(
    root: Path,
    *,
    orphan: bool,
    unparsed_heading: bool = False,
) -> None:
    """Minimal corpus: one current act, optionally drifted.

    ``orphan`` plants an INTEGRITY finding (a file no record claims);
    ``unparsed_heading`` plants an ADVISORY one (a section heading the
    grammar cannot read)."""
    (root / "lover").mkdir(parents=True, exist_ok=True)
    body = "# Skatteloven\n"
    if unparsed_heading:
        body += "### § ø-1. Ukjent form\n"
    (root / "lover" / "skatteloven.md").write_text(body, encoding="utf-8")
    if orphan:
        (root / "lover" / "opphevet.md").write_text("# Opphevet\n", encoding="utf-8")
    manifest = {
        "version": MANIFEST_VERSION,
        "generated_at": "2026-07-12T00:00:00Z",
        "documents": {
            "nl-1": {
                "doc_type": "lov",
                "xml_hash": "a" * 64,
                "markdown_path": "lover/skatteloven.md",
                "source_dataset": "gjeldende-lover",
                "last_seen": "2026-07-12T00:00:00Z",
                "status": "current",
                "slug": "skatteloven",
                "renderer_version": RENDERER_VERSION,
            },
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_audit_reports_clean_corpus_and_exits_zero(tmp_path: Path) -> None:
    _audit_corpus_fixture(tmp_path, orphan=False)

    result = runner.invoke(app, ["audit", "--corpus-path", str(tmp_path)])

    assert result.exit_code == 0
    assert "Corpus is clean" in result.stdout
    assert "1 current document" in result.stdout


def test_audit_exits_nonzero_on_integrity_drift_so_it_can_gate_ci(tmp_path: Path) -> None:
    """A file no manifest record claims. Exit 1 is what makes this usable as a
    CI gate rather than a report nobody reads."""
    _audit_corpus_fixture(tmp_path, orphan=True)

    result = runner.invoke(app, ["audit", "--corpus-path", str(tmp_path)])

    assert result.exit_code == 1
    assert "INTEGRITY (blocking)" in result.stdout
    assert "orphan_document (1)" in result.stdout
    assert "lover/opphevet.md" in result.stdout


def test_audit_advisory_only_findings_are_labeled_and_exit_zero(tmp_path: Path) -> None:
    """18 pre-existing unparsed_section_heading findings are registered
    follow-up work. If they failed the command, the CI gate would be
    permanently red — and a gate that is always red is one nobody reads,
    which is exactly how the 48 orphans stayed invisible. Advisory findings
    are therefore printed and labeled, but do not fail the default mode."""
    _audit_corpus_fixture(tmp_path, orphan=False, unparsed_heading=True)

    result = runner.invoke(app, ["audit", "--corpus-path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "ADVISORY" in result.stdout
    assert "non-blocking" in result.stdout
    assert "unparsed_section_heading (1)" in result.stdout
    assert "INTEGRITY" not in result.stdout  # no integrity section when empty


def test_audit_fail_on_all_makes_advisory_findings_block(tmp_path: Path) -> None:
    """The strict mode: for a corpus that should be advisory-clean too (e.g.
    after the section-grammar follow-up lands), `--fail-on all` restores the
    blanket gate."""
    _audit_corpus_fixture(tmp_path, orphan=False, unparsed_heading=True)

    result = runner.invoke(
        app,
        ["audit", "--corpus-path", str(tmp_path), "--fail-on", "all"],
    )

    assert result.exit_code == 1
    assert "unparsed_section_heading (1)" in result.stdout


def test_audit_fail_on_integrity_is_explicit_default(tmp_path: Path) -> None:
    """Spelling out the default must behave identically to omitting it."""
    _audit_corpus_fixture(tmp_path, orphan=True, unparsed_heading=True)

    result = runner.invoke(
        app,
        ["audit", "--corpus-path", str(tmp_path), "--fail-on", "integrity"],
    )

    assert result.exit_code == 1
    assert "INTEGRITY (blocking)" in result.stdout
    assert "ADVISORY" in result.stdout


def test_audit_never_writes_or_deletes(tmp_path: Path) -> None:
    """Read-only by contract: an audit that mutates the corpus it is auditing
    is not an audit. Guards against a future 'while we're here, fix it' edit."""
    _audit_corpus_fixture(tmp_path, orphan=True)
    before = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}

    runner.invoke(app, ["audit", "--corpus-path", str(tmp_path)])

    after = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    assert after == before
