"""Provider-agnostic embedding configuration and the one factory that builds adapters.

The engine and the MCP server both need an :class:`EmbeddingModel`. Before this
module they each reached for ``OpenAIEmbedder`` directly and each re-read
``OPENAI_API_KEY`` with its own copy of the fallback rule, so "which provider
does lovspor use" was answered in four places at once. Everything now goes
``EmbeddingConfig.from_env() -> create_embedder() -> EmbeddingModel``, and
provider-specific knowledge lives behind the adapter boundary.

**Embedding-space identity is the load-bearing concept here.** A vector only
means something relative to the model that produced it, and two models can emit
the same number of dimensions while describing completely unrelated spaces.
Comparing across them yields confident, plausible, meaningless scores — no
exception, no shape error, nothing a caller can detect. So a config does not
merely name a provider; it names a *space* (:attr:`EmbeddingConfig.space_id`),
and consumers compare space ids rather than dimensions.

What this module deliberately does NOT do: make it safe to point lovspor at a
different embedding model and keep searching an existing corpus. The corpus
sidecar format records the dimension and nothing else (see ``store.py``), so a
``.bin`` file cannot say which space it belongs to. Until that persistence
contract exists, compatibility with an existing corpus is provable only for
:data:`LEGACY_SPACE_ID` — the space every sidecar in ``lovverk`` was in fact
written in. ``CorpusReader`` therefore refuses semantic search for any other
configured space instead of guessing. Kept intentionally small: an explicit
dict of builders, not a plugin framework.
"""

import os
from typing import TYPE_CHECKING, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from lovspor.errors import ConfigError

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from lovspor.embeddings.model import EmbeddingModel

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL_NAME = "text-embedding-3-large"
DEFAULT_DIMENSION = 3072
"""Native dimensionality of text-embedding-3-large.

The OpenAI API supports dimensionality reduction via the ``dimensions``
parameter, but this engine uses native dim because storage savings (~33% for
1024-dim, ~92% for 256-dim) come at a ~1-3% Recall@5 cost on benchmarks. The
200 MB int8 footprint at 3072 dim is acceptable for the lovverk corpus.
"""

OPENAI_ENDPOINT = "https://api.openai.com/v1/embeddings"

LEGACY_SPACE_ID = f"{DEFAULT_PROVIDER}:{DEFAULT_MODEL_NAME}:{DEFAULT_DIMENSION}"
"""The space every embedding sidecar in the published corpus was written in.

Not a default anyone chose to record — it is a fact reconstructed from the code
that wrote those files, because the files themselves carry no model identity.
Treat it as the *only* space whose compatibility with an existing corpus can be
asserted; see the module docstring.
"""

UNKNOWN_SPACE_ID = "unknown"
"""Space of an adapter that does not declare one — never provably compatible."""

SUPPORTED_PROVIDERS = frozenset({DEFAULT_PROVIDER})


