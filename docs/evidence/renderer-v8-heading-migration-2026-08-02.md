# Renderer v8 Heading Migration — Evidence Record (2026-08-02)

Production migration for the h2 `legalArticleHeader` footnote-marker repair
(heading RCA classes A and D; PR #178, merged `bb0499e`, engine commit
`5c6325c`; defect register item 7 in
`lovspor-notebook/docs/adr/evidence/ADR-0004-evidence.md`).

**Provenance:** hand-assembled by the session that implemented, triggered and
verified the migration; every figure measured with the commands in
Verification Method. No pipeline wrote this file.

## The defect

An h2 `legalArticleHeader` rendered through the full inline walk, so sibling
footnote-reference markers landed inside the section heading line:

* **Class A** — 17 headings in 10 files unreadable by `SECTION_HEADING`
  (`## § 21.[^1] Straff`, `## § 1.[^1]`). Body under them fell out of every
  section index; two instruments were fully invisible
  (`instruks-om-delegering-fra-namsretten` 4/4 sections,
  `forskrift-om-utlegg-ved-lønnstrekk` 2/2 — `list_sections` = [], zero
  embedding vectors, `verify_quote` false negatives).
* **Class D** — 21 headings in 3 files that parsed with the marker polluting
  the captured title (`## § 1. Definisjoner[^1]`).

Repair: `_render_h2_legal_article_header` — span-based assembly like h3-h6;
titleless keeps the flat-act dangling dot; every footnote marker dropped
(in-span included, unlike h3-h6); title through `_inline` so link/emphasis
markup survives; no-value-span fallback unchanged. `RENDERER_VERSION` 7 → 8.
The heading grammar was NOT widened.

## Runs and commits

| Run | ID | Result |
|---|---|---|
| v8 migration sync | `30734612451` (workflow_dispatch, 2026-08-02T05:43Z) | success — `0 new, 0 changed, 0 removed, 5864 unchanged` (the 14 stamp-migrated documents are counted by the migration path, not in `unchanged`; 5,864 + 14 = 5,878) |
| No-op proof sync | `30734795059` (workflow_dispatch, 2026-08-02T05:51Z) | success — `0 new, 0 changed, 0 removed, 5878 unchanged`, no new corpus commits |

Corpus commits (`lovverk`): `d5fc99d85` `migration: re-render 14 documents
(renderer v8)` (28 files: 14 `.md` + 14 `embeddings/*.bin`) + `aaaa368d1`
`sync: update manifest, index, and history` (`manifest.json` only).

## Pre-merge dry-run (recorded in PR #178)

`scripts/audit_render_bytes.py --full` over 5,878 current documents at
`lovverk` HEAD `e59f40b41`, cached tarball vintage 2026-07-28: 14
byte-changing documents of 5,809 comparable, 39 headings (40 markers), every
delta exactly 4 bytes × marker count, **retrieved_at-only rendered-byte
churn 0** (acceptance target), fabricated tokens 0, VINTAGE_SKEW 69 (the
2026-07-31/08-01 upstream wave), manifest-only `last_seen` reconciliations
pending 3,695 (2,183 already consistent).

## Measured outcomes

* Migration commit `d5fc99d85`: exactly the 14 predicted documents; every
  `.md` diff is line-for-line marker removal on heading lines only
  (`## § 21.[^1] Straff` → `## § 21. Straff`); frontmatter lines touched:
  **0** — `retrieved_at` preserved, the PR #177 semantics holding through
  their first real migration [FACT, `--numstat` + diff grep].
* `retrieved_at`-only document changes: **0** [FACT].
* Manifest (`aaaa368d1`, vs pre-migration `e59f40b41`): current 5,878/5,878
  at `renderer_version: 8`; `xml_hash` changes 0; `last_changed` bumps 0;
  `total_changes` bumps 0; records added/removed 0 [FACT].
* Historical drift reconciled: file `retrieved_at` == manifest `last_seen`
  for **5,878/5,878** current records (was 3,695 drifted pre-migration);
  manifest-only, no file rewritten [FACT].
* history/: **0** files touched in either commit; INDEX: untouched — zero
  phantom law-change events [FACT].
* Post-migration audit: exit 0; `unparsed_section_heading` 18 → **1** (the
  class B `§ x-1` finding in `forskrift-om-kulturhistoriske-eiendommer`,
  still open by design); no other findings [FACT].
* Class A residue: `grep -E '^## §.*\[\^'` over the corpus — **0** lines.
  Class D residue on h2: **0**. Two h3 in-span title markers remain
  (`restkontrollforskriften` § 8, `bilansvarslova` § 19) — deliberate,
  h3-h6 delimit in-span markers by documented design [FACT].
* Embeddings: `instruks-om-delegering-fra-namsretten.bin` 16 B → 12,288 B;
  `forskrift-om-utlegg-ved-lønnstrekk.bin` 16 B → 6,164 B (16 B = the empty
  header of a document with zero vectors) [FACT].
* Hosted MCP (droplet corpus refreshed to `aaaa368d1`,
  `lovspor-fetch-corpus.service` 05:48Z):
  `get_section("eos-kontrolloven", "21")` → `§ 21. Straff`, clean title,
  all six cross-references valid; `list_sections` 4/4 and 2/2 for the two
  recovered instruments; `validate_citation("§ 21 eos-kontrolloven")` →
  `valid: true`; `verify_quote` of the § 21 penalty clause → `verified:
  true`; `corpus_status` fresh, age 0 days [FACT].
* Git history not rewritten: forward commits only, fast-forward pull.

## Still open (out of scope, tracked in defect register item 7)

Class B (`§ x-1` parser support), class C (`§§` range headings — invisible
to the audit; `forsk-om-premie-til-fiskerpensjon`'s `## §§ 6-8.` heading was
cleaned of its marker by this migration but remains outside the grammar),
`_CITATION_SECTION_ID` suffix handling, NLOD history backfill.

## Verification method

```bash
gh run view 30734612451 / 30734795059 --log | grep 'Sync complete'
git -C lovverk show d5fc99d85 --numstat            # 28 files, heading lines only
git -C lovverk show d5fc99d85 | grep -E '^[+-]retrieved_at' | wc -l   # 0
# manifest diff e59f40b41 vs aaaa368d1: renderer_version {8: 5878},
# xml_hash 0, last_changed 0, total_changes 0; retrieved_at==last_seen 5878/5878
grep -rEc '^## §.*\[\^' lovverk/lover lovverk/forskrifter              # 0
uv run lovspor audit --corpus-path <lovverk>       # exit 0, 1 advisory (class B)
# hosted: get_section / list_sections / validate_citation / verify_quote /
# corpus_status against https://lovspor.bartoszkobylinski.com/mcp
```
