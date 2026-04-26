"""Project-wide settings, loaded from environment (and ``.env`` if present).

``python-dotenv`` loads ``.env`` once at module import, then pydantic-style
field resolution reads ``os.environ``. Missing required values surface as
``ConfigError`` with a clear message. ``Path`` values are always returned
as absolute paths, resolved at load time, to avoid surprises when the
process ``cwd`` changes during a long-running sync.
"""

import os
from pathlib import Path
from typing import Self

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, field_validator

from lovspor.errors import ConfigError

_ENV_LOADED = False


def _ensure_env_loaded() -> None:
    global _ENV_LOADED  # noqa: PLW0603 - process-wide one-shot init
    if not _ENV_LOADED:
        load_dotenv(override=False)
        _ENV_LOADED = True


class Settings(BaseModel):
    """Runtime configuration resolved from env vars or explicit overrides."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_dir: Path
    lovverk_repo_path: Path
    git_commit_mode: str = "per-document"
    http_timeout_seconds: float = 120.0
    http_user_agent: str = "lovspor/0.1 (+https://github.com/bartoszkobylinski/lovspor)"
    log_level: str = "INFO"

    @field_validator("git_commit_mode")
    @classmethod
    def _supported_commit_mode(cls, value: str) -> str:
        if value not in {"per-document", "single"}:
            raise ValueError(
                f"git_commit_mode must be 'per-document' or 'single', got: {value!r}",
            )
        return value

    @field_validator("data_dir", "lovverk_repo_path")
    @classmethod
    def _absolute_paths(cls, value: Path) -> Path:
        return value.resolve()

    @classmethod
    def from_env(cls, **overrides: object) -> Self:
        """Resolve settings from the environment, with optional overrides.

        Required env vars:
            LOVSPOR_DATA_DIR
            LOVSPOR_OUTPUT_REPO_PATH (path to the lovverk clone)

        Optional env vars:
            LOVSPOR_GIT_COMMIT_MODE       (per-document | single)
            LOVSPOR_HTTP_TIMEOUT_SECONDS  (float)
            LOVSPOR_HTTP_USER_AGENT       (str)
            LOVSPOR_LOG_LEVEL             (DEBUG | INFO | WARNING | ERROR)
        """
        _ensure_env_loaded()
        data: dict[str, object] = {}

        raw_data_dir = overrides.get("data_dir") or os.environ.get("LOVSPOR_DATA_DIR")
        if raw_data_dir is None:
            raise ConfigError("LOVSPOR_DATA_DIR is required")
        data["data_dir"] = Path(str(raw_data_dir))

        raw_corpus = overrides.get("lovverk_repo_path") or os.environ.get(
            "LOVSPOR_OUTPUT_REPO_PATH",
        )
        if raw_corpus is None:
            raise ConfigError("LOVSPOR_OUTPUT_REPO_PATH is required")
        data["lovverk_repo_path"] = Path(str(raw_corpus))

        _apply_optional(data, "git_commit_mode", overrides, "LOVSPOR_GIT_COMMIT_MODE")
        _apply_optional(data, "log_level", overrides, "LOVSPOR_LOG_LEVEL")
        _apply_optional(
            data,
            "http_user_agent",
            overrides,
            "LOVSPOR_HTTP_USER_AGENT",
        )

        # Use explicit None check (not 'or') so an explicit override of
        # 0.0 — falsy but a valid timeout — wins over the env fallback.
        raw_timeout = overrides.get("http_timeout_seconds")
        if raw_timeout is None:
            raw_timeout = os.environ.get("LOVSPOR_HTTP_TIMEOUT_SECONDS")
        if raw_timeout is not None:
            try:
                data["http_timeout_seconds"] = float(str(raw_timeout))
            except ValueError as exc:
                raise ConfigError(
                    f"LOVSPOR_HTTP_TIMEOUT_SECONDS must be a float, got: {raw_timeout!r}",
                ) from exc

        return cls.model_validate(data)


def _apply_optional(
    data: dict[str, object],
    key: str,
    overrides: dict[str, object],
    env_var: str,
) -> None:
    value = overrides.get(key) or os.environ.get(env_var)
    if value is not None:
        data[key] = value
