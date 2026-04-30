"""Section-level semantic embeddings for the lovverk corpus.

This package provides:

- ``model``    — lazy singleton loader for the Norwegian-tuned embedding model
- ``sections`` — extract ``### § N-M.`` sections from rendered Markdown
- ``quantize`` — float32 ↔ int8 round-trip with per-batch scale
- ``store``    — binary file format read/write (see ``docs/embeddings.md``)
- ``search``   — top-K cosine similarity scan over an in-memory index

The pipeline at sync time:

    rendered Markdown
      -> sections.iter_sections (split on ### § headings)
      -> model.encode (jina-embeddings-v2-base-no, 768-dim, normalized)
      -> quantize.quantize_int8 (~99% similarity preserved at 1/4 storage)
      -> store.write_embeddings (per-doc <slug>.bin alongside the .md)

The pipeline at MCP query time:

    user query
      -> model.encode (single string, normalized)
      -> search.top_k (cosine sim against loaded index)
      -> dict[slug, section_id, score, snippet]

The store and search modules carry no dependency on PyTorch or
sentence-transformers, so they import cheaply at MCP server startup.
The model module imports sentence-transformers lazily on first use.
"""

from lovspor.embeddings.model import EmbeddingModel, JinaModel, get_default_model, set_model
from lovspor.embeddings.quantize import dequantize_int8, quantize_int8
from lovspor.embeddings.search import SearchHit, top_k_cosine
from lovspor.embeddings.sections import EmbeddingSection, iter_sections
from lovspor.embeddings.store import (
    EMBEDDING_DIM,
    EmbeddingFile,
    read_embeddings,
    write_embeddings,
)

__all__ = [
    "EMBEDDING_DIM",
    "EmbeddingFile",
    "EmbeddingModel",
    "EmbeddingSection",
    "JinaModel",
    "SearchHit",
    "dequantize_int8",
    "get_default_model",
    "iter_sections",
    "quantize_int8",
    "read_embeddings",
    "set_model",
    "top_k_cosine",
    "write_embeddings",
]
