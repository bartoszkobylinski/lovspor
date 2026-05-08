# MCP server — lovverk for AI assistants

`lovspor mcp` is a stdio MCP (Model Context Protocol) server that exposes the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) Norwegian-law corpus to AI assistants — Claude Desktop, Claude Code, or any other client that speaks MCP. The assistant gets ten read-only tools and uses them to answer real legal-research questions from the live corpus instead of stale training data.

This document covers the full setup: prerequisites, configuration for two common clients, every tool with sample input and output, the typical discovery flow, troubleshooting, limitations, and legal attribution.

---

## At a glance

- **Transport:** stdio. Each user runs their own copy locally; no network surface, no shared infrastructure, no auth needed.
- **Data path:** the server reads a local clone of the `lovverk` Markdown corpus. The lovspor scheduled workflow keeps `lovverk` current; the user runs `git pull` (or sets up a cron) to pick up updates.
- **Tools:** ten read-only, manifest-and-filesystem-only.
- **Engine sync:** untouched. MCP is a *consumer* of `lovverk`; the producer is the `.github/workflows/sync.yml` cron in `lovspor`. They're decoupled by design ([`docs/decisions.md` §1](decisions.md)).

---

## Prerequisites

1. **Local clone of `lovverk`**:

   ```bash
   git clone https://github.com/bartoszkobylinski/lovverk.git ~/lovverk
   ```

   Optional: keep it fresh with a daily cron, e.g. add to your crontab:

   ```cron
   30 5 * * * cd ~/lovverk && git pull --quiet
   ```

   (The lovspor sync runs daily at 04:00 UTC; pulling at 05:30 UTC catches it.)

