# Agentic CI

PR-driven pipeline: Claude/human implements → small PR → GitHub Actions drives the PR to
**READY TO MERGE** or an explicit **BLOCKED**. Merge is always human. Source spec:
`docs/LOVSPOR_AGENTIC_CI_IMPLEMENTATION.md`.

```text
PR opened/synchronize
  ├─ fast-ci        (ubuntu, 3.12: ruff, mypy, unit tests)
  ├─ Test           (existing workflow, matrix 3.12–3.14 — unchanged)
  ├─ codex-tests    (self-hosted `codex` runner: independent test author)
  │     └─ pushes `[agent:codex-tests]` → fresh synchronize run, old run cancelled
  └─ mutation       (ubuntu, no LLM: scripts/mutmut-pr.sh → mutation-result.json)
        └─ gate FAIL → Mutation Remediation workflow (Codex, tests only, max 2 cycles)
              └─ still failing / non-killable → BLOCKED + needs-human:mutation
```

## Roles

- **Claude Code local / human** — production code, small PRs, fast local checks.
  Never invokes Codex manually for PR testing; never waits locally for mutation results.
- **Codex CI** — independent test engineer. May touch `tests/` only, enforced
  mechanically by `scripts/ci/assert_codex_scope.sh` after every run (the prompt is not
  the boundary). Prompts: `.github/codex/pr-tests.md`, `.github/codex/mutation-remediation.md`.
  Two ChatGPT accounts with usage-based failover (`scripts/ci/codex_account_failover.py`,
  threshold 95%); when BOTH are rate-limited (exit 75, and only then) the same prompt goes
  to the tertiary author: `scripts/ci/claude_test_author.sh` runs a **non-Fable** Claude
  model headless (`CLAUDE_TESTS_MODEL`, default `claude-sonnet-5`; Fable is refused because
  it is the implementation author) on a subscription OAuth token
  (`CLAUDE_TESTS_OAUTH_TOKEN`, `sk-ant-oat…` only — API keys are refused and stripped so
  nothing bills per token). The commit marker names the actual author
  (`[agent:claude-tests]` / `[agent:claude-mutation]`); the scope guard applies unchanged.
- **Human** — merge, methodology changes, frozen benchmark decisions, every BLOCKED.

## Mutation policy (unchanged, now automated)

`scripts/mutmut-pr.sh <base-sha>` runs mutmut 3.7.0 for functions changed by the PR.
The script rebuilds the shadow tree for each run, then uses mutmut's warm baseline and
parallel workers. Module-level or otherwise unsafe-to-narrow changes fall back to the
affected module instead of being exempted.
No numeric score threshold exists or was added. The gate in `mutation-result.json`:

