# MCP server — lovverk for AI assistants

`lovspor mcp` is a stdio MCP (Model Context Protocol) server that exposes the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) Norwegian-law corpus to AI assistants — Claude Desktop, Claude Code, or any other client that speaks MCP. The assistant gets sixteen read-only tools and uses them to answer real legal-research questions from the live corpus instead of stale training data.

> **Distribution (updated 2026-08-03).** The engine is MIT-licensed open infrastructure, public on GitHub, and **Lovspor is distributed on PyPI** as [`lovspor`](https://pypi.org/project/lovspor/) — publishing resumed at `0.4.0`, the earlier `0.2.0`–`0.3.0` releases having been withdrawn during a July 2026 pivot. The commands below use the on-demand `uvx lovspor …` form, which resolves the latest release from PyPI; to run unreleased changes, use `uv run --project /path/to/lovspor lovspor …` from a checkout instead — see [§ Running from a local checkout](#running-from-a-local-checkout). Local stdio is a first-class, fully supported path either way. A hosted MCP endpoint (live since 2026-07-18) is an optional operated access layer — see [§ Streamable HTTP transport](#streamable-http-transport).

Sprint 9 added a four-layer grounding-and-verification path for AI consumers: `semantic_search` finds candidates by meaning, `get_section` returns verbatim text plus validated `cross_references`, `verify_quote` confirms a verbatim quote actually appears in the cited section, and `validate_citation` is the off-ramp for ambiguous citations.

This document covers the full setup: prerequisites, configuration for two common clients, every tool with sample input and output, the typical discovery flow, troubleshooting, limitations, and legal attribution.

---

## At a glance

- **Transport:** stdio by default (`lovspor mcp`) — each user runs their own copy locally; no inbound network surface, no shared infrastructure, no auth needed. The sole outbound call is [`semantic_search`](#semantic_searchquery-dataset-limit-min_score) embedding your query via OpenAI — see its **Privacy** note. A Streamable HTTP transport (`lovspor mcp-http`) serves the hosted instance, live since 2026-07-18 — see [§ Streamable HTTP transport](#streamable-http-transport).
- **Data path:** the server reads a local clone of the `lovverk` Markdown corpus. The lovspor scheduled workflow keeps `lovverk` current; the user re-runs `lovspor fetch-corpus` (which fast-forwards the cache) to pick up updates.
- **Tools:** sixteen read-only, manifest-and-filesystem-only (one of them, `semantic_search`, additionally calls the OpenAI embeddings API at query time — see the tool's section below). Three of the sixteen (`get_law_at`, `list_law_versions`, `diff_law_versions`) are time-machine tools that read past versions of acts directly from the corpus's git history. They answer corpus history — what the corpus contained at the end of a UTC date — never which provisions were legally in force on that date (ADR-0002; see the temporal contract under `get_law_at`).
- **Engine sync:** untouched. MCP is a *consumer* of `lovverk`; the producer is the `.github/workflows/sync.yml` cron in `lovspor`. They're decoupled by design ([`docs/decisions.md` §1](decisions.md)).

---

## Streamable HTTP transport

`lovspor mcp-http` serves the same sixteen read-only tools to remote clients over the MCP Streamable HTTP transport. It is what the hosted instance runs:

> ✅ **Live since 2026-07-18 at `https://lovspor.bartoszkobylinski.com/mcp`.** TLS is terminated by Caddy (automatic Let's Encrypt) on a dedicated DigitalOcean droplet provisioned from `deploy/digitalocean/`. **Every request requires authentication** — anonymous and invalid-token calls return 401. Both credential modes are active there (verified against production 2026-08-02): operator-issued opaque bearer tokens (`lovspor tokens issue`), and WorkOS AuthKit OAuth — the AuthKit pair is configured, so `/.well-known/oauth-protected-resource/mcp` serves RFC 9728 discovery. Access provisioning is operator-managed; whether AuthKit accepts new self-service sign-ups at any given time is an operator-side WorkOS setting, not a commitment of these docs. Per-credential quotas and rate limiting are enforced in both modes.
>
> ⚠️ **The app itself still speaks plaintext HTTP and binds `127.0.0.1` by design** — lovspor never terminates TLS, a proxy in front of it does. If you run `mcp-http` yourself, keep it on localhost behind a TLS-terminating proxy (see `deploy/digitalocean/README.md`): a bearer token on an exposed port travels in the clear and can be read and replayed.

```bash
uvx lovspor mcp-http --host 127.0.0.1 --port 8000
```

Defaults are `--host 127.0.0.1 --port 8000`; `--corpus-path` behaves exactly as it does for `lovspor mcp`. The MCP endpoint is served at `/mcp`.

### Authentication: two modes

lovspor is always an OAuth **resource server** — it verifies tokens, it never issues them. Which kind of token it accepts depends on whether the AuthKit pair is configured:

| | **Opaque tokens** (default) | **Hosted OAuth** (`--authkit-domain` + `--public-url`) |
|---|---|---|
| Configure | nothing — this is the default | both flags, or both env vars |
| Who it serves | developer clients that have a paste-a-token field (Claude Code, Cursor) | chat-app connectors that require OAuth 2.1 and have no such field (ChatGPT, Claude.ai) — **plus** everything the default mode serves |
| Credential | `lovspor tokens issue --label ...` → an `lsp_…` token | the user logs in through WorkOS; no manual step |
| Discovery | none — no `.well-known`, no `/authorize`, no `/token` | `GET /.well-known/oauth-protected-resource/mcp` advertises WorkOS as the authorization server (RFC 9728) |
| Quota bucket | per-credential `Limits` from the store | one shared default bucket for self-service users, keyed `workos:<sub>` |

Enabling hosted OAuth:

```bash
lovspor mcp-http \
  --authkit-domain https://your-project.authkit.app \
  --public-url https://lovspor.example.com/mcp
# or, equivalently, as env vars (what the systemd unit uses):
#   LOVSPOR_AUTHKIT_DOMAIN=https://your-project.authkit.app
#   LOVSPOR_PUBLIC_URL=https://lovspor.example.com/mcp
```

`--public-url` is **lovspor's own public `/mcp` URL**, not WorkOS's — it is the RFC 8707 resource identifier the access token is bound to, so a token minted for a different resource cannot open this server. It must match the URL clients actually connect to, including scheme and path.

> ⚠️ **The two are all-or-nothing: giving one a value without the other refuses to start** (`ConfigError`, before the port is bound). Half a config would otherwise boot straight past the auth boundary — discovery off, WorkOS JWTs never verified — so a typo'd deploy unit would look healthy while every self-service connector failed to log in. A service that won't start is the loud failure; that one is the quiet one.
>
> "Set" means *has a non-empty value*. `LOVSPOR_AUTHKIT_DOMAIN=` (present but empty) counts as unset and selects opaque-token mode, matching how the CLI treats an empty env var — so exporting an empty placeholder for one of them is safe, but it also won't warn you that OAuth is off.

Both token types are accepted at once in hosted mode: a bearer with two dots is verified as a WorkOS RS256 JWT (signature against the AuthKit JWKS, issuer, audience, expiry), anything else falls through to the credential store. Issued `lsp_…` tokens keep working unchanged when you turn OAuth on.

**How it differs from stdio** — both differences exist because an HTTP server shares one process across many clients, while stdio serves exactly one:

- **Tool bodies run on worker threads.** The MCP SDK calls a synchronous tool handler inline on its single event-loop thread, so one slow call (a `search_body` scan, a `semantic_search` embedding round-trip, a `git` subprocess for the time-machine tools) would stall *every* other client. Over HTTP each tool body is offloaded to a thread, so calls make progress concurrently.
- **Indices are warmed at startup.** The corpus indices (~270 MB body index measured 2026-07-29 on 5,913 docs, ~200 MB embeddings when an OpenAI key is configured) are built before the server accepts traffic, so no request pays a multi-second cold build while holding the reader's cache lock. The cost is a slower start and a higher memory floor. stdio deliberately stays lazy: a client that only queries metadata should never pay for an index it may not touch.

Two probes are exposed for operators, both unauthenticated and deliberately cheap (readiness stats `manifest.json` rather than parsing it or shelling out to git, so a probe loop cannot stall the server):

| Endpoint | Meaning |
|---|---|
| `GET /healthz` | process is up — `{"status": "ok"}` |
| `GET /readyz` | corpus is present — `{"status": "ready"}`, or `503` `{"status": "unavailable"}` |

Richer freshness (corpus age, staleness, HEAD commit) stays behind the [`corpus_status`](#corpus_status) tool rather than the probes.

---

## Prerequisites

1. **The `lovverk` corpus.** One command fetches it:

   ```bash
   uvx lovspor fetch-corpus
   ```

   This shallow-clones the corpus into `~/.cache/lovverk` (honouring
   `$XDG_CACHE_HOME`) and fast-forwards it on every re-run. `lovspor mcp`
   looks there by default, so you can omit `--corpus-path` entirely — the
   whole flow is `lovspor fetch-corpus` then `lovspor mcp`. Re-run
   `lovspor fetch-corpus` any time to refresh (the engine re-syncs daily at
   04:00 UTC).

   **Or clone it yourself** to a custom path you then pass via `--corpus-path`:

   ```bash
   git clone https://github.com/bartoszkobylinski/lovverk.git ~/lovverk
   ```

2. **`uv` installed locally** — see [astral.sh/uv](https://docs.astral.sh/uv/). The MCP client invokes the server on demand via `uvx lovspor`, which resolves the `lovspor` package from PyPI — no clone needed. (Plain `pip install lovspor` works too; then the command is just `lovspor`.)

   Contributors, and anyone running unreleased changes, use a checkout instead — see [§ Running from a local checkout](#running-from-a-local-checkout) below.

3. **Optional: `OPENAI_API_KEY`** in the environment if you want the `semantic_search` tool. Missing key disables only that one tool — the other fifteen keep working without it. See [`semantic_search`](#semantic_searchquery-dataset-limit-min_score) below for the trade-off and cost.

---

## Quickstart — Claude Desktop

Add the following to your Claude Desktop config (path varies by OS — see [Claude Desktop docs](https://modelcontextprotocol.io/quickstart/user) for the exact location):

```jsonc
{
  "mcpServers": {
    "lovverk": {
      "command": "uvx",
      "args": ["lovspor", "mcp"]
    }
  }
}
```

This uses the default `fetch-corpus` cache (`~/.cache/lovverk`). To point at a custom clone instead, add `"--corpus-path", "/absolute/path/to/lovverk"` to `args`. Restart Claude Desktop. The server appears in the MCP indicator at the bottom of a new conversation.

Try it: ask Claude *"Use the lovverk MCP tools to tell me when Skatteloven was last updated."*

## Quickstart — Claude Code

Same config shape, registered via the Claude Code CLI:

```bash
claude mcp add lovverk -- uvx lovspor mcp
```

(Add `--corpus-path <path>` after `mcp` only to override the default cache.) Or edit `~/.claude.json` directly with the JSON above. Then `claude` in a fresh session — `/mcp` lists the registered servers.

---

## Tools

All sixteen are read-only. None mutate the corpus or trigger a sync. Fifteen are pure local (manifest + filesystem + git on the local clone); `semantic_search` additionally calls the OpenAI embeddings API at query time to embed the user's query — see its section for details.

### `get_law(slug)`

Return the full Markdown of a Norwegian law or regulation.

- **`slug`** — the human-readable kortform identifier, e.g. `skatteloven`, `opplæringslova`, `trafikkforskriften`. Use `search_laws` or `list_recent_changes` to discover valid slugs.

**Sample call:** `get_law("skatteloven-sktl")`

**Sample output** (truncated):

```markdown
---
id: nl-19990326-014
slug: skatteloven-sktl
type: lov
title: Lov om skatt av formue og inntekt (skatteloven)
short_title: Skatteloven (sktl)
ministry: ["Finansdepartementet"]
date_in_force: "1999-03-26"
last_updated: "2024-12-20"
source_provider: Lovdata
source_license: NLOD 2.0
...
---

# Lov om skatt av formue og inntekt (skatteloven)

## Kapittel 1. Alminnelige bestemmelser

### § 1-1. Lovens virkeområde
...
```

The frontmatter carries the law's metadata and provenance; the body is the legal text rendered as Markdown.

### `get_section(slug, section_id)`

Return a single ``§`` section of an act — the surgical alternative to `get_law` when the user wants just one paragraph (e.g. *"What does § 5-12 of Skatteloven say?"*). Cheaper for the AI's context window than fetching the whole law.

- **`slug`** — the act's slug (same as for `get_law`).
- **`section_id`** — the bare numeric / hyphenated identifier. Examples: `"5-12"`, `"1"`, `"5-12a"`. Norwegian acts use `§ N` for single-chapter acts and `§ N-M` (chapter N, section M) for multi-chapter acts; both work. The obvious variants — leading `§` (`"§ 5-12"`), trailing dot (`"5-12."`), surrounding whitespace — are normalized to the bare id rather than erroring; the response always carries the canonical bare form.

**Sample call:** `get_section(slug="skatteloven-sktl", section_id="5-12")`

**Sample output:**

```json
{
  "slug": "skatteloven-sktl",
  "section_id": "5-12",
  "heading": "§ 5-12. Boligsparing for ungdom",
  "parent_chapter": "Kapittel 5. Alminnelig inntekt og fradragene",
  "body": "(1) Skattefradraget gis for sparing til bolig...\n\n(2) Fradraget reduseres ved utbetaling...",
  "cross_references": [
    {
      "text": "§ 9-3",
      "target_slug": "skatteloven-sktl",
      "target_section_id": "9-3",
      "valid": true,
      "reason": null
    }
  ]
}
```

The `cross_references` field (Sprint 9 PR-B3.5) lists every `§ N-M` reference detected in the body, deduplicated by target, with each entry already validated against the manifest. `target_slug` defaults to the current act when no other slug appears within ~80 chars of the match (a same-act ref); a different slug means the resolver picked it up as a cross-act ref. `valid` is true only when the target section actually exists in the target act, and `reason` carries a short explanation when invalid.

Use this list to decide whether a referenced section is safe to quote without a follow-up `validate_citation` call. The field is an empty list when the body has no `§` patterns.

Limitations: descriptive name references (*"i lov om X"* without a canonical slug) silently fall back to same-act and may false-positive validate. Chapter references (`kapittel 4`) and paragraph qualifiers (`første ledd`) are not extracted. `validate_citation` remains the off-ramp for ambiguous cases.

If the section is unknown the error message lists the act's available section ids in natural order (so `5-2` < `5-10`, not lexicographic) — the AI can recover without an extra `get_law` call.

### `list_sections(slug)`

List an act's table of contents: every `§` section id and heading, in document order. The navigation companion to `get_section` — when the AI doesn't know the exact section id, the TOC answers *"which section of Skatteloven covers X?"* without pulling the whole act through `get_law` (hundreds of KB for the big codes).

- **`slug`** — the act's slug (same as for `get_law`).

**Sample call:** `list_sections(slug="skatteloven-sktl")`

**Sample output** (truncated):

```json
[
  {
    "section_id": "1-1",
    "heading": "§ 1-1. Lovens virkeområde",
    "parent_chapter": "Kapittel 1. Alminnelige bestemmelser"
  },
  {
    "section_id": "5-12",
    "heading": "§ 5-12. Boligsparing for ungdom",
    "parent_chapter": "Kapittel 5. Alminnelig inntekt og fradragene"
  }
]
```

`section_id` feeds straight into `get_section`. Empty list when the act has no `§` sections. Unknown slug raises with near-miss suggestions and a pointer to `search_laws`.

### `get_law_history(slug)`

Return the per-act change history as structured JSON. Each event has `date`, `commit`, `type` (`added` / `updated` / `renamed` / `removed`), commit `subject`, optional `from_path` / `to_path` for renames, and optional `lines_added` / `lines_removed`. Newest first.

**Sample call:** `get_law_history("skatteloven-sktl")`

**Sample output:**

```json
{
  "schema_version": 1,
  "slug": "skatteloven-sktl",
  "doc_id": "nl-19990326-014",
  "events": [
    {
      "date": "2026-04-27",
      "commit": "0c40d0b",
      "type": "renamed",
      "subject": "migration: rename 4522 documents to slug-based filenames",
      "from_path": "lover/nl-19990326-014.md",
      "to_path": "lover/skatteloven-sktl.md"
    },
    {
      "date": "2026-04-26",
      "commit": "57c3052",
      "type": "added",
      "subject": "sync: 4522 new, 0 changed, 0 removed",
      "lines_added": 1284
    }
  ]
}
```

### `get_law_at(slug, target_date)`

Time-machine companion to `get_law`: returns the full Markdown of a law as the corpus recorded it at the end of a given UTC calendar date. Use it to anchor an answer to a specific past corpus state — e.g. *"what did this corpus serve for Skatteloven on 2026-05-01?"*, or reproducing what a consumer saw before a recent update.

**Temporal contract (ADR-0002):** the result represents the version available in the Lovspor corpus at the end of the specified UTC date. It does not establish which legal provisions were legally in force on that date. Corpus history starts when Lovspor first recorded the document; earlier states are not retrievable (asking for a date before an act entered the corpus is an error, not an approximation). The `date_in_force` frontmatter field is descriptive metadata and is not used to reconstruct validity history.

- **`slug`** — the act's *current* slug. Even if the kortform was different in the past, you pass today's slug — the corpus's git history is rename-aware and traces predecessors automatically (Sprint-4 slug migration, any subsequent Lovdata kortform change).
- **`target_date`** — ISO date `YYYY-MM-DD`. End-of-day UTC semantics: `"2026-04-15"` returns the version current at 23:59:59 UTC on April 15. Future dates are refused with a `ValueError` because they are almost always typos; use `get_law(slug)` for the current version.

Output mirrors `get_law`: YAML frontmatter (as it was at that revision — `retrieved_at`, `xml_hash`, `eu_basis`-or-absent reflect that point in time, not today's manifest) followed by the legal text in Markdown.

**Sample call:** `get_law_at("skatteloven-sktl", "2026-04-26")`

**Sample output** (truncated; pre-Sprint-4 era — note the absence of the `slug:` field that the Sprint 4 migration added):

```markdown
---
id: "nl-19990326-014"
type: "lov"
ref_id: "lov/1999-03-26-14"
title: "Lov om skatt av formue og inntekt (skatteloven)"
short_title: "Skatteloven – sktl"
language: "nb"
...
```

Raises if the slug is unknown or if the act first appeared in the corpus *after* `target_date` — the error message points to `get_law_history` so the AI can find the earliest available date.

### `list_law_versions(slug)`

Companion to `get_law_at`: lists the dates on which the act had distinct content versions. Each entry is a moment when the act's content actually changed — pure filename renames are filtered out because they don't yield different `get_law_at` output.

Returns oldest-first so the AI can reason about the timeline naturally (initial appearance → updates → today). Each entry has `date` (ISO `YYYY-MM-DD` — feed straight into `get_law_at`), `commit` (short SHA), `type` (`added` | `updated`), and `lines_added` / `lines_removed` (may be `null` for legacy bulk-mode commits).

Listed dates are the UTC dates on which the Lovspor corpus recorded a content change — not entry-into-force dates. They do not establish when a provision became legally applicable (ADR-0002).

**Sample call:** `list_law_versions("skatteloven-sktl")`

**Sample output:**

```json
[
  {
    "date": "2026-04-26",
    "commit": "57c3052",
    "type": "added",
    "lines_added": 3867,
    "lines_removed": 0
  }
]
```

Raises if the slug is unknown or if the corpus pre-dates the Sprint 5 history layer (no `history/<slug>.json`).

### `diff_law_versions(slug, date_a, date_b)`

What changed in an act between two dates, section by section. Builds on `get_law_at` + `list_law_versions`: rather than fetching one historical version, it resolves both dates to their commits and compares them. `date_a` is the "before" side (a later `date_a` yields a reverse diff); future dates are refused.

The comparison runs on rendered Markdown with the YAML frontmatter stripped, so metadata-only churn (`retrieved_at`, `xml_hash`) never surfaces as a change. Each added / removed / changed `§` section carries a stdlib unified diff of its heading and body; sections identical on both dates are omitted. `resolved_commit_a` / `resolved_commit_b` report which commits the two dates actually mapped to — a date rarely coincides with the day the law changed.

The temporal contract of `get_law_at` (ADR-0002) applies to both sides: each resolves to the version available in the Lovspor corpus at the end of the given UTC date, and the diff shows how the corpus record changed — not when the law became legally applicable. The response restates this machine-readably: `temporal_basis` is always `"corpus_history"`, `cutoff_timezone` is always `"UTC"`, and `legal_validity_determined` is always `false`.

**Sample call:** `diff_law_versions("skatteloven-sktl", "2020-01-01", "2024-01-01")`

**Sample output:**

```json
{
  "slug": "skatteloven-sktl",
  "date_a": "2020-01-01",
  "date_b": "2024-01-01",
  "resolved_commit_a": "57c3052e1b…",
  "resolved_commit_b": "9a1f3c8d04…",
  "temporal_basis": "corpus_history",
  "cutoff_timezone": "UTC",
  "legal_validity_determined": false,
  "summary": { "sections_added": 1, "sections_removed": 0, "sections_changed": 1 },
  "sections": [
    {
      "section_id": "5-12",
      "heading": "§ 5-12. Naturalytelser",
      "change_type": "changed",
      "unified_diff": "--- before\n+++ after\n@@ -1,2 +1,2 @@\n § 5-12. Naturalytelser\n-Gammel ordlyd.\n+Ny ordlyd."
    }
  ]
}
```

Raises if the slug is unknown or if either date predates the act's first appearance in the corpus (the message points to `get_law_history`).

### `list_recent_changes(dataset?, since?, limit?)`

List current laws ordered by most recent change first.

- **`dataset`** *(optional)* — `lover` or `forskrifter` (also accepts the full Lovdata keys `gjeldende-lover` / `gjeldende-sentrale-forskrifter`).
- **`since`** *(optional)* — ISO date `YYYY-MM-DD`. Only includes laws whose last change is on or after this date.
- **`limit`** *(default 20)* — max results. Must be non-negative.

**Sample call:** `list_recent_changes(dataset="lover", since="2026-04-01", limit=3)`

**Sample output:**

```json
[
  {
    "slug": "skatteloven-sktl",
    "doc_id": "nl-19990326-014",
    "title": "Lov om skatt av formue og inntekt (skatteloven)",
    "dataset": "lover",
    "last_changed": "2026-04-27",
    "total_changes": 2
  },
  {
    "slug": "opplaeringslova",
    "doc_id": "nl-19980717-061",
    "title": "Lov om grunnskolen og den vidaregåande opplæringa",
    "dataset": "lover",
    "last_changed": "2026-04-27",
    "total_changes": 2
  },
  ...
]
```

### `search_body(query, dataset?, limit?)`

Search the **full Markdown body** of every current law for a substring (case-insensitive). Complement to `search_laws`: that one matches only manifest metadata (`slug` + `title`); `search_body` scans the actual legal text. Use it when the user asks about a topic that may not appear in any law's title — e.g. *"kryptovaluta"*, *"boligkjøpsmodeller"*, *"kunstig intelligens"*.

- **`query`** — substring to match. Empty / whitespace-only queries return `[]`.
- **`dataset`** *(optional)* — `lover` or `forskrifter` (or the full Lovdata key) to restrict the scan.
- **`limit`** *(default 20)* — max results. Must be non-negative.

**Sample call:** `search_body(query="kryptovaluta", dataset="lover", limit=3)`

**Sample output:**

```json
[
  {
    "slug": "verdipapirhandelloven-vphl",
    "doc_id": "nl-20070629-075",
    "title": "Lov om verdipapirhandel",
    "dataset": "lover",
    "match_count": 4,
    "snippet": "...for kryptovaluta og andre virtuelle eiendeler er regulert i denne paragrafen..."
  }
]
```

Sorted by `match_count` descending, then by `slug` for stable ordering. The snippet is a ~100-char window around the **first** match (whitespace collapsed, leading/trailing `...` if not at the document boundaries).

**Footnote markers do not break the match.** The corpus reads `fra det tidspunktet[^1] Kongen bestemmer`; searching for that phrase without the marker still finds it. The snippet comes back as the corpus stores it, marker included. Above 2,000 characters the query is matched literally instead, so a marker inside a pasted-paragraph query does break it — that cap bounds the regex compile cost a caller can drive.

**Performance:** the body index is loaded lazily on the first call and stays resident. Measured 2026-07-29 on the renderer-5 corpus (5,913 docs, 113,457,896 characters): **~270 MB peak RSS** — four runs across two machines landed at 247, 271, 278 and 280 MB, so size a droplet from the top of that range, not the bottom. The body strings alone are ~209 MB (`sum(getsizeof)`) plus ~0.8 MB of dict and keys; the rest is the transient load-time peak, which is the part that actually decides whether a small droplet survives the warm-up. Cold load 1.3–1.9 s off a warm page cache. Every call after that is a full scan of that text — ~0.4 s, or ~0.5–0.6 s once the marker-tolerant pass runs over the 18.3% of documents that carry a footnote marker. Server startup stays fast for clients that only query metadata.

### `semantic_search(query, dataset?, limit?, min_score?)`

Top-K cosine semantic search over per-section embeddings. Use when the user's question uses different vocabulary than the law text — e.g. *"renter rights when the landlord doesn't fix things"* finds husleieloven sections about *manglende vedlikehold* even though the user said "rights" and "fix" rather than the Norwegian legal terms. Complement to `search_body` (substring) and `search_laws` (title/slug).

**Important — score is similarity, not relevance.** A high score means the section is *about a similar topic*; it does not prove the section answers the user's question. Always `get_section` the top hits and read the actual text before quoting. If you quote anything verbatim, run `verify_quote` as the final safety check. The recommended pattern is:

1. `semantic_search(query)` → top candidates
2. `get_section(slug, section_id)` for each top hit → read actual text + see `cross_references`
3. `verify_quote(slug, section_id, quote)` if you quote anything verbatim

- **`query`** — natural-language query string. Empty / whitespace-only queries return empty results.
- **`dataset`** *(optional)* — `lover` or `forskrifter` to filter.
- **`limit`** *(default 20)* — max results. Must be non-negative.
- **`min_score`** *(default 0.25)* — similarity floor; hits below it are dropped. Pass `0.0` to see every candidate.

**Sample call:** `semantic_search(query="renter rights when landlord refuses repairs", limit=3)`

**Sample output:**

```json
{
  "results": [
    {
      "slug": "husleieloven",
      "section_id": "5-3",
      "score": 0.71,
      "title": "Lov om husleieavtaler",
      "dataset": "lover",
      "citation_hint": "§ 5-3 husleieloven",
      "heading": "§ 5-3. Utleierens vedlikeholdsplikt",
      "snippet": "Utleieren plikter i leietiden å holde utleid husrom og eiendommen for øvrig i den stand som...",
      "last_changed": "2026-03-14"
    },
    {
      "slug": "husleieloven",
      "section_id": "5-7",
      "score": 0.62,
      "title": "Lov om husleieavtaler",
      "dataset": "lover",
      "citation_hint": "§ 5-7 husleieloven",
      "heading": "§ 5-7. Utbedring av mangel",
      "snippet": "Leieren kan kreve at utleieren retter en mangel dersom dette kan skje uten urimelig kostnad...",
      "last_changed": "2026-03-14"
    }
  ],
  "notice": null
}
```

Score is cosine similarity in `[-1, 1]`; useful matches are usually `> 0.4`. `citation_hint` is a paste-ready `§ <id> <slug>` string for quoting next to a claim. `heading` and `snippet` are the section's **actual corpus text** (first ~200 chars of the body), so every hit is self-grounding; `last_changed` is the act's last content change, for currency caveats. Null `heading`/`snippet` mean the embedding `.bin` references a section that no longer exists in the rendered Markdown (corpus drift) — verify such hits via `get_section` before trusting them.

**Sample output (no strong match):**

```json
{
  "results": [],
  "notice": "no sections scored >= 0.25 for this query (best candidate scored 0.22). The corpus has no strong match — do NOT cite a law from memory. Tell the user no strong match was found, or retry with different wording, use search_body for exact keywords, or lower min_score."
}
```

When `results` is empty the `notice` says why — the AI is expected to report "no strong match" instead of substituting training-data memory.

**Requires** an embedding credential — `OPENAI_API_KEY` — in the environment when the MCP server starts. The adapter (OpenAI `text-embedding-3-large`, 3072-dim, the default and currently only supported provider) is built eagerly at startup, so a malformed key fails fast rather than on the first tool call. A missing key, or a misconfigured provider, disables only this one tool with a clear runtime error naming the cause; the other fifteen keep working without any embedding configuration at all.

The provider is selectable via `LOVSPOR_EMBEDDING_*` (see [`docs/embeddings.md`](embeddings.md)). `semantic_search` only searches documents whose recorded **embedding space** matches the configured one: a document embedded by a different model, or one written before the space was recorded, is excluded rather than compared, because comparing vectors across two spaces returns plausible-looking nonsense instead of an error. Exclusions are reported — partial coverage comes back in the `notice` field, and a corpus with no usable document fails with an explanation. Documents predating the stamp stay excluded until a separate migration annotates them. See [`docs/embeddings.md`](embeddings.md) for the identity rules, the binary corpus format, and the model choice rationale.

**Privacy:** unlike the other fifteen tools, `semantic_search` is **not fully local** — the query text you pass is sent to the OpenAI embeddings API to be embedded (the corpus vectors were computed once at sync time; only your live query leaves the machine, per call). The Norwegian-law corpus is public, but your *question* is not — avoid pasting confidential or client-identifying text into a `semantic_search` query. Every other tool (`get_law`, `get_section`, `search_body`, …) is filesystem-and-git only and never leaves your machine.

**Performance:** the embedding index is loaded lazily on the first call (~5-10 s for the production corpus, ~200 MB resident at 3072-dim int8). Each query embeds via OpenAI (~100-300 ms round-trip) and runs a vectorized brute-force cosine scan (well under 100 ms). Per-call OpenAI cost is fractions of a cent.

If the corpus has no `.bin` files (early bootstrap state) or every `.bin` is from an older model with a different dim (post-migration state), `semantic_search` raises with a remediation message — different errors for different states so the operator sees what to do.

### `validate_citation(citation)`

Verify that a Norwegian-law citation string actually resolves in the corpus. **Citation-grounding guard** — call this before quoting a citation in a final answer to confirm both the act and the section exist.

- **`citation`** — a free-form citation string. The parser is permissive about order: `"§ 5-12 skatteloven-sktl"`, `"skatteloven-sktl § 5-12"`, `"§ 5-12 i skatteloven-sktl"`, `"§5-12 skatteloven-sktl"` all work.

**Sample call:** `validate_citation(citation="§ 5-12 skatteloven-sktl")`

**Sample output (valid):**

```json
{
  "valid": true,
  "slug": "skatteloven-sktl",
  "section_id": "5-12",
  "heading": "§ 5-12. Boligsparing for ungdom",
  "reason": null
}
```

**Sample output (invalid — slug not in canonical form):**

```json
{
  "valid": false,
  "slug": null,
  "section_id": "5-12",
  "heading": null,
  "reason": "ambiguous citation: § 5-12 found but no act identifier; many acts have a section by that id"
}
```

The `reason` field is human-readable and the AI can quote it verbatim to explain to the user why the citation couldn't be confirmed. Slug match is **strict** — `"skatteloven"` does not fuzzy-match production slug `"skatteloven-sktl"`. When the citation contains a near-miss token, the `reason` appends an advisory `did you mean skatteloven-sktl?` hint (the same hint appears in `get_law`/`get_section` unknown-slug errors), but validation itself never fuzzy-matches: AI consumers should confirm via `search_laws` and re-validate with the canonical slug.

### `verify_quote(slug, section_id, quote, occurrence=None)`

Anti-hallucination guard for verbatim citations. Before answering with text like *"§ 5-12 of Skatteloven says: 'Skattefradraget gis for...'"* call this with the verbatim quote you intend to attribute to that section. Returns `{verified, slug, section_id, reason}`.

- **`slug`** — the act's slug.
- **`section_id`** — the bare numeric id (same form as for `get_section`).
- **`quote`** — the verbatim string to verify against the section body.
- **`occurrence`** — optional, one-based, same convention as `get_section`. Only needed for the handful of acts where one id names more than one `§` (an appendix restarting its numbering, or a genuine upstream repeat) — the same case `semantic_search` flags with `ambiguous_section: true`. A duplicate id **without** an occurrence returns `verified: false` with every candidate occurrence listed in `reason` — re-call with `occurrence=N`. The quote is then checked against that occurrence's text **only**: a quote that actually lives in the other duplicate returns `verified: false`. This is deliberate — the guard never guesses an occurrence and never searches across the candidates, because either would convert ambiguity back into the hallucination it exists to catch. An out-of-range occurrence returns `verified: false` with the valid range.

**Ambiguity recovery flow:** `semantic_search` hit with `ambiguous_section: true` → `verify_quote(slug, section_id, quote)` → `verified: false` with the candidate list in `reason` → pick the occurrence (or read them via `get_section(slug, section_id, occurrence=N)`) → `verify_quote(slug, section_id, quote, occurrence=N)` → deterministic verdict.

Match is **case-insensitive, whitespace-tolerant, and typography-tolerant**: curly vs straight quotes, en/em dash vs hyphen, and soft hyphens are folded before matching (Norwegian legal text is sentence case but AIs sometimes capitalize for emphasis; chat clients rewrite quotes and dashes in transit — an honest quote must not fail over typography). Beyond that, punctuation and accents are NOT normalized — `§` is not the same as `$`, and `§ 5-12` is not the same as `§ 512`.

**Sample call:** `verify_quote(slug="skatteloven-sktl", section_id="5-12", quote="Skattefradraget gis for sparing til bolig")`

**Sample output (verified):**

```json
{
  "verified": true,
  "slug": "skatteloven-sktl",
  "section_id": "5-12",
  "reason": null
}
```

**Sample output (not verified — quote not in section):**

```json
{
  "verified": false,
  "slug": "skatteloven-sktl",
  "section_id": "5-12",
  "reason": "quote not found in § 5-12 of 'skatteloven-sktl' after case, whitespace and typographic-punctuation normalization. The quote may be from a different section, paraphrased rather than verbatim, or hallucinated. Call get_section('skatteloven-sktl', '5-12') to read the actual text."
}
```

Catches the most common citation hallucination: AI quotes words that are NOT in the section it cites (often pulled from a different section, paraphrased from memory, or invented). Does NOT catch faithful paraphrases — for those fall back to `get_section` and quote the original Norwegian.

Empty quote returns `verified=false` with a clear reason. Unknown slug or section returns `verified=false` with the `get_section` error message in `reason` (which already lists available sections).

### `get_eu_basis(slug)`

Return the EU / EEA legal basis of a Norwegian act — the list of CELEX identifiers (EU document ids) the act implements. Sourced from Lovdata's `<dd class="eeaReferences">` block at extraction time, normalized to uppercase, deduplicated in source order.

- **`slug`** — the act's kortform (same as for `get_law`).

**Sample call:** `get_eu_basis(slug="personopplysningsloven")`

**Sample output:**

```json
{
  "slug": "personopplysningsloven",
  "doc_id": "lov-2018-06-15-38",
  "title": "Lov om behandling av personopplysninger",
  "dataset": "lover",
  "eu_basis": ["32016R0679"]
}
```

CELEX format: `3<year><type-letter><number>`. Type letter `R` for regulation, `L` for directive, `D` for decision, etc. Examples:

- `32016R0679` — Regulation 2016/679 (GDPR)
- `32014L0090` — Directive 2014/90/EU
- `31993L0013` — Directive 93/13/EEC

`eu_basis` is `[]` (empty list) for acts with no EEA references, or only an `EØS-avtalen` annex link without specific directives / regulations. The tool raises `corpus predates Sprint 8 PR-D` if the manifest record carries `eu_basis: null` (legacy schema) — in that case suggest the user run the `refresh_command` from `corpus_status` to refresh.

### `search_eu_implementations(eu_doc_id)`

Reverse-lookup: which Norwegian acts implement a given EU document. Complement to `get_eu_basis` (Norwegian act → EU basis); this one goes EU document → Norwegian acts.

- **`eu_doc_id`** — a CELEX identifier. Case-insensitive (`"32016R0679"` and `"32016r0679"` are equivalent; lovspor stores them uppercase).

**Sample call:** `search_eu_implementations(eu_doc_id="32016R0679")`

**Sample output:**

```json
[
  {
    "slug": "personopplysningsloven",
    "doc_id": "lov-2018-06-15-38",
    "title": "Lov om behandling av personopplysninger",
    "dataset": "lover"
  }
]
```

Sorted by slug for stable output. Empty list when no current act references the given CELEX. Tombstones (removed acts) are excluded — they no longer "implement" anything for current-state queries. Records with `eu_basis: null` (legacy schema, awaiting Sprint 8 backfill) are silently skipped — the result is a partial answer, not an error; the migration will populate them on the next sync.

Use the returned `slug` values with `get_law` or `get_section` to fetch the implementing text.

### `corpus_status()`

Return current state of the local corpus plus freshness metadata. Call this proactively when other tools return unexpectedly empty results — a stale corpus (user forgot to `git pull`) is indistinguishable from a missing law from the AI's perspective.

**No parameters.**

**Sample call:** `corpus_status()`

**Sample output (fresh corpus):**

```json
{
  "manifest_generated_at": "2026-04-27T05:00:00+00:00",
  "manifest_age_days": 0,
  "is_stale": false,
  "schema_compatible": true,
  "total_current_documents": 5910,
  "head_commit": "0c40d0b",
  "head_commit_date": "2026-04-27",
  "head_commit_subject": "migration: generate history for 4522 documents",
  "refresh_command": "git -C /Users/you/lovverk pull",
  "notice": "Corpus is current (0 days old)."
}
```

**Sample output (stale corpus):**

```json
{
  "manifest_generated_at": "2026-04-13T05:00:00+00:00",
  "manifest_age_days": 14,
  "is_stale": true,
  "schema_compatible": true,
  "total_current_documents": 5908,
  "head_commit": "abc1234",
  "head_commit_date": "2026-04-13",
  "head_commit_subject": "sync: 0 new, 5 changed, 0 removed",
  "refresh_command": "git -C /Users/you/lovverk pull",
  "notice": "Corpus manifest is 14 days old. Run: git -C /Users/you/lovverk pull to refresh."
}
```

**Sample output (pre-Sprint-4 schema):**

```json
{
  "manifest_generated_at": "2026-04-26T19:05:00+00:00",
  "manifest_age_days": 1,
  "is_stale": true,
  "schema_compatible": false,
  "total_current_documents": 5910,
  "head_commit": "57c3052",
  "head_commit_date": "2026-04-26",
  "head_commit_subject": "sync: 4522 new, 0 changed, 0 removed",
  "refresh_command": "git -C /Users/you/lovverk pull",
  "notice": "Corpus manifest is on the pre-Sprint-4 schema (5910 of 5910 current documents have no slug field). MCP search/get tools cannot operate on this schema. Run: git -C /Users/you/lovverk pull to refresh."
}
```

`is_stale` flips to `true` when **either**:
- the manifest is more than **7 days old** (one week of skipped syncs), or
- `schema_compatible` is false (manifest pre-dates Sprint 4 — no slug fields on records, every search/get tool returns empty even though the corpus has thousands of files).

The server itself **never** runs `git pull` or fetches from the network — `refresh_command` is a copy-pasteable command for the user to run manually.

### `search_laws(query, dataset?)`

Search the corpus for laws whose slug or title contains `query` (case-insensitive substring match against manifest metadata only — no body-text scan).

- **`query`** — substring to match. Empty / whitespace-only queries return `[]`.
- **`dataset`** *(optional)* — `lover` or `forskrifter` (or the full Lovdata key) to filter.

**Sample call:** `search_laws("jernbane", dataset="forskrifter")`

**Sample output:**

```json
[
  {
    "slug": "forskrift-om-trafikkstyring-og-ruteplanlegging-pa-det-nasjonale-jernbanenettet",
    "doc_id": "sf-20120322-298",
    "title": "Forskrift om trafikkstyring og ruteplanlegging på det nasjonale jernbanenettet",
    "dataset": "forskrifter",
    "last_changed": "2024-08-15",
    "total_changes": 4
  },
  ...
]
```

Use `get_law(slug)` to fetch the full text of any result.

---

## Discovery flow

A typical AI-assistant interaction with this server follows the same pattern:

1. **`search_laws("topic")`** — find candidates by slug + title metadata. Fast, manifest-only.
2. **`search_body("topic")`** — when the topic doesn't appear in any title (e.g., concepts like *"kryptovaluta"*), scan the body text by substring.
3. **`semantic_search("question phrased naturally")`** — when the user's wording differs from the law's vocabulary (e.g. *"renter rights"* vs *manglende vedlikehold*), substring search misses but cosine-similarity over per-section embeddings finds the right section. Always follow up with `get_section` before quoting — score is similarity, not relevance proof.
4. **`get_law(slug)`** — pull the full text of the chosen candidate.
5. **`get_section(slug, "N-M")`** — when the user wants ONE paragraph, not the whole act. Surgical, cheaper on context window. Returns the section body plus `cross_references` (every internal `§` ref already validated) so the AI sees broken refs inline.
6. **`get_law_history(slug)`** — if the assistant needs to reason about *when the corpus recorded changes* to the law (e.g., "when did the corpus's text of § 5-12 last change?"), it pulls the history and inspects events. History events are corpus-recording dates, not entry-into-force dates (ADR-0002).
7. **`list_recent_changes(...)`** — for "what's new in the corpus" queries that don't start from a specific law.
8. **`validate_citation(citation)`** — pre-quote guard. Before quoting a citation in a final answer ("per § 5-12 of Skatteloven..."), call this to confirm both the act and the section actually exist in the corpus. Returns a `valid` bool plus a human-readable `reason` field the AI can quote.
9. **`verify_quote(slug, section_id, quote)`** — anti-hallucination final check. After writing a verbatim quote attributed to a section, call this to confirm the quote actually appears in that section. Catches the most common citation hallucination (AI quotes words that are not in the cited section).
10. **`get_eu_basis(slug)`** — when the user asks about a Norwegian act's EU origin ("does Personopplysningsloven implement GDPR?"), pull the list of CELEX identifiers the act implements.
11. **`search_eu_implementations(eu_doc_id)`** — reverse direction: when the user asks which Norwegian laws implement a given EU document ("which Norwegian laws implement GDPR?"), use the CELEX as the lookup key.
12. **`corpus_status()`** — sanity check. AI assistants should call this when the other tools return unexpectedly empty results, or when the user explicitly asks "is my corpus current?". The `notice` field is human-readable; the `refresh_command` is a copy-pasteable git command the user can run to update.

The sixteen tools compose: an assistant can stitch together a research workflow without ever needing direct filesystem or git access to `lovverk`. Sprint 9's anti-hallucination layer (`semantic_search` + `cross_references` field on `get_section` + `verify_quote` + `validate_citation`) is designed to make the *fuzzy* retrieval path safe — score-based similarity hits are always followed by verbatim-text reads and verbatim-quote checks before the AI quotes anything. The time-machine tools (`get_law_at`, `list_law_versions`, `diff_law_versions`) extend the same surface to corpus-history research: the AI can answer "what did this corpus hold for Skatteloven on 2026-05-01?" — or "how did the corpus record of Skatteloven change since then?" via `diff_law_versions` — by walking the corpus's git history without any extra plumbing. Per ADR-0002, these answers describe corpus states at UTC dates, never which provisions were legally in force.

---

## Running from a local checkout

The server also runs from a clone of this repo instead of the PyPI release — the path to use for contributing, or whenever you want unreleased changes. Add `"--corpus-path", "/absolute/path/to/lovverk"` only to override the default `fetch-corpus` cache:

```jsonc
{
  "mcpServers": {
    "lovverk": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/lovspor",
               "lovspor", "mcp",
               "--corpus-path", "/path/to/lovverk"]
    }
  }
}
```

---

## Limitations

- **Current laws only, with git-history point-in-time.** The corpus mirrors `gjeldende-lover` and `gjeldende-sentrale-forskrifter` — laws and central regulations as currently in force (no repealed acts). The time-machine tools (`get_law_at`, `list_law_versions`, `diff_law_versions`) reconstruct an act's text as of a past date, but only back to the **earliest corpus revision** — a law's text as of 2018-06-01 predates lovspor's tracking window and is not retrievable. Results are corpus states, not legal-applicability determinations, and the `date_in_force` frontmatter field is descriptive metadata only — it is not used to reconstruct validity history (ADR-0002).
- **No local or municipal regulations.** Only `sentrale forskrifter` are tracked.
- **`search_laws` matches metadata only.** A law mentioning "klima" in its body but not its title or slug will not be found via `search_laws("klima")` — use `search_body` for that. (The two tools are complementary; `search_laws` is fast, `search_body` is thorough.)
- **No Stortinget enrichment.** Parliamentary metadata (saker, voteringer, publikasjoner) is not surfaced. See [`docs/decisions.md` §3](decisions.md) for the rationale.
- **Limited body-level analytics.** `search_body` finds substrings; `semantic_search` (Sprint 9) finds section-level cosine matches; `get_section` retrieves one `§ N-M` and lists its internal cross-references. Cross-act analytics that go beyond per-section retrieval — e.g. "find every act citing Skatteloven", topic clustering across the whole corpus — are not in scope and would need a richer index than the current per-doc binary embedding files.
- **No paraphrase verification.** `verify_quote` confirms a verbatim string appears in a section; it does NOT verify that a paraphrase faithfully captures legal meaning. For semantic faithfulness the AI consumer must ground in `get_section` and quote the original Norwegian text directly.
- **`get_section` cross-references resolve canonical slugs only.** Descriptive name references in section bodies (*"i lov om X"* without a canonical slug) silently fall back to same-act in the `cross_references` list and may false-positive validate. `validate_citation` is the off-ramp for ambiguous cases.
- **History for tombstones is not generated.** A law that was once in the corpus but has since been removed has no `history/<slug>.json` file. The original commits are still in `lovverk`'s git log if you need them.
- **Corpus freshness depends on the user.** The server reads whatever is in your local `lovverk` clone. If you don't `git pull`, you'll get stale data. The `corpus_status()` tool flags this (`is_stale: true` past 7 days) and gives the AI a `refresh_command` to suggest, but the server itself never pulls — that's an explicit user action.
- **Body-text search uses substring matching, not BM25 / stemming.** Sprint 8 PR-A added `search_body` which scans full body text — a law mentioning "klima" only in its body IS findable via `search_body("klima")`. But matching is case-insensitive substring only: `search_body("skattefradrag")` will not find docs that only say "skattefradraget" (no stemming). Word-based / stemmed indexing is a follow-up if real use shows it matters.

---

## Troubleshooting

### "no current law with slug ..." or "every search returns []"

The slug doesn't exist in the manifest, or the manifest itself is too old to know about it. Common causes:

- **Stale local clone.** The most common cause. Ask the AI to *"call corpus_status() and tell me if my corpus is current"* — if `is_stale: true` (or `head_commit_date` is many days old), run the suggested `refresh_command` (`git -C /path/to/lovverk pull`) and start a fresh MCP session.
- The slug includes a Lovdata short-form abbreviation (e.g. Skatteloven is `skatteloven-sktl`, not `skatteloven`). Use `search_laws` to find the exact slug.
- The law was removed upstream — only `current` records are retrievable via `get_law`. `get_law_history` would also return "no current law" in this MVP (tombstone history is not exposed).
- Typo / case mismatch — slugs are always lowercase, hyphenated, with Norwegian Unicode preserved (`opplæringslova`, not `opplaeringslova`).

### "manifest references X but file is missing"

Corpus drift: the manifest knows about a file that isn't on disk. Run `git pull` in your `lovverk` clone — the file likely arrived in a commit you haven't pulled yet.

### "history file missing for ..., corpus may predate the Sprint 5 history layer"

The `history/<slug>.json` file isn't present. If your corpus predates the Sprint 5 history migration commit `0c40d0bf` (2026-04-27), refresh it — re-run `lovspor fetch-corpus` (or `git pull` a manual clone).

### "corpus path does not exist" / "is missing manifest.json"

The corpus (the `fetch-corpus` cache at `~/.cache/lovverk`, or a `--corpus-path` you passed) has no `manifest.json`. Run `lovspor fetch-corpus` to populate the default cache, or verify a custom path with `ls /path/to/lovverk/manifest.json`.

### "path X escapes corpus root"

Defensive error. Should not happen with a clean `lovverk` clone — the manifest is sanitized before commit. If you see this with the official `lovverk`, please open an issue. If you see it with a forked or hand-edited corpus, your manifest contains a `markdown_path` or `slug` that resolves outside the corpus root.

### Server doesn't appear in the client's MCP list

- Check the client's MCP logs (Claude Desktop: `~/Library/Logs/Claude/mcp*.log` on macOS).
- Verify `uv` is on your `PATH` — MCP clients often launch in a minimal shell environment without your full `PATH`.
- Try the command standalone: `uvx lovspor mcp` should start and wait for stdio input (append `--corpus-path /path/to/lovverk` if you use a custom clone).

---

## Legal & attribution

The legal text served by this MCP comes from [Lovdata](https://lovdata.no)'s public-data API and is licensed under [NLOD 2.0](https://data.norge.no/nlod/no/2.0/). Every rendered Markdown carries the attribution in its YAML front matter (`source_provider: Lovdata`, `source_license: NLOD 2.0`).

The `lovspor` engine code (this repo) is MIT-licensed. The `lovverk` corpus structure is CC0; the legal text within it remains under NLOD 2.0.

This project follows the conservative legal stance documented in [`docs/decisions.md` §4](decisions.md): only the Markdown derivative is published, never Lovdata's raw XML.

---

## Reporting issues

File issues at [github.com/bartoszkobylinski/lovspor/issues](https://github.com/bartoszkobylinski/lovspor/issues). For bugs in the corpus content (rendered text, missing acts), file at [github.com/bartoszkobylinski/lovverk/issues](https://github.com/bartoszkobylinski/lovverk/issues) instead.
