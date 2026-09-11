# Agentic CI

PR-driven pipeline: Claude/human implements → small PR → GitHub Actions drives the PR to
**READY TO MERGE** or an explicit **BLOCKED**. Merge is always human. Source spec:
`docs/LOVSPOR_AGENTIC_CI_IMPLEMENTATION.md`.

## What READY TO MERGE actually requires

The ruleset `main-branch-protection` (id 20274337, `active`, targeting the default
branch) is what turns a green pipeline into a merge. It carries four rules:

| rule | effect |
| --- | --- |
| `pull_request` | changes reach `main` only through a PR |
| `required_status_checks` | `fast-ci`, `mutation`, `test (3.12)`, `test (3.13)`, `test (3.14)` |
| `non_fast_forward` | no force-push |
| `deletion` | the branch cannot be deleted |

`codex-tests` is deliberately **not** in the required set: a docs-only PR skips it, and
a skipped required check blocks a PR forever. It is still read before merging — it is
the check that says whether the independent author agreed with the implementation.

### The gate was decorative until 2026-08-24 (issue #163)

The ruleset also carried an `update` rule — *restrict updates*, meaning only an actor
with bypass permission may update the ref at all. Merging a PR **is** an update, so it
failed on every merge regardless of the checks, and each merge to `main` was recorded
as a bypass:

```
sha=a32c050 update:fail required_status_checks:pass pull_request:pass   # PR #164
sha=bd627cb update:fail required_status_checks:pass pull_request:pass   # PR #161
sha=776fc9b update:fail required_status_checks:pass pull_request:pass   # PR #159
sha=a63237d update:fail required_status_checks:pass pull_request:pass   # PR #156
```

The same click that waived `update` waived the five required checks with it, so the
invariant this document states — merge only on a green pipeline — rested entirely on
the operator reading the checks first. The `update` rule has been removed; the four
rules above are what remains, and they are now reachable without a bypass.

Read `result` and the per-rule breakdown, never the summary alone:

```bash
sid=$(gh api "repos/bartoszkobylinski/lovspor/rulesets/rule-suites?ref=refs/heads/main&per_page=1" --jq '.[0].id')
gh api "repos/bartoszkobylinski/lovspor/rulesets/rule-suites/$sid" \
  --jq '"result=\(.result)  " + ([.rule_evaluations[]|"\(.rule_type):\(.result)"]|join(" "))'
```

