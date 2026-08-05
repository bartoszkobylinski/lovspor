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
  act's `.bin` is rewritten. A corpus-wide monolithic embeddings file
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
For the production corpus (5,880 documents as of 2026-08-04) with ~20 sections per doc on
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
  the other 15 tools are pure local).
- **Privacy.** Norwegian law text is publicly available under
  NLOD 2.0, so embedding the *corpus* through OpenAI at sync time
  is no leak. At MCP query time, though, the **user's own question**
  is sent to OpenAI to be embedded — that text leaves the machine,
  unlike every other MCP tool, which is fully local. Immaterial for
  public-law research, but worth knowing before pasting anything
  confidential into a `semantic_search` query.
- **Supply chain.** Dropped `trust_remote_code=True` from
  sentence-transformers loading. OpenAI API has been the more
  stable interface.

## Binary format (LSPE)

LSPE = LovSpor Embeddings. Little-endian header followed by contiguous
section records. Two format versions exist: version 1 (16-byte header,
no identity) and version 2 (ADR-0005 Stage 2: the same header with the
version byte set to `2`, followed by the 16-byte raw ESI digest). The
engine reads both; which version the writer emits is governed by the
cutover discipline below.

### Header (common prefix, 16 bytes)

| Offset | Size | Field         | Notes                                  |
|-------:|-----:|---------------|----------------------------------------|
|      0 |    4 | magic         | `b"LSPE"`                              |
|      4 |    1 | version       | `1` or `2`                             |
|      5 |    1 | reserved      | `0` (future flags)                     |
|      6 |    2 | section count | uint16, must equal records that follow |
|      8 |    4 | dim           | uint32, embedding dimension            |
|     12 |    4 | scale         | float32, dequantization scale          |

`dim` is recorded per-file so old and new files can coexist during
a model migration. It is a **shape** check only — it stops a numpy
alignment crash, and is never evidence that two files share an
embedding space (see below). The MCP server's
`_load_embedding_index` drops files whose `dim` disagrees with the
embedder's, so one file from an older model cannot crash the search
across the rest of the corpus; the drop is counted and reported in
`semantic_search`'s `notice`.

### Version-2 header extension (ADR-0005 Stage 2)

A version-2 file appends exactly one field to the common prefix:

| Offset | Size | Field      | Notes                                     |
|-------:|-----:|------------|-------------------------------------------|
|     16 |   16 | ESI digest | raw bytes of the `embedding_space_id`     |

The digest is the same 128-bit value the manifest records as 32 hex
characters. A version-2 sidecar therefore carries its own space
identity: `read_embeddings` on a detached v2 file returns it
(`EmbeddingFile.embedding_space_id`), and a consumer that reached the
file through the manifest cross-checks the two — a substituted
same-dimension `.bin` at a manifest-referenced path, undetectable
under Stage 1, refuses the search loudly under Stage 2.

A **version-1** header carries no identity and Stage 1 semantics
apply unchanged: a v1 `.bin` read on its own cannot tell you which
model produced it and is classified Unknown/legacy.

**Version discipline (ADR-0005 §3, binding).** The one coordinated
corpus-wide cutover (`lovspor migrate-lspe-v2`) landed on 2026-08-05
(lovverk `2fa5d121`); version 2 has been normal writer output since and
is the `lspe_writer_version` default. `LOVSPOR_LSPE_WRITER_VERSION=1`
remains an explicit rollback lever only. A reader meeting a version it
does not implement raises
`UnsupportedSidecarVersionError` — deliberately not a `ValueError` —
so the search path's per-file corrupt skip cannot silently shrink the
corpus when the reader is behind the format.

### Section record

Repeated `section count` times immediately after the header:

- 1 byte — section_id length in UTF-8 bytes (uint8, 1-255)
- N bytes — section_id (UTF-8)
- `dim` bytes — int8 quantized vector

Section ids match the `### § N-M` headings in the rendered Markdown
(`5-12`, `1`, `5-12a`, etc. — bare id, no `§` prefix).

**Section ids are not unique within a file.** A section longer than
the embedding model's input window (8 000 tokens) is split into
consecutive token-bounded chunks at sync time
(`split_to_token_chunks`), and every chunk is stored as its own
record under the same section_id — previously the tail of such
sections was silently truncated and invisible to semantic search.
Chunks have no overlap (a boundary-straddling phrase may embed
weaker in both chunks; accepted simple-first trade-off). Consumers
that rank search results must deduplicate by `(slug, section_id)`
keeping the best score — `EmbeddingIndex.top_k` does this for the
MCP server.

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