- `mutation not applicable` (no `src/lovspor/` changes) → **PASS**, reason `not_applicable`
- everything killed → **PASS**
- every surviving mutant registered in `mutation-equivalents.toml` → **PASS**, reason
  `equivalent_mutants_only` (issue #122). An entry is keyed by file + the mutation's `-`/`+`
  lines, never by mutant id, and needs a written justification or it is refused and reported.
  A survivor whose mutation diff could not be recovered never matches — the gate fails closed.
  An entry that argues from a **dependency's** behaviour rather than Python's own semantics
  names the test pinning that behaviour in `assumption_test` (issue #132); dependencies move,
  and a justification that quietly became false would keep waiving a survivor that is by then
  a real defect.
  Counts, score and the survivor list are unaffected; only the verdict moves.
- survived / timed-out / suspicious / uncovered mutants each → **FAIL** (reasons
  `surviving_mutants`, `timeout_mutants`, `suspicious_mutants`, `uncovered_mutants`).
  `mutmut-pr.sh` preserves the pipeline's existing 2/4/8 compatibility bitfield for
  aggregate survivor/timeout/suspicious state; these are not mutmut 3 process exit codes.
  The score counts only 🎉 killed, so other outcomes never inflate it. Gate FAIL → Codex remediation (≤ 2 `[agent:codex-mutation]`
  cycles) → then `needs-human:mutation` + BLOCKED. This automates the previous manual
  practice of Codex investigating survivors and proposing killer tests.

`mutation-result.json` (schema_version 1) is bound to the exact PR head SHA; a stale
result is ignored by the remediation workflow. Artifact `mutation-result-<SHA>` (JSON +
raw log) uploads on PASS and FAIL.

Each survivor carries what it changed, not just its id (issue #119). Mutmut 3 names a
mutant after the rewritten function variant and has no source position for it, so
`scripts/ci/mutation_survivors.py` reads the shadow tree while it is still on disk and
records `file`, `symbol`, `symbol_line` and the unified `diff`; `line` and `operator`
stay null by construction. Without the shadow tree the record degrades to what the id
proves and says so in `detail_source` — never a bare null that reads like missing data.
Ids renumber whenever the file changes upstream of the mutant, so a cross-round
comparison quotes the `diff`. The job summary lists the first ten survivors as
`id — file:line — replacement`, so a red gate is triageable without downloading anything.

## Anti-loop invariants

- `concurrency: pr-<PR#>` + `cancel-in-progress` — stale runs die on new SHA.
- **One Codex run at a time is the runner's job, not a concurrency group's.** There is one
  `codex`-labelled runner and one Codex subscription behind it, and GitHub queues jobs for a
  busy runner in a real FIFO. `codex-tests` and `remediate` therefore declare no shared group;
  the box-wide `flock` in the agent step still guards the other repositories' agents on the
  same machine.

  A repo-wide group used to do this and could not: GitHub keeps only ONE *pending* job per
  concurrency group, so a newer pending job **evicted** the older one, and
  `cancel-in-progress: false` did not prevent it (it governs *running* jobs). Signature of an
  evicted job, in case one ever reappears: conclusion `cancelled`, a couple of seconds,
  **zero steps** — it never started, so the escalation path that turns a real Codex failure
  into a triageable comment never ran either, and the PR went red with no comment and no
  label. Observed 2026-08-18/19 on PRs #121, #124, #125, #134 and #136 (issue #139); the
  recovery then was `gh run rerun <run-id> --failed`, one PR at a time.

  Remediation keeps a group **per head branch** (`mutation-remediation-<branch>`), where
  superseding an older head is the wanted behaviour and cannot starve another PR.

- Both agent jobs carry `timeout-minutes: 60` (issue #101). A hung job holds the runner
  against every later PR, and GitHub's default ceiling is 6 h; the box lock alone waits 20
  minutes before giving up, so the job ceiling sits above that and well under the default.
- `[agent:(codex|claude)-(tests|mutation)]` HEAD markers — an agent-authored HEAD is never
  reprocessed, whichever author produced it; all agent work is squashed into one marker
  commit per run.
- Remediation cycle count = `[agent:codex-mutation]` + `[agent:claude-mutation]` commits in
  the trailing agent-authored block; any human push resets it. The fallback author gets no
  extra cycles.
- Codex jobs run only for same-repo PRs (`head.repo.full_name == repository`); the
  fork-PR approval policy is set to "all outside collaborators".

## Failure escalation

- Codex's correct new test exposes a production bug → Codex reports it, does NOT fix
  production code. The `codex-tests` job itself applies `needs-implementation-fix`,
  comments on the PR with the failing tests, and preserves the test patch + pytest log
  as artifact `codex-tests-<head-sha>` (issue #95 — before this, a failing round died
  as a bare red check and the tests survived only in the run log); human relays to
  local Claude.
- Ambiguous/equivalent mutants → `needs-human:mutation`. BLOCKED is a valid end state,
  never to be silenced by weakening tests or thresholds.
- Codex output is normalized (`ruff format` + `ruff check` on `tests/`) before commit in
  both workflows, so the agent can never trip the pipeline's own lint gate (issue #66).
- If the PR branch advances while remediation is running, its rejected push is abandoned
  as superseded — the new head's own pipeline owns mutation from there. Any other
  remediation failure escalates itself: `needs-human:mutation` + a comment linking the
  failed run. A remediation run never dies silently (issue #67).

**Every failure escalates, not only a failing test.** Twice on 2026-08-24 a job
ended red and silent because the failure happened outside the one step the
escalation was watching: an unfixable lint (`RUF007`) in agent output failed the
normalize step before the tests ran (issue #160), and a hung Codex CLI was killed
by the job's own 60-minute ceiling, which *cancels* rather than fails and so
skipped the reporting steps (issue #157).

Two rules follow, and they are pinned by tests:

- The agent step carries its own `timeout-minutes` (45), strictly below the job's
  (60). A hang then fails the step, and the escalation still runs. A ceiling only
  on the job buys silence.
- `codex-tests` escalates on any failure. A failing Codex test labels
  `needs-implementation-fix` (the implementation is the suspect); anything else
  labels **`needs-human:pipeline`** and says so in the comment, because a pipeline
  defect is not evidence about the code under review. The agent's work for that
  round is preserved as artifact `agent-work-<sha>` instead of being discarded.
- Remediation escalates on `failure() || cancelled()`, since a job killed by its
  ceiling is not a failed job.
## Infrastructure

- Self-hosted runner: label `codex`, dedicated VM with no production data or secrets.
  Codex CLI authenticates via ChatGPT-managed `auth.json` in a persistent
  `CODEX_HOME=/home/runner/.codex-lovspor` (`cli_auth_credentials_store = "file"`).
  Never `OPENAI_API_KEY` on this runner. Auth self-refreshes; if it dies, reseed by
  running `codex login` in a scratch `CODEX_HOME` on a trusted machine, copying
  `auth.json` to the runner (`chmod 600`, owner `runner`), and deleting the scratch copy
  so exactly one copy of the refresh token exists.
- Push token: fine-grained PAT (this repo only; Contents RW, Pull requests RW) as secret
  `LOVSPOR_CI_PUSH_TOKEN`. Codex pushes use it so pushed commits retrigger the pipeline
  (`GITHUB_TOKEN` pushes would not).
- Mutation and fast-ci run on GitHub-hosted runners — free for this public repo, no LLM
  auth anywhere near them.
