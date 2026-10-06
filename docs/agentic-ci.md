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
  ├─ fast-ci        (ubuntu, 3.12: conflict-marker check, ruff, mypy, security scan,
  │                  equivalents register check (#535), unit tests)
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
parallel workers. Mutants are judged by `tests/unit/` only
(`pytest_add_cli_args_test_selection` in `pyproject.toml`): mutmut's stats pass records which
unit tests exercise each function and runs just those per mutant. Changed lines no mutant can
reach — module-level and class-body statements, decorated functions, decorated class headers —
are not run but named in an `unmeasured changed lines: ...` notice, carried into
`mutation-result.json` as `unmeasured_changed_lines` (#289, #292, #419, #420); only a file that
does not parse falls back to its whole-module pattern. The whole run has one wall-clock budget,
`MUTMUT_PR_FILE_BUDGET_SECONDS` (default 1200 s) × the number of changed files (issue #102),
enforced with `timeout(1)`; it is not a per-file budget.

A changed function with a `function-lines` entry in `scripts/quality/ratchet-baseline.toml`
is a large legacy function (issue #228, owner decision 2026-09-30): only the mutants on its
changed post-image lines run, not the whole body. `mutation_scope.py --legacy-plan` lists such
functions; `scripts/ci/mutation_legacy.py` generates the mutants without running them, reads
each one's line from `mutmut show`, and hands the script the exact mutant names to run. A
mutant whose line cannot be resolved runs. The rest are not run and are reported through the
`unmeasured_changed_lines` path as `(legacy remainder <function>: N of M mutants not run,
#228)`: never counted killed, never in the score, listed in the job summary under their own
heading. A PR whose legacy changes carry no mutant at all reports `mutation not applicable`
with that notice.

No numeric score threshold exists or was added. The gate in `mutation-result.json`:

- `mutation not applicable` (no `src/lovspor/` changes) → **PASS**, reason `not_applicable`
- everything killed → **PASS**
- every surviving mutant registered in `mutation-equivalents/` → **PASS**, reason
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
| an author test marked `@pytest.mark.codex_proposal`, on itself or an enclosing class (pytest inherits class marks) | advisory | the author itself said "stricter contract I propose", not "violation of what the PR states" — the prompt requires the distinction |
| any author test, once the PR has been blocked `CODEX_BLOCKING_CAP` times (repo variable, default 3) | advisory | the implementation side has converged; the cap gives the test side its missing notion of diminishing returns |
| any other author test | blocks | a contract violation within the cap |

Advisory tests are not discarded. The verdict marks each one `xfail(strict=True,
reason="codex proposal, round N — owner decision, see #248")` **in place** and the round
commits as usual. Only that exact shape — an
unconditional strict xfail with the round's reason — reads as a prior proposal; an
inactive `xfail(False, …)` does not, or `--apply` would write an active one over a
regression. The proposal stays in the tree, visible in `git blame`, and the day
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

## New `scripts/ci/` calls are guarded for one release cycle (issue #381)

For a `pull_request` run the workflow definition comes from the merge ref, but the jobs
check out the **PR head**. A step that lands on main together with the `scripts/ci/`
file it calls therefore reaches every open PR at once, while those PRs' checkouts do
not have the file yet. The step dies with `No such file or directory` and exit 127 —
on PR #373 (run 35896703581) that was `Normalize and lint Codex output` calling
`scripts/ci/normalize_agent_tests.sh`, added by #379. The round ends BLOCKED as a
pipeline failure, costs ~20 min of the single agent runner, and routes to a human,
although nothing about the implementation was wrong. `mutation-remediation.yml` has the
same shape: it runs from the default branch, and its jobs that check out the PR branch
(`ref: …workflow_run.head_branch`) call scripts from that branch.

The convention (owner decision 2026-09-29): every **new** workflow call to a
`scripts/ci/` file is guarded in its own `run:` block with `[ -x scripts/ci/NAME ]` and
a fallback, so a head that predates the script degrades instead of failing:

```yaml
run: |
  if [ -x scripts/ci/new_step.sh ]; then
    scripts/ci/new_step.sh tests/
  else
    echo "new_step.sh not on this head; falling back (issue #381)" >&2
    # the previous behaviour of this step, or a no-op when there was none
  fi
```

- **New** means not in `_ESTABLISHED_CI_SCRIPTS` in
  `tests/unit/test_agentic_ci_workflows.py` — an explicit list, not git history. It holds
  every script the workflows called when the convention landed; all open PRs then already
  contained `normalize_agent_tests.sh`, so no existing call needed a guard.
- **One release cycle** ends when every open PR branch contains the script (it was
  branched from, or has merged, a main that has it). Then a follow-up PR removes the guard
  and adds the name to `_ESTABLISHED_CI_SCRIPTS`, in the same commit.
- The script is committed executable (mode `100755`): `[ -x ]` is false for a
  non-executable file on every head, so the fallback would run forever. This holds for a
  Python helper called as `python3 scripts/ci/NAME.py` too.
- The fallback is the step's old behaviour where there was one, otherwise a no-op that
  says so on stderr. It never fakes the script's output: a later step that reads a file
  the script writes must tolerate its absence the same way.

`test_every_new_ci_script_call_in_a_workflow_is_guarded` parses every
`.github/workflows/*.yml` and fails on an unguarded new call; a guard in a different step,
or for a different script name, does not count.
`test_a_guarded_ci_script_is_executable_on_main` fails on a guarded script that is not
executable, and `test_every_established_ci_script_exists` on a stale list entry. The
larger fix — checking out the merge ref so workflow and files always come from one tree —
was not taken: the scope guard and the `BEFORE_SHA` arithmetic both assume the head.

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
  output). Survivors are called non-killable only when there were survivors: on PR #395
  that was said over two timed-out mutants and zero survivors (issue #423). The comment
  adds that the verdict stands only with a stated equivalence argument per survivor: the
  remediation prompt makes the agent attempt a killing test for every survivor first,
  after PR #453, where all 8 "non-killable" survivors fell to a test-only commit (issue
  #455). That killing test must drive the real code: the prompt forbids monkeypatching
  anything of the module under mutation, and a mutant only a mock can kill is reported
  as likely equivalent, not killed — on PR #422 two equivalent `run_sync` mutants were
  "killed" by mocking the orchestrator's own functions (issue #427). A
  timed-out mutant is neither killed nor survived — it got no verdict inside mutmut's
  per-mutant time limit — so the job summary and the gate's log line list survived,
  timed-out, suspicious, uncovered and no-verdict counts separately too.
- A remediation round whose agent **ran no command** is not a round that changed nothing
  (issue #472). On PR #469 (run 36670610064) the runner's `codex-code-mode-host` was
  missing (#448), the agent could not execute one command, `codex exec` still exited 0,
  and the sticky said "12 survivor(s) remediation called non-killable" — a verdict no
  agent gave. The Codex step now tees its transcript to
  `$RUNNER_TEMP/remediation-agent.log`; `scripts/ci/remediation_transcript.py` looks for
  the `exec` line `codex exec` prints before every command, and when there is none it
  hands the verifier a `not_run` message instead: "Mutation remediation did not run
  (reason) — no survivor was classified … Remediation: FAILED (agent_did_not_run)",
  with the gate reason and the unclassified counts from the artifact. The no-change
  wording is used only when the agent did run. The Claude fallback's transcript has no
  such marker, so the check runs on Codex rounds only.
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
- **A mutation round names the head it describes** (issue #477). On PR #469 the
  `mutation` sticky's first round (head `a85fe67`, 12 survivors) was read as the
  current state; the round for the real head (`99e00f7`, 5 survivors, run
  36679035597) had been appended correctly, at the bottom, with nothing saying the top
  was superseded. Both remediation jobs pass `STICKY_HEAD_SHA`, and the helper then
  opens each round with a bold `Head <sha7>` line, marks a round written for a head the PR
  has already left as `STALE` (it still posts: rounds are never dropped), and keeps
  one status line under the marker naming the head of the newest round and whether it
  is the PR head. The `pipeline` marker does not pass it and is unchanged.
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
- **A partial round reports what it already found (issue #220).** When the
  `codex-author` step itself fails — the provider refuses the model ("Selected
  model is at capacity"), the step hits its ceiling, the CLI dies — there is **no
  model fallback**: the Claude author takes over only on exit 75 (every account
  rate-limited), by owner decision 2026-09-29. Instead the step keeps its own
  output (`$RUNNER_TEMP/codex-author.log`), and on failure
  `scripts/ci/partial_round_failures.py` lifts every pytest `FAILED <nodeid>` line
  from it into `partial-round-failures.md`, uploaded with `agent-work-author-<sha>`.
  The `needs-human:pipeline` sticky comment appends that list, marked as leads the
  round never re-ran, not as a verdict. A round that found nothing adds nothing.
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
- **A lane that died in `Set up job` is the runner, not the diff and not Codex (issue
  #493).** When the Mac runner times out downloading a pinned action from
  `codeload.github.com`, `codex-author` fails in GitHub's own first step and its jobs
  payload holds that one step, concluded `failure` (runs 36784234807, 36823210811,
  36831117456). The classifier calls a failed lane whose failed step is `Set up job`,
  or that has no steps at all, `runner_setup`, and `codex-tests-report` says that no
  step of the workflow ran and gives the rerun (`gh run rerun <id> --failed`), with a
  note that an action reference that cannot resolve also fails there and is not
  cleared by a rerun. `codex-tests`' own pre-test escalation stands down when the
  author failed with no outputs at all (`needs.codex-author.outputs.skip == ''`, the
  output its first own step always writes): it cannot read the jobs API to tell this
  case apart, and its generic round used to make the reporter stand down on the label.
  The label stays `needs-human:pipeline`. No automatic rerun: a run cannot rerun
  itself while in progress, and doing it from a `workflow_run` workflow would add an
  `actions: write` surface for a failure a human clears with one command.
  The remediation lane gets the same wording (issue #499): a `remediate` job the
  runner never set up has no outputs, so `remediate-verify`'s dead-lane step reports
  it, and with `kind=runner_setup` it names the runner and the rerun instead of
  "ended 'failure' without reporting its own state". Its label stays
  `needs-human:mutation`.
- **A test-author round that ran no command fails the lane (issue #489).** On PR #469
  (head 99e00f7, run 36677467828) `codex-author` and `codex-tests` were green while the
  independent author never executed a command: `codex-code-mode-host` was missing
  (#448) and `codex exec` exited 0. After a Codex round, the step `Fail a round that
  executed no command` hands `$RUNNER_TEMP/codex-author.log` to
  `scripts/ci/author_transcript.py`, which reuses `remediation_transcript.py`'s `exec`
  marker check (#472) and, unlike the remediation lane, also fails closed on a missing
  or empty transcript — this lane's green check is itself the claim. The step writes
  the reason to the job output `not_run` and an `##[error]` annotation carrying `The
  independent test author executed no command (#489)`. `codex-tests`' generic pre-test
  escalation stands down when `not_run` is set, and `codex-tests-report` classifies:
  with the `failed to spawn code-mode host` line in the log it is `runner_tool` (the
  runner's Codex install, #448), otherwise `agent_did_not_run` (a lane failure). Both
  say that no independent test reviewed the PR and give the rerun; the label stays
  `needs-human:pipeline`. The Claude fallback's transcript has no `exec` marker, so the
  check runs on Codex rounds only.
- **A rejected credential is an operator action, not a pipeline failure (issue
  #270).** When the lane reached its own failure, `codex-tests-report` also fetches
  that job's log (`actions: read`) and hands it to the classifier with `--log`. The
  fetch passes `gh api --allow-escape-sequences` (every job log holds the runner's
  colour escapes, and the runner image's `gh` refuses to print them otherwise — the
  empty log that made PR #541's #448 round read `in_job`, issue #542), retries without
  the flag for an older `gh`, and warns when neither returns the log. A
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
  codex-lovspor`, on the Mac mini as the dedicated user `ci-lovspor`, run by a
  LaunchDaemon (#445; setup below). Fallback: `mikrus-codex` (`codex`), still registered.
  Codex CLI authenticates via ChatGPT-managed `auth.json` in two persistent homes under
  the runner user's home, `$HOME/.codex-lovspor` (primary) and
  `$HOME/.codex-lovspor-secondary`
  (`cli_auth_credentials_store = "file"`). The workflow derives both from `$HOME` on the
  runner; they are not repository variables, because a variable names one machine's paths.
  The agent step fails before any agent launches when the primary home has no
  `auth.json`. The secondary is optional: without it the step prints a `::warning::`
  and runs on the primary alone, with no account failover.
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
the Mac mini, as runner `mac-mini-lovspor` under a dedicated user `ci-lovspor`.
`mikrus-codex` stays registered as the fallback, and Mikrus keeps serving the other
repositories.

What changed for the lanes:

| Linux assumption | on the Mac |
| --- | --- |
| `runs-on: [self-hosted, linux, codex]` | `[self-hosted, macOS, codex-lovspor]`, a set `mikrus-codex` does not carry, so the lane cannot land there |
| `exec 9>/home/runner/.mikrus-agent.lock` | `exec 9>"$HOME/.agent-box.lock"`, under the runner user's home (the Mikrus lock when that file exists) |
| `flock -w 3600 9` (util-linux) | `python3 scripts/ci/fd_lock.py --wait 3600 9`: same flock(2) lock on the inherited fd, same exit codes (0 locked, 1 wait expired, 2 usage), no brew dependency |
| `nohup timeout 3300 bash -c '...'` (coreutils) | the sampler loop carries its own 3300 s deadline on `$SECONDS` |
| `free -m`, `ps --sort=-rss` (procps) | `vm_stat` when there is no `free`; `ps -A -o rss=,comm= \| sort -rn` |
| `vars.CODEX_PRIMARY_HOME` = `/home/runner/.codex-lovspor` | `$HOME/.codex-lovspor` (required, checked for `auth.json`) and `$HOME/.codex-lovspor-secondary` (optional) |

Unchanged: the fork guard (same-repo PRs only, never `dependabot[bot]`), the 3600 s lock
wait, the 105-minute step and 120-minute job ceilings, the contention message ending "this
is runner contention, not a fault in this PR", and the step states the lane-failure
classifier reads. Pinned in `tests/unit/test_agentic_ci_workflows.py`
(`TestTheCodexLanesRunOnTheMacMini`) and `tests/unit/test_fd_lock.py`.

The lock sits under the runner user's home, so it serializes every agent job that
`ci-lovspor` runs. On 2026-09-29 no other job on the Mac took it: the other projects'
runners run as the owner, their agent lanes still take the Mikrus lock, and the nightly
observatory (a launchd job) takes none. They share the machine (8 cores, 16 GB), not a lock.
lovspor's two lanes already queue on its single runner. The one exception is Mikrus: if
`/home/runner/.mikrus-agent.lock` exists, the step takes that lock instead, so a lane
pointed back at the fallback runner still queues behind its neighbours.

The runner runs as `ci-lovspor`, a dedicated standard account (owner decision on #445). The
Claude fallback's `--dangerously-skip-permissions` is therefore confined to that account.
It can still reach:

- its own files: the checkout, its tools, both Codex logins;
- the job's secrets, which sit in its environment (`CLAUDE_TESTS_OAUTH_TOKEN`, and on
  `remediate` a `GITHUB_TOKEN` that can write pull requests);
- anything world-readable on the Mac: `/opt/homebrew`, `/Applications`, `/Users/Shared`,
  `/tmp`, `/etc`, and any file another user left world-readable;
- the network, without limit, so whatever it can read it can send.

It cannot enter the owner's home, and therefore not `~/.ssh`, the `gh` credentials or any
project `.env`, because step 1 makes that home `700`. `ci-lovspor` IS in `staff`: macOS
nests every local account into it through `localaccounts`, whatever the primary group (read
off `id ci-lovspor` on 2026-09-29). So the owner's former `750`, group `staff`, would have
let it in. It cannot enter `/Volumes/T7` once step 2 has run, and it cannot `sudo`. The fork
guard still keeps outside code off the runner. Content inside the repository still reaches
the agent's prompt.

### Operator steps (run by the owner on the Mac mini)

A PR that changes these workflows queues its `codex-author` job until this runner is
online: nothing else carries `codex-lovspor`. A job stays queued for up to 24 h; after
that, `gh run rerun <run-id> --failed`. Run the blocks in order, in the owner's Terminal
(an admin account; `sudo` asks for its password). No brew install is needed. Every block
starts with `cd /tmp`: `sudo -u ci-lovspor` keeps the caller's working directory, and inside
the owner's now-`700` home that directory is unreadable to `ci-lovspor`. The shell then
prints `getcwd: ... Permission denied`, and codex, which looks up its config from the
working directory, dies with `Error loading configuration: Permission denied (os error 13)`.

1. Create `ci-lovspor`: a standard (non-admin) account with its own primary group, and
   close the owner's home to it. The home must go to `700`, and this is not optional:
   macOS nests every local account into `staff` through `localaccounts`, so `id ci-lovspor`
   shows `20(staff)`, and the owner's `750`, group `staff`, would stay readable to it. The
   password is random and never used: every step reaches the account through `sudo -u`.

   ```bash
   cd /tmp
   sudo dseditgroup -o create -r "lovspor CI runner" ci-lovspor
   CI_GID="$(dscl . -read /Groups/ci-lovspor PrimaryGroupID | awk '{print $2}')"
   sudo sysadminctl -addUser ci-lovspor -fullName "lovspor CI runner" -GID "$CI_GID" -shell /bin/zsh -home /Users/ci-lovspor -password "$(openssl rand -base64 32)"
   sudo dscl . -create /Users/ci-lovspor IsHidden 1
   sudo createhomedir -c -u ci-lovspor
   sudo chown -R ci-lovspor:ci-lovspor /Users/ci-lovspor
   sudo chmod 700 /Users/ci-lovspor
   chmod 700 /Users/bartoszkobylinski
   id ci-lovspor
   ```

2. Close `/Volumes/T7`. It is mounted with ownership disabled (`Owners: Disabled` on
   2026-09-29), and on such a volume every user can read everything. Enabling ownership
   makes the recorded owners (the volume root is uid 501, mode `775`) count, and dropping
   the "other" bits keeps `ci-lovspor` out. This changes how the drive treats permissions
   for every account.

   ```bash
   cd /tmp
   sudo diskutil enableOwnership /Volumes/T7
   sudo chmod o-rwx /Volumes/T7
   diskutil info /Volumes/T7 | grep Owners
   ```

3. Install the lane's tools for `ci-lovspor`, in its own `~/.local/bin`: `uv`, the
   standalone `codex` binary (no node needed) and the `claude` CLI for the fallback author.
   `python3`, `gh` (`/opt/homebrew/bin`, world-readable), `git` and `jq` (`/usr/bin`) are
   shared system paths.

   ```bash
   cd /tmp
   sudo -u ci-lovspor -H mkdir -p /Users/ci-lovspor/.local/bin
   sudo -u ci-lovspor -H sh -c 'curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh'
   sudo -u ci-lovspor -H sh -c 'cd /Users/ci-lovspor/.local/bin && curl -fsSL https://github.com/openai/codex/releases/latest/download/codex-aarch64-apple-darwin.tar.gz | tar xz && mv codex-aarch64-apple-darwin codex'
   sudo -u ci-lovspor -H bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
   sudo -u ci-lovspor -H env PATH=/Users/ci-lovspor/.local/bin:/opt/homebrew/bin:/usr/bin:/bin sh -c 'for t in uv codex claude python3 git gh jq; do printf "%-8s %s\n" "$t" "$(command -v "$t" || echo MISSING)"; done; codex --version; uv --version'
   ```

   **`codex-code-mode-host` (issue #448).** The `codex-aarch64-apple-darwin.tar.gz` asset
   holds `codex` alone. From codex-cli 0.159.0 the agent's commands run through a separate
   `codex-code-mode-host`, which that CLI looked for next to itself, at
   `/Users/ci-lovspor/.local/bin/codex-code-mode-host` (run 36670610064). Without it every
   tool call failed to spawn (`ERROR codex_core::tools::router: error=failed to spawn
   code-mode host …`), the agent ran no command, and `codex exec` still exited 0. The
   remediation lane now stops on that line and the verifier reports it as an
   infrastructure failure of this install, never as survivors called non-killable. The
   host ships as its own release asset, `codex-code-mode-host-aarch64-apple-darwin.tar.gz`
   (present in release `rust-v0.159.2`, read off the releases API on 2026-09-30; the
   tarball holds one file, `codex-code-mode-host-aarch64-apple-darwin`). Install both from
   one tag so the two binaries match:

   ```bash
   cd /tmp
   TAG="$(gh api repos/openai/codex/releases/latest --jq .tag_name)"; echo "$TAG"
   sudo -u ci-lovspor -H sh -c "cd /Users/ci-lovspor/.local/bin && curl -fsSL https://github.com/openai/codex/releases/download/$TAG/codex-aarch64-apple-darwin.tar.gz | tar xz && mv codex-aarch64-apple-darwin codex && curl -fsSL https://github.com/openai/codex/releases/download/$TAG/codex-code-mode-host-aarch64-apple-darwin.tar.gz | tar xz && mv codex-code-mode-host-aarch64-apple-darwin codex-code-mode-host && ls -l codex codex-code-mode-host && ./codex --version"
   ```

   Not established: whether the host must also be switched on with the
   `features.code_mode_host` setting the CLI's warning names, and whether a later CLI
   looks for it elsewhere. The check is the next remediation round's log: no
   `failed to spawn code-mode host` line, and `exec` blocks for the commands it ran.

4. Download and register the runner in `ci-lovspor`'s home. The owner's `gh` fetches the
   download URL and the registration token; the runner itself never sees the owner's
   credentials. `.path` is written by hand because `runsvc.sh` exports it as the jobs'
   `PATH`.

   ```bash
   cd /tmp
   RUNNER_URL="$(gh api repos/actions/runner/releases/latest --jq '.assets[] | select(.name | test("^actions-runner-osx-arm64-[0-9.]+\\.tar\\.gz$")) | .browser_download_url')"
   TOKEN="$(gh api -X POST repos/bartoszkobylinski/lovspor/actions/runners/registration-token -q .token)"
   sudo -u ci-lovspor -H bash -c "mkdir -p ~/actions-runner-lovspor ~/Library/Logs/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor && cd ~/actions-runner-lovspor && curl -fsSL '$RUNNER_URL' | tar xz && ./config.sh --unattended --url https://github.com/bartoszkobylinski/lovspor --token '$TOKEN' --name mac-mini-lovspor --labels codex-lovspor --work _work && cp bin/runsvc.sh runsvc.sh && echo /Users/ci-lovspor/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin > .path"
   ```

   `self-hosted`, `macOS` and `ARM64` are the runner's default labels; `codex-lovspor` is
   its only custom one.

5. Run it as a LaunchDaemon with `UserName=ci-lovspor`. The runner's own `./svc.sh` can't
   do this: in the macOS package (2.337.0, read from `~/actions-runner-capcycle/svc.sh`) it
   exits "Must not run with sudo", takes no user argument, and writes a LaunchAgent into
   the caller's `~/Library/LaunchAgents`. A LaunchAgent loads only inside that user's GUI
   login session, which `ci-lovspor` never has. A LaunchDaemon in `/Library/LaunchDaemons`
   starts at boot with no login. The plist is the runner's `actions.runner.plist.template`,
   with `GroupName`, an explicit `HOME` and `KeepAlive` added.

   ```bash
   cd /tmp
   sudo tee /Library/LaunchDaemons/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor.plist >/dev/null <<'EOF'
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
     <dict>
       <key>Label</key>
       <string>actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor</string>
       <key>ProgramArguments</key>
       <array>
         <string>/Users/ci-lovspor/actions-runner-lovspor/runsvc.sh</string>
       </array>
       <key>UserName</key>
       <string>ci-lovspor</string>
       <key>GroupName</key>
       <string>ci-lovspor</string>
       <key>WorkingDirectory</key>
       <string>/Users/ci-lovspor/actions-runner-lovspor</string>
       <key>RunAtLoad</key>
       <true/>
       <key>KeepAlive</key>
       <true/>
       <key>StandardOutPath</key>
       <string>/Users/ci-lovspor/Library/Logs/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor/stdout.log</string>
       <key>StandardErrorPath</key>
       <string>/Users/ci-lovspor/Library/Logs/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor/stderr.log</string>
       <key>EnvironmentVariables</key>
       <dict>
         <key>ACTIONS_RUNNER_SVC</key>
         <string>1</string>
         <key>HOME</key>
         <string>/Users/ci-lovspor</string>
       </dict>
       <key>ProcessType</key>
       <string>Interactive</string>
       <key>SessionCreate</key>
       <true/>
     </dict>
   </plist>
   EOF
   sudo chown root:wheel /Library/LaunchDaemons/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor.plist
   sudo chmod 644 /Library/LaunchDaemons/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor.plist
   plutil -lint /Library/LaunchDaemons/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor.plist
   sudo launchctl bootstrap system /Library/LaunchDaemons/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor.plist
   sudo launchctl print system/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor | grep -E '^\s+(state|pid) ='
   ```

   To restart it: `sudo launchctl kickstart -k system/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor`.
   To stop it: `sudo launchctl bootout system/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor`.

6. Log the Codex accounts in as `ci-lovspor`, each in its own home. The primary is
   required. The secondary is optional, and you can add it at any later time with the same
   command. Without it, the step warns on every round that account failover is disabled:
   when the primary reaches its usage limit, the round goes straight to the Claude fallback
   author rather than to a second Codex account. Use a fresh login each time, never a
   copied `auth.json`. `--device-auth` prints a URL and a
   one-time code. Open the URL in any browser, sign in with the matching ChatGPT account,
   and enter the code. If the account refuses device codes, run the same command without
   `--device-auth`: codex then prints a sign-in URL that calls back to `localhost:1455`, so
   open it in a browser on the Mac mini itself.

   ```bash
   cd /tmp
   for h in /Users/ci-lovspor/.codex-lovspor /Users/ci-lovspor/.codex-lovspor-secondary; do
     sudo -u ci-lovspor -H install -d -m 700 "$h"
     printf 'cli_auth_credentials_store = "file"\n' | sudo -u ci-lovspor -H tee "$h/config.toml" >/dev/null
   done
   sudo -u ci-lovspor -H env CODEX_HOME=/Users/ci-lovspor/.codex-lovspor /Users/ci-lovspor/.local/bin/codex login --device-auth
   sudo -u ci-lovspor -H env CODEX_HOME=/Users/ci-lovspor/.codex-lovspor /Users/ci-lovspor/.local/bin/codex login status
   ```

   Optional, now or later: the secondary account, which enables failover.

   ```bash
   cd /tmp
   sudo -u ci-lovspor -H env CODEX_HOME=/Users/ci-lovspor/.codex-lovspor-secondary /Users/ci-lovspor/.local/bin/codex login --device-auth
   sudo -u ci-lovspor -H env CODEX_HOME=/Users/ci-lovspor/.codex-lovspor-secondary /Users/ci-lovspor/.local/bin/codex login status
   ```

7. Verify the runner, and verify the isolation. Expect `mac-mini-lovspor  online  false
   self-hosted,macOS,ARM64,codex-lovspor` next to `mikrus-codex`, and `denied` on every
   path.

   ```bash
   cd /tmp
   gh api repos/bartoszkobylinski/lovspor/actions/runners --jq '.runners[] | [.name, .status, .busy, ([.labels[].name] | join(","))] | @tsv'
   sudo tail -n 50 /Users/ci-lovspor/Library/Logs/actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor/stdout.log
   for p in /Users/bartoszkobylinski /Users/bartoszkobylinski/.ssh /Users/bartoszkobylinski/.config/gh /Users/bartoszkobylinski/Programming/Python/lovspor/.env /Volumes/T7; do
     if sudo -u ci-lovspor -H test -r "$p"; then echo "READABLE  $p"; else echo "denied    $p"; fi
   done
   ```

8. Optional, and only after the Mac lane is proven (this PR merged, and a `codex-author`
   round and a remediation round green on the Mac): unregister `mikrus-codex` from
   **lovspor only**. Until then, and by default afterwards, it stays registered as the
   fallback. Pointing the lanes back at it is a one-line `runs-on` change per workflow, and
   the step then takes the Mikrus box-wide lock again. **Never delete
   `/home/runner/.codex-lovspor*` on Mikrus or the `CODEX_PRIMARY_HOME` /
   `CODEX_SECONDARY_HOME` repository variables:** the fallback needs them, and Mikrus serves
   other repositories. Stop the service first, then check the listener is gone (the
   runbook's `KillMode=process` warning):

   ```bash
   ssh root@100.92.40.52 'systemctl stop actions.runner.bartoszkobylinski-lovspor.mikrus-codex.service; pgrep -f "/home/runner/actions-runner/bin/Runner.Listener" || echo "listener stopped"'
   TOKEN="$(gh api -X POST repos/bartoszkobylinski/lovspor/actions/runners/remove-token -q .token)"
   ssh root@100.92.40.52 "cd /home/runner/actions-runner && ./svc.sh uninstall && su -s /bin/bash runner -c 'cd /home/runner/actions-runner && ./config.sh remove --token $TOKEN'"
   gh api repos/bartoszkobylinski/lovspor/actions/runners --jq '.runners[] | [.name, .status] | @tsv'
   ```

9. Install the runner watchdog (issue #493). On 2026-10-01 GitHub listed
   `mac-mini-lovspor` as `offline` while the LaunchDaemon was `running` and
   `Runner.Listener` alive: the listener lost its session in a night of network outages
   and never re-registered, and `KeepAlive` cannot see that because the process never
   exited. `codex-author` sat `queued` for hours until a manual `kickstart -k`.
   `scripts/ops/runner_watchdog.py`, run every five minutes by the root LaunchDaemon
   `deploy/launchd/no.lovspor.runner-watchdog.plist`, makes that restart — only after two
   consecutive readings of `offline` from the runners API while `launchctl print` says
   the daemon is `running`. An API it cannot reach is no evidence (no restart, streak
   kept); a daemon that is not running is launchd's own to restart.

   It needs a token that can list the repository's runners: a fine-grained personal
   access token for **`bartoszkobylinski/lovspor` only**, with repository permission
   **Administration: Read-only** and nothing else. It lives in a root-only file, never in
   the plist (which is world-readable). The script and plist are copied to root-owned
   paths so the root job never executes a file the login user can edit; re-run the two
   `install` lines after a change to either. Run from an up-to-date `main` checkout:

   ```bash
   cd /Users/bartoszkobylinski/Programming/Python/lovspor && git checkout main && git pull --ff-only
   sudo install -d -m 755 -o root -g wheel /usr/local/libexec/lovspor
   sudo install -m 755 -o root -g wheel scripts/ops/runner_watchdog.py /usr/local/libexec/lovspor/runner_watchdog.py
   sudo install -m 644 -o root -g wheel deploy/launchd/no.lovspor.runner-watchdog.plist /Library/LaunchDaemons/no.lovspor.runner-watchdog.plist
   plutil -lint /Library/LaunchDaemons/no.lovspor.runner-watchdog.plist
   sudo install -m 600 -o root -g wheel /dev/null /var/root/.lovspor-runner-watchdog-token
   printf 'Paste the token, then Enter: '; read -rs TOKEN; echo; printf '%s\n' "$TOKEN" | sudo tee /var/root/.lovspor-runner-watchdog-token >/dev/null; unset TOKEN
   sudo launchctl bootstrap system /Library/LaunchDaemons/no.lovspor.runner-watchdog.plist
   sudo launchctl kickstart system/no.lovspor.runner-watchdog
   sleep 10; sudo tail -n 5 /var/log/lovspor-runner-watchdog.log
   ```

   Expect a line like `2026-10-02T08:00:00Z wait: runner is online`. A `gh exit=…` line
   means the token was refused. To remove it:
   `sudo launchctl bootout system/no.lovspor.runner-watchdog`.
