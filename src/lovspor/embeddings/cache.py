"""The paid-embedding boundary, and the durable cache behind it (#802, design §6).

``_write_embeddings_for_doc`` is the only place corpus vectors are paid for,
and both the sync change path and the backfill/repair path reach it. It asks
:func:`quantized_document_vectors` for a document's int8 rows and scale:

* no cache (``LOVSPOR_EMBEDDING_CACHE_DIR`` unset, the default): exactly
  today's ``quantize_int8(embedder.encode(texts))``;
* cache: :class:`CachingEmbedder` reads the document entry first (a hit costs
  zero provider calls). On a miss it walks the embedder's own request plan,
  reuses every valid unit checkpoint, pays for each missing request with one
  ``encode`` call and persists that unit **before** the next request. Then it
  quantizes the whole document and persists the document entry.

A refused entry stops the run (``EmbeddingCacheError``); it is never paid
over. A request in flight when the process dies is lost however this is
built: no response means nothing to store.
"""

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from lovspor.embeddings.cache_format import CachedVectors, EntryKey, ReadPolicy, UnitKey
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from lovspor.embeddings.model import EmbeddingModel, count_input_tokens
from lovspor.embeddings.provider import DEFAULT_MODEL_NAME, UNKNOWN_SPACE_ID, space_id_of
from lovspor.embeddings.quantize import quantize_int8
from lovspor.errors import EmbeddingCacheError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from lovspor.settings import Settings

CACHE_DIR_VARIABLE = "LOVSPOR_EMBEDDING_CACHE_DIR"
MAX_AGE_VARIABLE = "LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS"
DEFAULT_MAX_AGE_DAYS = 30
_PROGRESS_EVERY = 50


@dataclass
class CacheProgress:
    """Counts for the sync log. Never text, keys or paths (design §6.1)."""

    documents: int = 0
    documents_cached: int = 0
    units_cached: int = 0
    units_paid: int = 0
    tokens_paid: int = 0

    def line(self) -> str:
        return (
            f"embedding-cache: documents={self.documents} "
            f"documents_cached={self.documents_cached} units_cached={self.units_cached} "
            f"units_paid={self.units_paid} estimated_tokens_paid={self.tokens_paid}"
        )


class CachingEmbedder:
    """The configured adapter, with a durable cache in front of its paid calls."""

    def __init__(self, inner: EmbeddingModel, store: LocalEntryStore, esi: str) -> None:
        self._inner = inner
        self._store = store
        self._esi = esi
        self.progress = CacheProgress()

    def encode(self, texts: list[str]) -> np.ndarray:
        return self._inner.encode(texts)

    def get_dimension(self) -> int:
        return self._inner.get_dimension()

    @property
    def space_id(self) -> str:
        return self._esi

    @property
    def descriptor(self) -> str:
        return str(getattr(self._inner, "descriptor", ""))

    @property
    def model_name(self) -> str:
        return str(getattr(self._inner, "model_name", DEFAULT_MODEL_NAME))

    def document_vectors(self, inputs: list[EmbeddingInput], input_hash: str) -> CachedVectors:
        """Int8 rows and scale of one non-empty document: cache first, then paid units."""
        key = EntryKey(
            esi=self._esi, input_hash=input_hash, dim=self.get_dimension(), rows=len(inputs)
        )
        self._count_document()
        stored = self._store.get(key)
        if stored is not None:
            self.progress.documents_cached += 1
            return stored
        matrix = self._assemble(inputs, input_hash)
        quantized, scale = quantize_int8(matrix)
        return self._store.put(_entry(key, quantized, scale))

    def _assemble(self, inputs: list[EmbeddingInput], input_hash: str) -> np.ndarray:
        texts = [item.text for item in inputs]
        bounds = _plan(self._inner, texts)
        if len(bounds) == 1:
            return self._pay(texts)
        parts = [
            self._unit(inputs[start:end], input_hash, index, len(bounds))
            for index, (start, end) in enumerate(bounds)
        ]
        return np.vstack(parts)

    def _unit(
        self, inputs: list[EmbeddingInput], input_hash: str, index: int, count: int
    ) -> np.ndarray:
        unit = UnitKey(index=index, count=count, input_hash=hash_embedding_inputs(inputs))
        key = EntryKey(
            esi=self._esi,
            input_hash=input_hash,
            dim=self.get_dimension(),
            rows=len(inputs),
            unit=unit,
        )
        stored = self._store.get(key)
        if stored is not None:
            self.progress.units_cached += 1
            return stored.vectors
        vectors = self._pay([item.text for item in inputs])
        return self._store.put(_entry(key, vectors.astype("<f4"), None)).vectors

    def _pay(self, texts: list[str]) -> np.ndarray:
        """One paid request's worth of vectors, shape-checked before it is stored."""
        vectors = np.asarray(self._inner.encode(texts), dtype=np.float32)
        self.progress.units_paid += 1
        self.progress.tokens_paid += sum(count_input_tokens(t, self.model_name) for t in texts)
        self._check_response(vectors, len(texts))
        return vectors

    def _check_response(self, vectors: np.ndarray, count: int) -> None:
        """Refuse a response the store would refuse to read back, before storing it."""
        if vectors.shape != (count, self.get_dimension()):
            raise EmbeddingCacheError(
                f"provider returned shape {vectors.shape} for {count} inputs "
                f"at dimension {self.get_dimension()}; nothing was stored",
            )
        if not np.isfinite(vectors).all():
            raise EmbeddingCacheError(
                "provider returned non-finite vector values; nothing was stored "
                "and no further request was sent",
            )

    def _count_document(self) -> None:
        self.progress.documents += 1
        if self.progress.documents % _PROGRESS_EVERY == 0:
            print(self.progress.line(), file=sys.stderr, flush=True)


