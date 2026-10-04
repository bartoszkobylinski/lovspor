---
name: mutation-testing
description: Load when triaging surviving mutants, reading a red or BLOCKED mutation gate (mutation-result.json, needs-human:mutation), or registering a provably-equivalent mutant in mutation-equivalents/.
---

# Mutation testing in lovspor

The short rules live in `CLAUDE.md` ("Testing strategy"). This is the detail behind
them, written against `scripts/mutmut-pr.sh`, `scripts/ci/mutation_scope.py`,
`scripts/ci/mutation_legacy.py`, `scripts/ci/mutation_to_json.py` and `pyproject.toml`
`[tool.mutmut]`. Longer reference: `docs/agentic-ci.md` "Mutation policy" and
`docs/decisions.md` §9a/§9c. When this file and the code disagree, the code wins —
fix this file.

## Who runs it

- The CI `mutation` job (`.github/workflows/pr-pipeline.yml`) runs
  `scripts/mutmut-pr.sh <base-sha>` on every PR. No LLM is involved.
- Claude does not run mutmut locally before opening a PR: it duplicates the CI job and
  slows the push cycle. `./scripts/mutmut-pr.sh --list` (prints the scope, runs nothing)
  is fine.
- Full-repo `uv run mutmut run` only when explicitly asked (baseline runs, §9a): it does
  not terminate in reviewable time (issue #4, §9c). A scoped score is authoritative only
  for the PR's surface and is never compared against the §9a baseline.

## What gets mutated

One mutmut 3.8.0 run, scoped to **functions**, not files:

- `mutation_scope.py` maps the PR's changed post-image lines (`base...HEAD`,
  `--diff-filter=ACMR`, `src/lovspor/**/*.py`) to their enclosing functions and emits
  one mutmut name pattern per changed function. A file that does not parse falls back to
  its whole-module pattern.
