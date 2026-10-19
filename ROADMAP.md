# lovspor — Roadmap

Last reviewed: 2026-10-19 · Reviewed against: e2da8d1b
Product: Lovspor (engine `lovspor` + corpus [`lovverk`](https://github.com/bartoszkobylinski/lovverk)) · Product roadmap: this file

The engine that keeps a versioned Markdown corpus of current Norwegian acts and central
regulations in sync with Lovdata's public-data API and serves it over MCP (stdio and a hosted
endpoint). The repository is public under AGPL-3.0 (`docs/decisions.md` §15, §18); `0.6.0` is the
current release. Active work is the local-law observatory and promotion of municipal regulations,
written requests for regulations under *offentleglova*, and a source-backed legislative crosswalk.

Older planning documents stay as history: `docs/roadmap.md` (option catalogue A–G, written
2026-04-29) and `docs/publication-plan.md` (phased plan, 2026-07-24). This file is the current view.

## Now
- Legislative crosswalk `LOV ↔ (Storting session, Lovvedtak) ↔ sak_id` (ADR-0017, accepted 2026-10-14) — issue #610; proof of the crosswalk merged in #613; groundwork #611, #612, #614, #616, #617, #619. Still gated on evidence coverage, artefact location, licensing and output schema (#610). Related: #618 (corrective re-sanction case).
- Innsyn requests for local regulations under *offentleglova* — issue #509; merged #512, #605, #609 (send a wave's letters over SMTP), #620, #629 (promote texts received under offentleglova). Open follow-ups #606, #625 (reminders, follow-ups and answers in parts cannot be recorded), #628 (a refused `classify-received` prints a raw validation dump).

## Next
Open issues, in no owner-set order.

Observatory (local-law capture, ADR-0010):
- Register the remaining municipalities and the fylkeskommuner — #431
- Nightly sweeps ending `degraded`; two sources fail every night without a verdict — #481
- HTTP client reset at TLS handshake by five municipalities that curl reaches — #508
- Most captured regulation pages hold no document text — #332 (offline report shipped in #464; issue open)
- Politeness budget is per source, not per server — #277
- Sequential nightly sweep cannot catch up inside its own SLA — #218

Corpus data quality:
- In-force date order (9 documents), 230 undated documents, footnote markers glued to words — #624 (from `docs/publication-plan.md` § Open questions)

Promotion of local regulations (ADR-0016):
- Front-matter-only change of a published version has no supported write path — #595
- A PDF's table of contents starts the body — #597
- `promote batch --write` records only the canonical page of a group — #589
- pypdf warns fontTools is missing; PDF extraction may be degraded — #573

MCP and hosted endpoint:
- `validate_citation` refuses natural short names (`folketrygdloven § 8-18`) — #585
- Release capability document recorded the hosted MCP as unavailable — #584
- `probe-token-file` exits 2 on an unreadable path instead of degrading — #470

LLHB benchmark:
- Runs for one model per provider (`docs/decisions.md` §16) — results in the repo so far cover one provider's model plus two Norwegian control runs; the remaining providers are planned (owner, 2026-10-17)

Developer tooling and CI:
- Dependency and tool policy in the fast harness (#323 Phase F) — #465
- Agentic CI defects — #600, #607, #608, #615, #621

## Later
- Migrate to `mcp` 2.x after the LLHB benchmark concludes — #70
- Evaluate Jev as an evidence reranker before deterministic retrieval — #358 (research)
- Phase 3 staleness benchmark: closed-form questions anchored on dated amendments — source: `docs/publication-plan.md` § Phase 3
- Phase 4 write-up and submission, target **2028** (moved from 2027, owner 2026-10-17) — source: `docs/publication-plan.md` § Phase 4
- Hosted-endpoint hardening still listed as outstanding — source: `docs/roadmap.md` Sprint 12 items 7 and 9:
  - log the auth-rejection reason, authenticated identity and `clientInfo` (never the token)
  - replace the Caddyfile `header_up Host` stopgap with FastMCP `allowed_hosts` (`deploy/digitalocean/Caddyfile:42-51` still describes the stopgap)
  - external availability monitoring of `/mcp`
- Grounded-research evidence-bundle tool (`research_law`) — source: `docs/roadmap.md` Sprint 12 item 4 ("evaluate")
- Option catalogue, not selected since 2026-05-18 (`docs/roadmap.md` § Options): structural AST (A1), cross-reference graph (A2), definitions index (A3), SQLite FTS5 + Norwegian stemmer (C2), `lovtidend-avd1` dataset (D-API-3), Sami-language texts (D-API-4), Høyesterett via domstol.no (D-DIRECT-1, parked pending a sourcing check), EUR-Lex text for CELEX ids (D-DIRECT-2), forarbeider (D-DIRECT-3), specialised tribunals (D-DIRECT-5), Docker image (E1), per-section Lovdata deep links (F1), manifest signing (F2). None has code on `main` (checked 2026-10-17).

## Done
Most recent first. PR numbers refer to this repository; work before the 2026-08-03 publication is cited by commit sha, because PR numbers from before that date belong to a different numbering.

Security and dependencies
- `mcp` floor raised to 1.30.0 for CVE-2026-59951 (#626) — #627

Local regulations
- `promote migrate` for extractor bumps, carrying an approval across a byte-identical rendering (`docs/decisions.md` §19) — #594, #601
- Promotion pipeline for local regulations (ADR-0016 S1–S11): identity layer, extractor, personal-data gate, renderer, `promote local`, withdraw, batch under a spot-check gate, backfill, evidence sidecar — #506, #513, #517, #563–#568, #575, #577, #591; code `src/lovspor/promotion/`
- MCP serves local regulations opt-in, with `get_observation_history`, an authority filter on `search_laws` and local counts in `corpus_status` — #533, #571, #580
- Observatory: capture candidates selected by path (#348) — #457, #504
- Observatory: correction records and `reattribute` (ADR-0015) — #441, #442
- Observatory: safe source-domain migration (#166) — #203
- Observatory: listing pages as a second discovery entry; a sitemap-less source is a refusal, not a silent zero (#151) — #154, #155, #182
- Observatory: a capped bootstrap lane is reported as capped (#172) — #174
- Observatory: per-sweep run record, 24h SLA, scheduled nightly with storage preflight and report-out (#167) — #170, #176, #177
- Observatory capture foundation (ADR-0010): model, storage boundary, append-only log, registry, polite capture, discovery, `capture-all` — #118, #128, #133, #134, #142, #143, #156; code `src/lovspor/observatory/`

Hosted MCP and site
- Privacy policy, terms of use, 30-day journald retention — #474, #475, #476
- Operator alert when a droplet unit fails (#478) — #482; retry of a failed corpus refresh — #390
- Hourly aggregate usage metrics (#479) — #485; load-test harness (#480) — #483
- Hosted OAuth re-enabled alongside opaque tokens (owner decision 2026-09-30) — #473
- Instance-wide ceiling and separate self-service limits, bounding total load and embedding spend (#251) — #252; code `src/lovspor/access.py`, `src/lovspor/quota.py`. Registration stays open: access is governed by these limits, not by approving who signs up (owner, 2026-10-17)
- Public site and corpus pages (ADR-0013/0014): generator, sitemaps, atomic release deploy, release envelope, `/connect/` and `/docs/` in two languages, landing page with LLHB results — #244, #249, #250, #257, #263, #265, #266, #268, #269, #336, #341, #586, #602
- Self-service OAuth via WorkOS AuthKit — `12326482`; Streamable HTTP transport (`lovspor mcp-http`) — `5a752a41`

Temporal layer
- `get_temporal_events` and attestation gate (ADR-0012) — #226, #227, #230, #439
- `recorded_at` transaction-time parameter on the retrieval tools (ADR-0011) — #222
- Canonical amendment events derived from amendment notes; not-in-force notices (ADR-0009) — #100, #112; code `src/lovspor/temporal.py`

Developer tooling and CI
- Mutation-gate budget: mutmut's setup gets its own allowance (#622) — #623

LLHB benchmark (`benchmarks/llhb/`, `docs/decisions.md` §16)
- Norwegian-model control runs (NorMistral, Borealis) on the frozen set — #180, #181, #183
- Confirmatory-ceremony tooling and host-level workload lock (#169) — #186, #189
- Layer-3 post-hoc artefact for the frozen Opus pair (#168) — #179
- v1 frozen dataset (250 cases), scorer, frozen Opus pair, stability runs, reporting layer — #49, #74, #78, #88, #93, #97, #150

Engine, corpus and distribution
- Engine relicensed MIT → AGPL-3.0 (`docs/decisions.md` §18) — #83 (`29178b64`)
- Embedding space identity and LSPE v2 sidecars (ADR-0005/0006) — #7, #8, #12, #19, #22, #24
- PyPI releases via Trusted Publishing: `0.4.0` #3, `0.5.0` #20, `0.5.1` #38, `0.6.0` #175
- Repository made public, open-infrastructure direction (`docs/decisions.md` §15; Phase 1 of `docs/publication-plan.md`) — completed 2026-08-03
- Renderer fixes to zero fabricated words corpus-wide (Phase 0 of `docs/publication-plan.md`); renderer now at v8 — `src/lovspor/rendering/markdown_renderer.py:63`, evidence in `docs/evidence/`

Before publication (sprints 0–10, `docs/decisions.md` §12)
- `diff_law_versions` — `61c3db04`; `get_law_at` + `list_law_versions` — `beafbbd0`
- `semantic_search` + `verify_quote` — `2bc5d8b1`; embeddings infrastructure — `d1697c51`
- EU cross-references (`eu_basis`) — `6b4f7dbf`
- `corpus_status` freshness signal — `3f7317bf`
- MCP server over stdio — `b4e49c06`
- Per-act history — `093c9345`, `f2893d08`
- Slug filenames, per-document commits, INDEX per dataset — `a37f2f96`; scheduled sync — `b6c83d16`
- Lovdata client, safe download and tar iteration — `44d53f16`, `e0cacaf4`, `ecb7052d`

## Unverified
- Phase 2 amendment graph as specified in `docs/publication-plan.md` (resolve bare references to ids, validate against the manifest, three-valued resolution, treaty amendments as a second instrument class) — `src/lovspor/temporal.py` derives amendment events with act refs (#112), but no document says the Phase 2 deliverables are complete.
- LLHB confirmatory replication on a second model, preregistered in `benchmarks/llhb/ANALYSIS-PLAN-fable5-v1.md` — the tooling is merged (#189), but no confirmatory result is in `benchmarks/llhb/results/`; whether the run happened is unknown.
- #234 (a failed `lovspor-fetch-corpus` run is silent) — retry shipped in #390 and unit-failure alerts in #482; the issue is still open, and its last comment (2026-09-30) asks for confirmation on the droplet.
- Hosted corpus runtime with operator-visible freshness (`docs/roadmap.md` Sprint 12 item 2) — a daily corpus fetch unit exists (`deploy/digitalocean/`); not marked done anywhere.
- Known open items in `docs/decisions.md` §14 (embedding-backfill crash recovery, `short_title` bracket stripping, legacy bulk-commit classification, whitespace-only change detection — the last one deliberately not fixed) — design notes with no issue attached.
- Public docs site (E2) and web UI (G2) — the static site from #249/#336 covers part of E2; whether that closes the option is not stated. G2 is listed as a strategic deferral.
- Superseded: the July 2026 hosted-product direction — superseded by the open-infrastructure decision (`docs/decisions.md` §1, §15, §17). Multi-jurisdiction (G1) stays a strategic deferral (`docs/roadmap.md` § Currently out of scope).
- Out of scope by law, not by choice: Lovdata's case-law database, editorial layer and commentary (§43 database right) — `docs/roadmap.md` § Currently out of scope.

## Sources
- `docs/roadmap.md`, `docs/decisions.md` (§1, §2, §12, §14–§19), `docs/publication-plan.md`
- `benchmarks/llhb/README.md`, `benchmarks/llhb/results/`
- `pyproject.toml`, `deploy/digitalocean/Caddyfile`, `src/lovspor/` (module listing and targeted greps)
- `git log` of `main` at e2da8d1b; issue and PR list of 2026-10-19
- `gh issue list -R bartoszkobylinski/lovspor --state all --limit 300`; `gh issue view` for #151, #166, #167, #168, #169, #172, #234, #251, #343, #349, #478, #479, #480, #610
- `gh pr list -R bartoszkobylinski/lovspor --state merged --limit 400`, `--state open`; `gh pr view 252`
