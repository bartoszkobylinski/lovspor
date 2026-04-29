# Persona-driven MCP evals

This suite exercises the MCP tool surface through realistic user personas without calling an external LLM. The runner builds a small synthetic `lovverk` corpus in a temporary directory, points `CorpusReader` at it, executes the expected tool calls from each scenario, and evaluates deterministic success criteria.

## Run

```bash
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
- `gap-revealed`: the scenario intentionally demonstrates that the current MCP tool surface cannot answer the query well.

The `Gaps revealed` section ranks declared gaps by persona reach and scenario frequency, with roadmap classes from `docs/roadmap.md`.

## Add Scenarios

1. Add or edit a persona scenario file under `evals/scenarios/<persona>.yaml`.
2. Keep exactly 10 scenarios per persona for the full suite.
3. Use current tools in `expected_tool_calls`; the runner supports `get_law`, `get_section`, `get_law_history`, `list_recent_changes`, `search_laws`, `search_body`, `validate_citation`, `get_eu_basis`, `search_eu_implementations`, and `corpus_status`.
4. Add `expected_outcome: gap_revealed`, `reveals_gap`, and `roadmap_class` when no current tool can answer the query well.
5. If a scenario needs new law text, update `evals/fixtures/synthetic_corpus.yaml`; do not clone the real `lovverk` corpus.

Run `uv run lovspor-eval --strict` after edits. A clean strict run means every normal scenario passed and every known gap was explicitly declared.
