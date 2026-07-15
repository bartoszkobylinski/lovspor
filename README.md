# lovspor

Norwegian law change tracker. Engine that produces the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) corpus from Lovdata's public-data API and serves it to AI assistants over MCP (Model Context Protocol).

## Status

**Production.** A scheduled GitHub Actions workflow runs daily at 04:00 UTC, pulls the latest tarballs from Lovdata, classifies each document as new / updated / renamed / removed, renders the changes to Markdown, and pushes the diff to `lovverk` as conventional-commit history. The corpus mirrors close to **6 000 acts** (Norwegian *lover* and central *forskrifter*), each with a structured per-act change history under `<dataset>/history/<slug>.json`. The exact live count is always available from the MCP `corpus_status` tool.

> **Distribution changed 2026-07-14 (commercial pivot to a hosted MCP service).** The engine is now private and the PyPI releases (`0.2.0`–`0.3.0`) were withdrawn. A hosted Lovspor MCP endpoint (Sprint 12) will be the supported consumer path; until it ships, the server runs locally from a checkout of this repo — read `uv run --project /path/to/lovspor lovspor …` wherever the commands below say `uvx lovspor …`. See [`docs/roadmap.md`](docs/roadmap.md).

Sprint 9 (MERGED 2026-05-06) added per-section embeddings to the corpus and a four-layer grounding-and-verification path to the MCP surface: `semantic_search` (cosine over embeddings), `verify_quote` (verbatim-citation guard), validated `cross_references` on `get_section`, and `validate_citation` as the off-ramp for ambiguous citations.

See [`docs/decisions.md`](docs/decisions.md) for the full architecture and design rationale.

## MCP server

`lovspor` ships a stdio MCP server that exposes the `lovverk` corpus to AI assistants — Claude Desktop, Claude Code, or any MCP client. Once configured, you can ask *"what changed in Skatteloven this year?"* or *"are there forskrifter about jernbane?"* and the assistant answers from the live corpus instead of stale training data.

**Setup — three steps:**

1. **Install [`uv`](https://docs.astral.sh/uv/) and clone this repo.** The engine is private and no longer on PyPI, so the server runs from your checkout via `uv run lovspor` (see the distribution note above).

2. **Fetch the corpus.** One command shallow-clones the legal text to the default cache (`~/.cache/lovverk`):

   ```bash
   uv run lovspor fetch-corpus
   ```

   Re-run it any time to update — it reports `cloned`, `updated`, or `unchanged`.

3. **Register the server.** `lovspor mcp` finds that cache automatically. With Claude Code:

   ```bash
   claude mcp add lovverk -- uv run --project /path/to/lovspor lovspor mcp
   ```

   Or add it to your client config directly — Claude Desktop's `claude_desktop_config.json`, or `~/.claude.json` for Claude Code:

   ```jsonc
   {
     "mcpServers": {
       "lovverk": {
         "command": "uv",
         "args": ["run", "--project", "/path/to/lovspor", "lovspor", "mcp"]
       }
     }
   }
   ```

Restart the client and `lovverk` appears in its MCP list. Fifteen of the sixteen tools work immediately — no key, and no network access beyond your local corpus clone.

**Optional — enable `semantic_search`.** The one search-by-meaning tool needs an OpenAI API key: it embeds *your query* at call time (the corpus vectors ship pre-computed, so you never re-embed the corpus yourself). Bring your own key via the server's `env`:

```jsonc
{
  "mcpServers": {
    "lovverk": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/lovspor", "lovspor", "mcp"],
      "env": { "OPENAI_API_KEY": "sk-...your-own-key..." }
    }
  }
}
```

It's your key in your own local config file — keep that file private and never commit it. Without a key, `semantic_search` is simply disabled; the other fifteen tools are unaffected.

Keep the corpus fresh by re-running `uv run lovspor fetch-corpus` (the engine re-syncs daily at 04:00 UTC); the `corpus_status` tool tells the assistant when your clone has drifted.

> **On invocation:** the commands above run the server from a local checkout via `uv run` (the `--project` flag points at wherever you cloned this repo). The former PyPI / `uvx lovspor` and `--from git+…` paths were retired with the 2026-07-14 pivot — see the distribution note under [Status](#status).

See [`docs/mcp.md`](docs/mcp.md) for the full setup guide, all sixteen tools documented with examples (`get_law`, `get_law_at`, `list_law_versions`, `diff_law_versions`, `get_section`, `list_sections`, `get_law_history`, `list_recent_changes`, `search_laws`, `search_body`, `semantic_search`, `validate_citation`, `verify_quote`, `get_eu_basis`, `search_eu_implementations`, `corpus_status`), troubleshooting, and limitations. The binary embedding format that powers `semantic_search` is documented in [`docs/embeddings.md`](docs/embeddings.md).

Persona-driven offline evals for the MCP tool surface live in [`evals/`](evals/). They are repo-only tooling — run them from a checkout with `uv run python -m evals.runner`.

## Sources

- `https://api.lovdata.no/v1/publicData/get/gjeldende-lover.tar.bz2` — current Norwegian laws
- `https://api.lovdata.no/v1/publicData/get/gjeldende-sentrale-forskrifter.tar.bz2` — current central regulations

Data is licensed under [Norsk lisens for offentlige data (NLOD) 2.0](https://data.norge.no/nlod/no/2.0/).

## License

The engine code in this repository is licensed under MIT. See [LICENSE](LICENSE).

The legal text produced by this engine is published in the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) repository under NLOD 2.0, with attribution to Lovdata.

## Related work

- [`cloveras/lovdata2`](https://github.com/cloveras/lovdata2) — JSON tooling and MCP server for the same Lovdata public data. `lovspor` is complementary, focused on Markdown rendering, Git-based change tracking, and an MCP server scoped to the `lovverk` corpus shape.
