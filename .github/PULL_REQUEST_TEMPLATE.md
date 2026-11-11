## Summary

<!-- 1-3 bullets of what changed and why -->

## Files changed

<!-- list of important files / functions -->

## What to verify

<!-- behaviors, invariants, edge cases — read by the Codex CI test author -->

## Definition of Done

- [ ] All commits atomic and bisectable
- [ ] `uv run ruff check` green
- [ ] `uv run ruff format --check` green
- [ ] `uv run mypy src/` green
- [ ] `uv run pytest` green
- [ ] Coverage ≥ 90% on changed files
- [ ] `/security-check` clean
- [ ] PR Pipeline green: fast-ci, codex-tests, ready (mutation is a nightly report on `main`, not a PR gate — `docs/agentic-ci.md`)
