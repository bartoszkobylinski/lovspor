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

Run `uv run mutmut run` and report the score. Investigate any survived mutants in critical paths:
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
