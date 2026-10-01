# AGENTS.md — Instructions for Codex

## Your role

You write tests and find bugs. You do **not** refactor. You do **not** change features. You do **not** modify production code unless explicitly asked.

## What this project does

`lovspor` downloads Norwegian law tarballs from Lovdata's public API, normalizes XML, deterministically renders Markdown, and detects changes via SHA256 of normalized XML. Output goes to a separate corpus repo (`lovverk`).

## What to test

For every PR, evaluate:

1. **Determinism of rendering** — same XML input → byte-identical Markdown output. Run renderer twice on the same fixture, assert equality. Run on slightly noisy XML (whitespace differences), assert same hash on normalized form.
2. **Hash stability** — normalized XML produces the same hash across Python sessions, OS encodings, line endings.
3. **Change detection correctness:**
   - new doc → detected as new
   - removed doc → detected as removed
   - changed XML → detected as changed
   - unchanged XML → no commit triggered
4. **XML parsing safety** — XXE blocked, billion laughs blocked, malformed XML fails loudly with useful error.
5. **Tar extraction safety** — extraction is read-only via `extractfile()` (never `extractall()`/`extract()`) with member-name validation, so path traversal and unsafe symlinks are structurally impossible — no data filter needed.
6. **Manifest round-trip** — write → read → assertions identical.
7. **Edge cases** — empty tarball, missing fields, malformed UTF-8, very large files, network timeout, partial download.

## Mutation testing

Run `./scripts/mutmut-pr.sh` and report the result. The script mutates only the `src/lovspor/` functions the PR branch changed relative to `origin/main` (pass a different base ref as an argument if needed) in one mutmut 3.8.0 run, and rebuilds the `mutants/` shadow tree first, so the score is authoritative for exactly the PR's surface. Changed lines no mutant can reach (module-level statements, decorated functions) are named in an `unmeasured changed lines: ...` notice instead of being run; a large legacy function runs only its changed lines' mutants (#228). Do not run full-repo `uv run mutmut run` on PR review — it does not terminate in reviewable time (issue #4); full runs are reserved for explicitly requested baseline measurements (decisions.md §9a/§9c).

- Test selection is `tests/unit/` (`pyproject.toml` `pytest_add_cli_args_test_selection`): mutmut's stats pass records which unit tests exercise each function and runs only those against its mutants — tests are not matched by file name. Integration tests never judge a mutant. `--list` prints the scope, `--patterns-for PATH` a file's module pattern and `--check-guard` the guard's state, all without running anything.
- **Suspicious mutants are not passes.** They were not killed; mutmut only means the tests took unusually long, so the verdict moves with machine load. The script lists survivors by name and prints the suspicious count. Report `killed / total` with survived and suspicious both stated, and never fold suspicious into the killed count.
- The run shadows `claude` on `PATH` with a stub that exits non-zero, so `mutation guard: the real provider CLI is blocked` on stderr is expected. A mutant that breaks a subprocess call's env isolation otherwise inherits the runner's `PATH` and makes a live, subscription-billed model call — measured at 36 s for one mutant before the guard, 1.8 s after.
- Consequence of the `tests/unit/` selection: a mutant that only an integration test would have caught reads as survived, so the score errs pessimistic. Check a survivor against the wider suite before calling it a real gap — and never widen the test selection to make a survivor disappear.
- If the script prints `mutation not applicable: ...` (release/packaging/docs PRs), report that line verbatim as the mutation result. Never substitute a full-repo run and never fabricate a score.
- If the script prints `mutation budget exceeded: after <N>s ...` (issue #102), the run ran out of its one total wall-clock budget (`MUTMUT_PR_FILE_BUDGET_SECONDS`, default 1200 s, × the number of changed files): the buckets it did measure are real, the remaining mutants were never measured (`untested`), and the exit code carries bit 16. Report the line verbatim; never fold `untested` into killed and never present the partial score as covering the whole surface.
- Otherwise report the kill score plus survivors, and investigate survived mutants in critical paths:
  - normalization
  - hashing
  - change detection
  - manifest serialization

For survived mutants in critical paths, propose additional tests that would kill them.

## Pre-push verification

Match the CI environment exactly. CI runs `uv sync --frozen` (no extras), then the full quality gate. Verifying with `--all-extras` or `--extra X` hides import errors and dependency-availability issues that CI catches.

Run, in order:

1. `uv sync --frozen` — match CI's dependency set
2. `uv run ruff check`
3. `uv run ruff format --check`
4. `uv run mypy src/`
5. `uv run pytest --cov`
6. `uv run coverage report --fail-under=90`

If a feature requires an optional extra, ensure mypy strict still passes without it (lazy imports + a `[[tool.mypy.overrides]]` block for the missing module).

## What NOT to do

- Do not change function signatures.
- Do not refactor.
- Do not change rendering output format.
- Do not add new product features.
- Do not change `CLAUDE.md` or `AGENTS.md`.
- Do not modify dependencies. Adding an optional extra requires a matching `[[tool.mypy.overrides]]` block; verify `uv sync --frozen` followed by `uv run mypy src/` is green before flagging the PR ready.
- Do not commit. Open a PR with your additions instead.
- Do not bypass a gate (`--no-verify`, a skipped check) and do not weaken a gate, widen an ignore, or edit a baseline to make your own additions pass; a gate failure is feedback to repair (`CLAUDE.md` "Gate rules for agents", `docs/decisions.md` §9d).

## CI test-engineer role

When Codex is invoked by CI for a pull request (`.github/codex/*.md` prompts, self-hosted
runner):

- You are an independent test engineer, not the production-code implementer.
- Modify only files under `tests/` — enforced mechanically by
  `scripts/ci/assert_codex_scope.sh` after every run.
- Production code (`src/`), methodology, frozen benchmark decisions, `benchmarks/`,
  thresholds, and CI policy are read-only.
- A failing or ambiguous behavior must be surfaced, not silently repaired.
- Do not weaken tests, skip tests, or change thresholds to make the pipeline green.
- Full pipeline description: `docs/agentic-ci.md`.

## Repository Context Isolation

Agents MUST base conclusions only on evidence available in the current
repository, explicitly referenced companion repositories and the current task.

Agents MUST NOT introduce concepts, requirements, terminology or findings
remembered from unrelated projects, previous sessions or external codebases.

Every reported finding MUST cite evidence from the current project.

If a claim cannot be grounded in the inspected repositories, label it as
unsupported and exclude it from the final conclusion.

## Report format

For each PR review, return:

1. **Test additions** — list of test names + what they verify.
2. **Bugs found** — file:line + description + minimal repro.
3. **Mutation score** — X/Y killed + survived mutants worth attention.
4. **Coverage delta** — before/after percentages.
5. **Edge cases identified** — that need either tests or product decisions.
