# Roadmap — strategic options after Sprint 8

> **Strategic options under evaluation.** This document is not a commitment. It is a structured menu of directions to argue against during sprint planning, written 2026-04-29 immediately after Sprint 8 closeout (PR #35 merged). It complements [`decisions.md`](decisions.md), which logs decisions actually made.

> **Commercial priority changed 2026-07-14.** The option catalogue below is preserved, but its previous priority ordering is superseded by the Hosted Lovspor MCP pivot. Sprint 12 now focuses on turning the existing local read-only MCP into a paid remote service. Structural depth, domain expansion, and local distribution remain candidate follow-ups rather than the immediate plan.

---

## Where the project stands today

### Engine (`lovspor`)
- Downloads `gjeldende-lover` and `gjeldende-sentrale-forskrifter` from Lovdata's NLOD 2.0 public-data API.
- Safely extracts XML members (XXE + billion-laughs blocked via lxml `safe_parser`; tar path traversal blocked by streaming members with `extractfile()` — never `extractall()`/`extract()`; decompression bombs bounded by a per-member size cap).
- Deterministic SHA256 over normalized XML.
- Deterministic Markdown rendering (frontmatter + body), stamped with a `RENDERER_VERSION` (currently 3).
- Change detection: new / changed / removed / unchanged + rename detection.
- Per-document conventional commits (`add(lov):`, `update(forskrift):`, `rename(...)`, `remove(...)`).
- Per-act history (`history/<slug>.{json,md}`) extracted via `git log --follow`.
- Per-dataset `INDEX.md`.
- Self-healing migrations (Sprint 4 slug, Sprint 5 history, Sprint 8 `eu_basis`) plus renderer-version self-healing: a renderer bump re-renders stale documents on the next syncs, rate-limited so one bump never rewrites the whole corpus at once.
- Upstream-drift tolerance: unknown Lovdata API fields no longer take the sync offline — they are accepted, surfaced, and auto-filed as a GitHub issue. Documents Lovdata serves as an error notice are withheld rather than published as placeholder text.
- Atomic corpus writes (manifest, documents, history) so a crashed sync cannot leave a half-written corpus.
- Daily 04:00 UTC scheduled sync via GitHub Actions; CI matrix across Python 3.12 / 3.13 / 3.14.
- 1161 tests (1058 unit + 94 integration + 9 Hypothesis property tests), 98% coverage, Codex review on every PR.

### Corpus (`lovverk`)
- ~5,900 Norwegian acts and central regulations rendered to Markdown (764 *lover* + 5,147 central *forskrifter* = 5,911 as of the 2026-07-12 sync; each dataset's `INDEX.md` carries the live count, and the MCP `corpus_status` tool reports it).
- Full git history, exposed since Sprint 10 through the time-machine tools below.
- Manifest as the single source of truth for change detection.

### MCP server (16 tools)
| Tool | Purpose |
|---|---|
| `get_law` | full Markdown of an act |
| `get_law_at` | full Markdown as of a target date — time-machine via `git log --follow` (Sprint 10) |
| `list_law_versions` | dates of distinct content versions, oldest-first (Sprint 10) |
| `diff_law_versions` | section-by-section diff of an act between two dates (Sprint 10 B2) |
| `get_section` | one `§` with parent chapter + validated `cross_references` |
| `list_sections` | an act's table of contents: every `§` id + heading |
| `get_law_history` | structured change events |
| `list_recent_changes` | sorted by `last_changed` |
| `search_laws` | slug + title metadata search |
| `search_body` | full-text body search, lazy 45 MB index |
| `semantic_search` | top-K cosine over per-section embeddings (Sprint 9) |
| `validate_citation` | structured validation guard for citations |
| `verify_quote` | verbatim-quote anti-hallucination check (Sprint 9) |
| `get_eu_basis` | Norwegian act → CELEX list |
| `search_eu_implementations` | CELEX → list of acts |
| `corpus_status` | freshness / staleness signal |

### Positioning
Parity-or-better with the polish-law-mcp ecosystem (Ansvar, numikel, janisz). The only Norwegian-law MCP server. 16 tools versus their ~13, with Sprint 9 closing the semantic-search gap and adding a four-layer grounding and verification path (`semantic_search` → `get_section` + `cross_references` → `verify_quote` → `validate_citation`), and Sprint 10 adding a git-history time-machine set (`get_law_at`, `list_law_versions`, `diff_law_versions`) that no other corpus-MCP can match because none of them version their corpus through git.

---

## Commercial pivot — Hosted Lovspor MCP (decided 2026-07-14)

The primary product is now a **paid remote MCP service for grounded conversations about Norwegian law**, not a locally distributed Python package. A user connects Lovspor to an MCP-capable AI client and can ask about current provisions, available historical revisions, exact changes, EU/EEA relationships, and citations without installing the engine or cloning the corpus.

The product promise is deliberately narrower than "no hallucinations": Lovspor supplies current, versioned legal text and machine-checkable evidence, while the AI client remains responsible for interpreting that evidence. Public language should say **grounded answers with verifiable citations**, never guarantee that an LLM cannot make a mistake.

### Product boundary

- `lovverk` remains public under NLOD 2.0 as the auditable corpus and provenance layer.
- The engine and hosted service remain private while the commercial direction is evaluated.
- The previously published MIT releases `0.2.0`–`0.3.0` were removed from PyPI on 2026-07-14. Copies already obtained remain governed by their original licence; future hosted-service code is not distributed through PyPI.
- Local stdio remains useful for development, tests, and operations, but is no longer the primary consumer distribution model.
- Monitoring and alerts remain possible follow-up products. They do not replace the core MCP experience.

### Sprint 12 — Hosted MCP foundation (active priority)

1. **Remote transport — SHIPPED (transport only).** `lovspor mcp-http` exposes the existing read-only tool surface over the MCP Streamable HTTP transport; stdio remains the development path. Tool bodies are offloaded to worker threads (the SDK calls sync handlers inline on its event loop, so one slow call would otherwise stall every client) and the corpus indices are warmed at startup (a cold build holds the reader's cache lock for seconds). `/healthz` and `/readyz` probes included. **Bearer auth and per-credential quotas now enforced (item 3), but still TLS-less** — localhost behind a TLS-terminating proxy only; it is not yet a deployable service.
2. **Hosted corpus runtime.** Run against an automatically refreshed `lovverk` clone, with health/readiness checks and an operator-visible freshness signal.
3. **Access control — SHIPPED except self-service OAuth and TLS.** Revocable beta credentials, per-credential quotas, rate limiting, and in-memory usage counters are enforced against manually issued tokens. Remaining: TLS (terminated upstream by a reverse proxy) and self-service OAuth before a broad launch.
4. **Grounded research workflow.** Evaluate a high-level `research_law` tool that returns an evidence bundle: matched sections, exact quotes, corpus revision, validation results, and source links. Preserve the 16 lower-level tools for composability.
5. **Trust layer.** Promote per-section Lovdata deep links (F1) and extend evals to measure tool selection, citation validity, quote fidelity, temporal-boundary handling, and unsupported-claim behaviour.
6. **Client adoption.** Publish connection instructions and tested examples for supported AI clients, plus a short comparison showing an ungrounded answer versus a Lovspor-grounded answer.
7. **Operational hardening.** Add deployment automation, timeouts, abuse controls, availability monitoring, backup/recovery, and a privacy policy that avoids retaining full legal queries by default.
8. **Commercial layer after company formation.** Add billing, subscriptions, self-service accounts, and production OAuth after the beta proves recurring use.

### Deferred, not rejected

Until the hosted beta is usable, do not start AST, Høyesterett ingestion, local regulations, FTS5, Docker, a broad web UI, or multi-jurisdiction work. Every option remains documented below and can be promoted when beta evidence shows that it removes a real adoption or answer-quality constraint.

---

## Known gaps

### Domain coverage

Grouped by source availability (restructured 2026-05-18 — see Class D for execution detail and "Currently out of scope" for the §43 reasoning behind the blocked items).

**In Lovdata's `publicData` API — pipeline work only:**
- **`gjeldende-lokale-forskrifter`** — municipal + county regulations, ~10× volume of central regulations (~37k docs).
- **`historiske-lover`** — repealed acts, for "what was the law in 2015?" questions that the time-machine tool can't reach (it can only see commits we have made).
- **`lovtidend-avd1-{year}`** — official change announcements (Norwegian Federal Register equivalent). Explains *why* a law changed, complementing our existing change-detection.
- **Sami-language datasets** — northern Sami, Lule Sami translations of selected acts.

**Outside Lovdata, on the source publisher's own site — new fetcher per source, legally clean:**
- **`domstol.no` — Høyesterett decisions 2004+** (~3k docs, ~100–150/yr). State-published, no Lovdata middleman, no §43 problem. Apex precedent only; ~5–10% of full case-law need.
- **`stortinget.no` API** — parliamentary records (saker, voteringer, komitéinnstillinger). Documented JSON API, 100 req/min. Enrichment overlay rather than primary corpus.
- **`stortinget.no` + `regjeringen.no` — forarbeider** (NOU, Ot.prp., Prop. L, Innst. S). The legislative-intent goldmine. PDF-heavy.
- **`eur-lex.europa.eu`** — resolve existing `eu_basis` CELEX identifiers to actual EU directive text. Closes the loop on EU-implementation acts.
- **Specialized administrative tribunals** — Datatilsynet, KOFA, Markedsrådet, Konkurransetilsynet, Finanstilsynet rulings + guidance. Per-institution, varies wildly.

**Partial — published subset only:**
- **Lagmannsrett (appellate) decisions** — sporadic individual high-profile rulings on `domstol.no/<court>/`. No systematic collection outside Lovdata.
- **Tingrett (district) decisions** — almost none published openly. Practically inaccessible as a corpus.
- **Specialized courts** (Arbeidsretten, Trygderetten, Riksrett) — per-institution sites with varying transparency.
- **English translations of Norwegian law / Høyesterett decisions** — partial coverage on domstol.no and individual ministry sites; full collection is Lovdata-Pro-only.

**Legally blocked by Lovdata §43 (database right) — see "Currently out of scope":**
- Lovdata's full `domsregister` (appellate + district + specialized, all courts).
- Lovdata's editorial layer (headnotes, sammendrag, stikkord, prejudikat-classification, cross-reference networks).
- Pre-2004 Høyesterett full decisions held only in Lovdata's collection.
- Lovdata's commentary (kommentarutgaver, Lovdata Pro).

### Structural depth
- **No AST.** The corpus is raw Markdown; there is no structured graph of `Lov → Kapittel → Paragraf → Ledd → Bokstav → Punkt`. Each tool parses text ad-hoc with its own regex (`get_section`, `validate_citation`). This compounds badly as more tools are added.
- **No cross-reference graph.** Norwegian acts cite each other constantly ("jf. § 5-12 i skatteloven"), but those edges are not extracted. No `get_inbound_references(slug, section_id)`.
- **No definitions index.** Most acts contain "I denne loven menes med..." — a goldmine for AI consumers — but the terms are not extracted.
- **No forskrift→lov mapping.** Each forskrift is issued under specific legal authority encoded in the source XML; this relationship is not exposed.

### Search quality
- **Substring matching only** in `search_body`. A search for `"kryptovaluta"` misses `"virtuell valuta"` — both terms appear in Norwegian tax guidance for the same concept. No tokenization, no Norwegian morphology (Norwegian inflects heavily — `skatteyteren` / `skatteytere` / `skatteyterne` is one term), no BM25 ranking. (Sprint 9 added `semantic_search` for cross-vocabulary matching, which addresses this from a different angle but does not replace BM25 / morphology for keyword queries.)
- **45 MB body index in RAM** is acceptable for ~5,900 docs but would scale to ~500 MB once local regulations are added. No SQLite FTS5 fallback.
- **No reranker on `semantic_search` results.** Top-K is raw cosine similarity; a domain-tuned reranker (cross-encoder) could filter "close-but-wrong" matches further. Deferred until eval shows a need.
- **No fuzzy slug match.** Callers must hit the canonical slug exactly.

### Operational
- **Diff tool — SHIPPED (Sprint 10 B2).** `diff_law_versions(slug, date_a, date_b)` returns a section-by-section diff between two dates — unique to this project, since competitors lack the git-based architecture. Built on the B1 time-machine (`get_law_at` + `list_law_versions`).
- **No quality monitor.** Whether every cross-reference in body text resolves to a real act is unknown.
- **No corpus signing.** The manifest could be GPG-signed. Useful once `lovverk` becomes a trust anchor for downstream consumers.

### Distribution
- **PyPI local distribution — WITHDRAWN 2026-07-14.** Versions `0.2.0`–`0.3.0` shipped, then were removed when the engine moved to a private hosted-service strategy. The `lovspor` project name remains reserved by its sole owner with no downloadable releases.
- **No Docker image.** Retained as a future private-deployment or enterprise option, not a current adoption priority.
- **No public docs site** (mkdocs).
- **No hosted MCP endpoint yet.** This is the active Sprint 12 priority. The Streamable HTTP *transport* now exists (`lovspor mcp-http`, with thread-offloaded tool bodies, startup index warming, and health/readiness probes), and it now enforces bearer-token auth and per-credential quotas + rate limiting — but it still has no TLS or deployment, so there is still nothing a consumer can connect to over the internet. stdio plus `lovspor fetch-corpus` remains the only usable path.

---

## Options under consideration

Grouped by class. Each entry estimates **leverage** (how much it unlocks), **novelty** (whether competitors already have it), and **effort** (rough size).

### Class A: Structural depth

**A1. Structural AST + table-of-contents tools**
- Parse Markdown into a Pydantic AST: `Lov(chapters=[Kapittel(sections=[Paragraf(...)])])`.
- New tools: `get_law_toc(slug)`, `get_chapter(slug, chapter_id)`.
- Refactor `get_section`, `list_sections`, and `validate_citation` (all currently text-parsing, already shipped) to consume the AST instead of parsing text repeatedly.
- **Leverage:** very high — every later tool builds on the AST.
- **Effort:** high. Norwegian legal structure is irregular: sometimes `§ 1`, sometimes `§ 1-1`, preambles without numbering, "Kap. III" in Roman numerals, appendices ("Vedlegg").
- **Risk:** the parser must be solid; an AST bug breaks every dependent tool.

**A2. Cross-reference graph**
- Scan body text for patterns like "§ N-M i <slug>", "loven § N", "etter forvaltningsloven § N", "jf. § N andre ledd".
- Build a graph: `<doc_id, section_id> → [(doc_id, section_id), ...]`.
- New tools: `get_inbound_references(slug, section_id?)`, `get_outbound_references(slug, section_id?)`.
- **Leverage:** high — surfaces the hidden structure of Norwegian law.
- **Novelty:** unique at section granularity. polish-law-mcp resolves at act granularity only.
- **Effort:** medium. Regex + heuristics; can ship without a full AST.
- **Pitfall:** ambiguous references ("§ 5" inside the same act vs. inside a cited act).

**A3. Definitions extraction**
- Many acts carry a `§ 1-X. Definisjoner` section or "I denne loven menes med..." block.
- Extract: term → definition → source `(slug, section_id)`.
- New tool: `get_definition(term, slug?)`. With no slug, search across all acts.
- Storage: `lovverk/definitions.json`.
- **Leverage:** medium-high. AI consumers asking "what does *forbruker* mean in Norwegian law?" get an authoritative answer.
- **Effort:** medium — pattern matching plus post-processing.

### Class B: Time and version

**B1. Time-machine — SHIPPED in Sprint 10 PR-A (this PR)**
- New tool: `get_law_at(slug, date: ISO)` — walks the file's full `git log --follow` lineage in-process (`--before` cannot be used with `--follow` because it blinds the rename-tracker to pre-cutoff commits) and feeds the matched `(sha, path-at-commit)` to `git show`. Pre-Sprint-4 historical paths (e.g. `lover/nl-19990326-014.md`) are traced transparently from today's slug.
- New tool: `list_law_versions(slug)` — reads the existing `history/<slug>.json` from Sprint 5, filters to `added` / `updated` events (renames skipped — content unchanged), returns oldest-first.
- Implemented as a new `lovspor.timetravel` module + two `CorpusReader` methods + two `@mcp.tool()` decorators. ~150 lines of net new code; reuses Sprint-5 `history/<slug>.json` and the manifest's `markdown_path`.
- End-of-day UTC semantics for `target_date`; future dates are refused with a `ValueError` (typo guard rather than alias to HEAD).

**B2. Diff tool — SHIPPED in Sprint 10 (this PR)**
- New tool: `diff_law_versions(slug, date_a, date_b)` — resolves both dates through the B1 time-machine, then diffs the two versions section by section.
- Output: `{slug, date_a, date_b, resolved_commit_a, resolved_commit_b, summary, sections}`; each added / removed / changed `§` carries a stdlib `difflib` unified diff of its heading and body. Frontmatter is stripped so metadata churn never shows.
- Implemented as a pure `_diff_section_maps` core + a `CorpusReader.diff_law_versions` method + one `@mcp.tool()`, reusing the Sprint-10 `timetravel` resolver (extended to report the resolved commit sha) and the existing section parser. No new dependency.
- **Leverage:** high. Answers "what exactly changed in skatteloven between 2020 and 2024?".
- Date input only for now; version-index input (`version_a`/`version_b` from `list_law_versions`) deferred as a possible follow-up.

### Class C: Search quality

**C1. Semantic search via embeddings — SHIPPED in Sprint 9 (PR #41 → #50, MERGED 2026-04-30 → 2026-05-06)**
- Per-section embeddings via `text-embedding-3-large` (3072-dim, int8-quantized, ~99% similarity preserved at 1/4 storage). Model chosen empirically — beat Norwegian-tuned alternatives by +24% Recall@5 on a 47-query benchmark.
- Storage: per-doc `<dataset>/embeddings/<slug>.bin` files, LSPE binary format. See [`docs/embeddings.md`](embeddings.md). Chosen over monolithic parquet/SQLite because per-doc sharding preserves git diff per-section semantics.
- New tools: `semantic_search(query, dataset?, limit?)` returns top-K with `score` + `citation_hint`; `verify_quote(slug, section_id, quote)` is the matching anti-hallucination guard for verbatim citations; `get_section` response gained a `cross_references` field listing every internal `§ N-M` ref already validated.
- See [`docs/decisions.md` Sprint 9 entry](decisions.md) for the full breakdown including the path-cascade hotfix train (#43-#46) that closed an entire bug class during the migration rollout.

**C2. SQLite FTS5 + Norwegian stemmer**
- Norwegian Snowball stemmer + FTS5 + BM25.
- Replaces the substring scan in `search_body`.
- Tool surface unchanged; results gain real relevance ranking.
- **Leverage:** medium-high. Incremental but real.
- **Effort:** medium. SQLite integration; decide whether the index ships in `lovverk` or is built on the fly.

### Class D: Domain expansion

Restructured 2026-05-18 around source-legality reality (see "Domain coverage" above for the full taxonomy). Three sub-classes:

#### D-API — additional Lovdata `publicData` datasets

Pure pipeline extension. Same engine, same legal posture, same MCP contract. New dataset key + slug-prefix per item.

**D-API-1. `gjeldende-lokale-forskrifter`** — local regulations.
- ~37k docs, ~10× current central forskrifter. Pushes `lovverk` toward GitHub's 1 GB soft repo-size warning; embeddings would push past it.
- Open question: subfolder (`lovverk/lokale-forskrifter/`) vs separate repo (`lovverk-lokal`). Leaning separate repo for audience-separation reasons — 95% of users don't need municipal regs and shouldn't pay the clone/RAM/embedding-disk cost.
- **Leverage:** medium. Niche audience — municipal lawyers, urban planners, building consultants.
- **Effort:** 2 sprints (pipeline + MCP-side dataset-filter adjustments).

**D-API-2. `historiske-lover`** — repealed acts.
- Modest volume. Static — no daily update cadence required.
- Enables "what was the law in 2015?" answers beyond the time-machine's reach (the time-machine can only see commits we have made; repealed-before-corpus laws need this dataset).
- **Leverage:** medium-high. Closes a real query type.
- **Effort:** 1 sprint.

**D-API-3. `lovtidend-avd1-{year}`** — official change announcements.
- Annual tarballs, small.
- Complements existing change-detection: explains *why* a law changed, not *what* it now says.
- **Leverage:** medium. Useful overlay on `list_recent_changes` / `get_law_history`.
- **Effort:** half-sprint.

**D-API-4. Sami-language texts** — northern + Lule Sami.
- Same XML shape as primary law texts; mostly manifest-key + frontmatter extension.
- **Leverage:** low by volume, high by cultural value.
- **Effort:** half-sprint.

#### D-DIRECT — state primary sources outside Lovdata

New fetcher per source (no Lovdata involvement, no §43 risk). Each source has its own format, metadata model, and update cadence — heavier engineering than D-API but legally clean.

**D-DIRECT-1. Høyesterett via `domstol.no`** — apex precedent.
- ~3k decisions 2004→present, ~100–150/yr. HTML + linked PDFs, no documented JSON API; "polite-fetch + cache" pattern.
- New domain model: `case_number`, `panel`, `dissens`, `prejudikat`, `cited_acts`. Decisions are immutable; no change-detection / time-machine machinery needed.
- New MCP tools: `get_decision(case_id)`, `list_decisions_citing(slug, section_id?)`. The latter is unique — linking apex precedent to law sections, no competitor has it.
- **Leverage:** very high. Closes the apex-precedent gap that the project has called "the largest qualitative gap" (the rest of case law sits behind §43; see "Currently out of scope").
- **Novelty:** unique. No other Norwegian-law MCP server has case law.
- **Effort:** 2 sprints (crawl + PDF→Markdown + new domain model + MCP tools).

**D-DIRECT-2. EUR-Lex CELEX resolution**
- We already extract `eu_basis` as CELEX identifiers (Sprint 8). Fetch the actual directive text on demand.
- EUR-Lex has a public API; legislation is CC0-equivalent.
- New MCP tool: `get_eu_text(celex)` or expand `get_eu_basis` to return text inline.
- **Leverage:** high for users working with EU-implementation acts (GDPR, NIS2, eIDAS, AI Act).
- **Effort:** 1 sprint.

**D-DIRECT-3. Forarbeider** — Ot.prp., NOU, Prop. L, Innst. S.
- Mixed sources: `stortinget.no` API for some metadata, `regjeringen.no` for NOUs as PDFs.
- Heavy PDF→text work. Cross-linking forarbeider→acts requires a slug-resolution layer.
- **Leverage:** high for serious legal research (legislative intent), niche otherwise.
- **Effort:** 2–3 sprints. (Previously listed as "out of scope — niche" — moved into D-DIRECT after the 2026-05-18 source audit clarified the engineering is real but the legal path is clean.)

**D-DIRECT-4. `data.stortinget.no`** — parliamentary records.
- Documented JSON API, 100 req/min.
- Enrichment overlay: which parties voted how on a given act, on what date, with what dissent.
- **Leverage:** medium. Adds political-context layer to existing acts.
- **Effort:** 1 sprint.

**D-DIRECT-5. Specialized administrative tribunals**
- Datatilsynet, KOFA, Markedsrådet, Konkurransetilsynet, Finanstilsynet — each has its own site, PDFs, varying transparency.
- Demand-driven: ship one when a real user surfaces an ask for that domain.
- **Leverage:** specialist value per industry.
- **Effort:** 1 sprint per institution.

#### D-BLOCKED — legally inaccessible

Documented for clarity; do not attempt. See "Currently out of scope" for the legal reasoning.

- **Lovdata's full `domsregister`** (appellate + district + specialized, all-court collection).
- **Lovdata's editorial layer** on case law (headnotes, sammendrag, stikkord, classification, cross-references).
- **Pre-2004 Høyesterett full decisions** held only in Lovdata's collection.
- **Lovdata's commentary** (kommentarutgaver, Lovdata Pro features).
- **Lagmannsrett / tingrett decisions in bulk** — the systematic collection only exists at Lovdata; only sporadic individual cases reach `domstol.no`.

### Class E: Distribution

**E1. Local package distribution — SHIPPED, THEN WITHDRAWN; Docker image — deferred**
- `pip install lovspor` / `uvx lovspor` shipped through `0.3.0`, then the releases were removed from PyPI on 2026-07-14 after the commercial pivot. This remains part of the project's distribution history, not the current consumer path.
- `docker run lovspor mcp ...` remains an option for private deployments and enterprise customers that require their own infrastructure.
- **Leverage:** low for the hosted default; potentially high for enterprise deployment.
- **Effort:** Docker image ~low.

**E2. Public docs site (mkdocs-material)**
- GitHub Pages.
- Showcase + remote-MCP connection tutorials + API/tool reference.
- **Effort:** low.

**E3. Hosted MCP endpoint — ACTIVE SPRINT 12; transport shipped 2026-07-16**
- Cloud-hosted server with auto-refreshing `lovverk`.
- Users configure a URL and authenticate instead of installing the engine or cloning the corpus.
- Commercial requirements: HTTPS MCP transport, credentials/OAuth, quotas, rate limits, usage metering, privacy controls, deployment, monitoring, and eventually billing.
- **Progress:** the Streamable HTTP transport exists (`lovspor mcp-http`) with bearer-token auth and per-credential quotas + rate limiting now enforced; TLS, usage metering, deployment, and monitoring remain outstanding, so there is still no endpoint a consumer can use over the internet.
- **Effort:** high (auth, hosting, SLA).
- **Risk:** the project becomes a SaaS, which is a different problem domain. This risk is now accepted deliberately and managed through a bounded beta before billing work.

### Class F: Trust and provenance

**F1. Per-section Lovdata deep links**
- Frontmatter on each section gains a `lovdata_url` anchored to the `§` on lovdata.no.
- AI consumers can cite back to Lovdata directly.
- **Leverage:** medium. User trust, verifiability.
- **Effort:** low.

**F2. Cryptographic manifest signing**
- GPG-sign the manifest on each sync; consumers verify on read.
- **Leverage:** low today, useful once `lovverk` is a downstream trust anchor.
- **Effort:** low.

### Class G: Long-game

**G1. Multi-jurisdiction**
- Same engine, different sources: Riksdagen (SE), retsinformation.dk (DK), Althingi (IS).
- Or: add a Polish module to compete head-on with polish-law-mcp.
- **Leverage:** very high (brand expansion).
- **Effort:** one sprint per jurisdiction.
- **Risk:** focus dilution.

**G2. Web UI for browsing `lovverk`**
- Static site (mkdocs or Next.js).
- For humans not using AI.
- **Leverage:** low. The audience is AI agents, not humans reading Markdown.
- **Effort:** medium.

---

## Recommendation

> **Status change 2026-07-14:** Hosted MCP (E3) supersedes the ordering below as the active commercial priority. The earlier recommendations are retained as the option backlog and should be reconsidered after the hosted beta identifies concrete retrieval, coverage, or adoption gaps.

### Active commercial priority

1. **Hosted Lovspor MCP foundation** (E3 / Sprint 12): remote transport, hosted corpus runtime, access control, quotas, privacy, and operational hardening.
2. **Grounded research workflow:** evaluate a high-level evidence-bundle tool while preserving the existing 16 composable tools.
3. **Per-section Lovdata deep links** (F1): make every answer easier to verify against the source provider.
4. **Client onboarding and eval evidence** (E2): tested connection paths, scenario demos, and measured citation/quote/tool-selection quality.

### Preserved option backlog

Top three by **value × novelty** (unchanged by the Sprint 11 hardening wave, which paid down review debt rather than opening new capability — Class B remains complete, and no new option displaced these):

1. **AST + cross-reference graph** (Classes A1, A2). With Class B now complete (`diff_law_versions` shipped), this is the next value×novelty target. A larger investment, but unblocks many later sprints. The AST enables: better `get_section`, structural diffs (complementing B2's textual diff), navigation tools, and a richer `cross_references` field on `get_section` (currently regex-based, B-tier scope).
2. **Høyesterett via `domstol.no`** (Class D-DIRECT-1). The realistic version of the old Class D2 "Domsregister" entry — Lovdata's full case-law collection is legally blocked (§43; see "Currently out of scope"), but apex precedent via the courts' own publication is sprint-sized and legally clean. Closes the largest qualitative gap reachable without legislative change. Genuinely novel — no Norwegian-law MCP has case law.
3. **Per-section Lovdata deep links** (Class F1). Low effort, medium leverage: each section's frontmatter gains a `lovdata_url` anchored to the `§`, so AI consumers can cite straight back to Lovdata for verification.

Top three by **adoption × reach**:

4. **Docker image** (remaining half of Class E1; local PyPI distribution shipped through 0.3.0 before being withdrawn). A `docker run lovspor mcp` path for private or enterprise deployments.
5. **Public docs site + showcase** (Class E2). Discoverability.
6. **`historiske-lover` + `gjeldende-lokale-forskrifter`** (Classes D-API-1, D-API-2). Pure pipeline work, no legal risk, closes two real corpus gaps. Local regulations likely as a separate `lovverk-lokal` repo for audience-separation reasons.

---

## Currently out of scope

**Promoted out of this section:** Hosted MCP endpoint (E3) was previously deferred because it moves the project into SaaS territory. That consequence is now accepted; E3 is the active Sprint 12 priority described above.

**Strategic deferrals:**
- **Multi-jurisdiction** (G1) — premature; would dilute focus.
- **Web UI** (G2) — the audience is AI agents, not humans.

**Legally inaccessible** (see Class D-BLOCKED above):

The judgments themselves are public domain under *åndsverkloven* §14, but Lovdata's *database* of them is protected under §43 (Norway's implementation of EU Database Directive 96/9/EC), and Lovdata's editorial enrichment (headnotes, sammendrag, classification, cross-references) is original creative work and copyright-protected.

The position was tested and settled in *Lovdata vs Liland & Edvardsen* (Høyesterett 2019, the "Rettspraksis.no" case): scraping individual judgments from Lovdata's collection violates §43 even though the underlying judgments are public domain. Substantial extraction = §43 violation. Settled Norwegian law.

Lovdata is the institutional consolidator since 1981; courts pipe decisions into them; the editorial layer compounded over ~45 years. The legal paths around this are: (a) `domstol.no` for courts that publish directly — Høyesterett does (Class D-DIRECT-1), most lower courts don't; (b) court-by-court archive harvesting from primary sources, gargantuan and mostly paper; (c) wait for legislation to extend NLOD coverage to case law (ongoing political pressure, no commitment, no timeline).

Until (c), the following remain out of scope as a matter of Norwegian law, not project preference:

- Lovdata's full `domsregister` (appellate + district + specialized).
- Lovdata's editorial layer on any case law.
- Pre-2004 Høyesterett full decisions held only in Lovdata's collection.
- Lovdata's commentary (kommentarutgaver, Lovdata Pro).

---

*Last reviewed: 2026-07-16 (Sprint 12 item 1 — Streamable HTTP transport shipped; the rest of E3 remains outstanding). Written 2026-07-14 for the commercial pivot to Hosted Lovspor MCP; PyPI withdrawal; E3 promoted to active Sprint 12 without removing the prior option catalogue. The post-Sprint-11 engine/corpus inventory and the 2026-05-18 source-legality structure remain in force. Roadmap is intended for quarterly review. Items move between classes through discussion in the issue tracker.*
