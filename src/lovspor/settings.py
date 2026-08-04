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
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from lovspor.embeddings.provider import EmbeddingConfig, credential_from_env
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
    reembed_guard_max_fraction: float = 0.02
    """ADR-0006 mass-re-embed guard, blast-radius dimension: a keyed sync
    whose input-hash repair selection exceeds this fraction of current
    documents fails closed before any provider call. 0.02 = ~117 documents on
    the 5,880-doc corpus — an order of magnitude above every observed normal
    daily change (0-3 docs) and below every historical broad-drift incident
    that should have stopped (797, 2,336, corpus-wide). Raise deliberately
    via ``LOVSPOR_REEMBED_GUARD_MAX_FRACTION`` or the explicit sync override.
    """
    reembed_guard_max_tokens: int = 1_000_000
    """ADR-0006 guard, workload dimension: cap on the selected inputs' total
    token volume (~$0.13 at current pricing). Independent of the count cap
    because a handful of very large acts carries real spend — the largest
    single act measures ~0.3M tokens, so a few of them trip this while
    staying far under the count threshold. Full corpus is ~37.9M tokens.
    Override: ``LOVSPOR_REEMBED_GUARD_MAX_TOKENS`` or the sync flag.
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

    Retained because it is the field the installed base configures and every
    existing caller reads. :attr:`embedding` is the provider-agnostic view of
    the same thing and is what builds the adapter; the two are kept consistent
    by :meth:`from_env`.
    """
    embedding: EmbeddingConfig = EmbeddingConfig()
    """Which embedding space to use, and how to reach it.

    Defaults reproduce the historical OpenAI configuration exactly, so an
    install that sets only ``OPENAI_API_KEY`` behaves as it always has.
    """

    @model_validator(mode="before")
    @classmethod
    def _mirror_embedding_credential(cls, data: object) -> object:
        """Keep ``openai_api_key`` and ``embedding.api_key`` in agreement.

        Every existing caller — including deployments and tests that build
        ``Settings`` directly rather than through :meth:`from_env` — sets
        ``openai_api_key`` alone. Without this the credential would reach the
        field but never the factory, and embeddings would silently stop being
        written: a backwards-compatibility break that presents exactly like
        "no key configured". Whichever side was populated fills the other; an
        explicit ``embedding.api_key`` wins.

        Runs *before* validation because the model is frozen, so there is no
        supported way to reconcile two fields once it is built.
        """
        if not isinstance(data, dict):
            return data
        key = data.get("openai_api_key")
        raw_embedding = data.get("embedding")
        embedding = (
            raw_embedding
            if isinstance(raw_embedding, EmbeddingConfig)
            else EmbeddingConfig.model_validate(raw_embedding or {})
        )
        if embedding.api_key and not key:
            data = {**data, "openai_api_key": embedding.api_key}
        elif key and not embedding.api_key:
            embedding = embedding.model_copy(update={"api_key": str(key)})
        return {**data, "embedding": embedding}

    @field_validator("git_commit_mode")
    @classmethod
    def _supported_commit_mode(cls, value: str) -> str:
        if value not in {"per-document", "single"}:
            raise ValueError(
                f"git_commit_mode must be 'per-document' or 'single', got: {value!r}",
            )
        return value

    @field_validator("reembed_guard_max_fraction")
    @classmethod
    def _guard_fraction_in_range(cls, value: float) -> float:
        if not 0 < value <= 1:
            raise ValueError(
                f"reembed_guard_max_fraction must be in (0, 1], got: {value!r}",
            )
        return value

    @field_validator("reembed_guard_max_tokens")
    @classmethod
    def _guard_tokens_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(
                f"reembed_guard_max_tokens must be positive, got: {value!r}",
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
            LOVSPOR_EMBEDDING_*           (see EmbeddingConfig.from_env)
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

        _apply_reembed_guard(data, overrides)

        embedding = _resolve_embedding(overrides)
        data["embedding"] = embedding
        if embedding.api_key is not None:
            data["openai_api_key"] = embedding.api_key

        return cls.model_validate(data)


def _resolve_embedding(overrides: dict[str, object]) -> EmbeddingConfig:
    """Embedding config for this Settings, with the credential always applied.

    The credential is resolved once, from one rule, and then applied to
    whichever config we end up with. Deriving it per branch is how it went
    missing twice — most recently for a caller overriding the model through
    ``embedding=``, which inherited no key at all. The symptom is
    indistinguishable from "no key configured": the sync simply stops writing
    sidecars and says nothing.
    """
    # Explicit None check, not `or`: an empty override means "no credential",
    # and falling through to the environment there would re-enable embeddings
    # for a caller who deliberately blanked the key. Same rule the numeric
    # fields above already follow. Env vars keep empty-means-unset, which is
    # the historical behaviour and the normal convention for them.
    raw_key = overrides.get("openai_api_key")
    if raw_key is None:
        raw_key = credential_from_env()
    override = overrides.get("embedding")
    if override is None:
        return EmbeddingConfig.from_env(**({"api_key": raw_key} if raw_key is not None else {}))
    try:
        embedding = (
            override
            if isinstance(override, EmbeddingConfig)
            else EmbeddingConfig.model_validate(override)
        )
    except ValidationError as exc:
        raise ConfigError(f"invalid embedding override: {exc}") from exc
    # An explicit config keeps its own credential; it inherits the ambient one
    # only when it declares none.
    if embedding.api_key is None and raw_key is not None:
        return embedding.model_copy(update={"api_key": str(raw_key)})
    return embedding


def _apply_reembed_guard(
    data: dict[str, object],
    overrides: dict[str, object],
) -> None:
    """Resolve the two ADR-0006 guard thresholds from overrides or env."""
    raw_fraction = overrides.get("reembed_guard_max_fraction")
    if raw_fraction is None:
        raw_fraction = os.environ.get("LOVSPOR_REEMBED_GUARD_MAX_FRACTION")
    if raw_fraction is not None:
        try:
            data["reembed_guard_max_fraction"] = float(str(raw_fraction))
        except ValueError as exc:
            raise ConfigError(
                f"LOVSPOR_REEMBED_GUARD_MAX_FRACTION must be a float, got: {raw_fraction!r}",
            ) from exc
    raw_tokens = overrides.get("reembed_guard_max_tokens")
    if raw_tokens is None:
        raw_tokens = os.environ.get("LOVSPOR_REEMBED_GUARD_MAX_TOKENS")
    if raw_tokens is not None:
        try:
            data["reembed_guard_max_tokens"] = int(str(raw_tokens))
        except ValueError as exc:
            raise ConfigError(
                f"LOVSPOR_REEMBED_GUARD_MAX_TOKENS must be an int, got: {raw_tokens!r}",
            ) from exc


def _apply_optional(
    data: dict[str, object],
    key: str,
    overrides: dict[str, object],
    env_var: str,
) -> None:
    value = overrides.get(key) or os.environ.get(env_var)
    if value is not None:
        data[key] = value
