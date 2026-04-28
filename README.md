# lovspor

Norwegian law change tracker. Engine that produces the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) corpus from Lovdata's public-data API and serves it to AI assistants over MCP (Model Context Protocol).

## Status

**Production.** A scheduled GitHub Actions workflow runs daily at 04:00 UTC, pulls the latest tarballs from Lovdata, classifies each document as new / updated / renamed / removed, renders the changes to Markdown, and pushes the diff to `lovverk` as conventional-commit history. The corpus currently mirrors **4 522 acts** (≈ 781 lover + ≈ 3 741 forskrifter) with a structured per-act change history under each `<dataset>/history/<slug>.json`.

See [`docs/decisions.md`](docs/decisions.md) for the full architecture and design rationale.

## MCP server

`lovspor` ships an MCP server that exposes the `lovverk` corpus to AI assistants like Claude Desktop and Claude Code. Once configured, you can ask things like *"what changed in Skatteloven this year?"* or *"are there forskrifter about jernbane?"* and the assistant fetches the answer from the corpus directly.

Quickstart for Claude Desktop / Claude Code (replace `/path/to/lovverk` with the location of your local clone):

```jsonc
{
  "mcpServers": {
    "lovverk": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/bartoszkobylinski/lovspor.git",
        "lovspor", "mcp",
        "--corpus-path", "/path/to/lovverk"
      ]
    }
  }
}
```

This runs the server on demand from this GitHub repo via [`uv`](https://docs.astral.sh/uv/) — no local clone of `lovspor` required, just the corpus.

See [`docs/mcp.md`](docs/mcp.md) for the full setup guide, all six tools documented with examples (`get_law`, `get_law_history`, `list_recent_changes`, `search_laws`, `search_body`, `corpus_status`), troubleshooting, and limitations.

## Sources

- `https://api.lovdata.no/v1/publicData/get/gjeldende-lover.tar.bz2` — current Norwegian laws
- `https://api.lovdata.no/v1/publicData/get/gjeldende-sentrale-forskrifter.tar.bz2` — current central regulations

Data is licensed under [Norsk lisens for offentlige data (NLOD) 2.0](https://data.norge.no/nlod/no/2.0/).

## License

The engine code in this repository is licensed under MIT. See [LICENSE](LICENSE).

The legal text produced by this engine is published in the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) repository under NLOD 2.0, with attribution to Lovdata.

## Related work

- [`cloveras/lovdata2`](https://github.com/cloveras/lovdata2) — JSON tooling and MCP server for the same Lovdata public data. `lovspor` is complementary, focused on Markdown rendering, Git-based change tracking, and an MCP server scoped to the `lovverk` corpus shape.
