# CLAUDE.md — lovspor

Contract for how Claude works in this project. Read at the start of every session. Project rules below extend the global rules in `~/.claude/CLAUDE.md`.

## Project context

`lovspor` is the **engine**. It downloads Lovdata public-data tarballs, normalizes XML, deterministically renders Markdown, hashes XML to detect changes, and commits only changed laws to the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) corpus repo.

This repo contains **only the engine**. Legal text never lives here. The corpus lives in `lovverk`.

## Critical constraints (non-negotiable)

- **Source:** only `https://api.lovdata.no/v1/publicData/`. Never scrape lovdata.no HTML.
- **License:** every output Markdown file carries NLOD 2.0 attribution in YAML front matter.
- **Renderer must be deterministic.** Same XML input → byte-identical Markdown output. Tested.
- **Hash is on normalized XML, never on rendered Markdown or HTML.** Change-detection invariant.
- **Raw XML never to git.** Cache in `data/cache/` is gitignored. Conservative posture: avoid argument over Lovdata's editorial markup.

## How I work in this project

### Small chunks
- 1 commit = 1 logical change (e.g. "add XML normalizer", "add hash function"). Not 1 commit per file. Not 1 commit per feature.
- Every commit must pass CI on its own. Bisectable.
- WIP commits with red tests are squashed before opening a PR.

### TDD per chunk
- Every new module starts with a failing test in `tests/unit/`.
- Minimal code to green. Refactor if needed. Move on.
- Integration tests in `tests/integration/` use real fixtures, not mocks.

### Pre-commit checklist (mandatory, every commit)
1. `uv run ruff check` — green
2. `uv run ruff format --check` — green
3. `uv run mypy src/` — green
4. `uv run pytest tests/unit/` — green
5. Invoke `/security-check` — clean
6. Then `git commit`

`pre-commit install` wires steps 1–4 to run automatically. Step 5 is manual until the skill auto-triggers.

### Branching
- Every change on a feature branch: `feat/`, `fix/`, `refactor/`, `test/`, `docs/`.
- Never commit to `main` directly. The single exception is the bootstrap commit `chore: initialize repository`.

### PR workflow (mandatory)
1. Feature complete on branch → push.
2. Open PR via web UI (or `gh pr create` if installed).
3. Fill the PR template, especially the **Codex review prompt** section.
4. **STOP.** Hand back to user: *"PR opened: <link>. Run Codex."*
5. User runs Codex, returns with report.
6. If Codex finds bugs: fix on the same branch, push. Hand back to user again.
7. **Only the user merges.** Never merge yourself.
8. After merge: provide deploy + log commands.

## Testing strategy

- `tests/unit/` — fast, isolated, one module at a time. Every public function covered.
- `tests/integration/` — pipeline end-to-end on real fixture tarballs and XML samples.
- `tests/fixtures/` — real XML/JSON samples from Lovdata, captured once, committed, never regenerated unless source schema changes.
- Mutation testing: **Codex runs `uv run mutmut run` on every PR review** and reports the kill score plus survivors. Claude does not run mutmut locally before opening a PR — it would be duplicate work and slow the push cycle. If a Codex round flags a critical-path survivor, fix it on the same branch and ask Codex to re-run. **mutmut 2.x only** — mutmut 3 has open bugs around editable installs and dataclasses. Consequence: **no PEP 695 type parameter syntax** (`def foo[T](...)`), because mutmut 2's parser predates PEP 695 and crashes. Use `TypeVar` from `typing` instead. Ruff rule `UP047` is globally disabled to prevent accidental reintroduction.
- HTTP transport mocked with `pytest-httpx` only. Logic is never mocked.

## Forbidden

- Commit raw XML or HTML from Lovdata to this repo.
- Scrape lovdata.no website HTML.
- Mock business logic in tests (mocking transport via `pytest-httpx` is fine).
- `subprocess.run(..., shell=True)`.
- Tar/zip extraction without `tarfile.data_filter` (CVE-2007-4559).
- `lxml.etree.parse()` without `resolve_entities=False`, `huge_tree=False` (XXE / billion laughs).
- Merge a PR before Codex review pass.
- Commit without `/security-check` pass.
- Commit message with AI/Claude attribution.

## Code rules (project-specific)

These extend global rules in `~/.claude/CLAUDE.md`:

- Type hints on every function signature, including return type.
- Max 4 params per function.
- Max 20 lines per function (extract if longer).
- Specific exceptions only — `LovsporError` hierarchy. Never `except Exception:`.
- Comments explain WHY only when non-obvious. No WHAT comments.
- Pydantic models for any data crossing module boundaries.
- `pathlib.Path`, never string paths.
- `httpx` async only if there's a real need; sync is fine for sequential downloads.

## Workflow commands

```bash
# Setup (once after clone)
./scripts/bootstrap.sh

# Daily
uv run pytest                         # all tests
uv run pytest tests/unit/             # fast loop
uv run ruff check && uv run ruff format --check
uv run mypy src/
# Mutation testing is Codex's job on PR review, not Claude's pre-push step.
# uv run mutmut run                   # only if explicitly asked
```

## Definition of done (per PR)

- All commits atomic and bisectable
- All tests pass: unit + integration
- Coverage ≥ 90% on changed files
- Mutation score reviewed by Codex (not by Claude pre-push)
- `/security-check` clean
- PR description filled with Codex prompt
- Codex review pass
- No commit to main without PR
