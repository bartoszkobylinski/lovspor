# Renderer v6 Production Migration — Evidence Record (2026-07-30)

Permanent evidence record of the renderer v5→v6 production corpus
migration, intended as primary evidence for a future ADR-0005.

**Provenance:** hand-assembled on 2026-07-30 by the interactive drafting
session that triggered and observed the migration. Every figure below was
measured in that session with the commands listed under Verification
Method — none is quoted from memory or from another document. The audit
output is pasted verbatim. `source: manual-unverified` does not apply to
the figures (each was measured); it applies only in the sense that no
pipeline wrote this file.

Corpus refs: pre-migration HEAD `e4270e4058de8e9eb881b47ff029359656a046e5`
(2026-07-30T06:25:45Z); post-migration HEAD
`e3660c4e255e787957dd5b3923acef528889e26b`. Engine at merge
`cbecde9` (PR #171, `fix/renderer-heading-spill-and-link-escape`,
2026-07-30T15:23:17+02:00), which bumped `RENDERER_VERSION` 5→6
(commit `6381e2f`).

---

## Workflow runs

| Run | ID | Trigger | Started (UTC) | Finished (UTC) | Conclusion |
|---|---|---|---|---|---|
| Migration sync | `30569659284` | `workflow_dispatch` | 2026-07-30T18:16:08Z | 2026-07-30T19:38:00Z | success |
| No-op proof sync | `30576140691` | `workflow_dispatch` | 2026-07-30T19:44:37Z | 2026-07-30T19:46:04Z | success |

The scheduled 04:00 UTC run of 2026-07-30 (`30519425616`, corpus push
`e4270e405` at 06:25:45Z) predated the v6 merge (13:23 UTC), so the
migration was triggered manually via `workflow_dispatch` — the sanctioned
production path (`.github/workflows/sync.yml` documents manual dispatch
support). Commits are authored by the sync bot, not a workstation.

## Migration commits (lovverk)

| SHA | Committed (UTC) | Subject |
|---|---|---|
| `1dc065f06b26d69771a7c3a2a4ff493d723e3115` | 2026-07-30T19:05:10Z | `migration: re-render 3775 documents (renderer v6)` |
| `e3660c4e255e787957dd5b3923acef528889e26b` | 2026-07-30T19:37:31Z | `sync: update manifest, index, and history` |

No other commits were produced by either run. In particular: **zero**
`update(lov)` / `update(forskrift)` commits.

## Re-render counts

* **3,775 documents re-rendered** (672 `lover/` + 3,103 `forskrifter/`),
  matching the commit subject exactly. Same order of magnitude as the v5
  migration (`51c074be7`, 3,786 documents).
* **151 documents had real body changes** (>2 changed lines by
  `--numstat`; the renderer fix: heading-spill splitting and link
  escaping — largest: `forskrifter/vinforskriften.md` +173/−1).
* **3,624 documents changed only frontmatter** — healing accumulated
  `retrieved_at` drift between file frontmatter and the sync context
  (files byte-unchanged in earlier syncs kept stale `retrieved_at`;
  the rewrite realigned them). Manifest records carry no `retrieved_at`
  field, so the manifest was unaffected by this healing.
* **3,123 embedding sidecars (`.bin`) refreshed** in the same migration
  commit (2,562 forskrifter + 561 lover). Total files in the migration
  commit: 6,898 (3,775 `.md` + 3,123 `.bin`), matching `git show --stat`.
* The remaining **2,139 current documents re-rendered byte-identically**
  and received only the manifest `renderer_version` stamp (no file
  content in the commit), per the peeling design in
  `src/lovspor/sync/orchestrator.py:1185-1200`.

## Manifest invariants (pre `e4270e405` vs post `e3660c4e2`)

Measured by loading both manifests and comparing per-record:

| Field / property | Result |
|---|---|
| Current documents | 5,914 (unchanged) |
| Removed documents | 7 (unchanged) |
| `renderer_version` histogram (current) | `{6: 5914}` — 100% on v6 |
| `xml_hash` changed | **0** records |
| `embedding_hash` changed | **0** records |
| `last_changed` changed (all statuses) | **0** records |
| `total_changes` changed (all statuses) | **0** records |
| `last_seen` bumped | 3,775 records — exactly the re-rendered set |
| Ids added / removed | 0 / 0 — membership unchanged |

## No corpus-change-history events

Three independent confirmations that the migration was not classified as
a change of the law:

1. Zero `update(lov)`/`update(forskrift)` commits (see above).
2. `git diff e4270e405 e3660c4e2 --stat -- lover/history forskrifter/history
   lover/INDEX.md forskrifter/INDEX.md` → **empty**. No history file and
   no INDEX changed in either commit; the tail commit touched
   `manifest.json` only.
