# LLHB v1 — Stage 4 Selection Rule

Status: documented selection rule required by FREEZE.md §2.6. Owner
acceptance of this document (merge) authorizes running the selection
tool; the freeze itself (artifacts + tag) is a separate owner step.

## 1. Principle

The 250 frozen cases are selected by a deterministic, documented rule
fixed BEFORE any benchmark model runs, independent of any anticipated
model performance (FREEZE.md §2.6). The rule uses only: the committed
pool artifacts, the owner's committed review decisions, and the frozen
category targets of METHODOLOGY §3. No wording, difficulty or
"interestingness" judgment enters selection — those judgments already
happened in owner review, per case, before this rule runs.

## 2. Sources

Exactly two pools feed selection; all earlier pools (Stage 3, 3.6-E
regen, F2 regen-v3, F3 regen-v4) are superseded evidence and feed
nothing:

1. `dataset/candidates/regen-v5/` — the F4-generator pool, all
   categories.
2. `dataset/candidates/topup-c4/` — the plan-B fresh-seed pool, C4
   only.

Both pools share the corpus pin `6ec7059d…` and the full F2–F4 rule
set; their id ranges (`1xx`, `2xx`) are disjoint.

## 3. Eligibility

A case is eligible when it is not dropped by any owner decision that
covers it:

- **C2 and C4 (100%-review categories):** eligible = cases with an
  explicit owner `keep` — from the v5 queue, the
  `regen-v5/review-c2c4/` genericity slice, or the
  `topup-c4/review-full/` slice. Every C2/C4 case in both pools has
  exactly one such decision; nothing unreviewed is eligible. This is
  the Stage-4 hardening of the semantic-genericity class (ruling #23):
  the generator cannot filter it, so owner eyes cover 100% of these
  two categories.
- **C5 and C8:** eligible = queue `keep` (100% mandatory review has
  always covered them).
- **C1, C3, C6, C7:** eligible = queue `keep` plus non-queued cases
  (machine-validated, covered by the 10% stratified sample per
  FREEZE.md §2.5).

## 4. Selection

Per category, in the fixed order C1…C8:

1. Order eligible cases by ascending `case_id` (numeric id part; the
   sampler's seeded shuffle fixed this order at generation time —
   v5 ids sort before top-up ids).
2. Walk the ordered list, taking a case unless its ground-truth
   provision `(expected_act_slug, expected_section_id)` already has 2
   selected cases in this category (FREEZE.md §2.3 cap). C8 cases carry
   no ground-truth provision (`expected_act_slug` is null — the ground
   truth is abstention), so the cap does not apply to them; C8
   diversity comes from the generation caps and 100% owner review.
3. Stop at the METHODOLOGY §3 target (C1 50, C2 40, C3 35, C4 30,
   C5 15, C6 35, C7 25, C8 20).

Fail closed: if any category cannot reach its target, selection aborts
with a report — no silent shortfall, no cross-category borrowing.

## 5. Gates (all must hold before the tool runs)

1. `review_cli check` reports zero pending for: the v5 queue, the
   `review-c2c4` slice, the `review-full` top-up slice.
2. Both pools verify against the pinned corpus (byte-identical
   regeneration recorded in their PRs).
3. The corpus pin is an ancestor of lovverk `origin/main`, re-verified
   against a fresh fetch at freeze time (FREEZE.md §3).

## 6. Output

- `dataset/frozen/llhb-v1.jsonl` — canonical JSONL (FREEZE.md §4).
- `dataset/frozen/llhb-v1.lock.json` — pins, per-cited-document
  `xml_hash` / `renderer_version` / `embedding_space_id` /
  `embedding_hash`, dataset SHA-256, freeze timestamp, and a reference
  to this document at its governing commit.
- `dataset/frozen/selection-report.json` — per-category eligible
  counts, selected ids, and every cap skip, so the walk is auditable.

The tool writes these only when every gate passes; the owner commits
them, records the FREEZE.md §2.5 sign-off in the notebook research
log, and tags `llhb-v1-freeze`.
