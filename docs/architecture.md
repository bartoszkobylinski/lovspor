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
| `observatory/model.py` | Capture records: an observation is an artifact, a failure, or a tombstone (a blob was removed). Two more kinds correct a filed record without rewriting it (ADR-0015): a `record_tombstone` retracts one record's claim, and a `refiled_observation` files the same observation again with the corrected field — only `authority_id` — and who, when and why. A record's key is the SHA-256 of its line as stored. Legal fields are absent by design — classification is deferred. | `ArtifactObservation`, `FetchFailure`, `Tombstone`, `RecordTombstone`, `RefiledObservation`, `Correction`, `RetrievalProvenance`, `record_key()` |
| `observatory/corrections.py` | The corrected view of the log (ADR-0015 §5, §6): the correction set is folded first, incrementally, and each corrected original is replaced at its own position by its re-filed observation, so order-dependent folds see what a correctly filed log would give. A correction is in force only with both halves under one `correction_id`. Every fold of attribution reads through it; `verify` alone reads the raw log and audits the corrections. | `CorrectionSet`, `corrected_view()`, `CorrectionAudit`, `audit_corrections()` |
| `observatory/storage.py` | The ADR-0010 §5 boundary: a root inside the engine repo or the corpus is refused, by path check rather than by convention. | `observatory_root()`, `ObservatoryRoot` |
| `observatory/log.py` | Append-only JSONL log + SHA-256-addressed blob store; snapshot verification, tombstone-aware. Records are fsynced on append, and the audit survives a log torn by an interrupted write instead of raising on it. The log is read as a stream: a caller that reduces it to a smaller answer folds records past a collector and keeps the answer, not the archive (issue #199). `scan_corrected_into()` is the same stream over the corrected view, and is what the capture-state folds and the freshness index read. | `ObservationLog`, `scan_into()`, `scan_lines_into()`, `scan_corrected_into()`, `corrections()`, `verify_snapshot()`, `LogScan` |
| `observatory/registry.py` | Eligibility vs activation: a source is fetchable only with a recorded access-policy check, and that check is bound to the domain it was performed for — a domain change withdraws the clearance rather than carrying it (issue #166). Two activated sources on one domain make the gate refuse rather than pick: an observation has to name the authority that published it, and a tie-break by id filed 5,980 of Arendal's pages under Grimstad while Grimstad went unfetched (issue #215). `lovdata.no` is denied centrally. | `authorise_capture()`, `activate()`, `replace_domain()`, `domains_claimed_twice()`, `claimants()`, `SourceRegistry` |
| `observatory/events.py` | Operator decision history beside the registry: append-only `source-events.jsonl`, one JSON object per line, locked and fsynced. `sources.json` is current state and cannot say what was withdrawn; an event fingerprints the record it replaced. | `SourceDomainReplaced`, `append_source_event()`, `record_fingerprint()` |
| `observatory/commands.py` | The `lovspor observatory` CLI: register a source as eligible, activate it with a reviewer's access-policy check, declare a listing entry point, list what is registered, discover and capture, sweep every activated source, run the scheduled sweep with its preflight and heartbeat, audit the snapshot, and report health. No `--registry` flag — the path resolves through `LOVSPOR_OBSERVATORY_ROOT` and the §5 boundary. | `observatory_app`; commands `register-source`, `activate-source`, `update-source`, `sources`, `discover`, `capture`, `capture-all`, `nightly`, `verify`, `repair`, `status`, `composition`, `survey` |
| `observatory/audit_commands.py` | The read-only audits: `verify` (log and blobs account for each other; corrections are complete, faithful and single) and `composition` (what the archive is made of, corrections counted apart). | `verify`, `composition` |
| `observatory/log_commands.py` | The operator acts that change what the log holds, each a dry run unless `--apply` is given: `repair` drops an unfinished final record; `reattribute` corrects the authority records were filed under by appending corrections (ADR-0015), refusing while a sweep holds the host lock. | `repair`, `reattribute` |
| `observatory/reattribution.py` | The logic under `reattribute`: the correction document, the register check (target covers the host, source no longer does), and the plan — per original, re-filed half then record tombstone; already-corrected originals skipped, half-written corrections completed with only their missing half. | `ReattributionRequest`, `check_registry()`, `plan_reattribution()`, `apply_plan()` |
| `observatory/entrypoint.py` | The seam where the CLI assembles the observatory app from every module that defines a command. A command registers by decorating `observatory_app`, which only happens if something imports its module — so importing the app directly would ship a CLI quietly missing them. Not in the package `__init__`: the MCP server imports this package and wants none of typer. | `observatory_app` (re-exported) |
| `observatory/survey.py` | What a probe of one host *means*, before anything registers it: six entries, kept apart so that "nothing machine-readable" never absorbs "assembled in the browser" (issue #194). Fetches nothing, so the rules are exercised against hand-written fixtures as ADR-0010 §5 requires. | `read_site_shape()`, `front_page_markers()`, `SiteShape`, `RobotsReadout`, `API_MARKERS` |
| `observatory/survey_probe.py` | Three requests per host at most — `robots.txt`, the conventional `/sitemap.xml`, the front page — and fewer once the answer is settled. Carries the user agent and the 7-second spacing every registered source was cleared with: a recon pass must not be more aggressive than the capture it scouts for. | `SiteProbe`, `ProbeSettings`, `SURVEY_USER_AGENT` |
| `observatory/survey_commands.py` | The `observatory survey` command. Writes `<root>/survey/<run-id>.jsonl`, one row per host — beside the observation log and deliberately not part of it, so every authority id in that log stays a registered one. An empty run is refused rather than written (issue #349). | `survey` |
| `observatory/fetch.py` | One URL, politely: activation gate, live robots.txt, per-source rate limit, byte cap. A redirect is followed only while its target stays inside the cleared domain (`www.X` → `X`, issue #159); one that would leave it is recorded and not followed. The artifact is filed under the URL that was **requested**, with the hops in `provenance.redirect_chain` — filing it under the destination left the requested URL permanently unobserved and re-fetched every pass (issue #211). Every outcome recorded. Also surfaces the sitemaps robots.txt declares, so discovery starts where the source says. | `Fetcher`, `CaptureSettings`, `RobotsGate`, `RateLimiter` |
| `observatory/discovery.py` | What is there to look at: sitemaps, sitemap indexes, Atom and RSS. Fetches nothing itself — every discovery document goes through `Fetcher`, so the gates apply and the document is recorded as an observation. Proposes candidates; capturing one is a separate decision. | `Discoverer`, `DiscoverySettings`, `parse_discovery_document()`, `Candidate` |
| `observatory/listing.py`, `observatory/listing_content.py` | Reading a declared listing page into candidates (issues #151, #514). A page with dated entries is read as a dated list — a `<time datetime>` dates only a row whose links lead to one URL, never a whole-page container; a page with none is read as an undated overview of the links in its content region (`<main>`/`<body>`, narrowed to `<article>`, chrome and breadcrumbs dropped). Structural, never CMS-specific; no JavaScript. | `read_listing()`, `parse_listing()`, `parse_undated_listing()`, `ListingReadout` |
| `observatory/outcomes.py` | What a fetch outcome means, as the one definition both the engine and a reader of the raw log use. A followed redirect is filed as `fetch_failure` and is not a failure — 316,109 of the archive's 416,532 such records — so counting `kind` reports 49.6% against a real 12.3% (issue #188). Two questions with deliberately opposite safe defaults: an unclassified outcome counts as a lost document, and counts for nothing when deciding whether a URL may be left alone. | `lost_the_document()`, `is_redirect_hop()`, `is_url_property()`, `ArchiveComposition`, `collect_composition()` |
| `observatory/selection.py` | Which of discovery's proposals capture fetches (issue #348): an allow-list of 23 regulation stems, substring-matched on the decoded, lowercased URL path. Not a classifier — ADR-0010's deferral of deciding what is law holds; it scopes crawl budget by what a path names. A proposal from a registered listing page bypasses the rule. Global, off unless `LOVSPOR_OBSERVATORY_CAPTURE_SELECTION=1`; off still counts what it would keep. | `selects()`, `choose()`, `Selection`, `selection_enabled()`, `REGULATION_PATH_STEMS` |
| `observatory/capture_pass.py` | One source's capture pass, shared by `capture` and every sweep lane: selection, then freshness, then the fetch, each outcome printed as it happens and every decline counted in the summary line. | `capture_proposals()`, `capture_candidates()`, `CaptureCounts`, `capture_summary()` |
| `observatory/freshness.py` | Whether a candidate is worth fetching again: the site's `lastmod` against what the log already holds, and for a candidate the site says nothing about, a 24-hour re-check window on our own last sighting (issue #209 — without it an undated candidate was re-fetched every pass and a crawl could not terminate). A URL that has never yielded content is judged instead on its run of failures that describe the URL rather than the moment, and waits out a doubling backoff capped at two windows (issue #204); the site reporting a change overrides the wait. Only ever declines work. | `worth_capturing()`, `UNDATED_RECHECK`, `FAILED_RECHECK`, `is_url_property()`, `collect_capture_state()`, `parse_site_lastmod()` |

Observed material is evidence that specific bytes were retrievable from a
recorded endpoint at a recorded time — never an assertion of law, and never
published until a per-source redistribution basis exists. Promotion into the
canonical corpus is an explicit, per-artifact step: `promotion/` below, of which
the identity layer, the extractor and the renderer exist so far; the writer does
not.

### `promotion/` — local regulations into `lovverk` (ADR-0016, in progress)

ADR-0016 (lovspor-notebook, proposed) makes this package the only writer of
`lovverk/lokale-forskrifter/`. Slice S1 is the identity layer; slice S2 the
extractor, the personal-data gate and the local renderer. All of it is pure,
no I/O, and wired to nothing yet — not the observatory, a CLI, MCP or
`lovverk`. The writer and the decision log are later slices. Local documents
are published on the basis of åndsverkloven § 14, never NLOD: the renderer
refuses output that mentions NLOD at all.

| Module | Responsibility | Key public API |
|---|---|---|
| `promotion/models.py` | The identity result: minted (`doc_id`, `lf`/`lk` scheme, `ref_id`, normalised title, content hash) or held (a typed reason, no id field at all). The authority block refuses a KLASS code whose length disagrees with its type (4 digits kommune, 2 fylkeskommune). | `Authority`, `AuthorityType`, `ExtractedRegulation`, `MintedIdentity`, `HeldIdentity`, `HoldReason`, `IdScheme`, `IdentityResult` |
| `promotion/identity.py` | ADR-0016 1a in order: `lf-yyyymmdd-nnnn` only from the regulation's own identification lines (FOR header, `Forskrift <dato> nr. <n>` title, `Kunngjort` line; never `Hjemmel`/`Endrer` lines or the body); else `lk-<authority>-<h12>` from authority, normalised title and stated vedtaksdato; else held. An `lf-` id whose ref-id a central record carries is held, never merged. `content_hash` is SHA-256 of layout-normalised extracted text, so whitespace churn mints no version. | `mint_identity()`, `find_identification_ids()`, `normalise_title()`, `normalise_text()`, `content_hash()` |
| `promotion/dates.py` | Dates as the text states them (`12. desember 2019`, `12.12.2019`, `2019-12-12`), a real calendar date or `None`; a draft's placeholder date (`X.X.2016`). | `parse_stated_date()`, `has_placeholder_date()` |
| `promotion/source_text.py` | Captured bytes to lines. HTML: the observatory's document region and hardened parser, page chrome (nav, sidebars, footers, breadcrumbs, update stamps) dropped before reading, so a CMS sidebar or a date stamp cannot mint a version. With several `<article>`s, the innermost one that starts the regulation at the region's own title and first section is read (when exactly one is), so sibling FAQ items and teaser lists after the last section stay out of the text (#576). PDF: `pypdf`, pinned exactly, page text only, never metadata. DOCX: `word/document.xml` in memory under a size cap, through `safe_parser`. | `source_form()`, `html_lines()`, `pdf_lines()`, `docx_lines()` |
| `promotion/anchors.py` | The regulation's start in extracted lines: the title line and the first section (`§ 1`/`Kapittel 1`). One definition shared by the HTML reader and the field splitter; imports nothing from the package. | `anchor_lines()`, `first_title()`, `first_section()` |
| `promotion/fields.py` | Identification block (title line up to the first `§ 1`/`Kapittel 1`) and body; pre-filled `title`, `hjemmel`, `vedtatt_av`, each verbatim, else empty; `vedtatt` and `ikraft`/`ikraft_text` are the value `stated_dates.py` resolves from every statement, empty when the statements disagree (#581). | `read_regulation()` |
| `promotion/personal_data.py` | The personopplysningsloven gate: fødselsnummer/D-nummer (mod-11), e-mail, phone, postal address, contact lines, signature blocks, bylines naming a person (`Publisert av`, `Sist endra av`, `Ansvarleg` …). Reports kind and line, never the value; holds, never redacts. | `screen_personal_data()` |
| `promotion/extract.py` | Bytes and media type (never the URL) to `ExtractedDocument` or a `HeldExtraction` with a typed reason: unsupported format, unreadable, empty, garbled (a PDF font without ToUnicode), Lovdata print, placeholder date, no body, no title, personal data. | `extract_regulation()`, `EXTRACTOR_VERSION` |
| `promotion/render.py` | One version to Markdown, byte-deterministic: ADR-0016's front matter in its fixed order (`source_license: "åndsverkloven § 14"`, `basis: "observed"`, `asserted: false`, `observed_at_first` in UTC; no `retrieved_at`, no `observed_at_last`), then the title, block and body. Refuses a content hash that is not the text's, and any NLOD mention. | `render_local_regulation()`, `LOCAL_RENDERER_VERSION` |

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