3. `last_changed` and `total_changes` unchanged on every record (table
   above). This is the designed behaviour: the history classifier
   returns no event for subjects matching `^migration: re-render `
   (`src/lovspor/history.py:241-246`).

## Corpus audit result (post-migration)

`uv run lovspor audit --corpus-path <lovverk>` at post-migration HEAD —
exit code 1, output verbatim:

```text
Corpus drift: 20 finding(s) across 5914 current document(s).

unparsed_section_heading (18):
  forskrifter/forskrift-om-kulturhistoriske-eiendommer.md
  forskrifter/forskrift-om-sanksjoner-mot-irak.md
  forskrifter/forskrift-om-særfradrag-p-g-a-sykdomsutgifter.md
  forskrifter/forskrift-om-tariff-for-lastelinjesertifikat.md
  forskrifter/forskrift-om-tariff-for-lastelinjesertifikat.md
  forskrifter/forskrift-om-trafikktrygd.md
  forskrifter/forskrift-om-trafikktrygd.md
  forskrifter/forskrift-om-utlegg-ved-lønnstrekk.md
  forskrifter/forskrift-om-utlegg-ved-lønnstrekk.md
  forskrifter/instruks-om-delegering-fra-namsretten.md
  forskrifter/instruks-om-delegering-fra-namsretten.md
  forskrifter/instruks-om-delegering-fra-namsretten.md
  forskrifter/instruks-om-delegering-fra-namsretten.md
  lover/eos-kontrolloven.md
  lover/forbrukermerkeloven.md
  lover/forsikringslovvalgsloven-intprfal.md
  lover/forsikringslovvalgsloven-intprfal.md
  lover/lov-om-kredittvurderingsbyråer.md

orphan_embedding (1):
  forskrifter/embeddings/endr-i-økodesignforskriften.bin

tombstoned_but_present (1):
  forskrifter/endr-i-økodesignforskriften.md
```

**`stale_render`: 0** — the migration's objective. (For contrast, the
same audit run against the pre-migration tree in a throwaway worktree
reported 5,934 findings: 5,914 `stale_render` — every current document,
v5 stamps against v6 code — plus the identical 20 below-the-line
findings.)

## Pre-existing findings — NOT caused by this migration

All 20 remaining findings pre-date the migration; the pre-migration
audit reported the identical set:

* `unparsed_section_heading` × 18 (12 distinct files, some with multiple
  offending sections) — identical list before and after migration.
  Pre-existing renderer/section-parsing debt; registered 2026-07-30 as
  item 7 of the ADR-0004 integrity-defect register
  (`lovspor-notebook/docs/adr/evidence/ADR-0004-evidence.md` §6).
* `tombstoned_but_present`: `forskrifter/endr-i-økodesignforskriften.md`
  and its `orphan_embedding` sidecar — the known integrity defect
  already registered in ADR-0004's evidence companion
  (`lovspor-notebook/docs/adr/evidence/ADR-0004-evidence.md`, defect
  register). Deliberately not fixed during the migration, per the
  project-owner ruling of 2026-07-30 (defects are follow-up
  implementation work).

## No-op confirmation

Second production sync (`30576140691`), triggered ~7 minutes after the
migration run finished, completed in 87 seconds with the engine log
line (verbatim from the Actions log):

```text
Sync complete at /home/runner/work/lovspor/lovverk: 0 new, 0 changed, 0 removed, 5914 unchanged.
```

and produced **zero new commits** in `lovverk` (`origin/main` HEAD still
`e3660c4e2` after the run). The migration is convergent: renderer stamps
match `RENDERER_VERSION = 6`, upstream XML unchanged, nothing left to do.

## Verification method

Commands used for the figures above (run 2026-07-30, from the local
clones; lovverk pulled to `e3660c4e2` first):

```bash
git log --format='%H %cI %s' -3 origin/main
git diff e4270e405 1dc065f06 --numstat -- '*.md' \
  | awk '{tot++; if ($1+$2>2) body++} END {print tot, body}'
git diff-tree --no-commit-id --name-only -r 1dc065f06   # md/bin breakdown
git diff e4270e405 e3660c4e2 --stat -- lover/history forskrifter/history \
  lover/INDEX.md forskrifter/INDEX.md                    # -> empty
# manifest comparison: python, git show e4270e405:manifest.json vs HEAD,
# per-record diff of renderer_version / xml_hash / embedding_hash /
# last_changed / total_changes / last_seen / membership
uv run lovspor audit --corpus-path <lovverk>             # exit 1, output above
gh run view 30569659284 / 30576140691 --json ...         # run metadata
```
