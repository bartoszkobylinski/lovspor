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
- **Human** — merge, methodology changes, frozen benchmark decisions, every BLOCKED.

## Mutation policy (unchanged, now automated)

`scripts/mutmut-pr.sh <base-sha>` runs PR-scoped mutmut 2.5.1 exactly as before.
No numeric score threshold exists or was added. The gate in `mutation-result.json`:

- `mutation not applicable` (no `src/lovspor/` changes) → **PASS**, reason `not_applicable`
- zero survivors → **PASS**
- any survivor → **FAIL** → Codex remediation (≤ 2 `[agent:codex-mutation]` cycles) →
  then `needs-human:mutation` + BLOCKED. This automates the previous manual practice of
  Codex investigating survivors and proposing killer tests.

`mutation-result.json` (schema_version 1) is bound to the exact PR head SHA; a stale
result is ignored by the remediation workflow. Artifact `mutation-result-<SHA>` (JSON +
raw log) uploads on PASS and FAIL.

## Anti-loop invariants

- `concurrency: pr-<PR#>` + `cancel-in-progress` — stale runs die on new SHA.
- `[agent:codex-tests]` / `[agent:codex-mutation]` HEAD markers — Codex never reprocesses
  its own commits; all Codex work is squashed into one marker commit per run.
- Remediation cycle count = `[agent:codex-mutation]` commits in the trailing
  agent-authored block; any human/Claude push resets it.
- Codex jobs run only for same-repo PRs (`head.repo.full_name == repository`); the
  fork-PR approval policy is set to "all outside collaborators".

## Failure escalation

- Codex's correct new test exposes a production bug → Codex reports it, does NOT fix
  production code. Label `needs-implementation-fix`; human relays to local Claude.
- Ambiguous/equivalent mutants → `needs-human:mutation`. BLOCKED is a valid end state,
  never to be silenced by weakening tests or thresholds.

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
