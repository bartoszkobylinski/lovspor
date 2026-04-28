# MCP server — lovverk for AI assistants

`lovspor mcp` is a stdio MCP (Model Context Protocol) server that exposes the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) Norwegian-law corpus to AI assistants — Claude Desktop, Claude Code, or any other client that speaks MCP. The assistant gets four read-only tools and uses them to answer real legal-research questions from the live corpus instead of stale training data.

This document covers the full setup: prerequisites, configuration for two common clients, every tool with sample input and output, the typical discovery flow, troubleshooting, limitations, and legal attribution.

---

## At a glance

- **Transport:** stdio. Each user runs their own copy locally; no network surface, no shared infrastructure, no auth needed.
- **Data path:** the server reads a local clone of the `lovverk` Markdown corpus. The lovspor scheduled workflow keeps `lovverk` current; the user runs `git pull` (or sets up a cron) to pick up updates.
- **Tools:** five read-only, manifest-and-filesystem-only.
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

All four are read-only. None mutate the corpus, trigger a sync, or reach the network.

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

1. **`search_laws("topic")`** — find candidates. Returns a list of `{slug, title, ...}` summaries.
2. **`get_law(slug)`** — pull the full text of the chosen candidate.
3. **`get_law_history(slug)`** — if the assistant needs to reason about *when* the law changed (e.g., "was § 5-12 in force in 2018?"), it pulls the history and inspects events.
4. **`list_recent_changes(...)`** — for "what's new in the corpus" queries that don't start from a specific law.
5. **`corpus_status()`** — sanity check. AI assistants should call this when the other tools return unexpectedly empty results, or when the user explicitly asks "is my corpus current?". The `notice` field is human-readable; the `refresh_command` is a copy-pasteable git command the user can run to update.

The five tools compose: an assistant can stitch together a research workflow without ever needing direct filesystem or git access to `lovverk`.

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
- **No body-text search.** `search_laws` matches against the manifest's `slug` and `title` fields only. A law mentioning "klima" in its body but not its title or slug will not be found via `search_laws("klima")`. Body-text indexing is a future sprint.
- **No Stortinget enrichment.** Parliamentary metadata (saker, voteringer, publikasjoner) is not surfaced. See [`docs/decisions.md` §3](decisions.md) for the rationale.
- **No section / paragraph addressing.** `get_law` returns the whole act; there's no `get_section(slug, "5-12")` yet.
- **History for tombstones is not generated.** A law that was once in the corpus but has since been removed has no `history/<slug>.json` file. The original commits are still in `lovverk`'s git log if you need them.
- **Corpus freshness depends on the user.** The server reads whatever is in your local `lovverk` clone. If you don't `git pull`, you'll get stale data. The `corpus_status()` tool flags this (`is_stale: true` past 7 days) and gives the AI a `refresh_command` to suggest, but the server itself never pulls — that's an explicit user action.

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
