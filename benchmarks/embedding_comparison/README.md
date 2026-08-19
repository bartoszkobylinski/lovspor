# Embedding model comparison

One-off empirical benchmark to pick the embedding model for Sprint 9 PR-B (semantic search). Compares four candidates head-to-head against the real `lovverk` corpus using realistic queries from `evals/scenarios/`.

## Models compared

| Model | Source | Dim | Max tokens | Cost |
|---|---|---:|---:|---|
| nb-sbert-v2-base | NbAiLab (local) | 768 | 256 | free |
| nb-sbert-v2-large | NbAiLab (local) | 1024 | 512 | free |
| text-embedding-3-small | OpenAI API | 1536 | 8191 | $0.02 / 1M tokens |
| text-embedding-3-large | OpenAI API | 3072 | 8191 | $0.13 / 1M tokens |

## Queries

47 queries extracted from `evals/scenarios/*.yaml` — every scenario whose `expected_tool_calls` includes a `slug_match` pattern. These are the same realistic 8-persona queries that drive the eval suite, so any retrieval failure here directly maps to a real user-frustration scenario.

## Metrics

- **Recall@5** — fraction of queries where the expected slug appears in the top-5 retrieved sections
- **MRR** (Mean Reciprocal Rank) — average of `1 / rank` across all queries; rewards earlier matches
- **Indexing time** — one-time cost to embed the full corpus
- **Median query latency** — per-query encoding + cosine scan

## Running

```bash
# Install the embeddings extra (sentence-transformers + torch)
uv sync --extra embeddings

# Provide your OpenAI key (or pass --skip-openai)
export OPENAI_API_KEY=sk-...
# or put OPENAI_API_KEY=sk-... in lovspor/.env (auto-loaded)

# Run the full benchmark
uv run python benchmarks/embedding_comparison/run.py \
    --lovverk-path /path/to/lovverk

# Subset:
uv run python benchmarks/embedding_comparison/run.py \
    --lovverk-path /path/to/lovverk --skip-openai      # local-only
uv run python benchmarks/embedding_comparison/run.py \
    --lovverk-path /path/to/lovverk --skip-nbailab     # API-only
```

Embeddings are cached to `benchmarks/embedding_comparison/.cache/<model>-<corpus-hash>.pkl` (gitignored). The hash covers every `(slug, section_id, text)` that gets embedded, in order, so a changed corpus is a cache miss rather than a silent hit against a matrix built from a corpus that no longer exists (issue #108). Re-runs over an unchanged corpus reuse cached embeddings; delete the cache dir to force re-indexing.

## Estimated runtime (M1 16 GB)

| Phase | Time |
|---|---|
| nb-sbert-v2-base indexing | ~15 min |
| nb-sbert-v2-large indexing | ~30 min |
| OpenAI 3-small + 3-large indexing (mostly API latency) | ~10 min combined |
| Query phase (47 queries × 4 models) | <5 min |
| **Total one-time** | **~60-90 min** |

Estimated OpenAI cost: ~$5-10 total for both 3-small + 3-large indexing the full corpus (~135K sections × ~500 tokens average).

## Output

`results-2026-04-30.md` (or whatever date you run): summary table, per-persona breakdown, per-query rank table, and a recommendation paragraph based on the actual numbers.

## What this is NOT

This is decision-support, not the eval suite. The eval suite (`evals/`) is permanent CI infrastructure that runs on every PR. This benchmark is a one-off to inform Sprint 9 PR-B. Once we pick a model, this script lives on as portfolio evidence ("I picked the model empirically") but is not part of the regular test loop.
