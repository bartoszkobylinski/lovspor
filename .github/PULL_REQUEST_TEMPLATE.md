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
- [ ] PR Pipeline green: fast-ci, codex-tests, mutation (a `not_applicable` mutation gate is a valid outcome for PRs with no `src/` changes)
