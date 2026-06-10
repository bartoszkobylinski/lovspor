"""Top-K cosine similarity over an in-memory int8 embedding index.

Vectorized brute-force scan: the index holds one contiguous int8
matrix plus precomputed per-row L2 norms, and a query is a single
chunked ``float32`` matmul over it. At the production corpus scale
(3072-dim, tens of thousands of sections) this answers in well under
100 ms on a laptop — the earlier per-row Python loop took over a
second per query for the same data. Still no ANN index (faiss,
hnswlib): brute force stays exact and deterministic, and the matmul
is fast enough that approximate search buys nothing here.

Quantization scale cancellation: cosine similarity is invariant
under positive per-vector scaling — ``dot(q, v*s) / ||v*s|| ==
dot(q, v) / ||v||`` for ``s > 0`` — and ``quantize_int8`` always
produces a strictly positive scale. Scores are therefore computed
directly on the raw int8 rows; the per-file scale never enters the
math and is dropped at index-build time.

Zero vectors (a section whose embedding quantized to all zeros) have
no defined direction, so they are dropped at build time — same
outcome as the previous implementation, which skipped them during
the scan.
"""

from dataclasses import dataclass

import numpy as np

_CHUNK_ROWS = 16384
"""Rows dequantized to float32 per matmul block.

Bounds peak transient memory to ``_CHUNK_ROWS * dim * 4`` bytes
(~200 MB at 3072-dim) instead of materializing the whole float32
matrix (~1.6 GB for the production corpus). Tests monkeypatch this
to a tiny value to exercise the chunk-boundary path."""


@dataclass(frozen=True)
class SearchHit:
    """One match from ``EmbeddingIndex.top_k``. ``score`` is cosine similarity in [-1, 1]."""

    slug: str
    section_id: str
    score: float


class EmbeddingIndex:
    """Immutable searchable view over per-section int8 embeddings.

    Build once via :meth:`from_entries` (the MCP server does this
    lazily on the first ``semantic_search`` call), then query with
    :meth:`top_k`. Tie-break is by slug ascending then section_id
    ascending so the output is deterministic for identical-score
    hits — this matters because MCP responses are cached by clients
    and a flaky order breaks downstream caching.
    """

    def __init__(
        self,
        slugs: list[str],
        section_ids: list[str],
        matrix: np.ndarray,
        norms: np.ndarray,
    ) -> None:
        self._slugs = slugs
        self._section_ids = section_ids
        self._matrix = matrix
        self._norms = norms
        self._unique_slugs = frozenset(slugs)

    @classmethod
    def from_entries(
        cls,
        entries: list[tuple[str, str, np.ndarray, float]],
    ) -> "EmbeddingIndex":
        """Build an index from ``(slug, section_id, int8_vector, scale)`` rows.

        The scale is accepted for call-site compatibility with
        ``read_embeddings`` output but ignored — see the module
        docstring for why it cancels out of cosine similarity.
        All vectors must be 1-D and share one dimension.
        """
        kept = [(slug, sid, v) for slug, sid, v, _scale in entries if v.any()]
        if not kept:
            return cls([], [], np.zeros((0, 0), dtype=np.int8), np.zeros(0, dtype=np.float32))
        dims = {v.shape for _, _, v in kept}
        if any(len(shape) != 1 for shape in dims) or len(dims) > 1:
            raise ValueError(
                f"inconsistent embedding dims in index: {sorted(dims)}",
            )
        matrix = np.stack([v for _, _, v in kept])
        return cls(
            slugs=[slug for slug, _, _ in kept],
            section_ids=[sid for _, sid, _ in kept],
            matrix=matrix,
            norms=_row_norms(matrix),
        )

    def __len__(self) -> int:
        return int(self._matrix.shape[0])

    @property
    def unique_slugs(self) -> frozenset[str]:
        """Slugs with at least one searchable (non-zero) vector."""
        return self._unique_slugs

    def top_k(
        self,
        query: np.ndarray,
        k: int,
        allowed_slugs: set[str] | None = None,
    ) -> list[SearchHit]:
        """Return the ``k`` highest-cosine-similarity matches.

        ``query`` must be a 1-D float32 array, L2-normalized — the
        model's ``encode`` already normalizes, so callers feed its
        output as-is. ``allowed_slugs`` restricts the scan to rows
        whose slug is in the set (dataset filtering); ``None`` means
        no restriction.
        """
        if query.ndim != 1:
            raise ValueError(f"query must be 1-D, got shape {query.shape}")
        if k <= 0 or len(self) == 0:
            return []
        scores = self._scores(query)
        if allowed_slugs is not None:
            mask = np.fromiter(
                (slug in allowed_slugs for slug in self._slugs),
                dtype=bool,
                count=len(self._slugs),
            )
            scores = np.where(mask, scores, -np.inf)
        return self._select_top(scores, k)

    def _scores(self, query: np.ndarray) -> np.ndarray:
        """Cosine similarity of ``query`` against every row, chunked."""
        q = np.ascontiguousarray(query, dtype=np.float32)
        raw = np.empty(len(self), dtype=np.float32)
        for start in range(0, len(self), _CHUNK_ROWS):
            block = self._matrix[start : start + _CHUNK_ROWS].astype(np.float32)
            raw[start : start + _CHUNK_ROWS] = block @ q
        return np.asarray(raw / self._norms, dtype=np.float32)

    def _select_top(self, scores: np.ndarray, k: int) -> list[SearchHit]:
        """Pick the top ``k`` rows with the documented deterministic tie-break.

        ``argpartition`` alone is unordered and breaks score ties
        arbitrarily, so every row tied with the k-th best score joins
        the candidate set and a full ``(-score, slug, section_id)``
        sort decides which survive. Rows masked to ``-inf`` never
        qualify.
        """
        k_eff = min(k, scores.shape[0])
        boundary = np.partition(scores, -k_eff)[-k_eff]
        if boundary == -np.inf:
            candidates = np.flatnonzero(scores > -np.inf)
        else:
            candidates = np.flatnonzero(scores >= boundary)
        ranked = sorted(
            candidates.tolist(),
            key=lambda i: (-scores[i], self._slugs[i], self._section_ids[i]),
        )[:k_eff]
        return [
            SearchHit(slug=self._slugs[i], section_id=self._section_ids[i], score=float(scores[i]))
            for i in ranked
        ]


def _row_norms(matrix: np.ndarray) -> np.ndarray:
    """Per-row L2 norms of an int8 matrix, computed in float32 chunks."""
    norms = np.empty(matrix.shape[0], dtype=np.float32)
    for start in range(0, matrix.shape[0], _CHUNK_ROWS):
        block = matrix[start : start + _CHUNK_ROWS].astype(np.float32)
        norms[start : start + _CHUNK_ROWS] = np.sqrt(np.einsum("ij,ij->i", block, block))
    return norms
