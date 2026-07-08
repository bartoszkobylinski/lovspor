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
- [ ] Mutation score reviewed by Codex on the PR (if logic changes)
- [ ] `/security-check` clean
- [ ] Codex review pass