This reader works on a detached file and keeps working unchanged.
What it cannot do is establish which embedding space the vectors
belong to: that identity lives in the manifest, so a sidecar read
without its manifest is **Unknown** and must not be compared against
query vectors.

## Provider configuration

Lovspor core requires **no embedding provider at all**. Without one the sync
renders Markdown, history and manifest exactly as usual, simply skipping
sidecars, and fifteen of the sixteen MCP tools work untouched — only
`semantic_search` is unavailable. Credentials are always operator-supplied;
nothing is bundled.

The application asks one factory for an adapter —
`EmbeddingConfig.from_env() -> create_embedder() -> EmbeddingModel` — and no
provider name appears in the sync engine or the MCP server. Configuration:

| Variable | Default | Meaning |
|---|---|---|
| `LOVSPOR_EMBEDDING_PROVIDER` | `openai` | Adapter to use. Unknown values are an error, never a silent fallback. |
| `LOVSPOR_EMBEDDING_MODEL` | `text-embedding-3-large` | Model name sent to the provider. |
| `LOVSPOR_EMBEDDING_DIMENSION` | `3072` | Vector width requested and stored. |
| `LOVSPOR_EMBEDDING_BASE_URL` | unset | Endpoint override for an OpenAI-protocol-compatible service. |
| `LOVSPOR_EMBEDDING_API_KEY` | unset | Provider-neutral credential. |
| `OPENAI_API_KEY` | unset | Still read, still with the `OPENAI_APIKEY` fallback. |

**Setting none of them reproduces the previous behaviour exactly.** An install
configured with only `OPENAI_API_KEY` needs no changes.

**`openai` is the only supported provider today.** The abstraction exists so
another adapter can be added behind `EmbeddingModel`; that is an extension
point, not a claim that arbitrary models work. See the limits below before
configuring anything away from the defaults.

## Embedding-space identity (ADR-0005 Stage 1)

Each embedding sidecar belongs to an **embedding space** — the model that produced
its vectors. The manifest records that space per document, in two fields:

| Field | Example | Purpose |
|---|---|---|
| `embedding_space` | `provider=openai;model=text-embedding-3-large;dim=3072;endpoint=default` | canonical descriptor, for humans and audit |
| `embedding_space_id` | first 128 bits of its SHA-256, hex | the value compared |

`semantic_search` compares the query embedder's identity against each document's
recorded identity and uses only documents that match. Three outcomes, and no fourth:
**same identity** → searchable; **different identity** → excluded; **no recorded
identity** → excluded. There is no "compatible enough" class, and dimension equality
is never accepted as evidence — two unrelated models routinely share a dimension.

**A document with no recorded identity is *Unknown*** — unproven rather than wrong —
and is excluded from search. Nothing infers its space from a 3072 dimension, from a
`.bin` existing, or from whatever provider is configured now. In the published corpus
this legacy population is closed: two predeclared grandfathering protocols failed to
establish the historical identity empirically, so on 2026-08-04 every current document
was regenerated under the recorded identity (migration completed; ADR-0005's fail-closed
fallback). The Unknown state remains reachable — a keyless sync writes records without
identity until the next keyed run embeds them, and an embedder that declares no identity
produces honestly unidentified records — and it is always excluded, never assumed.

Exclusions are never silent. If some documents are excluded, `semantic_search`
returns its results with a `notice` naming how many and why; if none qualify, it
fails with an explanation rather than answering over an empty corpus.

### What Stage 1 does not cover

* **Sidecars are not self-describing.** The identity lives in the manifest, so it is
  authoritative only for consumers that reach a sidecar *through* the manifest, and
  only for an untampered corpus. A `.bin` replaced by a same-dimension file from
  another space still matches the record.
* **`read_embeddings(path)` on a detached file tells you nothing about its space.**
  The file parses; its identity is Unknown.
* **Closed by Stage 2 (2026-08-05).** The §3 sequence completed: dual-reader
  release (0.5.0 on PyPI), propagation, and the coordinated cutover
  (`lovspor migrate-lspe-v2`, lovverk `2fa5d121`) — the published corpus is
  version 2 throughout and version 2 is normal writer output. The Stage 1
  limitations above are closed for every sidecar in the published corpus; a
  mixed corpus remains forbidden and the format-version rules above keep the
  failure mode loud if one ever appears.