def quantized_document_vectors(
    embedder: EmbeddingModel, inputs: list[EmbeddingInput], input_hash: str
) -> tuple[np.ndarray, float]:
    """Int8 rows and scale for a non-empty document — the one paid boundary."""
    if isinstance(embedder, CachingEmbedder):
        stored = embedder.document_vectors(inputs, input_hash)
        return stored.vectors, float(stored.scale or 0.0)
    return quantize_int8(embedder.encode([item.text for item in inputs]))


def with_embedding_cache(
    embedder: EmbeddingModel | None, settings: "Settings"
) -> EmbeddingModel | None:
    """Wrap ``embedder`` when ``LOVSPOR_EMBEDDING_CACHE_DIR`` is set; else return it as is."""
    raw_root = os.environ.get(CACHE_DIR_VARIABLE)
    if embedder is None or not raw_root:
        return embedder
    esi = _known_space(embedder)
    if settings.lspe_writer_version != 2:  # noqa: PLR2004 - the current format version
        raise EmbeddingCacheError(
            "the embedding cache serves only the LSPE version-2 writer; "
            "unset LOVSPOR_EMBEDDING_CACHE_DIR to write version 1",
        )
    forbidden = (settings.lovverk_repo_path, *_engine_checkout())
    store = LocalEntryStore.open(Path(raw_root), forbidden, _read_policy())
    return CachingEmbedder(embedder, store, esi)


def _known_space(embedder: EmbeddingModel) -> str:
    esi = space_id_of(embedder)
    descriptor = getattr(embedder, "descriptor", None)
    if esi is None or esi == UNKNOWN_SPACE_ID or not isinstance(descriptor, str) or not descriptor:
        raise EmbeddingCacheError(
            "the embedding cache needs an embedder that declares its embedding-space "
            "identity; Unknown is never comparable (ADR-0005)",
        )
    return esi


def _read_policy() -> ReadPolicy:
    raw = os.environ.get(MAX_AGE_VARIABLE)
    try:
        days = DEFAULT_MAX_AGE_DAYS if raw is None else int(raw)
    except ValueError:
        days = 0
    if days <= 0:
        raise EmbeddingCacheError(f"{MAX_AGE_VARIABLE} must be a positive integer, got: {raw!r}")
    return ReadPolicy(max_age=timedelta(days=days), clock=lambda: datetime.now(UTC))


def _engine_checkout() -> tuple[Path, ...]:
    return engine_checkout_of(Path(__file__).resolve())


def engine_checkout_of(module: Path) -> tuple[Path, ...]:
    """The nearest git work tree above ``module``, if there is one.

    Walks up rather than counting parents: mutmut runs the package from a
    ``mutants/`` shadow tree inside the checkout, and a virtualenv inside the
    checkout puts an installed copy below it too. Either way the checkout
    above must be forbidden (#826).
    """
    for parent in module.parents:
        if (parent / ".git").exists():
            return (parent,)
    return ()


def _plan(embedder: EmbeddingModel, texts: list[str]) -> list[tuple[int, int]]:
    """The adapter's own request plan; one unit when it publishes none."""
    plan = getattr(embedder, "plan_requests", None)
    bounds: list[tuple[int, int]] = plan(texts) if callable(plan) else [(0, len(texts))]
    edges = [start for start, _end in bounds] + [len(texts)]
    if edges[0] != 0 or any(end != edges[i + 1] for i, (_s, end) in enumerate(bounds)):
        raise EmbeddingCacheError("the adapter's request plan does not cover its inputs in order")
    return bounds


def _entry(key: EntryKey, vectors: np.ndarray, scale: float | None) -> CachedVectors:
    return CachedVectors(key=key, vectors=vectors, scale=scale, created_at=datetime.now(UTC))