```text
PR opened/synchronize
  ├─ fast-ci        (ubuntu, 3.12: conflict-marker check, ruff, mypy, unit tests)
  ├─ Test           (existing workflow, matrix 3.12–3.14 — unchanged)
  ├─ codex-author   (self-hosted `codex` runner: independent test author ONLY)
  │     └─ hands its work to the verdict lane as artifact `agent-tests-<head-sha>`
  ├─ codex-tests    (ubuntu: applies that patch, lints, runs the suite, verdict, push)
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

### What the scope guard does and does not decide

`assert_codex_scope.sh` answers two questions, and it is worth knowing which.

**It fails the job** when a path outside `tests/` changed, and when a test file that
existed at `BEFORE_SHA` was deleted or renamed away. Rename detection is deliberately
off: moving a human test out from under the name its failures are reported by loses
the same coverage as deleting it.

**It reports, without failing**, when lines were removed from a test file that already
existed — the count and the path go to stdout and to the job summary. Rewriting an
existing assertion is legitimate (on PR #161 the independent author tightened one), so
failing it would paint a good round as an implementation problem. But a tightened
assertion and a gutted one leave the job equally green, and the guard cannot tell them
apart. The report exists so a human reads that diff rather than trusting the check
colour. Issue #162 is where this gap was written down.

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
  busy runner in a real FIFO. `codex-author` and `remediate` therefore declare no shared group;
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

- The self-hosted agent jobs (`codex-author`, `remediate`) carry `timeout-minutes: 60`
  (issue #101); the hosted `codex-tests` lane carries 30. A hung job holds the runner
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

## Two lanes: who runs on the small box (issue #272)

The `codex`-labelled runner is a 1-shared-core / 2 GB container with no swap, shared by
five repositories. It exists for exactly one thing: the Codex session's `auth.json` is a
long-lived ChatGPT credential that cannot be handed to a hosted runner. Everything else
that used to run there was there by accident of job layout.

On 2026-09-10 the box died twice inside one afternoon under a single job (#272): once
inside the Codex step, once 28 minutes into `Run tests on Codex additions`. Load ~18 on
one core, 15 MB free of 2048. Both times the job's own step never completed and never
failed — it was still `in_progress` when GitHub recorded the job as `failure`.

So the lane is split:

| lane | machine | does |
| --- | --- | --- |
| `codex-author` | self-hosted `codex` | checkout, anti-loop, `uv sync`, the Codex/Claude session, scope guard, patch handoff |
| `codex-tests` | `ubuntu-latest` | applies the patch, scope guard, ruff, the **full unit suite**, convergence verdict, escalation, push |
| `remediate` | self-hosted `codex` | artifact gate, cycle count, both BLOCKED paths, the remediation session, scope guard, patch handoff |
| `remediate-verify` | `ubuntu-latest` | applies the patch, scope guard, ruff, the **full unit suite**, push or BLOCKED, escalation |

Invariants, enforced in `tests/unit/test_agentic_ci_workflows.py`:

- no step of `codex-author` runs `pytest tests/unit/`, and `.github/codex/pr-tests.md`
  tells the agent to run only the files it touched, by path — the first death was inside
  the agent step, where the prompt itself used to ask for a whole-suite run;
- the work crosses machines as artifact `agent-tests-<head-sha>`, applied with
  `git apply --index` so the scope guard on the verifier sees exactly what the box saw,
  and an unapplyable patch fails loudly instead of passing a pre-existing suite off as an
  independent round;
- the scope guard runs on **both** lanes;
- `codex-tests` runs on `!cancelled()`, and its first step fails the job when
  `codex-author` did not succeed: a box that dies must not leave a green check claiming
  an independent round happened;
- `codex-author` samples `free -m` and the top RSS processes every 10 s while the agent
  runs, uploaded as `agent-rss-<head-sha>`. #272 could not attribute its own deaths —
  nothing was sampling, and a container sees no host `dmesg`. The sampler is bounded by
  `timeout` because this runner does not force-kill process trees on cancellation.

Both agent lanes keep their own `Escalate…` step: the verifier cannot report a box that
died before it ever started, and `codex-tests-report` covers the PR lane from outside.

Measured on PR #269, the first real PR through the split: the agent job on the box went
from **877 s** (the old single job) to **225 s**, with the suite moving to a hosted lane
that took 200 s. The sampler recorded the Codex session at ~165 MB RSS with ~1.5 GB of the
box free — the agent session was never the expensive half.

Hosted minutes are free on this public repository, so the verdict lanes cost nothing and
run on 4 cores / 16 GB.

## Convergence: when does `codex-tests` stop? (issue #248)

The author treats "I can write a failing test" as "the implementation is wrong". On a
fresh PR those coincide. On a mature one they diverge: an author with unlimited rounds
can always invent a tighter contract than any spec or corpus fact requires, every
proposal is individually defensible, and nothing in the loop concludes "the contract is
satisfied". PR #244 ran fourteen rounds, #257 seven — the later ones each a narrower
variant of the same file-format class, each costing ~30 min of pipeline plus a fix
commit, with no signal separating "implementation broken" from "author still inventing".

`scripts/ci/codex_convergence.py` is the fixed point. After the author's tests run it
sorts every failure into one of three bins:

| failure | verdict | why |
|---|---|---|
| a test the author did **not** add | **blocks**, always | a pre-existing test broken by the PR is a regression, never a proposal |
| an author test marked `@pytest.mark.codex_proposal` | advisory | the author itself said "stricter contract I propose", not "violation of what the PR states" — the prompt requires the distinction |
| any author test, once the PR has been blocked `CODEX_BLOCKING_CAP` times (repo variable, default 3) | advisory | the implementation side has converged; the cap gives the test side its missing notion of diminishing returns |
| any other author test | blocks | a contract violation within the cap |

Advisory tests are not discarded. The verdict marks each one `xfail(strict=True,
reason="codex proposal, round N — owner decision, see #248")` **in place** and the round
commits as usual, so the proposal stays in the tree, visible in `git blame`, and the day
the owner implements it the strict xfail turns into a hard failure that says "remove
this marker". The owner decides which proposals to take; the pipeline no longer decides
for them by staying red. A round with advisory findings and no blockers is **green** and
reports into the same `pipeline` sticky comment (one marker per workflow); the advisory
body never contains the counted BLOCKED phrase, so it sits in the history without
inflating the round count.

The round count is read from the `pipeline` sticky comment by counting the phrase
`Codex-authored tests fail against this head` — a blocking round pushes no commit, so
the comment is the only durable record. The phrase is a contract shared by the verdict's
comment renderer and its counter (`BLOCKED_PHRASE`, pinned by test); changing it in one
place silently resets every open PR's count to zero.

## Failure escalation

- Codex's correct new test exposes a production bug → Codex reports it, does NOT fix
  production code. The `codex-tests` job itself applies `needs-implementation-fix`,
  comments on the PR with the failing tests **and the round number against the cap**, and
  preserves the test patch + pytest log as artifact `codex-tests-<head-sha>` (issue #95 —
  before this, a failing round died as a bare red check and the tests survived only in
  the run log); human relays to local Claude.
- Ambiguous/equivalent mutants → `needs-human:mutation`. BLOCKED is a valid end state,
  never to be silenced by weakening tests or thresholds.
- Codex output is normalized (`ruff format` + `ruff check` on `tests/`) before commit in
  both workflows, so the agent can never trip the pipeline's own lint gate (issue #66).
- If the PR branch advances while remediation is running, its rejected push is abandoned
  as superseded — the new head's own pipeline owns mutation from there. Any other
  remediation failure escalates itself: `needs-human:mutation` + a comment linking the
  failed run. A remediation run never dies silently (issue #67).

- **Every escalation of a workflow lands in that workflow's one sticky comment**,
  appended round by round, via `scripts/ci/pr_sticky_comment.sh <marker> <pr> <file>`.
  GitHub mails the PR author when a comment is *created*, not when one is edited:
  PR #230 sent ten notification mails carrying nine escalations, and the escalations
  are load-bearing, so the appending is what changed and not the content. The two
  workflows own separate markers (`pipeline`, `mutation`) because both can be in
  flight at once and a shared comment would let one round overwrite the other's.
  Rounds are appended, never overwritten; at GitHub's 65,536-character body limit
  the *oldest* rounds are dropped, so the escalation a human is being paged about
  is the last thing that can ever be lost. `remediate` checks the helper out from
  the **default branch**: it runs on `workflow_run` with a write token, and a job
  with that context must not run an escalation script the PR under review can
  rewrite. `codex-tests-report` takes the **PR head** instead — it runs on
  `pull_request`, where `codex-tests` already executes that head, and off the
  default branch the PR that *introduces* the helper would have none to call,
  leaving the reporter to die on a missing file: issue #193's silence again.
- A remediation **cycle notice** ("cycle 1/2 pushed test-only changes") applies no
  label and asks nothing of a human, so it goes to the run summary, not to a PR
  comment. It was two of PR #230's ten mails.

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
- **A dead machine is not a verdict on the diff (issue #272).** Before it writes
  anything, `codex-tests-report` reads the run's jobs payload and asks
  `scripts/ci/classify_lane_failure.py` which kind of failure this was. A lane job
  recorded as `failure` while one of its own steps is still `in_progress` never
  reached a verdict — that is a runner going away mid-job, and the comment says so,
  names the job and the frozen step, and gives the two next moves (`gh api
  …/actions/runners`, `gh run rerun <id> --failed`). A lane that failed on a
  completed step keeps the old pipeline-failure wording. Both still label
  `needs-human:pipeline`. Until this existed, both deaths on PR #269 were reported
  as `codex-tests BLOCKED before the tests ran` — the tests had run; the machine
  stopped.
- An agent job that dies **with its runner** is reported from outside it.
  Both escalations are steps of that job, and a step cannot run on a runner that
  no longer exists — so no in-job condition can cover the case (issue #193). On
  #192 five steps ran, the self-hosted box went offline mid-Codex-step, and the
  PR ended red with no label and no comment. The `codex-tests-report` job runs on
  `ubuntu-latest`, so it cannot share the failure mode it reports; it fires on
  `!cancelled() && (needs.codex-tests.result == 'failure' || needs.codex-author.result
  == 'failure')` (never on a concurrency
  cancellation, never on the skipped fork lane), stays silent when the in-job
  escalation already labelled, and otherwise applies `needs-human:pipeline` with
  the run link and a pointer at the runner list.
- A run that ends green **retracts** the labels a blocked round wrote. The `ready`
  job removes `needs-implementation-fix`, `needs-human:mutation` and
  `needs-human:pipeline` before reporting READY TO MERGE. Until 2026-08-26 the
  workflows only ever *added* labels (issue #191): #187 finished with every check
  green and `needs-implementation-fix` still on it from the round before the fix.
  The label is the durable half of the verdict — checks scroll off, labels stay on
  the PR list — so a stale one inverts the signal, and in the expensive direction: a
  genuinely blocked PR then looks exactly like a resolved one. `ready` is gated on
  `needs.mutation.result == 'success'`, so it stays silent on the run where the test
  author pushed and mutation was skipped in favour of a fresh run. It is not in the
  required set for the same reason `codex-tests` is not.
## Infrastructure

- Self-hosted runner: label `codex`, dedicated VM with no production data or secrets.
  Codex CLI authenticates via ChatGPT-managed `auth.json` in a persistent
  `CODEX_HOME=/home/runner/.codex-lovspor` (`cli_auth_credentials_store = "file"`).
  Never `OPENAI_API_KEY` on this runner. Auth self-refreshes; if it dies, reseed by
  running `codex login` in a scratch `CODEX_HOME` on a trusted machine, copying
  `auth.json` to the runner (`chmod 600`, owner `runner`), and deleting the scratch copy
  so exactly one copy of the refresh token exists.
- Push token: fine-grained PAT (this repo only; Contents RW, Pull requests RW) as secret
  `LOVSPOR_CI_PUSH_TOKEN`. The push happens on the hosted `codex-tests` lane, so the
  commits retrigger the pipeline (`GITHUB_TOKEN` pushes would not). The token is **not**
  checked out on the agent box: `codex-author` runs with `persist-credentials: false` and
  `permissions: contents: read`, so an agent session cannot reach the branch.
- Mutation and fast-ci run on GitHub-hosted runners — free for this public repo, no LLM
  auth anywhere near them.
