# Applying pinned owner changes in the sync: design (E2)

* **Status:** implemented and wired into `sync.yml` on a draft pull request.
  Merging it changes the production sync and needs the maintainer's approval.
* **Date:** 2026-11-25
* **Builds on:** `docs/owner-changes.md` (pin file, grammar, pull-request
  checks), `docs/publication-seal.md` (seal, continuous custody).
* **In this change:** `src/lovspor/owner_changes_apply.py` (refusal rules,
  merge gate, run-end check), `src/lovspor/owner_changes_run.py` (the pass the
  sync runs: pull-request head, attempt ref, README refresh, report),
  `lovspor ops owner-changes apply` and `check-applied`, the three steps in
  `sync.yml`, and their tests.

## Why

Once sealing is required, the sync refuses to start from a head it did not
seal. A maintainer change merged into lovverk directly would stop publication
until a re-bootstrap. The sync must therefore carry the change itself, inside
a run that starts from a sealed head and ends in a sealed run end.

## Steps in the `sync` job

| order | step | new |
|---|---|---|
| 1 | Verify starting head seal | no |
| 2 | Configure git author for sync commits | no |
| 3 | Record the temporal gate epoch before any corpus work | no |
| 4 | **Apply pinned owner changes** | yes |
| 5 | Flag under-embedded documents for re-embed | no |
| 6 | Run lovspor sync | no |
| 7 | **Check pinned owner changes are unchanged** | yes |
| 8 | Audit corpus integrity before push | no |
| 9 | **Report pinned owner changes not applied** | yes |
| 10 | Compose, upload, push, seal job | no |

Why there:
- After the epoch step: it pushes a note anchored at the starting head before
  any corpus work. An epoch anchored at an unpushed merge would leave origin
  with a note whose anchor is missing if the run then fails.
- Before the sync: a run with no upstream change still attests the current
  head, so the merged head is the one the sync attests and compose seals.
- The check after the sync and before the audit: if anything in the run
  rewrote a pinned file, the step fails and nothing is pushed.
- The report after the audit: refusals are reported whether or not anything
  else changed; it never fails the job.

## Step 4: apply

For each pin, in file order. The pin file is parsed first with
`lovspor ops owner-changes check`; a malformed file applies no pin and is
reported once.

1. A pin whose commit is already an ancestor of HEAD is skipped first, with
   no remote call, so a network failure never reports an applied pin.
   Otherwise `plan(repo, pin, pr_head, attempt_used)`, where `pr_head` comes from
   `git fetch origin refs/pull/<N>/head` and `attempt_used` from
   `git ls-remote origin refs/owner-changes/<commit>/<attempt>`:
   - commit already an ancestor of HEAD: skip, nothing to do;
   - attempt ref exists: refuse `attempt-consumed`;
   - `refs/pull/<N>/head` is not the pinned commit: refuse `stale-pin`;
   - a merge commit inside the pinned range: refuse `merge-commit`;
   - any change, in any single commit or in the range as a whole, outside
     `lokale-forskrifter/`, on a path with a component starting with `.`, or
     with a mode other than `100644` (or `000000` for an added or deleted
     side): refuse `out-of-scope`;
   - any path changed by the pin also changed on `main` since the merge base,
     compared without rename detection: refuse `overlap`.
2. Record the attempt, create-only on the server:
   `git push --porcelain --force-with-lease=<ref>: origin <commit>:<ref>` for
   `<ref>` = `refs/owner-changes/<commit>/<attempt>` (an empty expected value
   means the ref must not exist); continue only on the `*` new-reference line.
   A plain push would fast-forward an existing ref, so the lease is what makes
   one attempt per pin hold.
3. `merge_pin(repo, pin, base, message)` refuses `dirty` if the work tree has
   uncommitted tracked changes, then merges with the message
   `owner-change: lovverk#<N> <commit> attempt <K> (pinned in lovspor <sha>, run <id>)`:
   a conflict aborts the merge and refuses `conflict` (`merge-failed` for any
   other merge failure); a merge whose raw diff
   (`--no-renames --no-abbrev --raw`) against its first parent differs from the
   pin's raw diff against its merge base is reset and refused `gate`.
4. After all pins, only if one was applied: regenerate the README
   corpus-status block with the existing function and commit `README.md` alone.

A refused pin is skipped; the run continues and publishes without it.

## Step 7: run-end check

`changed_since_pin(repo, applied)`: for every applied pin and every path it
changed, the mode and blob at HEAD equal those at the pinned commit (absent in
both for a deletion). Any difference fails the step. A git failure while
undoing a refused merge raises an error instead of a refusal, so the run fails
and pushes nothing.

## Step 9: report

One issue per (commit, attempt), "Pinned owner change not applied:
lovverk#<N>", created once with the refusal code and detail, never
re-commented on later nights. The refusal detail is shown as a code block,
so path names from the pinned commit render literally. The existing
"Nightly sync did not complete" issue does not name the pins applied in a
failed run (see Open questions).

## Failure handling

- A pin is tried in at most one run: the attempt ref is pushed before the
  merge, so a run that fails later leaves it consumed. Retry: replace the line
  with attempt + 1.
- A run that fails after a merge pushes nothing to `main`.
- A run that pushed but whose seal job failed is the normal seal recovery;
  the commit is then on `main`, and later runs skip the pin as applied.

