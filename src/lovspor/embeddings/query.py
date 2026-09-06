"""Bounded, cached embedding of *search queries*.

Every other embedding in this system is paid for once, offline: the corpus
sections are encoded during sync and shipped as int8 sidecars in ``lovverk``.
``semantic_search`` is the one path that spends money at request time, and it
spends it on the caller's text — which, on an open endpoint, is a string chosen
by a stranger.

Two things follow, and neither is about load.

**A query is not a document.** ``OpenAIEmbedder`` truncates at the model's own
8191-token ceiling, which is the right bound when the input is a section of law.
For a question it is three orders of magnitude more than anyone needs, and it
sets the price of a single call: at ``text-embedding-3-large`` rates an
8000-token call costs ~31x what a 256-token one does. Capping the *query*
therefore caps the unit price of the paid tool, which counting calls cannot do.

**The same questions repeat.** "hvilke rettigheter har jeg som leietaker" is not
one user's private phrasing; it is what everyone asks. A bounded cache turns the
second and later askings into no spend at all, and the vector is deterministic
for a given query, so a cache hit is not an approximation of the answer — it is
the answer.

The cache is per process and in memory, like the quota counters: a restart
forgets it, which costs one re-embed per distinct query and nothing else.
"""

from __future__ import annotations

import threading
import unicodedata
from collections import OrderedDict

import numpy as np

from lovspor.access import int_setting_from_env
from lovspor.embeddings.model import DEFAULT_MODEL_NAME, EmbeddingModel, truncate_to_tokens

# A legal question is a sentence or two. Measured against the corpus's own
# example queries this is far above what any of them need, and 31x below the
# model's ceiling — the gap is entirely room for someone to paste a document
# into a search box, deliberately or by accident.
DEFAULT_MAX_QUERY_TOKENS = 256

# Bounded so a stream of distinct queries cannot grow it without limit. 3072
# float32 per entry is ~12 KB, so this is ~12 MB at capacity on a 2 GB box.
DEFAULT_CACHE_ENTRIES = 1024


class QueryEmbedder:
    """Embed one search query: capped, cached, and honest about the cap."""

    def __init__(
        self,
        embedder: EmbeddingModel,
        *,
        max_tokens: int = DEFAULT_MAX_QUERY_TOKENS,
        cache_entries: int = DEFAULT_CACHE_ENTRIES,
        model_name: str = DEFAULT_MODEL_NAME,
    ) -> None:
        self._embedder = embedder
        self._max_tokens = max_tokens
        self._cache_entries = cache_entries
        self._model_name = model_name
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, embedder: EmbeddingModel) -> QueryEmbedder:
        """Build with ``LOVSPOR_SEMANTIC_QUERY_*`` overrides, defaults otherwise."""
        return cls(
            embedder,
            max_tokens=int_setting_from_env(
                "LOVSPOR_SEMANTIC_QUERY_MAX_TOKENS", DEFAULT_MAX_QUERY_TOKENS
            ),
            cache_entries=int_setting_from_env(
                "LOVSPOR_SEMANTIC_QUERY_CACHE_ENTRIES", DEFAULT_CACHE_ENTRIES
            ),
        )

    def _key(self, query: str) -> str:
        """Normalize so trivial variants share one paid embedding.

        Case and surrounding whitespace do not change what was asked; NFKC folds
        the width and composition differences a copy-paste introduces. Interior
        wording is left exactly alone — two genuinely different questions must
        never collide onto one vector.
        """
        return unicodedata.normalize("NFKC", query).strip().casefold()

    def encode(self, query: str) -> tuple[np.ndarray, bool]:
        """Return ``(vector, was_truncated)`` for ``query``.

        A cache hit reports ``was_truncated`` for the *stored* query, which is
        the same query, because the cap is applied before the key is used.
        """
        text, truncated = truncate_to_tokens(query, self._max_tokens, self._model_name)
        key = self._key(text)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                # Refresh recency inside the same lock: a hit that did not move
                # the entry would let the hottest query age out.
                self._cache.move_to_end(key)
                return cached, truncated
        # Outside the lock: the network call must not serialize every other
        # caller's cache read behind one slow round-trip.
        vector = self._embedder.encode([text])[0]
        with self._lock:
            self._cache[key] = vector
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_entries:
                self._cache.popitem(last=False)
        return vector, truncated

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def cached_queries(self) -> int:
        """Entries currently held. For tests and diagnostics."""
        with self._lock:
            return len(self._cache)