class EmbeddingConfig(BaseModel):
    """Which embedding space to use, and the credentials to reach it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = DEFAULT_PROVIDER
    model_name: str = DEFAULT_MODEL_NAME
    dimension: int = DEFAULT_DIMENSION
    base_url: str | None = None
    """Override the provider's endpoint. ``None`` means the provider default."""
    api_key: str | None = None
    """``None`` means embedding capability is simply unavailable, not an error."""

    @field_validator("dimension")
    @classmethod
    def _positive_dimension(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(f"dimension must be positive, got: {value!r}")
        return value

    @property
    def space_id(self) -> str:
        """Stable identity of the vector space this config produces.

        A custom ``base_url`` is reported as ``openai-compatible:<host>:…``
        rather than ``openai:…``: the request/response protocol may be
        identical, but whatever serves that host is not necessarily the model
        OpenAI serves under that name, and labelling it "openai" would make
        provenance a guess. Different label, different space, no silent mixing.
        """
        if self.base_url is None:
            return f"{self.provider}:{self.model_name}:{self.dimension}"
        host = urlsplit(self.base_url).netloc or self.base_url
        return f"{self.provider}-compatible:{host}:{self.model_name}:{self.dimension}"

    @property
    def is_corpus_compatible(self) -> bool:
        """Whether this space is the one the published corpus holds.

        The honest answer is only available for :data:`LEGACY_SPACE_ID`; every
        other space is unprovable rather than known-wrong, and is refused for
        that reason.
        """
        return self.space_id == LEGACY_SPACE_ID

    @classmethod
    def from_env(cls, **overrides: object) -> Self:
        """Resolve embedding configuration from the environment.

        Optional env vars:
            LOVSPOR_EMBEDDING_PROVIDER    (default "openai")
            LOVSPOR_EMBEDDING_MODEL       (default "text-embedding-3-large")
            LOVSPOR_EMBEDDING_DIMENSION   (int, default 3072)
            LOVSPOR_EMBEDDING_BASE_URL    (str — provider endpoint override)
            LOVSPOR_EMBEDDING_API_KEY     (provider-neutral credential)
            OPENAI_API_KEY                (also reads OPENAI_APIKEY)

        Setting none of them reproduces the historical behaviour exactly: the
        OpenAI adapter, ``text-embedding-3-large``, 3072 dimensions, credential
        from ``OPENAI_API_KEY``.
        """
        data: dict[str, object] = {}
        _apply(data, "provider", overrides, "LOVSPOR_EMBEDDING_PROVIDER")
        _apply(data, "model_name", overrides, "LOVSPOR_EMBEDDING_MODEL")
        _apply(data, "base_url", overrides, "LOVSPOR_EMBEDDING_BASE_URL")

        raw_dimension = overrides.get("dimension")
        if raw_dimension is None:
            raw_dimension = os.environ.get("LOVSPOR_EMBEDDING_DIMENSION")
        if raw_dimension is not None:
            try:
                data["dimension"] = int(str(raw_dimension))
            except ValueError as exc:
                raise ConfigError(
                    f"LOVSPOR_EMBEDDING_DIMENSION must be an integer, got: {raw_dimension!r}",
                ) from exc

        raw_key = overrides.get("api_key")
        if raw_key is None:
            raw_key = credential_from_env()
        if raw_key is not None:
            data["api_key"] = str(raw_key)

        return cls.model_validate(data)


def credential_from_env() -> str | None:
    """The embedding credential, resolved from the environment.

    One function because this rule has been duplicated before: four call sites
    each re-implemented the ``OPENAI_APIKEY`` fallback, and every copy is a
    place a credential can go missing. Anything that needs a key asks here.

    The provider-neutral variable wins when both are set, but plain
    ``OPENAI_API_KEY`` — with its long-standing underscore-less fallback —
    keeps working untouched, because that is the whole installed base.
    """
    return (
        os.environ.get("LOVSPOR_EMBEDDING_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENAI_APIKEY")
    )


def _apply(
    data: dict[str, object],
    key: str,
    overrides: dict[str, object],
    env_var: str,
) -> None:
    value = overrides.get(key) or os.environ.get(env_var)
    if value is not None:
        data[key] = value


def create_embedder(
    config: EmbeddingConfig,
    *,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> "EmbeddingModel | None":
    """Build the adapter named by ``config``, or ``None`` if unusable.

    ``None`` means "embedding capability is unavailable" — the ordinary state
    of an install with no credential, in which the corpus still syncs and every
    MCP tool other than ``semantic_search`` still works.

    An unrecognised provider raises :class:`ConfigError` instead: that is a
    misconfiguration, and silently proceeding without embeddings (or worse,
    falling back to a different provider than the operator asked for) would
    hide it. Callers decide how loud to be — the sync lets it surface, the MCP
    server reports it through ``semantic_search`` so the other tools survive.
    """
    if config.provider not in SUPPORTED_PROVIDERS:
        raise ConfigError(
            f"unknown embedding provider: {config.provider!r}. "
            f"Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}. "
            "Set LOVSPOR_EMBEDDING_PROVIDER to a supported value, or unset it "
            "to use the default.",
        )
    if not config.api_key:
        return None

    # Local import: this module is the config surface and is imported by
    # settings.py, which the CLI loads on every command. The adapter drags in
    # httpx, numpy and tiktoken, and nothing that only reads config should pay
    # for them.
    from lovspor.embeddings.model import OpenAIEmbedder  # noqa: PLC0415

    kwargs: dict[str, object] = {}
    if timeout_seconds is not None:
        kwargs["timeout_seconds"] = timeout_seconds
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return OpenAIEmbedder(
        api_key=config.api_key,
        model_name=config.model_name,
        dim=config.dimension,
        base_url=config.base_url,
        **kwargs,  # type: ignore[arg-type]
    )


def space_id_of(embedder: "EmbeddingModel | None") -> str | None:
    """Space id an adapter declares, or :data:`UNKNOWN_SPACE_ID` if it declares none.

    ``None`` only for "no embedder at all". A third-party adapter written
    against the two-method protocol that predates ``space_id`` is not rejected
    outright — it is treated as an unknown space, which is exactly as
    compatible with an existing corpus as it is provable to be.
    """
    if embedder is None:
        return None
    declared = getattr(embedder, "space_id", None)
    if isinstance(declared, str) and declared:
        return declared
    return UNKNOWN_SPACE_ID