## Tests

In this change:
- refusal rules, one test each: consumed attempt, stale head, merge commit,
  out-of-scope paths (root files, central corpus, `history/`, `scripts/`,
  `.github/`, dot paths, a prefix look-alike), executable, symlink, an
  out-of-scope change reverted later in the same pin, overlap, rename against
  an edit on `main`;
- the merge gate: a conflict and a clean but different merge are both undone,
  leaving HEAD and the work tree as they were;
- integrity: a pinned file rewritten after the merge, and a pinned deletion
  restored, are both reported;
- step order: the rule above on synthetic step lists, the existing anchor
  steps in `sync.yml`, and a strict xfail on the real `sync.yml`.

Wiring, also in this change: the order test runs against the real
`sync.yml`; `tests/unit/test_owner_changes_run.py` runs the whole pass against
a local bare origin with `refs/pull/<N>/head` (applied, skipped on a later
run, consumed attempt and the next attempt, stale and missing pull request,
out of scope before any attempt ref, one refusal not stopping the next pin,
an attempt ref created meanwhile not overwritten, detached HEAD, the run-end
check and both CLI commands), the regression that the committed empty pin
file makes no git call, and the report step with a fake `gh` (one issue per
refusal, none when already filed).

Not yet done: one pin exercised end to end before sealing is required.

## Open questions

- Whether GitHub marks the lovverk pull request merged when its head reaches
  `main` through the sync push. If not, it is closed by hand.
- The git version on the runner. The gate does not depend on merge options.
- The sync clone is on the `main` branch; with at least one pin, the pass
  refuses to run on any other branch or a detached HEAD (an error, so the
  job fails before pushing). An empty pin file makes no git call at all.
- "Nightly sync did not complete" does not yet name the pins applied in a
  failed run; the attempt refs and the job log do. Follow-up.

## Deploy-key probe

Question: can the sync's deploy key create a ref under `refs/owner-changes/`
on lovverk, create-only, as `_record_attempt` does? Answered by one
owner-authorised run on 2026-11-25. The answer is yes.

- Workflow: `.github/workflows/owner-changes-ref-probe.yml`, added in lovspor
  #1033, merged as `c1b2b71094fbe06605fb07016add8eab7adb1711`, removed again
  in this pull request.
- The workflow was dispatched once, as run 45649164024 (2026-11-25T15:50:52Z). It
  ran on `c1b2b710` in environment `lovverk-sync`. Every step succeeded.
- Key: the run logged fingerprint
  `SHA256:2u9LOHT5MvZzD43sKSefASZU7ATifgXb8ydhP4vAwwA` (deploy key
  170454523). The private key was not printed, and it was removed at the end
  of the job.
- The one write was `git push --porcelain --force-with-lease=<ref>: <url>
  <target>:<ref>`. Exit 0, porcelain line:
  `*	c8c4bec40cd07effbee7dafd8ed735c49d3e69cc:refs/owner-changes/probe-20261125/1	[new reference]`.
- Ref created: `refs/owner-changes/probe-20261125/1` points at
  `c8c4bec40cd07effbee7dafd8ed735c49d3e69cc`, a lovverk commit sealed by
  `sync/seal-45415948204-1`, so the push carried no new object. The name
  cannot collide with a real attempt ref: real attempt refs use a 40-hex
  commit. The ref is kept as evidence.
- A repeated create was checked with dry runs only. There was no second write.
  - Create-only, different commit (a descendant of the target): exit 1,
    `!	HEAD:refs/owner-changes/probe-20261125/1	[rejected] (stale info)`.
  - Create-only, same commit: exit 0,
    `=	c8c4bec4…:refs/owner-changes/probe-20261125/1	[up to date]`.
    No write happens, but there is also no refusal. `_record_attempt` does not
    accept this: it requires the `[new reference]` line, and `plan` refuses
    before the push when the attempt ref exists.
  - Plain push of the descendant, without a lease: exit 0,
    `HEAD:refs/owner-changes/probe-20261125/1	c8c4bec4..56ee5fbe`. A plain
    push would fast-forward the ref. This is why `_record_attempt` uses the
    lease.
- Before and after:
  - The job compared `git ls-remote` of `refs/heads/main`, both notes refs
    and `refs/tags/sync/*`. They were identical. The only addition under
    `refs/owner-changes/*` was the probe ref.
  - An independent read-only snapshot, taken before (15:32:56Z) and after
    (15:52:43Z), gave the same diff: one added line, the probe ref.
  - main stayed `bcea15786c90a9f51f3194228eb4e50b716aba3b`, so the corpus tree
    and manifest are unchanged. Notes stayed `c3fab9d0` and the epoch stayed
    `37215650`. The ten `sync/*` tags were unchanged.
- An unrelated change happened earlier. At 12:45Z lovverk main was `c8c4bec4`.
  The scheduled sync, run 45628688850 (started 13:56Z), then moved it to
  `bcea1578` and added `sync/seal-45628688850-1`. That run finished before the
  probe's before-snapshot, and no sync run started between the two snapshots.
- Not tested: whether GitHub rulesets would block this namespace in future.
  Today the only lovverk ruleset (27725547) covers tags.
