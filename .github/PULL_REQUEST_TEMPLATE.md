## Summary

<!-- 1-3 bullets of what changed and why -->

## Files changed

<!-- list of important files / functions -->

## Codex review prompt

<!-- Paste this when running Codex on this PR -->

```
What changed:
- (files, functions)

What to verify:
- (behaviors, invariants, edge cases)

What to write tests for:
- (concrete test ideas)

What NOT to do:
- Do not refactor existing code
- Do not change feature behavior
- Only write tests and report bugs
```

## Definition of Done

- [ ] All commits atomic and bisectable
- [ ] `uv run ruff check` green
- [ ] `uv run ruff format --check` green
- [ ] `uv run mypy src/` green
- [ ] `uv run pytest` green
- [ ] Coverage ≥ 90% on changed files
- [ ] `uv run mutmut run` reviewed (if logic changes)
- [ ] `/security-check` clean
- [ ] Codex review pass