- Changed lines no mutant can reach — module-level and class-body statements, decorated
  functions (every typer command), decorated class headers — are not run. They are
  reported as `unmeasured changed lines: <file>:<lines> (<region>)` and land in
  `mutation-result.json` as `unmeasured_changed_lines` (#289, #292, #419, #420).
- No changed `src/lovspor/` file, or no mutatable function changed →
  `mutation not applicable: ...`, exit 0, gate PASS with reason `not_applicable`. Report
  that line verbatim; never substitute a full-repo run and never invent a score.
- **Legacy carve-out (#228):** a changed function with a `rule = "function-lines"` entry
  in `scripts/quality/ratchet-baseline.toml` runs only the mutants on its changed lines.
  `mutation_legacy.py generate` writes the mutants without running them, `select` picks
  those on changed lines via `mutmut show`; a mutant whose line cannot be resolved runs.
  The rest are reported as `(legacy remainder <function>: N of M mutants not run, #228)`
  — never counted killed, never in the score.

## Which tests judge a mutant

- `pyproject.toml` `[tool.mutmut]` sets `pytest_add_cli_args_test_selection =
  ["tests/unit/"]`. Mutmut's stats pass records which unit tests exercise each function
  and runs only those tests against that function's mutants. Tests are **not** chosen by
  file name: any test under `tests/unit/` that calls the function counts, wherever it
  lives. Integration tests never run against mutants — a mutant only an integration test
  would catch reads as survived.
- So a killing test belongs in `tests/unit/` and must actually exercise the mutated
  function. A survivor whose function has no covering unit test shows up as `no tests`
  (gate reason `uncovered_mutants`).
- Per-mutant time limit: `(estimated_time + timeout_constant) * timeout_multiplier`, with
  `timeout_constant = 7.0`, `timeout_multiplier = 15.0` (#423).

## The budget

- One total wall-clock budget for the whole `mutmut run`:
  `MUTMUT_PR_FILE_BUDGET_SECONDS` (default 1200) × the number of changed `.py` files
  under `src/lovspor/` (`total_budget=$((file_budget * count))` in `mutmut-pr.sh`). It is
  not a per-file budget: one slow file can spend the whole allowance. The variable keeps
  its old name.
- Enforced with `timeout(1)` around `mutmut run`; without `timeout` on `PATH` the script
  warns that the budget is unenforced. The legacy `generate`/`select` steps run before it,
  outside the budget. The CI job's `timeout-minutes: 60` is the backstop.
- Origin: issue #102, a PR touching `mcp.py` that ground 3 h+ toward a 6 h job kill with
  no verdict.
- On budget: `mutation budget exceeded: after <N>s — unmeasured mutants are untested`,
  exit bit 16, gate reason `budget_exceeded` (it outranks every per-bucket reason), and the
  PR routes straight to `needs-human:mutation` — Codex remediation is skipped, because
  tests cannot kill a mutant that was never measured. Report the line verbatim; the
  measured buckets are real, but never fold the unmeasured mutants into killed and never
  present the partial score as covering the whole surface.

## Reading the verdict

Gate reasons in `mutation-result.json`, in the order `mutation_to_json.py` checks them:
`tool_failed`, `not_applicable` (PASS), `budget_exceeded`, `baseline_tests_failed`,
`run_incomplete`, `unmeasured_mutants`, `surviving_mutants`, `timeout_mutants`,
`suspicious_mutants`, `uncovered_mutants`, then PASS as `equivalent_mutants_only` or `ok`.

- **Suspicious mutants were not killed.** Mutmut only means the tests took unusually
  long, so the verdict moves with machine load. Report `killed / total` with survived and
  suspicious both stated; never fold suspicious into killed.
- **Signal-killed mutants were not killed either.** Mutmut files them as "segfault", a
  bucket its progress line never prints but still counts in `done`; the script prints the
  shortfall as `unmeasured:` and the gate fails as `unmeasured_mutants` → straight to
  `needs-human:mutation`, no Codex remediation (#283).
- `error: ... no score for this PR — do not report one` (exit 3) means the tool failed:
  there is no score. Do not report one.
- The run shadows `claude` on `PATH` with a stub that exits non-zero, so
  `mutation guard: the real provider CLI is blocked` on stderr is expected.
- Survivor records carry `file`, `symbol` and the unified `diff` (#119). Mutant ids
  renumber whenever the file changes upstream of the mutant: compare survivors across
  rounds by their diff, never by id.

## Handling a survivor

- Survived / timed-out / suspicious / uncovered → gate FAIL → Codex remediation (at most
  2 `[agent:codex-mutation]` cycles) → then `needs-human:mutation` + BLOCKED.
- A critical-path survivor (normalization, hashing, change detection, manifest
  serialization) is fixed on the same branch. Pushing re-runs the pipeline; do not invoke
  Codex by hand (CLAUDE.md PR workflow, step 3).
- Kill it with a unit test that exercises the function. Never widen the test selection or
  the runner to make a survivor disappear.

## The equivalents register (`mutation-equivalents/`)

- Only for a survivor that is **provably equivalent**: the mutated code computes exactly
  what the original computes, so no test can ever kill it.
- One TOML file per entry, holding exactly one `[[equivalent]]` table, at
  `mutation-equivalents/<module dotted, under src/lovspor>/<symbol>-<hash8>.toml`
  (issue #516: a single file made every two registering PRs conflict). A file with more
  or fewer than one entry is refused, and so is a revived root `mutation-equivalents.toml`.
  Rules and naming: `mutation-equivalents/README.md`.
- An entry is keyed by file + the mutation's `-`/`+` lines, never by mutant id, and needs
  a written justification or it is refused (issue #122). A survivor whose diff could not
  be recovered never matches — the gate fails closed.
- A justification that argues from a **dependency** rather than from Python's own
  semantics also names the test pinning that behaviour in `assumption_test` (issue #132;
  see `tests/unit/test_mutation_equivalents_assumptions.py`) — a dependency bump must turn
  a stale justification into a red test, not into a silent waiver.
- When every survivor is registered the gate passes with reason `equivalent_mutants_only`
  and nothing else moves: the mutant is still counted, still scored against, still
  listed.
- It is not a way to retire an inconvenient survivor. A new or changed file there is
  reviewed as a test deletion.
- `uv run python scripts/ci/mutation_to_json.py --check-equivalents` checks every entry
  parses, is one diff (a `\n` quoted inside a TOML basic string breaks the line and is
  refused), and still names a line that exists in its file — a stale entry is reported and
  fails the check (#366).

## Tool version

- mutmut **3.8.0**, pinned at `pyproject.toml:84`. The 3.x migration landed (issue #91),
  so the old "2.5.1 pinned / no PEP 695 / `UP047` disabled" rules are gone: `UP047` is
  not in the ruff ignore list.
- PEP 695 generics now exist in `src/` (`temporal.py` `_relabel`, `temporal_events.py`
  `_narrowed` / `_payload`).
