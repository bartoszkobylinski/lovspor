# lovspor

Ask an AI assistant about Norwegian law and you get a confident answer — statute name,
section number, sometimes a quote. Nothing in that loop checks whether the section
actually exists. Lovspor does.

Lovspor keeps an open, daily-updated copy of Norwegian law — close to 6,000 acts and
central regulations from [Lovdata's public data](https://api.lovdata.no), with full
version history — and gives any AI assistant (Claude Desktop, Claude Code, Cursor, any
MCP client) tools to search it, quote it, and verify citations against the real text.

## What you can ask

Once connected, your assistant answers from the live corpus instead of stale training data:

- *"What changed in skatteloven this year?"*
- *"What did husleieloven § 9-6 say in May 2026?"* — full version history since April 2026, diffable between any two tracked dates (fetch the corpus with `--full-history`, see below)
- *"Does this paragraph exist?"* — `validate_citation` answers instead of guessing
- *"Is this quote verbatim?"* — `verify_quote` checks it against the actual text

## Quickstart

Requires [uv](https://docs.astral.sh/uv/). No clone, no account, no API key.

```bash
# 1. Fetch the legal corpus to ~/.cache/lovverk (re-run any time to update)
uvx lovspor fetch-corpus

# 2. Connect your AI client — Claude Code:
claude mcp add lovverk -- uvx lovspor mcp
```

Other MCP clients (Claude Desktop's `claude_desktop_config.json`, etc.):

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

Restart the client and `lovverk` appears in its MCP list. Fifteen of the sixteen tools
work immediately — no key, and no network access beyond your local corpus clone.

**The point-in-time tools need the full history.** `fetch-corpus` clones shallow
(`--depth 1`) by default, which keeps the download small and is enough for every
current-law tool. On a shallow clone, `get_law_at` and `diff_law_versions` reach only as
far back as the clone does. Fetch it whole instead:

```bash
uvx lovspor fetch-corpus --full-history
```

The same flag deepens an existing shallow clone in place (`git fetch --unshallow`), so this
is not a decision you have to get right the first time.

Full setup guide, all sixteen tools with examples, troubleshooting and limitations:
[`docs/mcp.md`](docs/mcp.md).

## What's inside — and what's not

**Inside:** all current Norwegian acts (*lover*) and central regulations (*sentrale
forskrifter*) from Lovdata's public-data API, re-synced daily at 04:00 UTC, each with a
structured per-act change history. Live count: the `corpus_status` tool.

**Not inside:** court decisions, preparatory works (*forarbeider*), agency circulars
(*rundskriv*), municipal regulations. A rule can be binding and absent here — an empty
result is not evidence that no such rule exists.

## Optional: search by meaning

`semantic_search` is the one tool that needs an embedding key (OpenAI; the corpus
vectors ship pre-computed — only your query is embedded). Bring your own key via the
server's `env`:

```jsonc
{
  "mcpServers": {
    "lovverk": {
      "command": "uvx",
      "args": ["lovspor", "mcp"],
      "env": { "OPENAI_API_KEY": "sk-...your-own-key..." }
    }
  }
}
```

It's your key in your own local config file — keep that file private and never commit
it. Without a key, `semantic_search` is simply disabled; the other fifteen tools are
unaffected. Details: [`docs/embeddings.md`](docs/embeddings.md).

## Optional: hosted endpoint

Don't want to self-host? A hosted MCP endpoint runs at
`https://lovspor.no/mcp` — ask for access, or see
[`docs/mcp.md`](docs/mcp.md) to run the same thing yourself.

## How it works

A scheduled workflow pulls Lovdata's public-data tarballs daily, classifies each
document as new / updated / renamed / removed, renders deterministic Markdown, and
pushes the diff to [`lovverk`](https://github.com/bartoszkobylinski/lovverk) — the
public corpus repo — as conventional-commit history. `lovspor` (this repo, on
[PyPI](https://pypi.org/project/lovspor/)) is the engine and MCP server; legal text
never lives here.

Architecture and design rationale: [`docs/decisions.md`](docs/decisions.md).
Release process: [`docs/releasing.md`](docs/releasing.md).
Offline evals for the MCP surface: [`evals/`](evals/) (repo-only tooling).

## Install from source

```bash
git clone https://github.com/bartoszkobylinski/lovspor
cd lovspor
./scripts/bootstrap.sh     # uv sync + pre-commit hooks
uv run lovspor --help
```

## Sources

- `https://api.lovdata.no/v1/publicData/get/gjeldende-lover.tar.bz2` — current Norwegian laws
- `https://api.lovdata.no/v1/publicData/get/gjeldende-sentrale-forskrifter.tar.bz2` — current central regulations

Data is licensed under [Norsk lisens for offentlige data (NLOD) 2.0](https://data.norge.no/nlod/no/2.0/).

## License

The engine code in this repository is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). See [LICENSE](LICENSE). Versions up to and including commit `632dae8` were published under MIT; everything after is AGPL-3.0. The corpus data in [lovverk](https://github.com/bartoszkobylinski/lovverk) remains NLOD 2.0 and is unaffected.

The legal text produced by this engine is published in the
[`lovverk`](https://github.com/bartoszkobylinski/lovverk) repository under NLOD 2.0,
with attribution to Lovdata.

## Related work

- [`cloveras/lovdata2`](https://github.com/cloveras/lovdata2) — JSON tooling and MCP
  server for the same Lovdata public data. `lovspor` is complementary, focused on
  Markdown rendering, Git-based change tracking, and an MCP server scoped to the
  `lovverk` corpus shape.
