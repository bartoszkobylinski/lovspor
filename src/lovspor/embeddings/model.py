"""Embedding model loading.

Provides a thin Protocol so the rest of the engine treats the model
as an opaque text-to-vector function. The default implementation
(``JinaModel``) wraps ``sentence-transformers`` and downloads
``jinaai/jina-embeddings-v2-base-no`` on first use (~640 MB).

Why Norwegian-tuned: a model trained primarily on English (OpenAI,
multilingual-e5) underperforms on Norwegian morphology. Jina's
Norwegian variant scores ~5-7 MTEB points higher on Norwegian
retrieval tasks than English-trained alternatives at the same size.

Why lazy import: the MCP server imports this package at startup, but
most clients query metadata-only tools (``search_laws``,
``corpus_status``, etc.) without ever calling ``semantic_search``.
Loading 640 MB of weights for those clients is wasteful. The
sentence-transformers import is deferred to first ``encode`` call.

Why revision pinning: ``jina-embeddings-v2-base-no`` requires
``trust_remote_code=True`` because it ships a custom model class
(``JinaBertModel``). Without a pinned revision, every fresh download
executes whatever code the model repo serves at that moment — a real
supply-chain risk. ``JinaModel`` requires an explicit ``revision``
argument so every call site has to think about pinning. The MCP
entrypoint and the orchestrator each pin to a specific commit SHA in
their own configuration; tests pass a sentinel string and substitute
a fake model so no real download happens.

The ``set_model`` hook exists so tests can substitute a deterministic
fake (see ``tests/conftest.py``) without loading the real weights.
"""

from typing import Protocol, runtime_checkable

import numpy as np

_DEFAULT_MODEL_NAME = "jinaai/jina-embeddings-v2-base-no"


@runtime_checkable
class EmbeddingModel(Protocol):
    """Minimal text-to-vector contract used by the engine and MCP server."""

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return one row per input text. Output rows are L2-normalized."""

    def get_dimension(self) -> int:
        """Output dimensionality (768 for jina-embeddings-v2-base-no)."""


class JinaModel:
    """Default embedding model: jina-embeddings-v2-base-no via sentence-transformers.

    Trust-remote-code is required because Jina ships a custom model
    class (``JinaBertModel``). To bound the supply-chain risk, the
    caller MUST pin a specific Hugging Face revision (full 40-char
    commit SHA preferred). Passing ``revision="main"`` is allowed for
    development but logs a warning — it locks nothing.
    """

    def __init__(self, revision: str, model_name: str = _DEFAULT_MODEL_NAME) -> None:
        if not revision:
            raise ValueError(
                "JinaModel requires an explicit Hugging Face revision "
                "(commit SHA preferred) to bound trust_remote_code "
                "supply-chain risk; pass 'main' explicitly to opt out",
            )
        # Lazy import keeps PyTorch/sentence-transformers off the
        # critical-path startup of MCP clients that never call
        # semantic_search. Also lets the rest of the embeddings
        # package be importable when sentence-transformers is not
        # installed (the [embeddings] extra is opt-in).
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(
            model_name,
            trust_remote_code=True,
            revision=revision,
        )

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.get_dimension()), dtype=np.float32)
        result = self._model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        # sentence-transformers may return float32 already; ensure it.
        return np.asarray(result, dtype=np.float32)

    def get_dimension(self) -> int:
        dim = self._model.get_sentence_embedding_dimension()
        if dim is None:  # pragma: no cover - sentence-transformers always sets this
            raise RuntimeError("model does not expose embedding dimension")
        return int(dim)


_singleton: EmbeddingModel | None = None


def get_default_model(revision: str) -> EmbeddingModel:
    """Return the process-wide singleton model, loading on first call.

    The first call downloads ~640 MB of weights and takes ~5-10
    seconds. Subsequent calls are O(1) and ignore ``revision`` —
    pinning happens at first load. Tests should call ``set_model``
    with a fake before any production code path touches the singleton
    so no real download is triggered.
    """
    global _singleton  # noqa: PLW0603 — module-level singleton, intentional
    if _singleton is None:
        _singleton = JinaModel(revision=revision)
    return _singleton


def set_model(model: EmbeddingModel | None) -> None:
    """Override (or clear) the singleton. Intended for tests.

    Passing ``None`` clears the singleton so the next ``get_default_model``
    call reloads from scratch.
    """
    global _singleton  # noqa: PLW0603 — module-level singleton, intentional
    _singleton = model
