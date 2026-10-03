# CLAUDE.md — lovspor

Contract for how Claude works in this project. Read at the start of every session. Project rules below extend the global rules in `~/.claude/CLAUDE.md`.

## Project context

`lovspor` is the **engine**. It downloads Lovdata public-data tarballs, normalizes XML, deterministically renders Markdown, hashes XML to detect changes, and commits only changed laws to the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) corpus repo.

This repo contains **only the engine**. Legal text never lives here. The corpus lives in `lovverk`.

## Cross-repo doc currency
- lovspor and lovverk are sibling repos that reference each other's facts.
- When changing one repo's docs, check whether the other references the same facts and update both in lockstep.

## Critical constraints (non-negotiable)

- **Source (Lovdata datasets — `lover/`, `forskrifter/`):** only `https://api.lovdata.no/v1/publicData/`. Never scrape lovdata.no HTML — that holds for every workstream, local law included.
- **License (Lovdata datasets):** every output Markdown file carries NLOD 2.0 attribution in YAML front matter.
- **Local regulations (`lokale-forskrifter/`) are a separate dataset with their own rules** (owner decision 2026-10-03, ADR-0016, lovspor-notebook #139). Lovdata publicData does not serve them (Lovtidend avd. II), so their only sources are the local-law observatory (ADR-0010: municipal and fylkeskommune websites, per-source access-policy check) and documents obtained from the authority under offentleglova (#509). They are published on the basis of **åndsverkloven § 14**, never NLOD — a local file that mentions NLOD is a defect. Only artifacts classified as enacted regulations are published, after human review, labelled observed-not-asserted (`asserted: false`) with their ObservedAt. Personal data is minimised under personopplysningsloven.
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
1. `scripts/quality/verify-fast.sh` — green (gitleaks staged scan, `ruff check`, `ruff format --check`, `mypy src/`, the size and complexity ratchets, the architecture boundaries, the release contracts in `tests/unit/test_release_contracts.py`)
2. Invoke `/security-check` — clean
3. Then `git commit`

Before every push: `scripts/quality/verify-deep.sh` — green (the fast gate, then the fail-closed security scan, then `uv run pytest tests/unit/ -q -m "not network"` — network-marked tests answer for a third-party credential, not for the diff, #359).

`uv run pre-commit install` installs both hooks: pre-commit runs `verify-fast.sh`, pre-push runs `verify-deep.sh` (a clone whose hooks predate #323 re-runs it to add pre-push). Step 2 is manual until the skill auto-triggers. CI stays authoritative: fast-ci and the Test matrix run lint, mypy and the unit suite on every PR, whatever ran locally. Measured costs and the stage split: `docs/decisions.md` §9d.

### Gate rules for agents (mandatory)
- A gate failure is feedback to repair, not an obstacle to route around.
- `git commit --no-verify` and `git push --no-verify` are forbidden for agent-authored work. The only exception is an operator emergency: the owner may bypass a local hook when the gate itself is broken and a fix cannot wait for its repair; CI stays authoritative, so the bypass skips local feedback, never the merge gate.
- Run `scripts/quality/verify-fast.sh` before reporting work complete, even when no hook fired (hooks not installed, a fresh worktree).
- Never weaken a gate, widen an ignore, or edit a baseline to make your own change pass, unless changing that gate's policy is itself the task.
- A new entry in `scripts/quality/ratchet-baseline.toml`, or a raised value in an existing one, is an exception the owner approves: argue for it in the PR description. Never add or raise one to get your own change past the ratchet — split the function or the file instead.

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

## Owner decisions carry observed state

A request for an owner decision names the state it was read from, and that state
is **read at the time of asking** — not recalled from a note, a handoff, or the
start of the session. Paste the reading.

Origin (2026-08-25, #151): a recon note from 2026-08-20 listed Bergen and
Sandefjord as unregistered. Five days later that was false — both were
registered, active, and had been crawled; Bergen alone had 7,840 artifacts and
had hit the bootstrap round cap. The decision request repeated the note as
current fact, so the owner was asked to decide whether to register two
municipalities that were already running. Correct reasoning on a stale premise
reads exactly like correct reasoning, which is what makes it expensive.

The same session produced the code-side twin: a comment asserting that
`resolve_base_href=False` stopped a page's `<base href>` from moving discovery's
proposals. It did not, and only a test written against the *claim* rather than
the output found it. Both are one rule — **assert the premise, do not carry it**
— and it applies to prose about the world as much as to comments about code.

## A new field is not shipped until an operator can reach it

Any new field on the registry, or on any configuration a human is expected to
set, ships with the supported command that writes it and with a test asking
the operator's question: **can this state be reached using only supported
interfaces?** Coverage of the field, its validation and its consumers does not
answer that question — those tests construct the state directly, which is the
one move the operator cannot make.

Origin (2026-08-25, #182 → #184): `listing_entry_points` shipped with the
model, its domain validator, both consumers, 3,850 unit tests, 113 integration
tests, an import smoke test and a clean security check — and no command could
write it. The only route was hand-editing `sources.json`, which is exactly the
route that skips the validator refusing an entry point outside the cleared
domain. So the feature's activation step went around the guarantee the feature
was built on, and every test passed while it did.

Passing the URL to `discover --entry-point` was not the workaround either: the
HTML reader is gated on registry membership, so an undeclared listing reaches
the XML parser and is declined. When the only path to a new state is one the
supported interface refuses, the feature does not exist yet.

## Testing strategy

- `tests/unit/` — fast, isolated, one module at a time. Every public function covered.
- `tests/integration/` — pipeline end-to-end on real fixture tarballs and XML samples.
- `tests/fixtures/` — real XML/JSON samples from Lovdata, captured once, committed, never regenerated unless source schema changes.
- Mutation testing: **the CI `mutation` job runs `./scripts/mutmut-pr.sh` on every PR** (no LLM; mutmut **3.8.0**, pinned at `pyproject.toml:84`) — one function-scoped run over the `src/lovspor/` functions the PR changed, under one total wall-clock budget, publishing `mutation-result.json`. `mutation not applicable` is a valid outcome, not a skipped step. Survivors route to Codex remediation (max 2 cycles, then `needs-human:mutation`); `budget_exceeded` and `unmeasured_mutants` route straight to `needs-human:mutation`. Claude does not run mutmut locally before opening a PR (`./scripts/mutmut-pr.sh --list` to preview scope is fine); full-repo `uv run mutmut run` only when explicitly asked (§9a). **Suspicious and signal-killed mutants were not killed** — never fold them into the killed count. A survivor is killed by a test or, only if **provably equivalent**, registered in `mutation-equivalents.toml` with a written justification (plus `assumption_test` when it argues from a dependency) — never a waiver for an inconvenient survivor. A critical-path survivor is fixed on the same branch. Triaging survivors, a red mutation gate, or registering an equivalent: load the `mutation-testing` skill (`.claude/skills/mutation-testing/SKILL.md`) first.
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
scripts/quality/verify-fast.sh        # fast gate = pre-commit hook: gitleaks, ruff, format, mypy, ratchets, boundaries, release contracts
scripts/quality/verify-deep.sh        # deep gate = pre-push hook: fast gate + security scan + unit suite
uv run pytest                         # all tests
uv run pytest tests/unit/             # unit suite alone
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
