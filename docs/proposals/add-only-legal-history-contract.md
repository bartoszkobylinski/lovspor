# ADD-ONLY contract for legal history (proposal)

* **Status:** PROPOSED — OWNER DECISION REQUIRED. Decides nothing and authorises nothing. No agent marks it Accepted; the owner fields at the end are blank.
* **Date:** 2026-11-16
* **Scope:** the product goal "existing Lovspor + provision-level legal history + source-backed legislative provenance", as an **addition**. This page is narrow on purpose. It sets the invariants an addition must keep, and it names the CI gates that check them. It refines no accepted ADR: ADR-0017, ADR-0019, ADR-0020, ADR-0023 and ADR-0024 already forbid serving, materialisation into lovverk and publication of the research outputs. This contract says what "additive" means for the existing product once those are allowed.
* **Gates:** `tests/unit/test_add_only_contract.py`, with goldens in `tests/fixtures/add_only/` and the read-only capture `scripts/quality/capture_add_only_baseline.py`.

## Observed state (read 2026-11-16, at the time of writing)

| what | reading |
|---|---|
| lovspor `origin/main` | `94a9276daa6b06aae770e765c8f2357a399cfada` (PR #947 merge) |
| lovverk `main` | `c310127ecc65192f8260eab56bd3809ce0354130` (2026-11-13T11:51:43Z). `identity.json` and `slugs.json` return HTTP 404, so the corpus is legacy, identity schema 0 |
| lovverk `manifest.json` at that commit | sha256 `e50707122713e1f89d03703c37ec3347ab85b5a7e09094ca7bd24dd3054442a7`: 6 026 records, 5 905 current and 121 removed (5 262 forskrift, 764 lov) |
| production 301/410 map at that commit | 830 redirects and 5 332 gone prefixes. `redirect-map.json` sha256 `bf1669ee…8119`, Caddy snippet sha256 `9f4336dd…659e` |
| ADR statuses (lovspor-notebook `main`) | ADR-0017, 0019, 0020, 0023, 0024 Accepted, as architectural direction only. The identity decision ADR-0026 is Proposed, with no file |
| open lovspor PRs | 11: see classification |

## Classification of work, with evidence

**A. Existing production features.**
- **MCP:** 18 tools served by `build_server` (`src/lovspor/mcp.py`) and `ServedCorpus.register` (`mcp_local.py`). Hosted as `lovspor mcp-http` on the droplet.
- **Static site:** URLs `/{lov|forskrift}/{slug}/` and `/…/paragraf/{pid}/` (`publish/pages.py`). The 301/410 map comes from the corpus's git rename history (`publish/redirects.py`).
- **Search:** `search_laws`, `search_body` and `semantic_search`.
- **Temporal tools:** `get_law_at`, `list_law_versions`, `diff_law_versions`, `get_temporal_events`, and `recorded_at` on five tools (`get_section`, `get_temporal_events`, `search_body`, `validate_citation`, `verify_quote`).
- **Sync:** `lovspor sync` writes lovverk, run by `sync.yml`.
- **Rendering:** deterministic and pinned to `RENDERER_VERSION`.

**B. Additive research.**
- `src/lovspor/legaltime/`: the replay engine (ADR-0020), label lineage and step evidence (ADR-0023, unwired), and nested amendments (ADR-0024).
- `src/lovspor/crosswalk/`: ADR-0017/0019.
- `src/lovspor/forarbeider/`.
- Proof that production does not reach them (G4):
  - Outside their own packages, the only module that imports them is `ops_cli.py`, to register operator commands.
  - Importing every production module (`lovspor.mcp`, `mcp_local`, `mcp_contract`, `sync.orchestrator`, `publish.emit`, `publish.redirects`, `site`, `rendering`, `storage.manifest`, `embeddings`, `corpus_audit`) loads **zero** research modules.
  - No setting, MCP tool or `sync.yml` step reaches them (G5).
  - `crosswalk/paths.py` refuses to write into lovverk. legaltime writes only to `--dest` and its cache.

**C. Identity / URL migration** (#871, #911/#917, #921, #938, #949, #953): `src/lovspor/identity/`, migration M, `slugs.json`, `identity.json`.

**B does not depend on C.** Checked with `grep -rlE` over `src/lovspor` on `94a9276d`:
- `legaltime → identity` 0, `crosswalk → identity` 0, `identity → legaltime|crosswalk` 0;
- `crosswalk ↔ legaltime` 0;
- an AST import-closure check by the class-C audit found no transitive path;
- `src/lovspor/legaltime/identity.py` is ADR-0023 *label lineage* for provisions, a different concept from the slug registry.

So **migration M is not a prerequisite** of any step in the roadmap below. Nothing in B reads `slugs.json` or `identity.json`.

### Open PRs that could affect existing functionality

| PR | class | touches | note |
|---|---|---|---|
| #917 | C | `identity/`, `publish/redirects.py`, sync start, audit gate | Changes legacy redirects: moves onto an occupied path become 301s (#939), so **invariant 1** is at stake. The number of affected URLs is unverified. Also adds a sync-start refusal. |
| #933 | A | sync, embeddings, storage | Changes what a sync pays for. A production path, not research. |
| #757 | A | all workflows | Workflow change, so G6/G7 review it. |
| #955 | B | `legaltime/` | `REPLAY_VERSION` 10, research only. |
| #834 | B | `crosswalk/` | research only |
| #946, #760, #758 | other | dependency bumps | test matrix |
| #885, #831, #750 | other | tests, gitleaks, `server.json`/docs | — |

## Product invariants (the contract)

1. Existing document IDs, public slugs, URLs, redirects and 410 behaviour stay unchanged.
2. Existing search, MCP tools, temporal endpoints, publication, sync and document rendering stay available.
3. No replacement of the lovverk corpus, the identity schema or current persistence.
4. No deletion, rename or incompatible change to an existing public API.
5. New historical functionality is optional and **off by default**.
6. With the flag off, all existing behaviour stays unchanged.
7. Historical records are a separate, read-only evidence overlay with source hashes, provenance and explicit unknowns. They are never written into lovverk documents, manifest, registry or git history (ADR-0020 D10, ADR-0017/0019 D3).
8. Existing functions keep working when history data is missing, corrupt or unavailable.
9. No forced migration or backfill is a prerequisite for enabling the feature. In particular, migration M is not.
10. Merging a research PR changes nothing in production.

## Enforcement: the gates

All gates run in the unit suite, so they run in fast-ci and in the Test matrix on every PR. A failure means a change to the production surface. The owner decides it; the golden is never refreshed to make a PR pass.

| gate | invariant | pins | evidence it bites (temporary edit → failing test) |
|---|---|---|---|
| G1 URLs | 1 | each of the 5 905 production URLs is still derived from its manifest record by the publisher's own route table, slug grammar and `document_url`; the corpus counts and the 301/410 digests | `_ROUTES["forskrift"] = "lov"` → `test_g1_every_production_url…` |
| G2 MCP surface | 2, 4 | 18 tool names, each with its parameter names; additions fail too and are reviewed as additions | `get_law_at` renamed → G2 and G3 |
| G3 temporal defaults | 2, 6 | wire output of 10 version and `recorded_at` calls on a deterministic two-state git corpus. The 21 other calls stay pinned by `test_mcp_existing_calls.py` | the same rename → G3 |
| G4 research boundary | 5, 6, 10 | no production import of `legaltime`/`crosswalk`/`forarbeider`, both statically and at runtime in a fresh interpreter | `from lovspor.legaltime import run` in `mcp.py` → both G4 tests |
| G5 history off | 5, 6 | no setting, MCP tool or `sync.yml` command for legal time, crosswalk, forarbeider or replay | a `legal_history_enabled` setting → G5; `lovspor ops legal-time replay` in `sync.yml` → G5 and G6 |
| G6 deployment boundary | 10 | `sync.yml`: triggers, schedule, each job's `if`/`environment`, checkout refs, secret names, `git push` lines | a `ref: production` on checkout → G6 |
| G7 destructive migration | 3, 9 | no workflow runs `identity-apply`/`identity-plan`, a force-push, `filter-branch`, `reset --hard`, `update-ref -d` or `branch -D`; only `identity/apply.py` writes `identity.json` | `git push --force origin main` in `sync.yml` → G6 and G7 |

**Real-corpus check (operator, read-only).** `scripts/quality/capture_add_only_baseline.py --corpus <lovverk clone> --sha c310127e… --check tests/fixtures/add_only/production-urls-c310127e.json` rebuilds the inventory and the 301/410 map with the production publisher, then compares byte for byte.
- Run twice on 2026-11-16: `baseline reproduced`, about 3.7 min each, on a local clone that only read objects.
- CI has no lovverk clone, so CI checks the URL derivation (G1), and this script checks the map.

**What the gates do not cover** (stated, not hidden):
- Response bodies of `semantic_search`, `get_temporal_events` with attestation, and `get_observation_history` beyond the existing goldens.
- The hosted Caddy path list.
- A full sync's output (manifest, `INDEX.md`, registry).
- The 301/410 map in CI, which is digest-only here and re-checked by the operator script.
- The existing goldens (`test_publish_*`, `test_mcp_existing_calls.py`, `test_rendering_golden.py`, `test_site_routes.py`) stay the authority for what they pin.

## Deployment boundary audit

**Finding.** A merge to `main` reaches production with no further human step:
1. `sync.yml` runs daily at 04:00 UTC and checks out the tip of `main` (no `ref:`). It runs `lovspor sync` with `LOVVERK_DEPLOY_KEY` and pushes lovverk `main` plus `refs/notes/temporal-attestations`. Nothing gates it on a release, tag or environment (#898).
2. The droplet's `lovspor-fetch-corpus.timer` (05:30 UTC) runs `git pull --ff-only` on lovverk. The MCP reloads when `manifest.json` changes. Engine code on the droplet is deployed by hand, and so is the static site.
3. **New, #956:** a manual `workflow_dispatch` from any branch runs that branch's code with the write key. There is no main-only guard and no protected environment.

**Already evidenced:** merged class-C code changed production behaviour in legacy mode without migration M.
- Every sync now writes `history/by-id/<doc_id>.json`: four files in lovverk `forskrifter/history/by-id`, first added in `c310127e`, the first sync after #890.
- Every sync refuses a shallow clone or a non-root corpus, and refuses a manifest write that drops a `doc_id`.

These are safety refusals plus additive files, not a URL change. But they show that invariant 10 does not hold today for any PR that touches a production path.

**Separations proposed** (only S2 is in this PR):
- **S2, done here as G6/G7:** a CI tripwire on the boundary. No behaviour change. It makes any change to the triggers, refs, secrets or pushes fail review.
- **S3, done here for imports as G4:** production entry points load no research package. The CLI still imports `ops_cli`, which registers the research commands at startup. Lazy registration would remove even that. It is not done here because it changes `cli.py` startup.
- **S1, needs the owner (STOP condition):**
  - `sync.yml` checks out a reviewed production SHA kept in a file, behind a main-only guard;
  - the deploy key moves into a protected `production` environment.
  
  This is the only separation that makes invariant 10 hold for every PR. It changes production sync behaviour and repository settings, so it is **not implemented** and needs explicit owner authorisation (#898, #956).

## Roadmap: dependency graph (only these nodes)

```
existing Lovspor (lovverk corpus, recorded time, MCP, site)    [A, in production]
   │  reads: lovverk git history, Lovtidend publicData archives (separate cache)
   ▼
provision identity / history                                    [B: legaltime/identity.py, step*, structure.py]
   │  ADR-0023 label lineage: Phases A, B1–B4a merged, unwired; B4b (chapter identity/history) open
   ▼
legal-time reconstruction                                       [B: legaltime replay, legaltime-replay-9 on main; #955 → 10]
   │  0 of 1 540 pilot sections proven under the D7 blanket; owner rulings #169, #171, #172, #173 open (#861)
   ▼
legislative evidence                                            [B: crosswalk P0–P5 merged, release candidate path]
   │  R3/R4 BLOCKED, R2 rejected (2026 archive research_only), dataset repo P6, publish decision, #818
   ▼
optional public read interface                                  [not designed; needs its own ADR]
      ADR-0020 D2/D10/D13, ADR-0017 gate 4: own parameter names, off by default, read-only overlay
```

**Edges proven from code, not assumed:**
- Reconstruction runs **without** the ADR-0023 lineage today, because replay is keyed by label and step evidence is unwired. So "provision identity → reconstruction" is a quality dependency (renames, B4b), not a hard prerequisite.
- Legislative evidence (crosswalk) imports nothing from legaltime, so it can ship **in parallel**. Act-level provenance does not wait for provision-level replay.
- The read interface depends only on the artifacts of the two research nodes, never on lovverk changes.

**Migration M is outside this graph.** No node imports `lovspor.identity` or reads `slugs.json`/`identity.json`. It is not required unless a future design proves otherwise.

**Minimum remaining work, by node:**
1. **Provision history:** nothing needed for an act-level v1. Provision-level dating needs B4b and the D7 reach sets (#861 step 4).
2. **Reconstruction:**
   - owner rulings #169, #171, #172, #173;
   - parsing-only grammar work;
   - proven sections greater than 0 on the eight pilot acts.
   
   Until then every provision verdict is `unresolved` under D7, which a read interface must show as unknown, never as a date.
3. **Legislative evidence:** R3 and R4 rulings, a first signed release candidate, the separate dataset repository (P6), an explicit publication decision, and #818.
4. **Read interface:** a new ADR for:
   - the off-by-default flag, a new MCP tool or parameter name (never a change to `get_law_at`, `get_temporal_events` or `recorded_at`);
   - the overlay store outside lovverk;
   - the "missing or corrupt overlay → existing answers unchanged" rule;
   - licence and attribution for served history.

**Earliest usable additive version (v1):** an off-by-default, read-only, **act-level** provenance overlay. For a law it gives LOV ↔ Lovvedtak ↔ sak with source hashes and explicit unknowns, from a crosswalk release candidate, served by a new tool or page that reads a separate artifact. It needs no provision-level replay, no lovverk change and no migration M.

What blocks it is owner decisions, not engineering on the existing product: R3, R4, P6, the publication decision, the read-interface ADR, and S1 (so that merging v1 does not deploy it implicitly). Provision-level history is v2, blocked by D7 (0 proven).

## Preservation checklist (for every PR that adds history)

- [ ] G1–G7 green, with no golden edited in the same PR (an edited golden is an owner decision, named in the PR).
- [ ] No change to an existing MCP tool's name or parameters. A new tool is a reviewed addition, and off by default.
- [ ] No write to lovverk, by a new command or by sync. The overlay lives outside the corpus.
- [ ] No new `sync.yml` step, secret, ref or push.
- [ ] Missing, corrupt or unreadable history data leaves every existing answer byte-identical (test it).
- [ ] No dependency on `lovspor.identity`, `slugs.json` or `identity.json`, unless proven necessary and decided separately.
- [ ] Research outputs stay `research_only` until their own release gate. Committing research JSON to the public repo is a publication question for the owner (see decision 4).

## Owner decisions (blank; the owner fills these, never an agent)

1. Accept this contract (invariants 1–10 and gates G1–G7) as the rule for legal-history work: ______
2. S1, the production ref pin, main-only dispatch and a `production` environment for `LOVVERK_DEPLOY_KEY` (#898, #956), changes production sync. Authorise or decline: ______
3. #917 changes legacy redirects (invariant 1): hold it until its URL diff on the real corpus is reviewed against `production-urls-c310127e.json`, or accept it as a separate decision: ______
4. Does committing legal-time research JSON (`docs/research/data/legal-time-*.json`) to the public repo count as publication under ADR-0020 D13? ______
5. v1 scope: act-level provenance overlay first (recommended), or wait for provision-level history: ______

## Verdict

**BLOCKED, with exact evidence.**
- **Ready on the engine side:** B is not reached by production (G4, G5). B does not depend on C. The URLs, MCP surface, temporal defaults and deployment boundary are pinned (G1–G3, G6, G7). The real-corpus baseline is reproduced.
- **What blocks ADD-ONLY READY:** invariant 10 cannot hold while `sync.yml` deploys `main` automatically (#898) and a dispatch from any branch can push to lovverk (#956). The only fix (S1) changes production sync and needs the owner's authorisation, which this work may not grant itself.
- The contract itself also awaits owner acceptance.
