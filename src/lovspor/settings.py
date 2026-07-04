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

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, ConfigDict, field_validator

from lovspor.errors import ConfigError

_ENV_LOADED = False


def _ensure_env_loaded() -> None:
    global _ENV_LOADED  # noqa: PLW0603 - process-wide one-shot init
    if not _ENV_LOADED:
        # usecwd=True searches from the working directory the CLI was launched
        # in, not from this module's installed location. Without it,
        # find_dotenv walks up from site-packages and never sees the user's
        # project .env — so `lovspor mcp` run from a checkout with a .env would
        # miss LOVVERK_CORPUS_PATH / OPENAI_API_KEY. Empty result -> no-op.
        load_dotenv(find_dotenv(usecwd=True), override=False)
        _ENV_LOADED = True


def load_env() -> None:
    """Load ``.env`` into the process environment (idempotent, one-shot).

    Public entry point for code that reads ``os.environ`` directly without
    building :class:`Settings` — notably the MCP server, which reads
    ``OPENAI_API_KEY`` from the environment to enable ``semantic_search``.
    ``override=False`` means an already-exported variable always wins.
    """
    _ensure_env_loaded()


class Settings(BaseModel):
    """Runtime configuration resolved from env vars or explicit overrides."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_dir: Path
    lovverk_repo_path: Path
    git_commit_mode: str = "per-document"
    max_removal_ratio: float = 0.10
    """Abort a sync that would remove more than this fraction of any one
    dataset's current documents (guards against an empty/truncated upstream
    tarball wiping the corpus). Applies only to datasets above a minimum
    doc count; see ``_REMOVAL_GUARD_MIN_DOCS``. Raise it for a run with a
    genuine mass removal via ``LOVSPOR_MAX_REMOVAL_RATIO``.
    """
    http_timeout_seconds: float = 120.0
    http_user_agent: str = "lovspor/0.1 (+https://github.com/bartoszkobylinski/lovspor)"
    log_level: str = "INFO"
    openai_api_key: str | None = None
    """OpenAI API key for the text-embedding-3-large embedder.

    Required for the engine sync (``_write_one`` writes per-doc
    embeddings) and for the MCP ``semantic_search`` tool. Read from
    ``OPENAI_API_KEY`` env var or accepts ``OPENAI_APIKEY`` as
    fallback (some users have it without underscore from older
    docs).
    """

    @field_validator("git_commit_mode")
    @classmethod
    def _supported_commit_mode(cls, value: str) -> str:
        if value not in {"per-document", "single"}:
            raise ValueError(
                f"git_commit_mode must be 'per-document' or 'single', got: {value!r}",
            )
        return value

    @field_validator("max_removal_ratio")
    @classmethod
    def _ratio_in_unit_interval(cls, value: float) -> float:
        if not 0.0 < value <= 1.0:
            raise ValueError(
                f"max_removal_ratio must be in (0, 1], got: {value!r}",
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
            LOVSPOR_MAX_REMOVAL_RATIO     (float in (0, 1])
            LOVSPOR_HTTP_TIMEOUT_SECONDS  (float)
            LOVSPOR_HTTP_USER_AGENT       (str)
            LOVSPOR_LOG_LEVEL             (DEBUG | INFO | WARNING | ERROR)
            OPENAI_API_KEY                (str — also reads OPENAI_APIKEY)
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

        # Explicit None check (not 'or') so an explicit override of a
        # small ratio is not swallowed by the env fallback.
        raw_ratio = overrides.get("max_removal_ratio")
        if raw_ratio is None:
            raw_ratio = os.environ.get("LOVSPOR_MAX_REMOVAL_RATIO")
        if raw_ratio is not None:
            try:
                data["max_removal_ratio"] = float(str(raw_ratio))
            except ValueError as exc:
                raise ConfigError(
                    f"LOVSPOR_MAX_REMOVAL_RATIO must be a float, got: {raw_ratio!r}",
                ) from exc

        # OpenAI key: try the canonical env var first, then the
        # underscore-less variant some users have. Override always wins.
        raw_key = overrides.get("openai_api_key")
        if raw_key is None:
            raw_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_APIKEY")
        if raw_key is not None:
            data["openai_api_key"] = str(raw_key)

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
