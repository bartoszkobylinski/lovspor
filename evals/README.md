# Persona-driven MCP evals

This suite exercises the MCP tool surface through realistic user personas without using an external LLM as the *driver* (the runner is deterministic — it invokes tools directly from each scenario's `expected_tool_calls`). The runner builds a small synthetic `lovverk` corpus in a temporary directory, points `CorpusReader` at it, executes the expected tool calls, and evaluates deterministic success criteria.

`semantic_search` is the one tool that talks to a remote service: it embeds the query through OpenAI's `text-embedding-3-large`. Scenarios that use `semantic_search` therefore require `OPENAI_API_KEY` in the environment; without the key those scenarios are reported as gap-revealed (skipped). The other tools the runner exercises stay pure local. (The MCP server itself serves fifteen tools; the runner currently drives twelve — the Sprint 10 time-machine pair `get_law_at` / `list_law_versions` and the `list_sections` TOC tool are not yet scripted in any scenario.)

## Run

```bash
# Optional — enables semantic_search scenarios. Without it they
# report as gap-revealed (skipped) with a clear note.
export OPENAI_API_KEY=sk-...

uv run lovspor-eval
uv run lovspor-eval --persona anne
uv run lovspor-eval --strict
```

`--strict` exits non-zero on `fail` or `partial`. Scenarios with `expected_outcome: gap_revealed` are informational and do not fail strict mode.

Reports are written to `evals/results/<date>.md`. The first baseline is [`results/2026-04-29.md`](results/2026-04-29.md).

## Interpret Results

- `pass`: all success criteria passed and no unexpected tool error occurred.
- `partial`: at least one criterion passed, but one or more criteria failed.
- `fail`: no criteria passed, or a tool failed unexpectedly.
- `gap-revealed`: the scenario intentionally demonstrates that the current MCP tool surface cannot answer the query well — OR the scenario uses `semantic_search` and `OPENAI_API_KEY` is unset (the `note` field disambiguates).

The `Gaps revealed` section ranks declared gaps by persona reach and scenario frequency, with roadmap classes from `docs/roadmap.md`.

## Add Scenarios

1. Add or edit a persona scenario file under `evals/scenarios/<persona>.yaml`.
2. Keep exactly 10 scenarios per persona for the full suite.
3. Use current tools in `expected_tool_calls`; the runner supports the **twelve** MCP tools: `get_law`, `get_section`, `get_law_history`, `list_recent_changes`, `search_laws`, `search_body`, `semantic_search`, `validate_citation`, `verify_quote`, `get_eu_basis`, `search_eu_implementations`, and `corpus_status`.
4. Add `expected_outcome: gap_revealed`, `reveals_gap`, and `roadmap_class` when no current tool can answer the query well.
5. If a scenario needs new law text, update `evals/fixtures/synthetic_corpus.yaml`; do not clone the real `lovverk` corpus.
6. For Sprint 9 anti-hallucination flow, the recommended chain is `semantic_search` (find candidates) → `get_section` (read verbatim text + see validated `cross_references`) → `verify_quote` (confirm the verbatim quote before citing). `validate_citation` is the off-ramp for ambiguous citations.

Run `uv run lovspor-eval --strict` after edits. A clean strict run means every normal scenario passed and every known gap was explicitly declared.