2. **`uv`** (or `uvx`) installed locally — see [astral.sh/uv](https://docs.astral.sh/uv/). The MCP client invokes the server via `uvx`, which fetches and runs `lovspor` directly from this GitHub repo without you needing to clone or install it manually.

   No PyPI publish required (yet). If you prefer `pip install`, see [§ If you prefer pip install](#if-you-prefer-pip-install) below.

---

## Quickstart — Claude Desktop

Add the following to your Claude Desktop config (path varies by OS — see [Claude Desktop docs](https://modelcontextprotocol.io/quickstart/user) for the exact location):

```jsonc
{
  "mcpServers": {
    "lovverk": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/bartoszkobylinski/lovspor.git",
        "lovspor", "mcp",
        "--corpus-path", "/absolute/path/to/lovverk"
      ]
    }
  }
}
```

Replace `/absolute/path/to/lovverk` with your local clone path. Restart Claude Desktop. The server appears in the MCP indicator at the bottom of a new conversation.

Try it: ask Claude *"Use the lovverk MCP tools to tell me when Skatteloven was last updated."*

## Quickstart — Claude Code

Same config shape, registered via the Claude Code CLI:

```bash
claude mcp add lovverk uvx \
  --from "git+https://github.com/bartoszkobylinski/lovspor.git" \
  -- lovspor mcp --corpus-path /absolute/path/to/lovverk
```

Or edit `~/.claude.json` directly with the JSON above. Then `claude` in a fresh session — `/mcp` lists the registered servers.

---

## Tools

All ten are read-only. None mutate the corpus, trigger a sync, or reach the network.

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
- **`section_id`** — the bare numeric / hyphenated identifier WITHOUT the `§` prefix or trailing dot. Examples: `"5-12"`, `"1"`, `"5-12a"`. Norwegian acts use `§ N` for single-chapter acts and `§ N-M` (chapter N, section M) for multi-chapter acts; both work.

**Sample call:** `get_section(slug="skatteloven-sktl", section_id="5-12")`

**Sample output:**

```json
{
  "slug": "skatteloven-sktl",
  "section_id": "5-12",
  "heading": "§ 5-12. Boligsparing for ungdom",
  "parent_chapter": "Kapittel 5. Alminnelig inntekt og fradragene",
  "body": "(1) Skattefradraget gis for sparing til bolig...\n\n(2) Fradraget reduseres ved utbetaling..."
}
```

If the section is unknown the error message lists the act's available section ids in natural order (so `5-2` < `5-10`, not lexicographic) — the AI can recover without an extra `get_law` call.

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

**Performance:** the body index is loaded lazily on the first call (~3-5 s for the production 4522-doc corpus, ~45 MB resident); subsequent calls are O(N) substring scans (~100-200 ms typical). Server startup stays fast for clients that only query metadata.

### `validate_citation(citation)`

Verify that a Norwegian-law citation string actually resolves in the corpus. **Zero-hallucination guard** — call this before quoting a citation in a final answer to confirm both the act and the section exist.

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

The `reason` field is human-readable and the AI can quote it verbatim to explain to the user why the citation couldn't be confirmed. Slug match is **strict** — `"skatteloven"` does not fuzzy-match production slug `"skatteloven-sktl"`. AI consumers should use canonical slugs from `search_laws`.

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
  "total_current_documents": 4522,
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
  "total_current_documents": 4520,
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
  "total_current_documents": 4522,
  "head_commit": "57c3052",
  "head_commit_date": "2026-04-26",
  "head_commit_subject": "sync: 4522 new, 0 changed, 0 removed",
  "refresh_command": "git -C /Users/you/lovverk pull",
  "notice": "Corpus manifest is on the pre-Sprint-4 schema (4522 of 4522 current documents have no slug field). MCP search/get tools cannot operate on this schema. Run: git -C /Users/you/lovverk pull to refresh."
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
2. **`search_body("topic")`** — when the topic doesn't appear in any title (e.g., concepts like *"kryptovaluta"*), scan the body text instead. Heavier but catches semantic matches.
3. **`get_law(slug)`** — pull the full text of the chosen candidate.
4. **`get_section(slug, "N-M")`** — when the user wants ONE paragraph, not the whole act. Surgical, cheaper on context window. Reuses the same body cache as `search_body`.
5. **`get_law_history(slug)`** — if the assistant needs to reason about *when* the law changed (e.g., "was § 5-12 in force in 2018?"), it pulls the history and inspects events.
6. **`list_recent_changes(...)`** — for "what's new in the corpus" queries that don't start from a specific law.
7. **`validate_citation(citation)`** — zero-hallucination guard. Before quoting a citation in a final answer ("per § 5-12 of Skatteloven..."), call this to confirm both the act and the section actually exist in the corpus. Returns a `valid` bool plus a human-readable `reason` field the AI can quote.
8. **`get_eu_basis(slug)`** — when the user asks about a Norwegian act's EU origin ("does Personopplysningsloven implement GDPR?"), pull the list of CELEX identifiers the act implements.
9. **`search_eu_implementations(eu_doc_id)`** — reverse direction: when the user asks which Norwegian laws implement a given EU document ("which Norwegian laws implement GDPR?"), use the CELEX as the lookup key.
10. **`corpus_status()`** — sanity check. AI assistants should call this when the other tools return unexpectedly empty results, or when the user explicitly asks "is my corpus current?". The `notice` field is human-readable; the `refresh_command` is a copy-pasteable git command the user can run to update.

The ten tools compose: an assistant can stitch together a research workflow without ever needing direct filesystem or git access to `lovverk`.

---

## If you prefer pip install

PyPI publish for `lovspor` is **planned** but not yet shipped. Once published, the config simplifies to:

```jsonc
{
  "mcpServers": {
    "lovverk": {
      "command": "uvx",
      "args": ["lovspor", "mcp", "--corpus-path", "/absolute/path/to/lovverk"]
    }
  }
}
```

(or `pip install lovspor` + `lovspor mcp ...` if you don't use `uv`). For now, the `--from git+https://...` form above is the primary path.

You can also clone `lovspor` and run from source if you want to develop or pin a specific commit:

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

- **Current laws only.** The corpus mirrors `gjeldende-lover` and `gjeldende-sentrale-forskrifter` — laws and central regulations as currently in force. No historical point-in-time reconstruction (a law's text as of 2018-06-01 is *not* directly retrievable; only the current text plus a per-act change-event history are available).
- **No local or municipal regulations.** Only `sentrale forskrifter` are tracked.
- **`search_laws` matches metadata only.** A law mentioning "klima" in its body but not its title or slug will not be found via `search_laws("klima")` — use `search_body` for that. (The two tools are complementary; `search_laws` is fast, `search_body` is thorough.)
- **No Stortinget enrichment.** Parliamentary metadata (saker, voteringer, publikasjoner) is not surfaced. See [`docs/decisions.md` §3](decisions.md) for the rationale.
- **No body-level analytics beyond search + section access.** `search_body` finds substrings; `get_section` retrieves a specific `§ N-M` (Sprint 8 PR-B). Cross-act analytics (e.g. "find every act citing Skatteloven", topic clustering, semantic similarity) are not in scope for this MVP and would need a richer index than the current Markdown corpus.
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

The `history/<slug>.json` file isn't present. If you cloned `lovverk` before the Sprint 5 history migration commit `0c40d0bf` (2026-04-27), `git pull` to fetch it.

### "corpus path does not exist" / "is missing manifest.json"

The `--corpus-path` argument doesn't point at a valid `lovverk` clone. Verify with `ls /path/to/lovverk/manifest.json` — if absent, re-clone (`git clone https://github.com/bartoszkobylinski/lovverk.git`).

### "path X escapes corpus root"

Defensive error. Should not happen with a clean `lovverk` clone — the manifest is sanitized before commit. If you see this with the official `lovverk`, please open an issue. If you see it with a forked or hand-edited corpus, your manifest contains a `markdown_path` or `slug` that resolves outside the corpus root.

### Server doesn't appear in the client's MCP list

- Check the client's MCP logs (Claude Desktop: `~/Library/Logs/Claude/mcp*.log` on macOS).
- Verify `uvx` is on your `PATH` — MCP clients often launch in a minimal shell environment without your full `PATH`.
- Try the command standalone: `uvx --from git+https://github.com/bartoszkobylinski/lovspor.git lovspor mcp --corpus-path /path/to/lovverk` should start and wait for stdio input.

---

## Legal & attribution

The legal text served by this MCP comes from [Lovdata](https://lovdata.no)'s public-data API and is licensed under [NLOD 2.0](https://data.norge.no/nlod/no/2.0/). Every rendered Markdown carries the attribution in its YAML front matter (`source_provider: Lovdata`, `source_license: NLOD 2.0`).

The `lovspor` engine code (this repo) is MIT-licensed. The `lovverk` corpus structure is CC0; the legal text within it remains under NLOD 2.0.

This project follows the conservative legal stance documented in [`docs/decisions.md` §4](decisions.md): only the Markdown derivative is published, never Lovdata's raw XML.

---

## Reporting issues

File issues at [github.com/bartoszkobylinski/lovspor/issues](https://github.com/bartoszkobylinski/lovspor/issues). For bugs in the corpus content (rendered text, missing acts), file at [github.com/bartoszkobylinski/lovverk/issues](https://github.com/bartoszkobylinski/lovverk/issues) instead.
