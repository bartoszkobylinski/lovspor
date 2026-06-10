"""Section-level semantic embeddings for the lovverk corpus.

This package provides:

- ``model``    — OpenAI text-embedding-3-large client with token-aware truncation
- ``sections`` — extract ``### § N-M.`` sections from rendered Markdown
- ``quantize`` — float32 ↔ int8 round-trip with per-batch scale
- ``store``    — binary file format read/write (see ``docs/embeddings.md``)
- ``search``   — top-K cosine similarity scan over an in-memory index

The pipeline at sync time:

    rendered Markdown
      -> sections.iter_sections (split on ### § headings)
      -> model.encode (OpenAI text-embedding-3-large, 3072-dim, normalized)
      -> quantize.quantize_int8 (~99% similarity preserved at 1/4 storage)
      -> store.write_embeddings (per-doc <slug>.bin alongside the .md)

The pipeline at MCP query time:

    user query
      -> model.encode (single string, normalized)
      -> search.EmbeddingIndex.top_k (cosine sim against loaded index)
      -> dict[slug, section_id, score, snippet]

Model choice rationale: the empirical benchmark in
``benchmarks/embedding_comparison/results-2026-04-30.md`` showed
``text-embedding-3-large`` beating Norwegian-tuned alternatives by
+24% Recall@5 over 47 realistic queries on the lovverk corpus.
"""

from lovspor.embeddings.model import (
    DEFAULT_DIMENSION,
    DEFAULT_MODEL_NAME,
    EmbeddingModel,
    OpenAIEmbedder,
)
from lovspor.embeddings.quantize import dequantize_int8, quantize_int8
from lovspor.embeddings.search import EmbeddingIndex, SearchHit
from lovspor.embeddings.sections import EmbeddingSection, iter_sections
from lovspor.embeddings.store import (
    EMBEDDING_DIM,
    EmbeddingFile,
    read_embeddings,
    write_embeddings,
)

__all__ = [
    "DEFAULT_DIMENSION",
    "DEFAULT_MODEL_NAME",
    "EMBEDDING_DIM",
    "EmbeddingFile",
    "EmbeddingIndex",
    "EmbeddingModel",
    "EmbeddingSection",
    "OpenAIEmbedder",
    "SearchHit",
    "dequantize_int8",
    "iter_sections",
    "quantize_int8",
    "read_embeddings",
    "write_embeddings",
]
