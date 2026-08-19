# Architecture

`lovspor` is the **engine**. It downloads Lovdata public-data tarballs, extracts
and normalizes XML, hashes the normalized XML to detect changes, deterministically
renders Markdown, and commits only the changed laws into the sibling
[`lovverk`](https://github.com/bartoszkobylinski/lovverk) corpus repo. It also
ships a read-only **MCP server** that serves that corpus to AI clients.

Legal text never lives in this repo — only the code that produces it. See
[`decisions.md`](decisions.md) for the *why* behind each choice; this document is
the *how the code is organized and flows* view.

## High-level flow

```
Lovdata public API
        │  GET /v1/publicData/list, /get/{filename}
        ▼
download tar.bz2  ──►  data/cache/  (gitignored)
        │
        ▼
extract XML members (in-memory, never extractall)
        │
        ▼
SHA-256 on normalized (C14N) XML          ◄── the change-detection key
        │
        ▼
diff against manifest.json (prior state)
        │
        ▼
render changed docs → Markdown  (+ optional OpenAI embeddings sidecar)
        │
        ▼
write to the lovverk clone (sibling working tree)
        │
        ▼
git commit per changed document  (+ a final manifest/index/history commit)
```

The rationale-level version of this diagram, including commit modes, lives in
[`decisions.md` §5](decisions.md).

## Module map

Grouped by subpackage under `src/lovspor/`. Subpackage `__init__.py` files are
empty package markers except `embeddings/__init__.py`, which is a re-export
facade.

### Top level — `src/lovspor/`

| Module | Responsibility | Key public API |
|---|---|---|
| `cli.py` | Typer CLI entry point; loads `.env` in the group callback before Typer resolves `envvar=` options. | `app`; commands `info`, `seed`, `sync`, `mcp`, `fetch-corpus`, `repair-embeddings`; sub-apps `tokens`, `observatory` |
| `settings.py` | Runtime config resolved from env / `.env`; frozen Pydantic model; paths resolved absolute. | `Settings.from_env()`, `load_env()` |
| `errors.py` | Exception hierarchy so callers never catch bare `Exception`. | `LovsporError` + `NetworkError`, `ParseError`, `RenderError`, `ExtractionError`, `ConfigError`, `CorpusStateError`, `MassRemovalError` |
| `retry.py` | Dependency-free exponential-backoff retry helper. | `retry_with_backoff(...)` |
| `history.py` | Per-act change history from `git log --follow --numstat`; writes `history/<slug>.{json,md}`. | `extract_history()`, `write_history()`, `render_history_markdown()` |
| `timetravel.py` | Time-machine: a doc's text as of a past date via `git log --follow` + `git show <sha>:<path>`. | `get_law_at_revision(...)`, `resolve_law_at_revision(...)` |
| `mcp.py` | Stdio MCP server exposing 16 read-only tools over a local `lovverk` clone. | `serve()`, `build_server()`, `CorpusReader` |
| `corpus_fetch.py` | Clone / fast-forward the local `lovverk` cache (`~/.cache/lovverk`) that `lovspor fetch-corpus` populates and `lovspor mcp` reads by default. | `fetch_corpus()`, `default_corpus_path()`, `is_corpus()`, `FetchResult`, `CorpusFetchError` |

### `sources/` — Lovdata API boundary

| Module | Responsibility | Key public API |
|---|---|---|
| `sources/lovdata.py` | HTTP client for `api.lovdata.no/v1/publicData`: list catalogue, stream-download archives with sha256 + size verify, atomic `.part` rename, retry on 429/5xx. | `LovdataClient`, `LovdataArchive`, `DownloadResult` |

### `extraction/` — tar safety

| Module | Responsibility | Key public API |
|---|---|---|
| `extraction/tarball.py` | Safe in-memory iteration of XML members from a `.tar.bz2`. Never `extractall`/`extract`; validates member names. | `iter_tarball_xml()`, `TarballMember` |

### `parsing/` — normalization + hashing

| Module | Responsibility | Key public API |
|---|---|---|
| `parsing/xml_normalizer.py` | Hardened lxml parser (XXE / billion-laughs safe) + W3C C14N canonicalization + SHA-256 change-detection hash. | `safe_parser()`, `canonicalize_xml()`, `hash_normalized_xml()` |

### `rendering/` — XML → Markdown (deterministic)

| Module | Responsibility | Key public API |
|---|---|---|
| `rendering/markdown_renderer.py` | Deterministic Lovdata-HTML → Markdown body; escapes Markdown specials; raises `RenderError` on dropped text. | `render_markdown()` |
| `rendering/document.py` | Extract doc metadata from `<header><dl>`; build the full front-matter model incl. EU basis (CELEX). | `build_frontmatter()`, `extract_xml_metadata()`, `LegalDocumentFrontMatter` |
| `rendering/frontmatter.py` | Deterministic minimal YAML serializer (no PyYAML). | `serialize_frontmatter()` |
| `rendering/slug.py` | Human-readable filename slug (short_title→title→doc_id), UTF-8 byte cap, deterministic collision resolution. | `derive_slug()`, `resolve_collisions()` |

### `storage/` — manifest ledger

| Module | Responsibility | Key public API |
|---|---|---|
| `storage/manifest.py` | The change-detection ledger; deterministic sorted-JSON read/write; versioned schema. | `read_manifest()`, `write_manifest()`, `Manifest`, `ManifestRecord` |

### `sync/` — orchestration + IO + git

| Module | Responsibility | Key public API |
|---|---|---|
| `sync/orchestrator.py` | **The pipeline.** `run_sync()` composes download→extract→hash→diff→render→embed→commit; handles renames, tombstones, the mass-removal guard, and one-time migrations. | `run_sync()`, `SyncReport` |
| `sync/change_detector.py` | Pure classifier: upstream hashes vs prior manifest → new/changed/removed/unchanged. | `detect_changes()`, `ChangeSet` |
| `sync/document_io.py` | Compose renderer + front matter + filesystem IO; dataset↔subdir mapping; write/delete docs; generate `INDEX.md`. | `render_full_document()`, `write_document()`, `generate_index()` |
| `sync/git_commit.py` | Thin `git` CLI wrappers (list args, never `shell=True`). | `add()`, `commit()`, `has_staged_changes()` |

### `embeddings/` — semantic-search vectors

| Module | Responsibility | Key public API |
|---|---|---|
| `embeddings/model.py` | OpenAI `text-embedding-3-large` client (sync httpx), tiktoken-aware truncation, batching + retry, L2-normalized output; token chunker. | `OpenAIEmbedder`, `EmbeddingModel` (Protocol), `split_to_token_chunks()` |
| `embeddings/sections.py` | Split rendered Markdown into `### § N-M.` embedding units. | `iter_sections()`, `EmbeddingSection` |
| `embeddings/quantize.py` | float32 ↔ int8 linear quantization with per-batch scale. | `quantize_int8()`, `dequantize_int8()` |
| `embeddings/store.py` | Binary `<slug>.bin` (LSPE) format read/write; atomic temp-rename. | `write_embeddings()`, `read_embeddings()`, `EmbeddingFile` |
| `embeddings/search.py` | In-memory int8 top-K cosine index; chunked matmul; deterministic tie-break; dedup by `(slug, section_id)`. | `EmbeddingIndex`, `SearchHit` |

The binary embedding format is documented in [`embeddings.md`](embeddings.md); the
data models these modules exchange are in [`data-model.md`](data-model.md).

### `observatory/` — local-law capture, a separate trust domain

Lokale forskrifter are outside the canonical corpus: they are not in the free
Lovdata dataset tier, and no permitted bulk source for Lovtidend Avdeling II is
known. ADR-0010 (lovspor-notebook) answers that with an observation layer rather
than a second ingest — it captures raw evidence from municipal sites, keeps it
outside every repository, and asserts nothing about law. No module here knows
how to reach `lovverk`.

| Module | Responsibility | Key public API |
|---|---|---|
| `observatory/model.py` | Capture records: an observation is an artifact, a failure, or a tombstone. Legal fields are absent by design — classification is deferred. | `ArtifactObservation`, `FetchFailure`, `Tombstone`, `RetrievalProvenance` |
| `observatory/storage.py` | The ADR-0010 §5 boundary: a root inside the engine repo or the corpus is refused, by path check rather than by convention. | `observatory_root()`, `ObservatoryRoot` |
| `observatory/log.py` | Append-only JSONL log + SHA-256-addressed blob store; snapshot verification, tombstone-aware. Records are fsynced on append, and the audit survives a log torn by an interrupted write instead of raising on it. | `ObservationLog`, `verify_snapshot()`, `LogScan` |
| `observatory/registry.py` | Eligibility vs activation: a source is fetchable only with a recorded access-policy check. `lovdata.no` is denied centrally. | `authorise_capture()`, `activate()`, `SourceRegistry` |
| `observatory/commands.py` | The `lovspor observatory` CLI: register a source as eligible, activate it with a reviewer's access-policy check, list what is registered, and audit the snapshot. No `--registry` flag — the path resolves through `LOVSPOR_OBSERVATORY_ROOT` and the §5 boundary. | `observatory_app`; commands `register-source`, `activate-source`, `sources`, `verify` |
| `observatory/fetch.py` | One URL, politely: activation gate, live robots.txt, per-source rate limit, byte cap, redirects not followed. Every outcome recorded. | `Fetcher`, `CaptureSettings`, `RobotsGate`, `RateLimiter` |
| `observatory/discovery.py` | What is there to look at: sitemaps, sitemap indexes, Atom and RSS. Fetches nothing itself — every discovery document goes through `Fetcher`, so the gates apply and the document is recorded as an observation. Proposes candidates; capturing one is a separate decision. | `Discoverer`, `DiscoverySettings`, `parse_discovery_document()`, `Candidate` |

Observed material is evidence that specific bytes were retrievable from a
recorded endpoint at a recorded time — never an assertion of law, and never
published until a per-source redistribution basis exists. Promotion into the
canonical corpus is an explicit, per-artifact step that does not exist yet.

## The sync pipeline

There is **one orchestrator**: `run_sync(settings)` in `sync/orchestrator.py`.
Both the `seed` and `sync` CLI commands call it — they are the same pipeline; a
missing manifest just makes the change detector classify everything as new.

In order:

1. **CLI entry** (`cli.py`) — the group callback runs `load_env()` first, then the
   command builds `Settings.from_env()` and calls `run_sync()`.
2. **Preconditions** — the corpus must be a git repo; a **dirty worktree aborts**
   with `CorpusStateError` (crash residue would otherwise be misread as "nothing
   changed"). The prior `manifest.json` is read here.
3. **One-time migrations** — Sprint 5 history backfill, Sprint 8 `eu_basis`
   re-render, and Sprint 9 embeddings backfill (the last only if an OpenAI key is
   set). Each fires once and emits its own commit(s) before regular work.
4. **Collect upstream** — download each tracked dataset tarball
   (`gjeldende-lover`, `gjeldende-sentrale-forskrifter`) into
   `data/cache/archives/`, iterate XML members, extract metadata, derive the base
   slug, and **compute `hash_normalized_xml(member)`**. Slug collisions are
   resolved **per dataset**.
5. **Change detection** — `detect_changes(upstream_hashes, prior)` returns disjoint
   sorted `new/changed/removed/unchanged`; renames are then identified among
   unchanged-content docs whose slug/path moved. **No-op fast path:** if nothing is
   new/changed/removed/renamed, return early *without* rewriting the manifest or
   committing — the "nothing to do → zero commits" contract.
6. **Mass-removal guard** — abort with `MassRemovalError` if any single dataset
   (≥ 20 current docs) would lose more than `max_removal_ratio` (default 10%).
7. **Carry forward** — unchanged records are copied verbatim (preserving
   `last_seen` so the manifest stays byte-identical); tombstones (`status="removed"`)
   are carried so removals stay permanent.
8. **Render + embed** — for each new/changed/renamed doc, write the `.md`
   (front matter + body) and, when an embedder is available, the
   `<dataset>/embeddings/<slug>.bin` sidecar. A two-phase write-all-then-delete
   sequence prevents path-cascade corruption when slugs shuffle between docs.
9. **Commit** — mode-aware (`git_commit_mode`, default `per-document`): one commit
   per add/update/rename/remove, then history is extracted (`git log --follow` needs
   those commits to exist), then one final commit bundling manifest + `INDEX.md` +
   history. The `single` mode and migration paths use one bulk commit plus a history
   follow-up (history needs the docs commit first).
10. **Return** a `SyncReport` of new/changed/removed/unchanged counts.

## Key invariants (enforced in code)

| Invariant | Where | How |
|---|---|---|
| **Deterministic rendering** (same XML → byte-identical Markdown) | `rendering/markdown_renderer.py`, `rendering/frontmatter.py` | No wall-clock in body; custom deterministic YAML serializer; front-matter key order = model field order. Manifest and `.bin` writes are likewise byte-deterministic. |
| **Hash on normalized XML** (never on Markdown/HTML) | `parsing/xml_normalizer.py:hash_normalized_xml` | SHA-256 of C14N-canonicalized XML, called only on raw XML bytes. A documented gap: `remove_blank_text=True` makes inter-element whitespace hash-invisible (accepted — [`decisions.md` §14](decisions.md)). |
| **Safe XML parser** (no XXE, billion-laughs, network, or unbounded tree) | `parsing/xml_normalizer.py:safe_parser` | `resolve_entities=False, no_network=True, huge_tree=False, remove_comments=True`. Unresolved custom entities raise `ParseError`. |
| **Tar safety / CVE-2007-4559** | `extraction/tarball.py` | Never `extractall`/`extract`; reads members into memory via `extractfile()`; rejects null bytes, absolute names, and `..` components. |
| **Mass-removal guard** | `sync/orchestrator.py:_guard_mass_removal` | Per-dataset abort if removed/total exceeds `max_removal_ratio`; guards against a truncated/empty upstream tarball wiping the corpus. |
| **Tombstones** (removals are permanent) | `sync/orchestrator.py` (`_tombstone`, `_carry_tombstones`) | Removed docs keep their record with `status="removed"`, carried forward every sync. |
| **No shell injection** | `sync/git_commit.py`, `history.py`, `timetravel.py` | Every `subprocess.run` uses list args, never `shell=True`. |

## External boundaries

- **Lovdata public-data API** — reached only from `sources/lovdata.py`
  (`GET /list`, `GET /get/{filename}`). The only outbound HTTP to Lovdata; no HTML
  scraping anywhere.
- **OpenAI API** — reached only from `embeddings/model.py`
  (`text-embedding-3-large`, 3072-dim). On the engine side it is optional (no key →
  embeddings skipped, Markdown still produced). On the MCP side it powers only
  `semantic_search`; see the privacy note in [`mcp.md`](mcp.md).
- **git / the `lovverk` corpus** — `sync/git_commit.py` (write path), plus
  `history.py`, `timetravel.py`, and `mcp.py` (read paths) shell out to `git`
  against the local clone. Nothing in the engine code pushes to GitHub — the
  scheduled workflow does that.

## MCP server

`lovspor mcp` builds a `FastMCP("lovverk")` server (`mcp.py`) transported over
**stdio** with 16 read-only tools. A `CorpusReader` reads `manifest.json` plus the
Markdown / `.bin` files from the local clone, caching in memory and dropping caches
when `manifest.json`'s mtime changes so a `git pull` under a long-lived server is
picked up. With no `--corpus-path` / `LOVVERK_CORPUS_PATH`, it defaults to the
`fetch-corpus` cache (`~/.cache/lovverk`), so the consumer flow is `lovspor
fetch-corpus` then `lovspor mcp`. Full tool reference, setup, and limitations are
in [`mcp.md`](mcp.md).
