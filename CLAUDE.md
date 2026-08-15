# CLAUDE.md — lovspor

Contract for how Claude works in this project. Read at the start of every session. Project rules below extend the global rules in `~/.claude/CLAUDE.md`.

## Project context

`lovspor` is the **engine**. It downloads Lovdata public-data tarballs, normalizes XML, deterministically renders Markdown, hashes XML to detect changes, and commits only changed laws to the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) corpus repo.

This repo contains **only the engine**. Legal text never lives here. The corpus lives in `lovverk`.

## Cross-repo doc currency
- lovspor and lovverk are sibling repos that reference each other's facts.
- When changing one repo's docs, check whether the other references the same facts and update both in lockstep.

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
1. Feature complete on branch → push → open PR (`gh pr create`), fill the PR template.
2. From PR open, **GitHub Actions owns the handoff** (`docs/agentic-ci.md`): fast-ci,
   the existing Test matrix, an independent Codex test author on the self-hosted runner,
   and the PR-scoped mutation gate run automatically.
3. Do not manually invoke Codex for PR testing. Do not wait locally for mutation results.
4. The pipeline ends READY TO MERGE (all checks green) or BLOCKED with a label:
   `needs-human:mutation` or `needs-implementation-fix` + a short PR comment.
5. On `needs-implementation-fix`: read the failing test, fix the implementation on the
   same branch; change the test only if it provably contradicts the spec.
6. A BLOCKED result is a required escalation, not permission to guess.
7. **Only the user merges.** Never merge yourself.
8. After merge: provide deploy + log commands.

### Finding = issue (mandatory)

Any defect found in the pipeline, tooling, or process while working —
an agent misbehaving, a gate not firing, a workflow gap, a contract not
honored — is filed as a GitHub issue IMMEDIATELY, in the same session
it was found: `gh issue create`, evidence inline (run id, commit, exact
log line), label `agentic-ci` for pipeline defects. A finding reported
only in chat is a finding lost. The fix PR references the issue; the
issue is closed by the fix, never by forgetting. This is separate from
fixing: file first, even when the workaround is already pushed.
Origin (2026-08-11, PR #64): two pipeline defects — codex-tests pushing
unformatted commits (#66) and the remediation path not firing on a
mutation failure (#67) — existed only in a chat transcript until the
owner mandated this rule.

## Testing strategy

- `tests/unit/` — fast, isolated, one module at a time. Every public function covered.
- `tests/integration/` — pipeline end-to-end on real fixture tarballs and XML samples.
- `tests/fixtures/` — real XML/JSON samples from Lovdata, captured once, committed, never regenerated unless source schema changes.
- Mutation testing: **the CI `mutation` job runs `./scripts/mutmut-pr.sh` on every PR** (no LLM involved) and publishes `mutation-result.json`; surviving mutants route to the Codex remediation workflow (max 2 cycles, then `needs-human:mutation`). The script scopes mutation to the `src/lovspor/` files the PR changed (full-repo runs don't terminate in reviewable time — issue #4, decisions.md §9c) and prints an explicit `mutation not applicable` notice for PRs with no engine-logic changes; that notice is a valid review outcome, not a skipped step. Each file is mutated against **its own test module**, falling back to `tests/unit/` when there is none: the configured runner used to run all of `tests/unit/` per mutant (~32 s each), which is why reviews kept abandoning the run part-way. The trade is that a mutant only another module's test would kill reads as survived — the score errs pessimistic, never optimistic. **Suspicious mutants were not killed either** — never fold them into the killed count. Each file's run is bounded by a wall-clock budget (`MUTMUT_PR_FILE_BUDGET_SECONDS`, default 1200 s — issue #102, born of a PR touching `mcp.py` grinding 3 h+ toward a 6 h job kill with no verdict): on budget the unmeasured mutants surface as `untested`, the gate fails with reason `budget_exceeded`, and the PR routes straight to `needs-human:mutation` — Codex remediation is skipped, because tests cannot kill a mutant that was never measured. Full-repo `uv run mutmut run` only when explicitly asked (baseline runs, §9a). Claude does not run mutmut locally before opening a PR — it would be duplicate work and slow the push cycle (`./scripts/mutmut-pr.sh --list` to preview scope is fine). If a Codex round flags a critical-path survivor, fix it on the same branch and ask Codex to re-run. The tool is **mutmut 2.5.1 (pinned)**; the old blanket "mutmut 2.x only" ban is lifted — the 3.x bugs it cited look stale on review — and the 3.x migration is its own change (issue #91), never a side effect. Until it lands: **no PEP 695 type parameter syntax** (`def foo[T](...)`) — the pinned 2.x parser crashes on it; use `TypeVar` from `typing`; ruff `UP047` stays disabled.
- HTTP transport mocked with `pytest-httpx` only. Logic is never mocked.

## Forbidden

- Commit raw XML or HTML from Lovdata to this repo.
- Scrape lovdata.no website HTML.
- Mock business logic in tests (mocking transport via `pytest-httpx` is fine).
- `subprocess.run(..., shell=True)`.
- Tar/zip extraction without `tarfile.data_filter` (CVE-2007-4559).
- `lxml.etree.parse()` without `resolve_entities=False`, `huge_tree=False` (XXE / billion laughs).
- Merge a PR before the PR Pipeline (fast-ci, codex-tests, mutation) is green or its BLOCKED state is explicitly resolved by the user.
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
# Mutation testing is the CI mutation job's work, not Claude's pre-push step.
# ./scripts/mutmut-pr.sh              # PR-scoped run (CI); --list previews scope
# uv run mutmut run                   # full-repo baseline — only if explicitly asked
```

## Definition of done (per PR)

- All commits atomic and bisectable
- All tests pass: unit + integration
- Coverage ≥ 90% on changed files
- `/security-check` clean
- PR description filled per template
- PR Pipeline green: fast-ci, codex-tests, mutation (or BLOCKED explicitly resolved by the user)
- No commit to main without PR
