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
  ├─ fast-ci        (ubuntu, 3.12: conflict-marker check, ruff, mypy, security scan, unit tests)
  ├─ Test           (existing workflow, matrix 3.12–3.14 — unchanged)
  ├─ codex-author   (self-hosted `codex-lovspor` runner on the Mac mini: independent test author ONLY)
  │     └─ hands its work to the verdict lane as artifact `agent-tests-<head-sha>`
  ├─ codex-tests    (ubuntu: applies that patch, lints, runs the suite, verdict, push)
  │     └─ pushes `[agent:codex-tests]` → fresh synchronize run, old run cancelled
  └─ mutation       (ubuntu, no LLM: scripts/mutmut-pr.sh → mutation-result.json)
        └─ gate FAIL → Mutation Remediation workflow
              ├─ budget_exceeded / tool_failed / baseline_tests_failed
              │     → BLOCKED + needs-human:mutation, no Codex (nothing measured to kill)
              └─ survivors → Codex, tests only, max 2 cycles
                    └─ still failing / non-killable → BLOCKED + needs-human:mutation
```

## Roles

- **Claude Code local / human** — production code, small PRs, local gates:
  `scripts/quality/verify-fast.sh` at commit, `scripts/quality/verify-deep.sh` at push
  (`docs/decisions.md` §9d; agent rules in `CLAUDE.md`). No workflow installs git hooks,
  so neither gate runs on a CI lane; the lanes' own steps stay the gate there.
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

`scripts/mutmut-pr.sh <base-sha>` runs mutmut 3.8.0 for functions changed by the PR.
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
- mutants that got no verdict → **FAIL**, reason `unmeasured_mutants` (issue #283). mutmut
  files a child killed by a signal — including the `-9` its own CPU-limit escalation sends — as
  "segfault", a bucket its progress line never prints but still counts in the `done` total.
  `mutants.unmeasured` is that shortfall. It outranks the per-bucket reasons and, like
  `budget_exceeded`, routes straight to `needs-human:mutation`: tests cannot kill a mutant that
  was never measured.

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

- The self-hosted agent jobs (`codex-author`, `remediate`) carry `timeout-minutes: 120`
  (issue #101, raised in #382); the hosted `codex-tests` lane carries 30. A hung job holds
  the runner against every later PR, and GitHub's default ceiling is 6 h. The agent step
  carries 105 minutes of its own: up to 60 queueing for the host agent lock plus the 45 an
  agent round is allowed, in one step because the lock is fd 9 and no later step inherits
  it. The job ceiling sits above that and well under the default.
- `[agent:(codex|claude)-(tests|mutation)]` HEAD markers — an agent-authored HEAD is never
  reprocessed, whichever author produced it; all agent work is squashed into one marker
  commit per run.
- Remediation cycle count = `[agent:codex-mutation]` + `[agent:claude-mutation]` commits in
  the trailing agent-authored block; any human push resets it. The fallback author gets no
  extra cycles.
- Codex jobs run only for same-repo PRs (`head.repo.full_name == repository`); the
  fork-PR approval policy is set to "all outside collaborators".
- Codex jobs are also skipped for `dependabot[bot]` (#361): that actor receives no
  repository secrets, so the lanes could not check out and every bump landed in
  `needs-human:pipeline`. The mutation gate accepts the skipped lane from that actor
  only; fast-ci and the Test matrix still run on the bump.

## Two lanes: who runs on the small box (issue #272)

Since #445 the agent lanes run on lovspor's own runner on the Mac mini (see
[Mac mini runner](#mac-mini-runner-issue-445)). The split below was made for the box they
left and is kept: the verdict lane still must not share a machine with the agent.

The `codex`-labelled runner was a 1-shared-core / 2 GB container with no swap, shared by
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
| `codex-author` | self-hosted `codex-lovspor` (Mac mini) | checkout, anti-loop, `uv sync`, the Codex/Claude session, scope guard, patch handoff |
| `codex-tests` | `ubuntu-latest` | applies the patch, scope guard, ruff, the **full unit suite**, convergence verdict, escalation, push |
| `remediate` | self-hosted `codex-lovspor` (Mac mini) | artifact gate, cycle count, both BLOCKED paths, the remediation session, scope guard, patch handoff |
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
`remediate-verify` covers the remediation lane the same way (#254): it also runs when
`remediate` ended `failure` or `cancelled`, because a box that dies mid-job never
evaluates the job's outputs and `run`/`pr` arrive empty. With no PR to name, it resolves
the open PR from the branch itself, classifies the death with
`scripts/ci/classify_lane_failure.py`, and labels `needs-human:mutation` unless the
pipeline was green, the head moved on, or the label is already there.

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
| a test the author neither added nor changed | **blocks**, always | a pre-existing test broken by the PR is a regression, never a proposal. "Changed" is the function's AST at `before-sha` vs the head, decorators included, so a rewrite or a new parametrize case is the author's work (#354, #264); a test that carried a `codex proposal` xfail at `before-sha` is a proposal whatever the author did to its marker |
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
- Codex output is normalized before commit in both workflows, so the agent can never
  trip the pipeline's own lint gate (issue #66): `scripts/ci/normalize_agent_tests.sh`
  runs `ruff format`, then ruff's **safe** fixes, then puts an explicit `# noqa: <rule>`
  on every violation still standing (`ruff check --add-noqa`). Until 2026-09-23 the step
  hard-failed on that residue, and a lint with no safe fix — `RUF001` on a test whose
  subject *is* the ambiguous character (#256), `SIM105` on a `try/except/pass` (#232) —
  ended the round before the tests ran, discarded a correct test, and reported the
  cause as a pipeline failure. The suppression is the same edit the owner made by hand
  to recover both rounds. Nothing is suppressed silently: each one is a `::warning`
  annotation on its file and line, is listed in the step summary, and sits in the pushed
  diff for review with the rest of the agent's commit. Unsafe fixes are never applied —
  they can change what a test asserts. A draft ruff cannot read (a syntax error) still
  fails the step, into the pipeline-failure escalation below. Pinned by
  `tests/unit/test_normalize_agent_tests.py`, which runs the real ruff.
- When remediation changes nothing, the `needs-human:mutation` comment names what
  blocked the gate, bucket by bucket (`mutation_gate.py --no-change`, read on the agent
  lane from the default-branch helper and handed to the verifier as the `blocked` job
  output). "Survivors classified non-killable" is said only when there were survivors:
  on PR #395 it was said over two timed-out mutants and zero survivors (issue #423). A
  timed-out mutant is neither killed nor survived — it got no verdict inside mutmut's
  per-mutant time limit — so the job summary and the gate's log line list survived,
  timed-out, suspicious, uncovered and no-verdict counts separately too.
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
skipped the reporting steps (issue #157). The first class no longer reaches the
escalation at all — the normalize step suppresses what it cannot fix and says so
(above) — but the escalation stays, for the draft that does not parse.

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
- **A rejected credential is an operator action, not a pipeline failure (issue
  #270).** When the lane reached its own failure, `codex-tests-report` also fetches
  that job's log (`actions: read`) and hands it to the classifier with `--log`. A
  `##[error]` annotation carrying git's refusal of the credential — `could not read
  Username for 'https://github.com': terminal prompts disabled`, verbatim from the
  expired `LOVSPOR_CI_PUSH_TOKEN` of 2026-09-10, or `Authentication failed for
  'https://github.com/…'` — makes the verdict `credential`. The comment names the
  job, the failed step, the matched line and the secret the job authenticates with
  (`LOVSPOR_CI_PUSH_TOKEN` for `codex-tests`, the per-run `GITHUB_TOKEN` otherwise),
  and gives the `gh secret set` / `gh run rerun` moves. The label stays
  `needs-human:pipeline`. A log that cannot be fetched is no evidence: the verdict
  falls back to the old wording. `mutation-remediation.yml` does not pass `--log`
  and is unchanged.
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
- The same green run **resolves the sticky comment** those rounds wrote (issue #255).
  Retracting the labels was only half of it: #250 reached READY with every check green
  and its `pipeline` comment still ending on a round-3 BLOCKED, so the durable text a
  reader opens contradicted the labels. `ready` now appends a READY TO MERGE round with
  the run link, through the same `pr_sticky_comment.sh pipeline` helper — **appended,
  never rewritten**, because the convergence verdict counts blocked rounds out of that
  comment's history, and the READY body carries none of the counted phrase (pinned:
  no job of the workflow may write it). A PR that was never escalated on has no
  sticky comment and gets none — creating one would mail the author on every green PR.
  The job checks the helper out from the PR head, as `codex-tests-report` does.
## Infrastructure

- Self-hosted runner: `mac-mini-lovspor`, labels `self-hosted, macOS, ARM64,
  codex-lovspor`, on the Mac mini (#445; setup below). Codex CLI authenticates via
  ChatGPT-managed `auth.json` in two persistent homes under the runner user's home,
  `$HOME/.codex-lovspor` (primary) and `$HOME/.codex-lovspor-secondary`
  (`cli_auth_credentials_store = "file"`). The workflow derives both from `$HOME` on the
  runner; they are not repository variables, because a variable names one machine's paths.
  The agent step fails before any agent launches when either home has no `auth.json`.
  Never `OPENAI_API_KEY` on this runner. Auth self-refreshes; if it dies, log in again in
  that home (`CODEX_HOME=... codex login`). Never copy an `auth.json` between machines: two
  copies of one refresh token invalidate each other.
- Push token: fine-grained PAT (this repo only; Contents RW, Pull requests RW) as secret
  `LOVSPOR_CI_PUSH_TOKEN`. The push happens on the hosted `codex-tests` lane, so the
  commits retrigger the pipeline (`GITHUB_TOKEN` pushes would not). The token is **not**
  checked out on the agent box: `codex-author` runs with `persist-credentials: false` and
  `permissions: contents: read`, so an agent session cannot reach the branch.
- Mutation and fast-ci run on GitHub-hosted runners — free for this public repo, no LLM
  auth anywhere near them.

## Mac mini runner (issue #445)

Until 2026-09-29 the Codex lanes ran on `mikrus-codex` (`self-hosted, Linux, X64, codex`),
whose box-wide lock `/home/runner/.mikrus-agent.lock` five repositories share. On
2026-09-27 another repository held it for 60 minutes and both #439 and #442 failed
`codex-author` before a test was written. Owner decision 2026-09-29: lovspor's lanes move to
the Mac mini, which already runs one launchd runner per project (`~/actions-runner-<name>`,
runner `mac-mini-<name>`, one custom label). Mikrus stays for the other repositories.

What changed for the lanes:

| Linux assumption | on the Mac |
| --- | --- |
| `runs-on: [self-hosted, linux, codex]` | `[self-hosted, macOS, codex-lovspor]`, a set `mikrus-codex` does not carry, so the lane cannot land there |
| `exec 9>/home/runner/.mikrus-agent.lock` | `exec 9>"$HOME/.agent-box.lock"`: per host, under the runner user's home |
| `flock -w 3600 9` (util-linux) | `python3 scripts/ci/fd_lock.py --wait 3600 9`: same flock(2) lock on the inherited fd, same exit codes (0 locked, 1 wait expired, 2 usage), no brew dependency |
| `nohup timeout 3300 bash -c '...'` (coreutils) | the sampler loop carries its own 3300 s deadline on `$SECONDS` |
| `free -m`, `ps --sort=-rss` (procps) | `vm_stat` when there is no `free`; `ps -A -o rss=,comm= \| sort -rn` |
| `vars.CODEX_PRIMARY_HOME` = `/home/runner/.codex-lovspor` | `$HOME/.codex-lovspor` and `$HOME/.codex-lovspor-secondary`, checked for `auth.json` |

Unchanged: the fork guard (same-repo PRs only, never `dependabot[bot]`), the 3600 s lock
wait, the 105-minute step and 120-minute job ceilings, the contention message ending "this
is runner contention, not a fault in this PR", and the step states the lane-failure
classifier reads. Pinned in `tests/unit/test_agentic_ci_workflows.py`
(`TestTheCodexLanesRunOnTheMacMini`) and `tests/unit/test_fd_lock.py`.

The host lock serializes only jobs that take it. On 2026-09-29 no other runner on the Mac
did: the other projects' agent lanes still take the Mikrus lock, and the nightly observatory
(a launchd job) takes none, so the two share the machine (8 cores, 16 GB), not a lock.
lovspor's own two lanes already queue on its single runner.

Every Mac runner runs as the owner's login user, and this one follows that pattern. That
is a weaker isolation than Mikrus's dedicated VM: an agent session, and in particular the
Claude fallback, which runs with `--dangerously-skip-permissions`, runs with read access to
that user's home (SSH keys, `gh` credentials, other checkouts). The fork guard keeps
outside code off the runner; content inside the repository still reaches the agent's
prompt. A dedicated macOS user for this runner would close that gap.

### Operator steps (run by the owner on the Mac mini)

A PR that changes these workflows queues its `codex-author` job until the runner below is
online: nothing else carries `codex-lovspor`. A job stays queued for up to 24 h.

1. Register the runner and install it as a launchd service. Run this from a normal
   Terminal session, not over a stripped-down shell: `config.sh` records the current `PATH`
   in `.path`, and the jobs need `codex`, `claude`, `uv`, `gh`, `jq` and `python3` on it
   (all present on 2026-09-29; no brew dependency is added).

   ```bash
   mkdir -p ~/actions-runner-lovspor && cd ~/actions-runner-lovspor
   curl -fsSL -o actions-runner.tar.gz "$(gh api repos/actions/runner/releases/latest --jq '.assets[] | select(.name | test("^actions-runner-osx-arm64-[0-9.]+\\.tar\\.gz$")) | .browser_download_url')"
   tar xzf actions-runner.tar.gz && rm actions-runner.tar.gz
   ./config.sh --unattended \
     --url https://github.com/bartoszkobylinski/lovspor \
     --token "$(gh api -X POST repos/bartoszkobylinski/lovspor/actions/runners/registration-token -q .token)" \
     --name mac-mini-lovspor \
     --labels codex-lovspor \
     --work _work
   ./svc.sh install && ./svc.sh start && ./svc.sh status
   tr ':' '\n' < ~/actions-runner-lovspor/.path | grep -E 'nvm|\.local/bin|homebrew'
   ```

   `self-hosted`, `macOS` and `ARM64` are the runner's default labels; `codex-lovspor` is
   the only custom one, as `capcycle` is on `mac-mini-capcycle`.

2. Log both Codex accounts in, each in its own home (a browser sign-in each; the primary
   account first). A fresh login, never a copied `auth.json`.

   ```bash
   for h in "$HOME/.codex-lovspor" "$HOME/.codex-lovspor-secondary"; do
     mkdir -p "$h" && chmod 700 "$h"
     printf 'cli_auth_credentials_store = "file"\n' > "$h/config.toml"
   done
   CODEX_HOME="$HOME/.codex-lovspor" codex login
   CODEX_HOME="$HOME/.codex-lovspor-secondary" codex login
   chmod 600 "$HOME/.codex-lovspor/auth.json" "$HOME/.codex-lovspor-secondary/auth.json"
   CODEX_HOME="$HOME/.codex-lovspor" codex login status
   CODEX_HOME="$HOME/.codex-lovspor-secondary" codex login status
   ```

3. Verify: the runner is online and idle with the lane label, and the queued job starts.

   ```bash
   gh api repos/bartoszkobylinski/lovspor/actions/runners --jq '.runners[] | [.name, .status, .busy, ([.labels[].name] | join(","))] | @tsv'
   tail -n 50 ~/Library/Logs/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor/stdout.log
   ```

   Expected: `mac-mini-lovspor  online  false  self-hosted,macOS,ARM64,codex-lovspor`. If
   the queued job expired first, re-run it: `gh run rerun <run-id> --failed`.

4. Keep `mikrus-codex` registered until the Mac lane is proven: this PR merged, and a
   `codex-author` round and a remediation round green on the Mac. Until the merge, PRs
   built from `main` still target `codex` and need it.

5. Then remove it from lovspor. **This deregisters the runner and deletes the Mikrus copies
   of both Codex logins; it cannot be undone.** Stop first, and check the listener is gone
   before removing (the runbook's `KillMode=process` warning):

   ```bash
   ssh root@100.92.40.52 'systemctl stop actions.runner.bartoszkobylinski-lovspor.mikrus-codex.service; pgrep -f "/home/runner/actions-runner/bin/Runner.Listener" || echo "listener stopped"'
   TOKEN="$(gh api -X POST repos/bartoszkobylinski/lovspor/actions/runners/remove-token -q .token)"
   ssh root@100.92.40.52 "cd /home/runner/actions-runner && ./svc.sh uninstall && su -s /bin/bash runner -c 'cd /home/runner/actions-runner && ./config.sh remove --token $TOKEN'"
   ssh root@100.92.40.52 'rm -rf /home/runner/.codex-lovspor /home/runner/.codex-lovspor-secondary'
   gh variable delete CODEX_PRIMARY_HOME --repo bartoszkobylinski/lovspor
   gh variable delete CODEX_SECONDARY_HOME --repo bartoszkobylinski/lovspor
   gh api repos/bartoszkobylinski/lovspor/actions/runners --jq '.runners[] | [.name, .status] | @tsv'
   ```

   The box-wide lock and the other repositories' runners on Mikrus are untouched.
