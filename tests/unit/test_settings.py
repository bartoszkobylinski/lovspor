"""Tests for lovspor.settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

import lovspor.settings as settings_module
from lovspor.errors import ConfigError
from lovspor.settings import Settings, load_env


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every LOVSPOR_* env var so each test starts from a clean slate."""
    for key in list(__import__("os").environ):
        if key.startswith("LOVSPOR_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_APIKEY", raising=False)


def test_load_env_invokes_dotenv_loader_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The public load_env() delegates to the guarded one-shot .env loader —
    the MCP server relies on it since it reads os.environ without Settings."""
    calls: list[bool] = []
    monkeypatch.setattr(settings_module, "_ENV_LOADED", False)
    monkeypatch.setattr(settings_module, "load_dotenv", lambda **_kwargs: calls.append(True))

    load_env()
    load_env()  # one-shot: the guard prevents a second load

    assert calls == [True]


def test_from_env_reads_required_vars(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))
    settings = Settings.from_env()
    assert settings.data_dir == (tmp_path / "data").resolve()
    assert settings.lovverk_repo_path == (tmp_path / "lovverk").resolve()


def test_from_env_raises_config_error_when_data_dir_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))
    with pytest.raises(ConfigError, match="LOVSPOR_DATA_DIR"):
        Settings.from_env()


def test_from_env_raises_config_error_when_output_path_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ConfigError, match="LOVSPOR_OUTPUT_REPO_PATH"):
        Settings.from_env()


def test_from_env_applies_optional_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))
    monkeypatch.setenv("LOVSPOR_GIT_COMMIT_MODE", "single")
    monkeypatch.setenv("LOVSPOR_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOVSPOR_HTTP_TIMEOUT_SECONDS", "42.5")
    monkeypatch.setenv("LOVSPOR_HTTP_USER_AGENT", "test-agent")
    settings = Settings.from_env()
    assert settings.git_commit_mode == "single"
    assert settings.log_level == "DEBUG"
    assert settings.http_timeout_seconds == 42.5
    assert settings.http_user_agent == "test-agent"


def _required_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))


def test_max_removal_ratio_defaults_to_ten_percent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _required_env(monkeypatch, tmp_path)
    assert Settings.from_env().max_removal_ratio == pytest.approx(0.10)


def test_max_removal_ratio_reads_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LOVSPOR_MAX_REMOVAL_RATIO", "0.5")
    assert Settings.from_env().max_removal_ratio == pytest.approx(0.5)


def test_max_removal_ratio_explicit_override_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LOVSPOR_MAX_REMOVAL_RATIO", "0.8")

    settings = Settings.from_env(max_removal_ratio=0.25)

    assert settings.max_removal_ratio == pytest.approx(0.25)


def test_max_removal_ratio_rejects_non_float(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LOVSPOR_MAX_REMOVAL_RATIO", "half")
    with pytest.raises(ConfigError, match="LOVSPOR_MAX_REMOVAL_RATIO"):
        Settings.from_env()


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_max_removal_ratio_must_be_in_unit_interval(bad: float) -> None:
    with pytest.raises(ValidationError, match="max_removal_ratio"):
        Settings(
            data_dir=Path("/d"),
            lovverk_repo_path=Path("/r"),
            max_removal_ratio=bad,
        )


def test_max_removal_ratio_allows_one_to_disable_guard() -> None:
    settings = Settings(
        data_dir=Path("/d"),
        lovverk_repo_path=Path("/r"),
        max_removal_ratio=1.0,
    )

    assert settings.max_removal_ratio == pytest.approx(1.0)


def test_from_env_uses_defaults_when_optional_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))
    settings = Settings.from_env()
    assert settings.git_commit_mode == "per-document"
    assert settings.log_level == "INFO"
    assert settings.http_timeout_seconds == 120.0
    assert settings.http_user_agent == "lovspor/0.1 (+https://github.com/bartoszkobylinski/lovspor)"
    assert settings.openai_api_key is None


def test_from_env_explicit_override_beats_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "from-env"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "from-env"))
    settings = Settings.from_env(
        data_dir=tmp_path / "from-override",
        lovverk_repo_path=tmp_path / "from-override",
    )
    assert settings.data_dir == (tmp_path / "from-override").resolve()


def test_from_env_rejects_malformed_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))
    monkeypatch.setenv("LOVSPOR_HTTP_TIMEOUT_SECONDS", "not-a-number")
    with pytest.raises(ConfigError, match="must be a float"):
        Settings.from_env()


def test_from_env_rejects_unknown_commit_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))
    monkeypatch.setenv("LOVSPOR_GIT_COMMIT_MODE", "stale")
    with pytest.raises(ValidationError):
        Settings.from_env()


def test_settings_is_frozen(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "lovverk",
    )
    with pytest.raises(ValidationError):
        settings.git_commit_mode = "single"  # type: ignore[misc]


def test_settings_rejects_extra_fields(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            data_dir=tmp_path / "data",
            lovverk_repo_path=tmp_path / "lovverk",
            future_field="tolerated",  # type: ignore[call-arg]
        )


def test_data_dir_is_always_absolute(tmp_path: Path) -> None:
    """Paths are resolved at construction time to avoid cwd surprises."""
    settings = Settings(
        data_dir=Path("relative-dir"),
        lovverk_repo_path=tmp_path / "lovverk",
    )
    assert settings.data_dir.is_absolute()


def test_explicit_zero_timeout_override_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """MEDIUM regression guard: passing http_timeout_seconds=0.0 explicitly
    must not fall through to the env default. Codex PR #15 reproducer.
    Same fix pattern as the earlier lovdata.py timeout bug."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))
    monkeypatch.setenv("LOVSPOR_HTTP_TIMEOUT_SECONDS", "999.0")
    settings = Settings.from_env(http_timeout_seconds=0.0)
    assert settings.http_timeout_seconds == 0.0


def test_from_env_reads_openai_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))
    monkeypatch.setenv("OPENAI_API_KEY", "canonical-key")

    settings = Settings.from_env()

    assert settings.openai_api_key == "canonical-key"


def test_from_env_reads_legacy_openai_api_key_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))
    monkeypatch.setenv("OPENAI_APIKEY", "legacy-key")

    settings = Settings.from_env()

    assert settings.openai_api_key == "legacy-key"


def test_from_env_openai_api_key_override_beats_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("LOVSPOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", str(tmp_path / "lovverk"))
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    settings = Settings.from_env(openai_api_key="override-key")

    assert settings.openai_api_key == "override-key"
