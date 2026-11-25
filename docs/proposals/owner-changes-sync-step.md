# Applying pinned owner changes in the sync: design (E2)

* **Status:** design and tested library only. **Not wired.** `sync.yml` is
  unchanged; changing it needs the maintainer's separate approval.
* **Date:** 2026-11-25
* **Builds on:** `docs/owner-changes.md` (pin file, grammar, pull-request
  checks), `docs/publication-seal.md` (seal, continuous custody).
* **In this change:** `src/lovspor/owner_changes_apply.py` (refusal rules,
  merge gate, run-end check), its tests, and the step-order rule as a test
  (`tests/unit/test_owner_changes_sync_order.py`, with a strict xfail on the
  real `sync.yml`).

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

1. `plan(repo, pin, pr_head, attempt_used)`, where `pr_head` comes from
   `git fetch origin refs/pull/<N>/head` and `attempt_used` from
   `git ls-remote origin refs/owner-changes/<commit>/<attempt>`:
   - commit already an ancestor of HEAD: skip, nothing to do;
   - attempt ref exists: refuse `attempt-consumed`;
   - `refs/pull/<N>/head` is not the pinned commit: refuse `stale-pin`;
   - a merge commit inside the pinned range: refuse `merge-commit`;
   - any change, in any single commit or in the range as a whole, outside
     `lokale-forskrifter/`, on a path with a component starting with `.`, or
     with a mode other than `100644`: refuse `out-of-scope`;
   - any path changed by the pin also changed on `main` since the merge base,
     compared without rename detection: refuse `overlap`.
2. Record the attempt, create-only:
   `git push --porcelain origin <commit>:refs/owner-changes/<commit>/<attempt>`;
   continue only if the porcelain flag is `*` (new ref).
3. `merge_pin(repo, pin, base, message)` with the message
   `owner-change: lovverk#<N> <commit> attempt <K> (pinned in lovspor <sha>, run <id>)`:
   a conflict aborts the merge and refuses `conflict`; a merge whose raw diff
   (`--no-renames --no-abbrev --raw`) against its first parent differs from the
   pin's raw diff against its merge base is reset and refused `gate`.
4. After all pins, only if one was applied: regenerate the README
   corpus-status block with the existing function and commit `README.md` alone.

A refused pin is skipped; the run continues and publishes without it.

## Step 7: run-end check

`changed_since_pin(repo, applied)`: for every applied pin and every path it
changed, the blob at HEAD equals the blob at the pinned commit (absent in both
for a deletion). Any difference fails the step.

## Step 9: report

One issue per (commit, attempt), "Pinned owner change not applied:
lovverk#<N>", created once with the refusal code and detail, never
re-commented on later nights. The existing "Nightly sync did not complete"
issue names the pins applied in a failed run.

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

With the wiring (not in this change): the three steps in `sync.yml` (remove
the xfail mark), the attempt-ref push and the issue step against a local bare
repository, and one pin exercised end to end before sealing is required.

## Open questions

- Whether GitHub accepts the deploy key's push to `refs/owner-changes/*`.
  Test against lovverk before wiring step 4.
- Whether GitHub marks the lovverk pull request merged when its head reaches
  `main` through the sync push. If not, it is closed by hand.
- The git version on the runner. The gate does not depend on merge options.
