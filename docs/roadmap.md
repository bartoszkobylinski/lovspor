# Roadmap — strategic options after Sprint 8

> **Strategic options under evaluation.** This document is not a commitment. It is a structured menu of directions to argue against during sprint planning, written 2026-04-29 immediately after Sprint 8 closeout (PR #35 merged). It complements [`decisions.md`](decisions.md), which logs decisions actually made.

---

## Where the project stands today

### Engine (`lovspor`)
- Downloads `gjeldende-lover` and `gjeldende-sentrale-forskrifter` from Lovdata's NLOD 2.0 public-data API.
- Safely extracts XML members (XXE blocked, billion-laughs blocked, tar path traversal blocked, lxml `safe_parser`, `tarfile.data_filter`).
- Deterministic SHA256 over normalized XML.
- Deterministic Markdown rendering (frontmatter + body).
- Change detection: new / changed / removed / unchanged + rename detection.
- Per-document conventional commits (`add(lov):`, `update(forskrift):`, `rename(...)`, `remove(...)`).
- Per-act history (`history/<slug>.{json,md}`) extracted via `git log --follow`.
- Per-dataset `INDEX.md`.
- Three self-healing migrations (Sprint 4 slug, Sprint 5 history, Sprint 8 `eu_basis`).
- Daily 04:00 UTC scheduled sync via GitHub Actions.
- 503 tests, 98% coverage, Codex review on every PR.

### Corpus (`lovverk`)
- ~4500 Norwegian acts and central regulations rendered to Markdown.
- Full git history. Time-travel "as of date" already exists for free — just not yet exposed.
- Manifest as the single source of truth for change detection.

### MCP server (12 tools)
| Tool | Purpose |
|---|---|
| `get_law` | full Markdown of an act |
| `get_section` | one `§` with parent chapter + validated `cross_references` |
| `get_law_history` | structured change events |
| `list_recent_changes` | sorted by `last_changed` |
| `search_laws` | slug + title metadata search |
| `search_body` | full-text body search, lazy 45 MB index |
| `semantic_search` | top-K cosine over per-section embeddings (Sprint 9) |
| `validate_citation` | zero-hallucination guard for citations |
| `verify_quote` | verbatim-quote anti-hallucination check (Sprint 9) |
| `get_eu_basis` | Norwegian act → CELEX list |
| `search_eu_implementations` | CELEX → list of acts |
| `corpus_status` | freshness / staleness signal |

### Positioning
Parity-or-better with the polish-law-mcp ecosystem (Ansvar, numikel, janisz). The only Norwegian-law MCP server. 12 tools versus their ~13, with Sprint 9 closing the semantic-search gap and adding a four-layer anti-hallucination story (`semantic_search` → `get_section` + `cross_references` → `verify_quote` → `validate_citation`).

---

## Known gaps

### Domain coverage
- **`gjeldende-lokale-forskrifter`** — local regulations, ~10x volume of central regulations. Available in the same Lovdata API.
- **`historiske-lover`** — repealed acts, for "what was the law in 2015?" questions.
- **`forarbeider`** — preparatory works (Ot.prp., NOU, Innst., Prop.). Important for legislative-intent reasoning.
- **`domsregister`** — Supreme Court (Høyesterett) decisions. Without case law, AI cannot reason about precedent.
- **Sami-language datasets** — northern Sami, lule Sami.

### Structural depth
- **No AST.** The corpus is raw Markdown; there is no structured graph of `Lov → Kapittel → Paragraf → Ledd → Bokstav → Punkt`. Each tool parses text ad-hoc with its own regex (`get_section`, `validate_citation`). This compounds badly as more tools are added.
- **No cross-reference graph.** Norwegian acts cite each other constantly ("jf. § 5-12 i skatteloven"), but those edges are not extracted. No `get_inbound_references(slug, section_id)`.
- **No definitions index.** Most acts contain "I denne loven menes med..." — a goldmine for AI consumers — but the terms are not extracted.
- **No forskrift→lov mapping.** Each forskrift is issued under specific legal authority encoded in the source XML; this relationship is not exposed.

### Search quality
- **Substring matching only** in `search_body`. A search for `"kryptovaluta"` misses `"virtuell valuta"` — both terms appear in Norwegian tax guidance for the same concept. No tokenization, no Norwegian morphology (Norwegian inflects heavily — `skatteyteren` / `skatteytere` / `skatteyterne` is one term), no BM25 ranking. (Sprint 9 added `semantic_search` for cross-vocabulary matching, which addresses this from a different angle but does not replace BM25 / morphology for keyword queries.)
- **45 MB body index in RAM** is acceptable for 4500 docs but would scale to ~500 MB once local regulations are added. No SQLite FTS5 fallback.
- **No reranker on `semantic_search` results.** Top-K is raw cosine similarity; a domain-tuned reranker (cross-encoder) could filter "close-but-wrong" matches further. Deferred until eval shows a need.
- **No fuzzy slug match.** Callers must hit the canonical slug exactly.

### Operational
- **No time-machine tool.** `git log --follow` exists; `get_law_at(slug, date)` does not. Trivial to add.
- **No diff tool.** `diff_law_versions(slug, date_a, date_b)` would be unique to this project — competitors lack the git-based architecture.
- **No quality monitor.** Whether every cross-reference in body text resolves to a real act is unknown.
- **No corpus signing.** The manifest could be GPG-signed. Useful once `lovverk` becomes a trust anchor for downstream consumers.

### Distribution
- **Not on PyPI.** `uvx --from git+...` is friction for most users.
- **No Docker image.**
- **No public docs site** (mkdocs).
- **No hosted MCP endpoint.** Each user clones `lovverk` themselves.

---

## Options under consideration

Grouped by class. Each entry estimates **leverage** (how much it unlocks), **novelty** (whether competitors already have it), and **effort** (rough size).

### Class A: Structural depth

**A1. Structural AST + table-of-contents tools**
- Parse Markdown into a Pydantic AST: `Lov(chapters=[Kapittel(sections=[Paragraf(...)])])`.
- New tools: `get_law_toc(slug)`, `get_chapter(slug, chapter_id)`, `list_sections(slug)`.
- Refactor `get_section` and `validate_citation` to consume the AST instead of parsing text repeatedly.
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

**B1. Time-machine**
- New tool: `get_law_at(slug, date: ISO)` — uses `git log --before=<date> -- <markdown_path>` and `git show <commit>:<path>`.
- New tool: `list_law_versions(slug)` — distinct content snapshots in history.
- **Leverage:** high. No competitor has a git-based architecture, so this is a unique selling point.
- **Novelty:** unique to this project.
- **Effort:** low. Estimated ~200 lines on top of existing history infrastructure.

**B2. Diff tool**
- New tool: `diff_law_versions(slug, date_a, date_b)` or `diff_law_versions(slug, version_a, version_b)`.
- Output: section-by-section unified diff plus a summary.
- Builds on B1.
- **Leverage:** high. Answers "what exactly changed in skatteloven between 2020 and 2024?".
- **Effort:** medium. Diff library plus careful formatting.

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

**D1. Local regulations (`gjeldende-lokale-forskrifter`)**
- Add the dataset to the sync pipeline.
- ~10x volume; likely warrants a separate corpus repo (`lovverk-lokal`) to avoid bloating the main one.
- **Leverage:** medium. Specific audience — municipal lawyers, urban planners.
- **Effort:** medium.

**D2. Domsregister (Supreme Court cases)**
- Different schema, different licensing.
- **Leverage:** very high. Without case law, AI cannot reason about precedent — this is the largest qualitative gap.
- **Effort:** high. A different pipeline, different rendering.

**D3. Forarbeider (preparatory works)**
- Ot.prp., NOU, Innst. — *legislative intent*, important for interpreting acts.
- **Leverage:** medium (academic / specialist).
- **Effort:** high.

### Class E: Distribution

**E1. PyPI publish + Docker image**
- `pip install lovspor`, `docker run lovspor mcp ...`.
- **Leverage:** high in adoption terms.
- **Effort:** low (1–2 days).
- **Missing pieces:** version bump strategy, classifiers, PyPI README, trusted publishing through GitHub Actions.

**E2. Public docs site (mkdocs-material)**
- GitHub Pages.
- Showcase + tutorials + API reference.
- **Effort:** low.

**E3. Hosted MCP endpoint**
- Cloud-hosted server with auto-refreshing `lovverk`.
- Users configure a URL instead of cloning the corpus.
- **Effort:** high (auth, hosting, SLA).
- **Risk:** the project becomes a SaaS, which is a different problem domain.

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

Top three by **value × novelty**:

1. **Time-machine + diff tool** (Class B). Cheapest in cost-per-impact (~3 days). Unique — no competitor has a git-based architecture. Answers a real legal-research question: "what did this law say in 2018?". Builds on infrastructure already present.
2. **AST + cross-reference graph** (Classes A1, A2). A larger investment, but unblocks many later sprints. The AST enables: better `get_section`, structural diffs (complementing class B), navigation tools, and a richer `cross_references` field on `get_section` (currently regex-based, B-tier scope).
3. **Domsregister** (Class D2). The most valuable domain expansion — without case law, legal AI is weak. Sprint 9 closed the semantic-search gap (C1), so the next major leverage move is in domain coverage rather than search quality.

Top three by **adoption × reach**:

4. **PyPI publish** (Class E1). One day of work, opens the project to mass adoption.
5. **Public docs site + showcase** (Class E2). Discoverability.
6. **Local regulations** (D1). Concrete audience (municipal lawyers, urban planners); separate corpus repo to avoid bloating the main one.

---

## Currently out of scope

- **Multi-jurisdiction** (G1) — premature; would dilute focus.
- **Hosted MCP endpoint** (E3) — moves the project into SaaS territory, a different problem from the OSS engine.
- **Web UI** (G2) — the audience is AI agents, not humans.
- **Forarbeider** (D3) — niche; low leverage relative to effort.

---

*Last reviewed: 2026-05-07 (post-Sprint-9 update). Roadmap is intended for quarterly review. Items move between classes through discussion in the issue tracker.*
