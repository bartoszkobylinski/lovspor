# ADD-ONLY gates: who reviews a pin change, how the surface is extended, and when a merge reaches the corpus (proposal)

* **Status:** PROPOSED — DOCUMENTATION ONLY. It decides nothing and authorises nothing. This PR changes no test, golden, workflow, CODEOWNERS entry or repository setting, and nothing in `lovverk`. Each slice below would be a later, separate PR, and each needs its own explicit authorisation from the owner.
* **Date:** 2026-11-16
* **Tracking:** #958 (the gates, open), #959 (S1 proposal, draft), #898, #956.
* **Authority:** none. The contract the gates enforce is under owner review: "The contract these gates enforce is under owner review (not accepted)" (#958, `tests/unit/test_add_only_contract.py:4-5`). It is kept privately for that review (#958, PR body) and is **not reproduced here**. Nothing below depends on its text.
* **Scope:** this repository only. Three questions the gates raise and do not answer: who reviews a change to a pin, how the surface is extended, and when a merged change reaches the corpus.

## Observed state (read 2026-11-16 from `origin/main` `94d1d208`; ruleset `main-branch-protection`, id 20274337)

Pull requests:
* #958 is open, not a draft, head `e2b03012`. It adds `tests/unit/test_add_only_contract.py` (gates G1 to G7), `tests/unit/test_capture_add_only_baseline.py`, the goldens in `tests/fixtures/add_only/` and `scripts/quality/capture_add_only_baseline.py`. It is not merged, so none of G1 to G7 runs on `main`.
* #959 is a draft, head `7e657851`. It adds `docs/proposals/sync-production-boundary-s1.md` and changes nothing else.

Pinned on `main` today:
* The eighteen MCP tool names, as a list (`tests/unit/test_mcp.py:5306`), and their count in the CLI help (`tests/unit/test_cli.py:94-97`).
* The tool listing, against the anchor `benchmarks/llhb/runner/tool-surface-v9.json`: the names on every interpreter, and on Python 3.12 the hash of the listing, which covers each tool's "name, description, input and output schema" (`src/lovspor/tool_surface.py:61-62`; `tests/unit/test_llhb_mcp_surface.py:258-270`). The test says: "regenerating the document is an apparatus decision to make explicitly, not a fixture to patch."
* The wire output of the existing calls (`tests/unit/test_mcp_existing_calls.py:1-9`): "a change to it is a change to the served surface, decided explicitly, never a fixture to refresh."
* URL continuity (`docs/url-contract.md`, version 1; `tests/unit/test_publish_url_continuity.py`): "A 404 for such a URL is a defect" (`docs/url-contract.md:65`).

How the surface has been extended so far:
* Nine versioned anchors exist, `tool-surface-v1.json` to `tool-surface-v9.json`. A surface change gets a new anchor: "v1 through v8 stay committed untouched so the runs that recorded their hashes remain verifiable" (`tests/unit/test_llhb_mcp_surface.py:344-345`).
* Additions went that way: v5 "Adds `get_temporal_events`", and in v8 "two existing tools gain one optional parameter each" (`benchmarks/llhb/TOOLING.md:33,36`).

What #958 adds to those pins (line numbers are those of its gate module):
* G1: each production URL is still derived from its manifest record.
* G2: the tool names again, with each tool's parameter names, in a golden that runs on every interpreter.
* G3: the wire output of ten version and `recorded_at` calls.
* G4: no production module imports a research package. The one allowed importer is `ops_cli.py` (`RESEARCH_IMPORTERS`, line 61).
* G5: no `Settings` field and no MCP tool whose name matches `legal.?time|legal.?history|crosswalk|forarbeid|replay`, and no research command in `sync.yml` (lines 258-272).
* G6: the triggers, checkout refs, secrets and pushes of `sync.yml`.
* G7: no workflow applies an identity plan or rewrites history.

Legal history:
* It is not served. `docs/decisions.md:1036-1037`: "nothing is served, published or written into `lovverk`; serving, materialisation and publication each need their own decision (ADR-0020 Decision 13)."
* `Settings` (`src/lovspor/settings.py:51-104`) has no field for it. The research packages `legaltime`, `crosswalk` and `forarbeider` are registered only as operator commands (`src/lovspor/ops_cli.py:18-28`).

Deployment (`.github/workflows/sync.yml`):
* `cron: "0 4 * * *"` (line 9) and `workflow_dispatch` (line 10).
* Job `sync` (line 35) has no `if:` and no `environment:`. Its checkout (line 47) has no `ref:`.
* It pushes `refs/notes/temporal-attestations` (line 251) and `main` (line 255) of `lovverk` with `LOVVERK_DEPLOY_KEY` (line 77).
* The last six scheduled runs started between 11:11 and 12:24 UTC. The runs of 2026-11-14, 2026-11-15 and 2026-11-16 failed. `lovverk` `main` is unchanged since 2026-11-13 (`c310127e`).
* #898 and #956 are open.

Review mechanics:
* The ruleset requires `test (3.12)`, `test (3.13)`, `test (3.14)`, `fast-ci` and `mutation`. It sets `require_code_owner_review: true` and `required_approving_review_count: 0`. Auto-merge is off (`allow_auto_merge: false`).
* `.github/CODEOWNERS` covers three paths, all for identity, and says it is "Kept narrow on purpose" (lines 1-6).
* The CI test author may write `tests/*.py`, `tests/fixtures/*` and `tests/*/fixtures/*` (`scripts/ci/assert_codex_scope.sh:17`). That includes every golden under `tests/fixtures/` and the gate module. The anchors under `benchmarks/` are outside it. The same script reports lines removed from existing test files and does not block them (lines 77-94).

## What the gates leave open

| # | Question | On `main` today | With #958 | Slice |
|---|---|---|---|---|
| 1 | Who reviews a change to a pin? | prose, and the scope guard's report | the same | 2 |
| 2 | How is the surface extended? | a new versioned anchor, by practice | G2 also fails on an addition | 3 |
| 3 | When does a merged change reach the corpus? | at the next scheduled sync (#898, #956) | G6 shows a change to that boundary | 4 |

## Proposed slices (each needs its own authorisation)

### Slice 1: land the gates (#958)

There is nothing to design. It is the owner's merge decision on #958. Until then the research import boundary and the absence of a history setting are observed facts, not CI invariants.

### Slice 2: a change to a pin is a reviewed change

Fact: the rule is stated in prose. The two test docstrings quoted above. #958's gate module: a failure "is decided by the owner, and the golden is never just refreshed to make a PR pass" (lines 7-8). And `CLAUDE.md:50`: never "edit a baseline to make your own change pass, unless changing that gate's policy is itself the task".

Fact: little backs it mechanically. A PR can change a pin and the code it pins in one diff. The ruleset asks for code-owner review on the three identity paths only. The CI test author can write every golden under `tests/fixtures/`.

Proposed, part a: CODEOWNERS entries for the pinned files and the gate module.

```text
/tests/fixtures/add_only/                       @bartoszkobylinski
/tests/unit/test_add_only_contract.py           @bartoszkobylinski
/tests/fixtures/mcp-existing-calls.json         @bartoszkobylinski
/benchmarks/llhb/runner/tool-surface-v*.json    @bartoszkobylinski
```

With `require_code_owner_review: true` already set, a PR that touches one of them then merges the way the identity paths do.

Proposed, part b: a deny rule in `scripts/ci/assert_codex_scope.sh` for `tests/fixtures/add_only/*` and `tests/fixtures/mcp-existing-calls.json`, so an automated round cannot rewrite those goldens. The allowlist has no entry to remove: `tests/fixtures/*` covers them, because `*` crosses `/` there (line 10). A new rule is needed, with cases beside the existing scope-guard tests (`tests/unit/test_agentic_ci_scripts.py:160`) and the matching sentence in `AGENTS.md:78-80`.

Limits:
* **It widens a file that is narrow on purpose.** Part a reverses the stated policy of `.github/CODEOWNERS`. That is part of the decision.
* **It is an owner bypass, not a review.** `docs/identity-approval.md:20-23`: "GitHub cannot tell the owner from an automation that holds the owner's token. A code-owner review cannot be given by the PR's author, so every PR touching an owned path is merged by an owner bypass". The entries make a pin change visible and deliberate. They do not prove who decided it.
* **The entries cover the pinned data, not every assertion.** The test modules that assert the other pins are not owned: `tests/unit/test_mcp.py`, `tests/unit/test_mcp_existing_calls.py`, `tests/unit/test_llhb_mcp_surface.py`, `tests/unit/test_publish_url_continuity.py`. Neither are `benchmarks/llhb/runner/check_fairness.py` and the helper the gates import, `tests/unit/local_dataset_fixtures.py` (gate module, line 52). A change there can weaken a pin without touching an owned path. Owning them too would put most MCP test changes behind an owner bypass. That trade-off is the owner's.
* **Part b leaves the gate module writable** for the test author (`tests/*.py`). The guard reports removed lines there. It does not refuse them.
* CODEOWNERS is read from the base branch (GitHub behaviour, not tested here).
* A stronger form is a signed approval per pin change, as `docs/identity-approval.md` describes for the identity list. Its list and commands ship with #917, which is open, and no CI job verifies such a signature yet (`docs/identity-approval.md:30,61-62`).

### Slice 3: how the surface is extended

Fact: #958 treats an addition to a pinned surface as a decision point, on purpose. G2 fails on a new tool ("a new MCP tool: review it as an addition", line 123) and on a changed set of parameter names (line 124). The module docstring: "Adding a tool or a parameter fails here too, so an addition is reviewed as one" (lines 17-18).

Fact: G2 compares tool names and parameter names only. A changed type, default or description does not fail G2. It fails the listing hash that `main` already pins, on the Python 3.12 leg.

Fact: the repository already has a practised way to extend the surface: the next versioned anchor, with the earlier anchors left in place.

Proposed: write that practice down in one place. An addition to the MCP surface is one PR that touches, at least:

1. the next `tool-surface-vN.json`, with the earlier anchors untouched;
2. the three places that name the current anchor: `tests/unit/test_llhb_mcp_surface.py:353`, `benchmarks/llhb/runner/check_fairness.py:47` and `tests/unit/test_llhb_check_fairness.py:214`;
3. for a new tool, the name list (`tests/unit/test_mcp.py:5306`) and the count in the CLI help (`tests/unit/test_cli.py:94-97`);
4. after #958, `tests/fixtures/add_only/mcp-tools.json`, where a new tool adds a key and a new parameter adds a name to an existing tool's list.

It leaves `tests/fixtures/mcp-existing-calls.json` and the G3 golden unchanged, and it argues for each pin it changed in the PR description, as `CLAUDE.md:51` already asks for a ratchet-baseline entry. The list above was read from the tests on 2026-11-16. It is not claimed complete.

A PR that removes or renames a tool or a parameter is not an addition. In #958's own words it "is decided by the owner".

No new CI check is proposed. A check that the golden only gained entries would compare names, and would pass a new required parameter. The listing hash sees every schema change and cannot tell an addition from a break. Telling them apart stays with the review of slice 2.

What the gates do not single out: a PR that first serves research output.

* If it imports a research package into a production module, it fails G4.
* If it adds a setting or a tool whose name matches the G5 pattern, it fails G5.
* A tool that reads a research artifact without importing those packages, under a name the pattern does not match, fails only G2 and the listing hash, as any new tool does.

Proposed: such a PR is never handled as an ordinary addition. It says in its description that it serves research output. `docs/decisions.md:1036-1037` already says that serving needs its own decision, so that decision is named in the PR. Where the PR must change G4 or G5, that is a change of gate policy, which `CLAUDE.md:50` allows only when it "is itself the task".

### Slice 4: when a merge reaches the corpus (S1, #959)

This proposal adds nothing to S1. It records how the slices depend on it.

Fact: the scheduled sync runs the tip of `main` and writes `lovverk` (#898), and a dispatch from any branch runs with the write key (#956). So a merged engine change is run against the corpus by the next scheduled sync, and its writes land if that run completes. #959 proposes the boundary in three parts: a main-only guard, a reviewed production ref, and the write key in a protected environment.

Until S1 or an equivalent is implemented, slices 1 to 3 show what a change does to the pins. They do not decide when it reaches the corpus.

## What must be tested when it is implemented

* Slice 2, part a: a throwaway PR that touches one owned path and one that touches none, to confirm which of them asks for code-owner review.
* Slice 2, part b: scope-guard cases for a write to `tests/fixtures/add_only/` (refused) and to another fixture (allowed), in both lanes.
* Slice 3: none. It is documentation. Its list is checked against the next real addition.

## What this proposal does not do

* It does not merge, mark ready or change #958 or #959.
* It does not reproduce the contract or any part of it.
* It does not touch `sync.yml`, repository settings, secrets, environments, the droplet or `lovverk`.
* It serves, enables or publishes no research output.
* It does not run, plan or schedule the identity migration of #871.

## Order and dependencies

* Slices 2 and 3 name files that #958 adds, so they follow slice 1. The entries for `mcp-existing-calls.json` and the anchors do not depend on #958.
* S1 is #959's own decision. Its implementation updates the G6 golden "in the **same** PR" (#959, line 68). So it needs #958 merged first, and under slice 2 it touches an owned path.

## Costs and risks

* **More owner bypasses.** Slice 2 puts every change to an owned pin on the owner's desk. That is its purpose, and it adds one step to each additive MCP change.
* **A tripwire, not a proof.** See the limits of slice 2.
* **Several pins of the same names.** After #958 the tool names are pinned by the name list, the anchor and the G2 golden, and their count by a CLI test. An addition has to change all of them. Slice 3 writes that down. It does not remove the duplication.

## Alternatives considered

* **Prose only, no mechanism:** today's state. The rule lives in docstrings and in `CLAUDE.md`, and a PR can still refresh a pin in the diff that needs it.
* **A signed approval per pin change** instead of CODEOWNERS: it would show the owner's decision. Heavier, and unverified by CI today.
* **Drop the G2 golden and rely on the anchor:** fewer pins. But the anchor's hash runs on one interpreter leg only, and G2 runs on all three.

## Owner decision (blank; the owner fills this, never an agent)

* Slice 1 (merge #958): ______
* Slice 2, part a (CODEOWNERS entries; narrow as listed, or wider): ______
* Slice 2, part b (a deny rule for the goldens in the test author's scope guard): ______
* Slice 3 (the written procedure for extending the surface; the rule for a PR that serves research output): ______
* Slice 4: decided in #959. No field here.
* Whether any part of the contract is published in this repository: ______
* Advisors consulted and date: ______
