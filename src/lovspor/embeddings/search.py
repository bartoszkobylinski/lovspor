"""Top-K cosine similarity over an in-memory int8 embedding index.

Brute-force scan. With ~135K sections at 768 dimensions the per-
query cost is ~50 ms on a modern laptop — well within an interactive
budget — and avoids the operational complexity of an ANN index
(faiss, hnswlib) plus the determinism risk those introduce.

The index is loaded lazily by the MCP server (see ``CorpusReader``),
mirroring the body-text index pattern from ``search_body``. Each
entry keeps the int8 vector and per-doc scale; cosine similarity is
computed by dequantizing on the fly. Pre-dequantizing at load time
would triple the memory footprint to ~400 MB float32 for the full
corpus — rejected as unnecessary for sub-second queries.
"""

import heapq
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class SearchHit:
    """One match from ``top_k_cosine``. ``score`` is cosine similarity in [-1, 1]."""

    slug: str
    section_id: str
    score: float


@dataclass(order=False)
class _HeapEntry:
    """Min-heap entry whose ordering encodes 'worst-kept-hit-on-top'.

    ``heapq`` is a min-heap, so ``heap[0]`` must be the entry that
    should be discarded first when a better candidate arrives. For
    top-K-best-matches with deterministic tie-break by slug + section
    ascending, the 'worst' entry is the one with:

    1. lowest score (lower scores get discarded first)
    2. on equal score: highest slug (alphabetically later loses ties)
    3. on equal slug: highest section_id

    The bug fixed here (Codex PR-A round 1): the previous tuple-based
    heap used Python's natural tuple ordering, which sorts slug
    ascending. With ``candidate > heap[0]`` and equal scores, an
    alphabetically later candidate displaced an earlier one — the
    opposite of the documented contract.
    """

    score: float
    slug: str = field(compare=False)
    section_id: str = field(compare=False)

    def __lt__(self, other: "_HeapEntry") -> bool:
        if self.score != other.score:
            return self.score < other.score
        if self.slug != other.slug:
            return self.slug > other.slug
        return self.section_id > other.section_id


def top_k_cosine(
    query: np.ndarray,
    index: list[tuple[str, str, np.ndarray, float]],
    k: int,
) -> list[SearchHit]:
    """Return the ``k`` highest-cosine-similarity matches in ``index``.

    ``query`` must be a 1-D float32 array, L2-normalized. The model's
    ``encode`` already normalizes, so callers feed its output as-is.

    ``index`` entries are ``(slug, section_id, int8_vector, scale)``.
    The vector is dequantized inline, normalized, and dotted against
    the query.

    Tie-break is by slug ascending then section_id ascending so the
    output is deterministic for identical-score hits — this matters
    because MCP responses are cached by clients and a flaky order
    breaks downstream caching.
    """
    if k <= 0 or not index:
        return []
    if query.ndim != 1:
        raise ValueError(f"query must be 1-D, got shape {query.shape}")

    heap: list[_HeapEntry] = []
    for slug, section_id, vector_int8, scale in index:
        vector_float = vector_int8.astype(np.float32) * scale
        norm = float(np.linalg.norm(vector_float))
        if norm == 0.0:
            continue
        score = float(np.dot(query, vector_float / norm))
        entry = _HeapEntry(score=score, slug=slug, section_id=section_id)
        if len(heap) < k:
            heapq.heappush(heap, entry)
        elif heap[0] < entry:
            # heap[0] is the 'worst' kept entry per our __lt__: lowest
            # score, or equal score with alphabetically later slug.
            # ``heap[0] < entry`` means entry is strictly better, so
            # we replace heap[0] (which is what heappushpop does after
            # a push).
            heapq.heappushpop(heap, entry)

    return [
        SearchHit(slug=e.slug, section_id=e.section_id, score=e.score)
        for e in sorted(heap, key=lambda e: (-e.score, e.slug, e.section_id))
    ]
