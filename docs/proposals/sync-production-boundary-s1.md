# S1: a reviewed production ref for the lovverk sync (proposal)

* **Status:** PROPOSED — DOCUMENTATION ONLY. Nothing in this PR changes `sync.yml`, repository settings, secrets, environments or the droplet. The owner reviews it with other advisors first. Implementation is a later, separate PR, and only on the owner's explicit authorisation.
* **Date:** 2026-11-16
* **Tracking:** #898 (a merge to `main` deploys the sync), #956 (a dispatch from any branch runs unmerged code with the lovverk write key). It is the blocker of the ADD-ONLY contract's invariant 10 (`docs/proposals/add-only-legal-history-contract.md`, PR #958).

## Observed state (read 2026-11-16 from `origin/main` `94a9276d`)

`.github/workflows/sync.yml`:
* `on:` has `schedule: "0 4 * * *"` and `workflow_dispatch` with the input `repair_embeddings` (lines 7-18).
* `permissions: contents: read, issues: write`, and `concurrency: group: sync`.
* Job `sync`: no `if:` and no `environment:`. Its first step `actions/checkout@3d3c42e5…` (v7.0.1) has **no `ref:`**: the schedule runs the tip of the default branch, and a dispatch runs the dispatching branch.
* Secrets: `LOVVERK_DEPLOY_KEY` (repository-level, used for the lovverk clone and push) and `OPENAI_API_KEY`.
* Push step (lines 240-258): `git push origin refs/notes/temporal-attestations`, then `git push origin main` when the corpus is ahead.

Effect:
* A PR merged before 04:00 UTC writes lovverk by about 04:0x the same day.
* The droplet's `lovspor-fetch-corpus.timer` pulls lovverk at 05:30 UTC, and the MCP reloads when the manifest changes.
* No human step sits between a merge and production.

## Proposed change (three parts, each independently adoptable)

### Part 1: main-only guard (closes #956)

```yaml
jobs:
  sync:
    # Only main's reviewed history may write the production corpus (#956).
    if: github.ref == 'refs/heads/main'
```

A dispatch from any other branch is skipped. It neither fails nor runs. The schedule always runs on the default branch, so it is unaffected.

### Part 2: a reviewed production ref (closes #898)

A one-line file `deploy/production-engine-ref` holds the full 40-character SHA of the engine commit production runs. The checkout pins it:

```yaml
      - name: Read the reviewed production engine ref
        id: prod
        run: |
          ref=$(tr -d '[:space:]' < deploy/production-engine-ref)
          [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || { echo "::error::production-engine-ref is not a full SHA"; exit 1; }
          git merge-base --is-ancestor "$ref" origin/main || { echo "::error::ref is not on main"; exit 1; }
          echo "ref=$ref" >> "$GITHUB_OUTPUT"
      - name: Checkout lovspor (engine) at the production ref
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ steps.prod.outputs.ref }}
```

A first checkout of `main`, with full history, reads the file and proves the SHA is on `main`. The second checkout runs the pinned engine.

**The deploy act** is a PR that changes only `deploy/production-engine-ref`, reviewed and merged by the owner. CODEOWNERS covers that file. Merging research or feature PRs then changes nothing in production until such a PR names their commit.

**Rollback** is a PR that sets the previous SHA.

### Part 3: the write key in a protected environment

* Create the GitHub environment `production`. Deployment branches: `main` only. A required reviewer is optional, since a daily run needing approval would stall the sync. The branch rule is the important part.
* Move `LOVVERK_DEPLOY_KEY` (and `OPENAI_API_KEY`) from repository secrets to `production` environment secrets.
* Add `environment: production` to the `sync` job.

After this, no job outside `production` on `main` can read the key, whatever its YAML says.

## What must be tested when it is implemented

* `tests/unit/test_add_only_contract.py` G6 golden (`tests/fixtures/add_only/sync-boundary.json`) is updated in the **same** PR. That PR is the reviewed boundary change: `if` set, `environment: production`, checkout `ref` = the step output.
* A new unit test parses `sync.yml` and requires all of the following:
  * the job `if` is exactly the main guard;
  * every checkout that runs engine code uses the pinned ref;
  * `environment: production` is set;
  * no secret is referenced outside that job.
* A unit test on `deploy/production-engine-ref`: one full lowercase SHA, a trailing newline at most.
* A workflow-free rehearsal: run the "read ref" shell block against a fixture repo. Cover a valid SHA, a short SHA, a SHA not on `main`, and a missing file.
* After merge, the owner watches one scheduled run: `gh run list --workflow sync.yml --limit 1` → `gh run view <id> --log | grep -E "production ref|Checkout"`.

## Costs and risks

* **Deploys become explicit:** a fix to the engine reaches the corpus only after its ref PR. That is the point of invariant 10, but urgent sync fixes need two PRs.
* **First adoption:** the initial `production-engine-ref` must be the SHA production effectively runs today, the tip of `main` at adoption time, so the switch changes no behaviour.
* **Embedding-cache staging, keepalive and other workflows** do not write lovverk. They are out of scope.
* **The droplet's engine** is already deployed by hand. Its runbook could adopt the same ref file later. That is not part of S1.
* **Repository settings** (environment, secrets) are owner actions in GitHub's UI or API. No agent performs them.

## Alternatives considered

* **Release tags instead of a ref file:** equivalent. A tag is less visible in review and can be moved, so a ref file in a reviewed PR is easier to audit.
* **A separate deploy repository:** stronger isolation, but more moving parts.
* **Keep `main` deploying, add more tests:** the tripwire (G6) shows boundary changes, but it does not stop an ordinary PR's code from deploying. Invariant 10 would remain unmet.

## Owner decision (blank; the owner fills this, never an agent)

* Part 1 (main-only guard): ______
* Part 2 (reviewed production ref): ______
* Part 3 (`production` environment for the write key): ______
* Advisors consulted and date: ______
