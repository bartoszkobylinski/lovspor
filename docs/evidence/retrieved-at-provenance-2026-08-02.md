# retrieved_at Provenance Drift — RCA and Fix Evidence (2026-08-02)

RCA and correction of the renderer-migration provenance drift. Blocks
nothing retroactively; **must be merged before any RENDERER_VERSION 8
migration**.

**Provenance:** hand-assembled by the session that performed the read-only
RCA and implemented the fix; every figure measured (commands in the RCA
session and the dry-run below). No pipeline wrote this file.

## The defect — a self-perpetuating churn loop

Renderer-only migrations seeded the file's `retrieved_at` from the manifest
`last_seen` (`_rerender_upstream`), while `_write_one` unconditionally
re-stamped the record's `last_seen = now` on every write. Each migration
therefore healed yesterday's drift into the file and simultaneously
manufactured tomorrow's in the manifest — preservation read from a field
its own write invalidated.

Measured churn (retrieved_at-only file changes):

* v6 migration (`1dc065f06`, 2026-07-30): **3,624 of 3,775** changed files.
* v7 migration (`a2fba0656`, 2026-08-01): **3,693 of 3,695**.

Evidence chain for one record (`nl-20120622-043`): pre-v5 file `07-27` ==
`last_seen` `07-27`; the v5 stamp path bumped `last_seen` → `07-28` with the
file untouched; v6 wrote the file to `07-28` while stamping `last_seen` →
`07-30`; v7 wrote `07-30` while stamping `08-01`. 300/300 sampled v7-healed
documents showed the identical pattern.

## Semantic contract (now recorded in docs/data-model.md)

`retrieved_at` and `last_seen` are the SAME source-observation timestamp —
when the sync first retrieved/observed the current upstream content
version. Invariant: XML unchanged → both preserved and equal; XML changed →
both advance to the observing sync's time. A renderer-only re-render is an
artifact-generation event, not an observation; the v1 model deliberately
has no artifact-render timestamp.

## The correction (implementation-only; no renderer change, no version bump)

* `_published_retrieved_at` / `_preserved_observation`
  (`sync/orchestrator.py`): the seed for renderer-only writes is the
  Published Rendering's own frontmatter `retrieved_at` — the side that
  never lied; a drifted prior `last_seen` is never trusted (fallback only
  when the file value is unrecoverable).
* `_write_one` stamps the record's `last_seen` from the same carried
  observation (`upstream.retrieved_at or now`), so both sides always agree.
* The byte-identical no-op path reconciles a historically drifted manifest
  `last_seen` to the file value — manifest-only, no file rewritten, no
  standalone migration.
* Renamed-document and legacy-migration write paths use the same preserved
  observation.

Regression tests (deterministic timestamps): unchanged sync preserves both;
content update advances both; renderer-only migration preserves both while
applying the fix; drifted state (file T1 / manifest T2) recovers to T1/T1
with T2 untrusted; repeated migrations reach a byte-identical fixpoint.

## Dry-run of the pending v8 migration (2026-08-02, NOT executed)

`scripts/audit_render_bytes.py --full` (seeds contexts from each file's own
frontmatter — the fixed mechanism) over all 5,878 current documents at
`lovverk` HEAD `e59f40b41`, cached tarball vintage 2026-07-28:

* Comparable documents: 5,809 — **0 byte-changing**, 0 FLAG.
* **retrieved_at-only rendered-byte churn: 0** (acceptance target met).
* Manifest-only `last_seen` reconciliations pending: **3,695** (the v7-healed
  set; file `07-30` vs manifest `08-01`); 2,183 records already consistent.
  These reconcile as manifest-only updates on the next legitimate renderer
  migration — reported separately from renderer output changes, per design.
* VINTAGE_SKEW: 69 — documents whose upstream changed after the cached
  tarball date (the 2026-07-31/08-01 upstream wave); not comparable, not
  churn.
* Unexpected changes: none.

A future v8's file-write surface therefore reduces to the documents its
renderer change actually affects, plus one manifest-only reconciliation
pass.