## Model changes

A vector only means something relative to the model that produced it. Two
unrelated models routinely emit the same number of dimensions while describing
entirely different spaces, and cosine similarity across them yields scores in
the normal `[-1, 1]` range that look completely plausible. There is no
exception, no shape error, no signal of any kind — just confident, meaningless
ranking. That is what the identity above exists to prevent.

An endpoint configured through `LOVSPOR_EMBEDDING_BASE_URL` is part of the
identity in full — scheme, host, port and path. Two models served from one
gateway on different paths are different spaces, so the path is not discarded.
Query strings and fragments are rejected rather than stripped.

**Changing the model now selects the affected documents automatically.** A
document whose recorded identity differs from the configured one is stale, and
the next sync with a provider configured re-embeds and re-stamps it. Deleting
sidecars by hand is no longer the mechanism.

The one case that is *not* automatic is a record with no recorded identity:
it is left alone rather than re-embedded, because absent identity alone is
never staleness — silently regenerating would spend real money and settle an
identity question by accident. For the published corpus that question WAS
settled, deliberately: the evidence-gated grandfathering attempts could not
prove the historical identity, and the whole current corpus was regenerated
under the recorded identity on 2026-08-04. The rule stays in force for any
future identity-less record (keyless writes, an identity-less adapter): it is
excluded from semantic search until re-embedded through the normal keyed
lifecycle — a content change, or regeneration under an identity-declaring
provider configuration.

## Migration story

When the configured embedding model changes, every document whose recorded
identity no longer matches becomes stale, and the next sync with a provider
configured re-embeds and re-stamps exactly those documents. **Manual sidecar
deletion is no longer the migration mechanism** — that instruction, which
earlier versions of this document gave, is obsolete.

Corrupt sidecars are repaired the same way: a `.bin` that no longer parses is
stale and is regenerated, rather than being skipped at search time forever.

Two cases are still not automatic, both deliberately:

* **Documents with no recorded *space* identity** are left alone — absent ESI
  alone is never staleness. See "Model changes" above; for the published
  corpus this population was closed by the 2026-08-04 regeneration, and the
  rule now guards against silent re-embedding of any future identity-less
  record. (Note the deliberate contrast with the *input* identity below,
  where absence IS stale on keyed runs.)
* **A dimension change** additionally makes existing files unreadable to the
  current index builder, which skips them with a stderr log and counts them;
  if that leaves the index empty, `semantic_search` fails with a message naming
  the remedy rather than answering over nothing.

## Embedding input identity (ADR-0006)

The space identity above answers *which model* produced the vectors; a second,
independent axis answers *whether the stored vectors were generated from the
exact ordered inputs the current pipeline would produce* — section extraction,
heading grammar, ordering, and token-bounded chunking. Four historical
incidents (2,336 / 797 / 26 / 1 documents) came from pipeline changes that no
existing identity could see, and the count-based repair heuristic is blind to
boundary drift that preserves chunk counts.

Every keyed writer therefore stamps `embedding_input_hash` into the manifest:
the SHA-256 of the length-prefixed ordered `(section_id, chunk_text)` stream it
actually embedded (a sectionless document stamps the digest of the empty
stream). On every keyed sync the engine reconstructs each current document's
inputs locally — no provider call — and treats an **absent or mismatching**
value as stale. Absence is stale on purpose, the opposite of the ESI rule: an
absent input hash is unverifiable, and an old keyed writer strips the field on
rewrite, so absent-immunity would leave old-transformation vectors searchable
indefinitely.

What makes absent-is-stale safe is the **mass-re-embed guard**: the complete
repair population is selected and sized first — document count/fraction AND
token workload, both computed deterministically — and an unexpectedly large
scope fails closed *before any provider call*. Ordinary small repairs proceed
automatically; a corpus-wide selection (an unannotated corpus, a broad
pipeline drift, mass field-stripping) stops for an explicit
`lovspor sync --allow-mass-reembed`. The scheduled workflow never passes the
override. Provider output has no influence on the input hash, so provider
nondeterminism cannot create churn.

The `repair-embeddings` command remains available as diagnostic/recovery
tooling during the rollout, superseded as the normal drift detector. The
one-time metadata migration for the published corpus is
`lovspor annotate-input-identity` — manifest-only, drift-guarded, idempotent,
and keyless.
