# Embeddings — per-section semantic vectors

The lovverk corpus carries one binary embedding file per document
alongside its rendered Markdown:
`<dataset>/embeddings/<slug>.bin`. Each file holds the int8-quantized
vector for every `### § N-M` section of that act.

Sprint 9 added this layer to power MCP `semantic_search` — substring
search (`search_body`) cannot match *"renter rights"* against
sections about *manglende vedlikehold*; embeddings can.

## Why per-doc sharding

One file per act, not one monolithic blob across the corpus.

- **Git diff per-section.** When a single act changes, only that
  act's `.bin` is rewritten. A 4500-act monolithic embeddings file
  would rewrite end-to-end on any sync, producing 200+ MB git blobs
  per push and burying real legal-text changes under embedding
  churn.
- **Independent verifiability.** A third party can download a
  single act's `.md` and `.bin`, recompute the embedding, and
  compare byte-for-byte (deterministic format — see below).
  Monolithic formats need the whole corpus to verify one act.
- **Lazy loading.** The MCP server's index loads every `.bin` once
  on the first `semantic_search` call. With per-doc files, a
  partial corpus (e.g. early bootstrap) just yields an incomplete
  index — no all-or-nothing failure.

## Why int8 quantization

Each component of the float32 embedding is rescaled and rounded to
a signed 8-bit integer with one shared `scale` per file. Dot product
with a normalized query reproduces cosine similarity to ~99% of
float32 fidelity, at **1/4 the storage cost**.

text-embedding-3-large outputs 3072-dim float32 = 12 288 bytes per
section. int8 with per-file scale = 3072 + 4 bytes per section.
For the production 4500-doc corpus with ~30 sections per doc on
average, that is ~200 MB of `.bin` files instead of ~1.6 GB of
float32. Acceptable for git tracking; float32 would not be.

The scale is per-file, not global, because section vectors within
one act share a similar magnitude distribution (same model, same
domain) — a per-file scale captures that range tighter than a
single corpus-wide scalar would.

## Model choice — text-embedding-3-large

Empirical benchmark over 8 personas × 47 realistic queries
(`benchmarks/embedding_comparison/results-2026-04-30.md`) compared
four candidates:

- `text-embedding-3-large` (OpenAI, 3072-dim, paid API)
- `text-embedding-3-small` (OpenAI, 1536-dim, paid API)
- `nb-sbert-v2-large` (NbAiLab, 1024-dim, local, Norwegian-tuned)
- `nb-sbert-v2-base` (NbAiLab, 768-dim, local, Norwegian-tuned)

`text-embedding-3-large` won by **+24% Recall@5** over the best
Norwegian-tuned alternative. Counter-intuitive — a generalist model
beating a domain-tuned one — but the benchmark was strict and the
queries were realistic AI-consumer phrasings.

Trade-offs accepted with the OpenAI choice:

- **Paid API.** ~$5-15/year for the production sync cadence on a
  hobby project. Trivial vs the +24% retrieval quality gain.
- **Network dependency.** Sync now needs OpenAI reachable. Same
  for the MCP server (only when `semantic_search` is invoked —
  the other 11 tools are pure local).
- **Privacy.** Norwegian law text is publicly available under
  NLOD 2.0; embedding it through OpenAI is no leak.
- **Supply chain.** Dropped `trust_remote_code=True` from
  sentence-transformers loading. OpenAI API has been the more
  stable interface.

## Binary format (LSPE)

LSPE = LovSpor Embeddings. Little-endian, 16-byte header followed by
contiguous section records.

### Header (16 bytes)

| Offset | Size | Field         | Notes                                  |
|-------:|-----:|---------------|----------------------------------------|
|      0 |    4 | magic         | `b"LSPE"`                              |
|      4 |    1 | version       | `1`                                    |
|      5 |    1 | reserved      | `0` (future flags)                     |
|      6 |    2 | section count | uint16, must equal records that follow |
|      8 |    4 | dim           | uint32, embedding dimension            |
|     12 |    4 | scale         | float32, dequantization scale          |

`dim` is recorded per-file so old and new files can coexist during
a model migration. The MCP server's `_load_embedding_index` checks
each file's `dim` against the embedder's expected dimension and
silently drops mismatched files (with an operator-visible stderr
log) so one stale file from an older model cannot crash the search
across the rest of the corpus.

### Section record

Repeated `section count` times immediately after the header:

- 1 byte — section_id length in UTF-8 bytes (uint8, 1-255)
- N bytes — section_id (UTF-8)
- `dim` bytes — int8 quantized vector

Section ids match the `### § N-M` headings in the rendered Markdown
(`5-12`, `1`, `5-12a`, etc. — bare id, no `§` prefix).

## Read/write API

`lovspor.embeddings.store` is the single canonical implementation
of both directions.

```python
from lovspor.embeddings.store import (
    EmbeddingFile,
    read_embeddings,
    write_embeddings,
    EMBEDDING_DIM,  # 3072
)

# Write
write_embeddings(
    path,
    sections=[("5-12", int8_vector_3072d), ("5-13", ...)],
    scale=0.0042,
    dim=EMBEDDING_DIM,
)

# Read
result = read_embeddings(path)
# result.dim, result.scale, result.sections (list of (section_id, int8 vector))
```

Same input → byte-identical file (deterministic byte order, no
timestamps, no platform-dependent encoding). Property tested.

## Migration story

When the embedding model changes (or its native dimension changes —
`text-embedding-3-small` is 1536, `text-embedding-3-large` is
3072, etc.), every existing `.bin` becomes stale.

The MCP server handles this gracefully:

1. `_load_embedding_index` checks each `.bin`'s header `dim`
   against the embedder's `get_dimension()`. Mismatched files are
   skipped with a stderr log.
2. The dropped count is tracked in `CorpusReader._stale_bin_count`.
3. If the index ends up empty *and* any files were dropped,
   `semantic_search` raises with a "corpus needs to be re-embedded"
   message pointing at `lovspor sync`.
4. If the index ends up empty with no drops, the bootstrap message
   ("no embeddings — run lovspor sync") fires instead. Different
   states, different remediation.

The next sync overwrites every `.bin` with current-dim content via
the orchestrator's per-doc `_write_one` flow. No manual migration
step needed.
