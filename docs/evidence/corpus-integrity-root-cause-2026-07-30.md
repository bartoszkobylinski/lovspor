# Corpus integrity — root-cause analysis of ADR-0004 register defects 1–3

Date: 2026-07-30. Status: analysis only — **no correction executed**.

## 1. Scope and method

- **[FACT]** Read-only investigation. Repositories and refs used:
  - Corpus: `/Users/bartoszkobylinski/Programming/Python/lovverk` at HEAD `e3660c4e2` (post renderer-v6 migration, 2026-07-30). Historical states inspected only via `git show <sha>:<path>`, `git log`, `git diff` — no checkout, no worktree.
  - Engine: `/Users/bartoszkobylinski/Programming/Python/lovspor` (working tree, `main`). Engine code at defect time verified per defect date:
    - **2026-07-24 run (defects 1+2):** deployed `main` tip was `d20aa3d` (2026-07-23 22:20:50; no later `main` commit before the 06:23 sync — verified via `git log --since/--until`). `git diff d20aa3d main -- src/lovspor/sync/` is empty (0 lines) — the sync code that ran is byte-identical to HEAD.
    - **2026-07-14 run (defect 3):** deployed `main` tip was `a845ec1` (2026-07-13 17:56:35; the next `main` commit is 2026-07-14 08:00, after the 06:08 sync). `git diff a845ec1 main -- src/lovspor/sync/` is **not** empty: the sole change is `63d0825` (2026-07-23, embeddings-staleness helpers `_embedding_section_count` → `_expected_section_id_counts`/`_stored_section_id_counts`), confined to `orchestrator.py` at lines ≥1595 (+34/−15; hunks `@@ -1595` and `@@ -1627`) — above every orchestrator line cited below (500-547, 698-704, 1089-1110, 1356). The defect-3 files outside `sync/` are covered separately: `git diff a845ec1 main -- src/lovspor/rendering/slug.py src/lovspor/history.py` is empty (slug.py unchanged since `6691c42`, 2026-07-04; history.py since `37f1c64`, 2026-07-11).
    - So every line citation below describes the code that actually ran on its defect date.
  - Register: `/Users/bartoszkobylinski/Programming/Python/lovspor-notebook/docs/adr/evidence/ADR-0004-evidence.md` §6 (items at lines 570 and 581).
- Defects analyzed (the first three items of the ADR-0004 integrity-defect register):
  1. `sf-20260305-0354` (`endr-i-økodesignforskriften`): manifest `removed`, file still on disk.
  2. Orphan embedding sidecar `forskrifter/embeddings/endr-i-økodesignforskriften.bin`.
  3. Two manifest ids (`sf-20090520-0534` removed, `sf-20260710-1545` current) sharing `forskrifter/forskrift-om-omregningsfaktorer.md`.
- Governing rulings respected: ADR-0003 (lovverk git history is append-only, no rewrite) and ADR-0004 (Accepted 2026-07-30: `manifest.json` is authoritative for current corpus membership).
- Method: manifest bisect via `git show <sha>:manifest.json`, full `git log --follow` per path, commit forensics with `git diff-tree --name-status`, engine code reading, and live reproduction of the failure predicate against the corpus repo. Every load-bearing claim below was verified directly against the repositories during this analysis, not taken on faith from intermediate notes.

## 2. Defect 1 — `endr-i-økodesignforskriften`: tombstoned in manifest, file never deleted

### 2.1 Complete git history of the id and path

`git log --follow -- forskrifter/endr-i-økodesignforskriften.md` (complete, verified — 5 commits, no deletion ever):

| Commit | Date | Subject | What changed |
|---|---|---|---|
| `57c305290` | 2026-04-26 | sync: 4522 new, 0 changed, 0 removed | Born as `forskrifter/sf-20260305-0354.md` |
| `3dddeca2a` | 2026-04-27 | migration: rename 4522 documents to slug-based filenames | Renamed to `forskrifter/endr-i-økodesignforskriften.md` |
| `3d499af6b` | 2026-04-29 | migration: backfill eu_basis for 4523 documents | eu_basis added |
| `afd229bde` | 2026-07-10 | migration: re-render 2016 documents (renderer v1) | Re-render |
| `1e97c3804` | 2026-07-11 | migration: re-render 2047 documents (renderer v3) | **Last content change ever** |

Manifest side (bisect of `manifest.json`):

| Commit | Date | Record status |
|---|---|---|
| `0c27c3d8c` … `ffcc89628` | 2026-07-11 → 2026-07-23 | `current` (last_seen `2026-07-11T14:44:44.915821Z`) |
| **`3baf017db`** | **2026-07-24 06:23:52, author `lovspor-sync[bot]`** | **flipped to `removed`** — the only record flipped in that commit |
| `b67637e42` … `e3660c4e2` (HEAD) | 2026-07-25 → 2026-07-30 | `removed`, unchanged |

- **[FACT]** The flip commit `3baf017db` (`sync: update manifest, index, and history`) touched `manifest.json`, `forskrifter/INDEX.md` (delisted the slug, 5153 → 5152), and history files of three *other* (updated) documents. It deleted **zero** files: `git diff-tree --name-status -r 3baf017db | awk '$1=="D"' | wc -l` → 0. Verified directly.
- **[FACT]** No `remove(forskrift): endr-i-økodesignforskriften` commit exists anywhere: `git log --all --grep='endr-i-økodesignforskriften'` is empty; `git log --diff-filter=D --follow` on the path is empty. Verified directly.
- **[FACT]** Healthy siblings all got a per-doc remove commit deleting md + bin, e.g. `f79f0f0b1` (2026-07-11, `remove(forskrift): endr-i-bilforskriften`, `D` md + `D` bin — verified), `5909363d7` (forsvars-og-sikkerhetsanskaffelser), `a1414557b` (`remove(lov): endringslov-til-alkoholloven`).
- **[FACT]** The manifest record at HEAD carries the exact `_tombstone()` field signature — `embedding_hash`/`eu_basis`/`last_changed`/`renderer_version`/`total_changes` all `null`, `removed_reason: null`, `xml_hash 77dfe69b…` (verified against `HEAD:manifest.json`). `_tombstone` (`lovspor/src/lovspor/sync/orchestrator.py:1089-1110`, verified) rebuilds a record from only the fields it passes; its sole call site is the `changes.removed` loop (`orchestrator.py:504`). The doc genuinely left the upstream dataset that day.
- **[FACT]** Why removed 2026-07-24: the endring's own `date_in_force` is `2026-07-24` (file frontmatter, verified at HEAD), and the parent `økodesignforskriften` was updated in the same run (`2e0738c59`; parent record `last_changed: 2026-07-24`). Lovdata dropped the amendment the day it entered force and folded it into the parent.

### 2.2 Root cause — pick with evidence

**Removal-flow bug in the engine: `_drop_orphan_paths` silently discards non-ASCII deletion paths.** Not a rename, not a slug collision, not a placeholder, not migration logic.

Chain (all code verified in `lovspor` at `main`, byte-identical to the deployed `d20aa3d`):

1. Removal loop (`src/lovspor/sync/orchestrator.py:500-547`): tombstones the record (line 504), then `delete_document(prior_path)` (line 518; `path.unlink(missing_ok=True)`, `sync/document_io.py:116-118`) and unlinks the `.bin` sidecar (line 532) — filesystem-only operations on the CI runner.
2. Per-doc commit (`orchestrator.py:1262-1272`, `_commit_per_doc_actions_only`, verified): `git_add(repo, action.all_paths_to_stage)`, then commit **only** `if has_staged_changes(repo)`.
3. `git_add` filters every path through `_drop_orphan_paths` (`src/lovspor/sync/git_commit.py:90, 96-119`, verified):
   ```python
   result = _run(["ls-files", "--", *rel_paths], cwd=repo_abs)
   tracked = set(result.stdout.splitlines())
   ...
   if p in tracked or (repo_abs / p).exists():
       kept.append(p)
   ```
4. **[FACT]** `git ls-files` C-quotes non-ASCII paths by default (`core.quotepath=true`). Reproduced live in lovverk during this analysis: `git ls-files -- "forskrifter/endr-i-økodesignforskriften.md"` returns literally `"forskrifter/endr-i-\303\270kodesignforskriften.md"` — quotes plus octal escapes. The raw UTF-8 string `p` is therefore **not** in `tracked` (membership test reproduced False), and `.exists()` is also False because step 1 already unlinked the file. Both the `.md` and `.bin` paths are classified "orphans" and dropped; `git_add` early-returns on an empty list (`git_commit.py:91-92`); nothing is staged; the `remove(...)` commit is skipped. The working-tree deletion evaporated with the ephemeral CI runner; only the manifest tombstone rode the tail sync commit (`3baf017db`).
5. **[FACT]** Neither `_run` (`git_commit.py:33-49`) nor the sync workflow sets `core.quotepath`: `.github/workflows/sync.yml` configures only `user.name`/`user.email`/`commit.gpgsign` (lines 140-142, verified; `grep quotepath` → no hits).
6. **[FACT]** Perfect correlation: every ASCII-slugged tombstone had its files deleted by a per-doc remove commit; the **only** non-ASCII slug ever removed is the **only** orphan. Distribution proof from the earlier era: the manual cleanup `dd49c96e0` (2026-07-13, "remove 48 orphaned documents and their embedding sidecars") deleted 82 paths — **all 82 git-quoted, i.e. non-ASCII** (verified: 82 `D` entries, 82 starting with `"`), while no automated `remove(...)` commit in lovverk history ever deleted a non-ASCII path.
7. **[RATIONALE]** The in-sync path-collision skip (`orchestrator.py:515-516`, `prior_path in written_paths`) cannot have fired: the run's per-doc commits (`bc6b71b4c`, `2e0738c59`, `2811cbcb7`) wrote different paths. The only remaining gate between an executed removal action and a missing commit is `_drop_orphan_paths`.
8. **[FACT]** Irony: `_drop_orphan_paths` was added as a robustness fix (docstring `git_commit.py:97-110`; lovspor `1d52e29`, 2026-05-05) to recover from manifest-vs-tree drift — and its comparison bug is what created this drift.
- **[OPEN]** The CI runner's log for the 2026-07-24 run is not preserved in either repo, so the runtime trace is inferred, not observed. The code identity, the live reproduction of the failing predicate, the ASCII/non-ASCII sibling asymmetry, and the `_tombstone` field signature admit no competing explanation consistent with all four observed facts (flip present, remove commit absent, both files alive, INDEX delisted).
- Unicode normalization ruled out: `ø` (U+00F8) has no NFD decomposition; tree, manifest, and frontmatter all carry NFC bytes `c3 b8`. The quoting of git's *output* is the sole mismatch.

### 2.3 Which artifact reflects intended current upstream state

**The manifest.** Per ADR-0004 (Accepted 2026-07-30), `manifest.json` is authoritative for current corpus membership. Here that ruling matches the evidence independently: the doc left `gjeldende-sentrale-forskrifter` on 2026-07-24 (§2.1), so `status: "removed"` is the true upstream state. The on-disk file is a stale renderer-v3 artifact (last content commit `1e97c3804`, 2026-07-11) whose frontmatter `status: "current"` predates the tombstone — v6 migration `1dc065f06` correctly did **not** re-render it (its diff-tree, read with `core.quotepath=off`, touches only the parent `økodesignforskriften.md`/`.bin`), and `renderer_version` stays `null`. Frontmatter and disk are both wrong; INDEX.md (slug absent) agrees with the manifest.

The MCP layer already behaves per the ruling: `_load_slug_index` filters `status == "current"` (`lovspor/src/lovspor/mcp.py:1746-1765`, verified), and body/embedding indexes iterate current records only (`mcp.py:1613-1618`, `1428-1430`) — so `get_law`/`list_sections` for this slug raise `CorpusNotFoundError` and the stale `.md`/`.bin` never leak into any tool. The residual harm is a file that reads as law in force to anyone browsing the raw repo.

### 2.4 Can the next normal sync self-heal? — NO (code-cited)

- `detect_changes` builds `manifest_current` from `status == "current"` records only (`lovspor/src/lovspor/sync/change_detector.py:45-48`, verified) — a tombstoned doc can never re-enter `changes.removed`, so the deletion is never retried.
- `_carry_tombstones` carries the record verbatim across syncs (`orchestrator.py:1082-1086`).
- Nothing in sync compares manifest to disk — stated outright in `corpus_audit.py:1-11` (verified docstring): "Nothing compares the *manifest* against *disk* — so a file that has fallen out of the manifest is invisible to all of them and can never self-heal."
- `audit_corpus` **does** detect it (`tombstoned_but_present`) but is read-only and manual-CLI-only (`cli.py:256-281`), wired into no workflow (`grep -r audit .github/workflows/` → no hits, verified). It **existed at damage time** (`a0a8936`, 2026-07-13 — eleven days before the 2026-07-24 sync; only the 48-orphan era of 2026-05-19 → 2026-07-03 that its docstring cites predates it), but nothing ever ran it against the 2026-07-24 result — no evidence of any audit run appears in either repo before the 2026-07-30 register.

### 2.5 Which invariant/test failed

The invariant "`git_add` stages a deletion for a tracked-but-missing path" **was tested — ASCII-only**: `tests/unit/test_sync_git_commit.py:114-123` `test_add_stages_deletion_for_tracked_missing_path` uses `tracked.txt` (verified; the neighbouring test even covers dash-prefixed hostile names). `grep -c '[øæåØÆÅ]' tests/unit/test_sync_git_commit.py` → **0** (verified) — zero Norwegian letters in the git-commit test file, and no removal test anywhere uses a non-ASCII slug, in an engine whose corpus is Norwegian law. With an `ø` in the filename the existing test fails on current code.

## 3. Defect 2 — orphan embedding sidecar `endr-i-økodesignforskriften.bin`

**Coupled to defect 1 — same event, same root cause, second arm.** Evidence:

- **[FACT]** Complete history of the sidecar is a single commit: `65a8d4ba4` (2026-06-11, "migration: backfill embeddings for 4134 documents") — born and never touched again (verified: `git log` on the path shows exactly one commit).
- **[FACT]** The file is 16 bytes: `4c53 5045 0100 0000 000c 0000 0000 803f` = magic `LSPE`, version 1, **0 sections**, dim 3072, scale 1.0 (verified by `xxd`). This is **intentional**, not corruption: `_write_embeddings_for_doc` (`orchestrator.py:931-962`) writes a header-only file for docs with no `### §` sections; the endring's body has only `## I`/`## II`/`## Forordninger` headings. The doc was therefore never semantically searchable, and was correctly absent from the 2026-07-23 flag-797 re-embed batch (`ae66190ff` left the record byte-identical).
- **[FACT]** The removal loop unlinks the sidecar (`orchestrator.py:526-533`) and stages it via the same `git_add` → `_drop_orphan_paths` path as the `.md` — the identical quoting mismatch dropped it from staging in the same skipped `remove(...)` commit (§2.2). The defect is solely that the file should have been deleted with the document on 2026-07-24.
- Self-heal: **NO.** The only sidecar reconciliation, `_needs_sprint9_embeddings_migration` (`orchestrator.py:1525-1535`, verified), skips tombstones with the comment "their files don't exist" — a false assumption in exactly this defect; no code path deletes a `.bin` unowned by any current record. `audit_corpus` detects it (`orphan_embedding`) but only manually, after the fact.
- MCP effect: none — `_load_embedding_index` iterates current manifest records only (`mcp.py:1428-1430`), so the orphan (and empty) vector file can never influence `semantic_search`.
- Failed invariant/test: same as defect 1 (the sidecar deletion is staged through the same `git_add` call).

## 4. Defect 3 — two ids on `forskrifter/forskrift-om-omregningsfaktorer.md`

### 4.1 Two DISTINCT upstream documents

**[FACT]** (frontmatter of the same file before/after the overwrite, `git show ce3df5a13~1:` vs `ce3df5a13:`):

| | `sf-20090520-0534` (tombstone) | `sf-20260710-1545` (current) |
|---|---|---|
| `ref_id` | `forskrift/2009-05-20-534` | `forskrift/2026-07-10-1545` |
| `title` | Forskrift om omregningsfaktorer fra produktvekt og antall til rund vekt | Forskrift om bruk av omregningsfaktorer for omregning fra produktvekt og antall til rund vekt (forskrift om omregningsfaktorer) |
| `short_title` | `Forskrift om omregningsfaktorer` | `Forskrift om omregningsfaktorer` — **identical** |
| `date_in_force` | 2009-05-20 | 2026-07-10 |

Two distinct real forskrifter — the 2009 act repealed and replaced upstream by a 2026 act carrying the same official kortform. **[FACT]** This is the only duplicate `markdown_path` among all 5921 manifest records at HEAD (verified by counter over every record).

### 4.2 History of both ids and the path

| Commit | Date | Subject | What changed |
|---|---|---|---|
| `57c305290` | 2026-04-26 | sync: 4522 new… | Old doc born as `forskrifter/sf-20090520-0534.md` |
| `3dddeca2a` | 2026-04-27 | migration: rename… | Renamed to the slug path |
| `0c40d0bf3` | 2026-04-27 | migration: generate history… | History files created with `doc_id: sf-20090520-0534` |
| `3d499af6b` | 2026-04-29 | migration: backfill eu_basis | `last_seen` bumped to `2026-04-29T11:20:30` — the value frozen in today's tombstone (verified at HEAD) |
| …through `1cac8a607` | → 2026-07-12 | daily syncs | `status: current` throughout |
| **`ce3df5a13`** | **2026-07-14 06:08:39** | **`add(forskrift): forskrift-om-omregningsfaktorer`** | **`M` (not `A`)** on both the `.md` and the `.bin` (verified `--name-status`) — the new doc **overwrote the old doc's file and sidecar in place** |
| **`afaf0d03e`** | **2026-07-14 06:08:41** | sync: update manifest, index, and history | Old id tombstoned (path kept), new id registered on the **same** `markdown_path`; history files re-stamped `doc_id: sf-20260710-1545` |
| `85177318e`, `51c074be7`, `1dc065f06` | 07-27 → 07-30 | renderer v4/v5/v6 | Re-renders of the (new) doc |

**[FACT]** No `remove(forskrift)` commit for the old id ever, no omregningsfaktorer file deletion anywhere in history, and never a second file for the new id (`git log --all -S'sf-20260710-1545'` hits only `ce3df5a13` and `afaf0d03e`). PR #3's orphan cleanup (`dd49c96e0`/`a43a008e6`, same day) touched neither this file nor `manifest.json`.

### 4.3 Root cause — pick with evidence

**Slug collision between a repealed doc and its same-short-title replacement, with collision resolution blind to the manifest** — not a rename onto an occupied path, not a tombstone reassigning a path (the tombstone *kept* its path; the *add* took the file over).

1. `derive_slug` prefers `short_title` (`lovspor/src/lovspor/rendering/slug.py:54-66`, verified: `candidate = short_title or _strip_brackets(title) or doc_id`) → both acts derive `forskrift-om-omregningsfaktorer`.
2. `resolve_collisions` (`slug.py:68-89`, verified) is called only over the **current upstream tarball, per dataset** (`orchestrator.py:698-704`, verified: `base_slugs = {doc.doc_id: doc.slug for doc in docs}`). On 2026-07-14 the 2009 doc had left the tarball, so the resolver saw no competitor and gave the new doc the bare slug — the tombstone-to-be's exact `markdown_path`. **There is no uniqueness check against the prior manifest's paths, tombstoned or otherwise.**
3. The add phase wrote the new doc onto that path (`ce3df5a13`, `M` not `A`), also overwriting the embeddings sidecar (bin 15386 → 30757 bytes).
4. The removal loop's **skip-if-reused guard** (`orchestrator.py:515-516` for the `.md`, `529-530` for the `.bin`; verified, with the in-code comment citing the "Production crash 2026-05-05 reproducer") saw `prior_path in written_paths` and set `remove_paths = ()` — a **designed manifest-only tombstone**. Correct at file level: deleting would have wiped the new owner's text in force. This — not the defect-1 quoting bug — is why no remove commit exists here.
5. `_tombstone` preserves `markdown_path` **by design** ("audit trail", `orchestrator.py:1089-1110`, verified), and nothing ever reconciles the pointer afterwards. **[DECISION → defect]** The unreconciled tombstone pointer is the actual manifest defect: the path-ownership invariant is enforced on disk but never in the manifest.

### 4.4 The proven harm — identity conflation in history / timetravel

- **[FACT]** History is keyed by slug and built via `git log --follow` on the file path (`lovspor/src/lovspor/history.py:123-137`, verified: "`--follow` traces back through renames"). `--follow` knows paths, not doc ids — so the walk crosses the 2026-07-14 identity boundary into the 2009 act's commits.
- **[FACT]** Verified at HEAD: `forskrifter/history/forskrift-om-omregningsfaktorer.json` carries `doc_id: sf-20260710-1545` yet contains 4 events including the **old** act's rename (`from_path: forskrifter/sf-20090520-0534.md`, 2026-04-27) and the old act's birth (2026-04-26). Its "added" event for `ce3df5a` records `+51/−25` lines — an "add" with removals, betraying the overwrite.
- **[FACT]** This contaminates the manifest: `total_changes = len(history.events)` (`orchestrator.py:1356`, verified), so the new record claims `total_changes: 4` (verified at HEAD) despite exactly 1 corpus event of its own.
- MCP effect: `get_law` serves the correct current text, but `get_law_history` returns the fused history and `get_law_at` time-travels across the identity boundary, serving the **2009 act's text** for pre-2026-07-14 dates under the 2026 act's slug.

### 4.5 Authoritative artifact, self-heal, failed invariant

- **Authoritative state:** the manifest is *correct about membership* (old id removed, new id current — both true upstream states); the defect is the duplicate `markdown_path` pointer plus the fused per-slug history. Disk is correct (the file legitimately belongs to the live act). ADR-0004's manifest-authority ruling holds; what it does not yet cover is path-pointer uniqueness for tombstones.
- **Self-heal: NO — and partially by design.** `_carry_tombstones` carries the pointer verbatim; `audit_corpus` deliberately does not flag a tombstone whose path a live act owns (`corpus_audit.py:110-121` `_live_markdown_paths`, verified docstring: "reporting it invites a 'cleanup' that deletes text in force"; test `tests/unit/test_corpus_audit.py:95`; cf. lovspor fix `8a55da0`). The history contamination is not merely un-healed but **re-created**: every future update of the new act re-runs `extract_history` over the same `--follow` walk.
- **Failed invariant/test:** "history/timetravel events attributed to a slug belong to that slug's doc_id." The add+remove-same-path engine behaviour is tested and intentional (`tests/integration/test_orchestrator.py:1522`), but no test anywhere exercises `extract_history`/`get_law_at` across a doc-identity change at a constant path. Document identity was never part of the path-ownership invariant.

## 5. Linkage analysis — one root cause or several?

**Two distinct root causes; defects 1+2 are one event.**

- **Defects 1 and 2 share a single root cause and a single event**: the 2026-07-24 06:23 sync, one skipped `remove(...)` commit, both files (`.md` + `.bin`) dropped by the same `_drop_orphan_paths` call (§2.2). They are two arms of one failure — an unintentional engine **bug** (non-ASCII path quoting).
- **Defect 3 is a different mechanism**: slug allocation blind to the manifest (§4.3) plus two **designed** behaviours (skip-if-reused file protection, tombstone path preservation) whose composition was unowned. No quoting involved; the slug is pure ASCII; the file-level outcome was *correct*.
- **What genuinely connects all three** (family resemblance, not shared cause):
  1. Both removal events produced a manifest-only tombstone with no `remove(...)` commit — but for opposite reasons (defect 1: bug dropped the staged deletion; defect 3: guard correctly declined to delete).
  2. Both are permanent because tombstones are excluded from change detection (`change_detector.py:45-48`) and carried verbatim (`orchestrator.py:1082-1086`) — the shared **amplifier**, not the cause.
  3. Both trace to sync-time invariants never checked against the prior manifest/disk: `corpus_audit.py:1-11` names the gap for defects 1/2; `orchestrator.py:698-704` has no manifest-path uniqueness check for defect 3.
- Historical continuity for the defect-1/2 cause: the same quoting bug produced the pre-PR-#89 orphan era (48 documents, 2026-05-19 → 2026-07-03, all 82 cleaned-up paths non-ASCII — `dd49c96e0`, verified); after PR #89 (`_carry_tombstones`) the identical leak surfaces as tombstoned-but-present + orphan-bin instead of an orphan document. `sf-20260305-0354` is the first post-#89 victim.
- **[FACT]** The defect-1/2 root cause (the `ls-files` quoting mismatch) is documented nowhere in either repo's docs prior to this report.

## 6. Proposed minimum correction per defect — PROPOSED, NOT EXECUTED

All corrections land as **new commits on top of `e3660c4e2`**; none require touching any historical state.

**Defect 1 (corpus):** one commit `remove(forskrift): endr-i-økodesignforskriften` deleting `forskrifter/endr-i-økodesignforskriften.md` — the commit the 2026-07-24 sync should have produced (sibling shape: `f79f0f0b1`). The manifest is already correct (ADR-0004 authority; §2.3) and needs no change. Optionally append the missing removal event to `forskrifter/history/endr-i-økodesignforskriften.{json,md}` (history files are kept for tombstones by design, `corpus_audit.py:36-41`).

**Defect 2 (corpus):** delete `forskrifter/embeddings/endr-i-økodesignforskriften.bin` in the same commit as defect 1 (one event, one correction; sibling remove commits delete md + bin together).

**Defects 1+2 (engine):** make `_drop_orphan_paths` quoting-safe — run `ls-files` with `-z` and NUL-split (robust form), or `-c core.quotepath=off`, at `lovspor/src/lovspor/sync/git_commit.py:113`. Until this ships, **any** of the corpus's many `ø`/`å`/`æ`-slugged docs will reproduce defects 1+2 on its next upstream removal; the same latent class affects rename-away-from-non-ASCII-slug (old-path deletes, `orchestrator.py:473-475` region).

**Defect 3 (corpus):** minimum manifest correction consistent with manifest authority: regenerate `forskrifter/history/forskrift-om-omregningsfaktorer.{json,md}` truncated at the identity boundary (`ce3df5a13`), so the events belong to `sf-20260710-1545` only, and recompute `total_changes` (4 → 1) via the normal `_record_with_history` path — one new sync-shaped commit. The tombstone's stale `markdown_path` pointer needs a policy decision **[OPEN]**: either (a) leave it (it is the designed audit trail; `_live_markdown_paths` already tolerates it) and fix only the history/derived fields, or (b) add an explicit `superseded_by`-style disambiguation to the manifest schema. Option (a) is the minimum. The old act's own provenance (its 2026-04-26 → 2026-04-29 events) should be preserved somewhere id-keyed rather than silently dropped — otherwise it exists only in raw git.

**Defect 3 (engine):** teach history extraction to stop at identity boundaries — e.g. stop the `--follow` walk where the file's frontmatter `id` differs from the record's `doc_id` (`history.py:123-137` + `_parse_events`), and/or make `resolve_collisions` consult prior-manifest `markdown_path`s (`orchestrator.py:698-704`) so a replacement act gets `-2` instead of the tombstone's slot. The first fixes the harm; the second prevents recurrence.

**Would any historical git state need rewriting? NO.** Every correction above is a new commit appended to lovverk history, exactly like the healthy sibling removals and every past migration. ADR-0003 (append-only) is fully compatible: the defects live in the *current tree state and current derived files*, not in past commits; past commits are evidence and must remain untouched. Nothing in any proposal amends, drops, or reorders an existing commit.

## 7. Regression-test plan

**Defects 1+2 — extend `lovspor/tests/unit/test_sync_git_commit.py`:**
- `test_add_stages_deletion_for_tracked_missing_nonascii_path` (mirror of the ASCII test at lines 114-123): seed and commit `forskrifter/endr-i-økodesignforskriften.md`, `unlink()`, call `add()`, assert `git status --porcelain` shows `D` for the raw UTF-8 path. **Fails on current code** (path dropped as orphan) — would have caught the bug directly.
- Same for a dash-prefixed + non-ASCII combination and for the `.bin` sidecar path.

**Defects 1+2 — extend `lovspor/tests/integration/test_orchestrator.py`:**
- `test_run_sync_removes_disappearing_document_with_nonascii_slug` (mirror of `test_run_sync_removes_disappearing_document`, line 2961): doc with slug `endr-i-økodesignforskriften` leaves upstream; assert a `remove(forskrift): …` commit exists, the `.md` and `.bin` are absent from the commit's tree, and the manifest tombstone is present. Catches the full pipeline, including the has_staged_changes gate at `orchestrator.py:1270-1272`.
- Sweep: parametrize every existing removal/rename-flow test over an ASCII and a non-ASCII slug (no filename or slug in the file contains `ø`/`æ`/`å` — the only two grep hits, lines 92 and 243, are the "Virkeområde" section-title strings inside document content; verified).

**Defect 3 — extend `lovspor/tests/unit/test_history.py` (new cross-identity case):**
- `test_extract_history_stops_at_doc_identity_boundary`: repo where doc A (id X) lives at path P, then doc B (id Y) overwrites P in an `add` commit (M, not A — the `ce3df5a13` shape); run `extract_history(repo, P, doc_id=Y, slug=s)`; assert events contain **only** commits at-or-after the overwrite, no event references A's `from_path`, and `len(events) == 1`. Fails on current code (returns 4 fused events).

**Defect 3 — extend `lovspor/tests/integration/test_orchestrator.py`:**
- Extend `test_run_sync_add_remove_path_collision_commits_manifest_without_remove_path` (line 1522, the test that blesses the file-level behaviour) with new assertions: the regenerated history's `doc_id` matches the new record AND its events exclude the old doc's provenance AND the new record's `total_changes == 1`. Currently the test asserts file survival only — the identity invariant is unasserted.
- New: `test_resolve_collisions_against_prior_manifest_paths` (unit, `tests/unit/` beside slug tests): prior manifest holds a tombstone with `markdown_path` `P`; a new upstream doc slugifies to `P`'s slug; assert the new doc receives a suffixed slug (or, if policy (a) of §6 is chosen, assert the explicit takeover is recorded). Fails on current code (`orchestrator.py:698-704` never sees the manifest).

**Detection net (both defect families) — CI wiring, not a test file:** run `lovspor audit` (`cli.py:256-281`) as a post-sync workflow step failing on new findings. It already catches `tombstoned_but_present` and `orphan_embedding`; it deliberately excludes the defect-3 shape, so the identity invariant must live in the tests above, not in audit.

## 8. Read-only attestation

During this analysis: **no file was modified, no artifact deleted, no manifest membership changed, and no git history rewritten** in `lovverk`, `lovspor`, or `lovspor-notebook`. All repository access was via read-only commands (`git show`, `git log`, `git diff`, `git diff-tree`, `git ls-files`, `git ls-tree`, file reads, and a read-only Python scan of `manifest.json`). `lovverk` remains at HEAD `e3660c4e2`. The only file created is this report, as explicitly authorized. All proposed corrections in §6 are **PROPOSED, NOT executed**.

---

## 9. Repair completed — dated cross-reference (2026-07-31)

The corrections proposed in §6 were implemented and verified in
production on 2026-07-31:

* Engine fixes merged to `lovspor` main at `473f0c0` (branch
  `fix/corpus-integrity-rca-corrections`, 8 commits, Codex-reviewed,
  1,714 tests green).
* Corpus repair commits (forward-only, on top of `e3660c4e2`):
  `b40b1d8f6` `remove(forskrift): endr-i-økodesignforskriften` and
  `25bb3add5` `rename(forskrift): forskrift-om-omregningsfaktorer-2026`;
  pushed fast-forward `e3660c4e2..25bb3add5` — no history rewritten
  (`git merge-base --is-ancestor` confirmed).
* Post-repair `lovspor audit`: all integrity categories zero
  (`tombstoned_but_present` 0, `orphan_embedding` 0,
  `duplicate_path_ownership` 0, `identity_mismatch` 0); 18 advisory
  `unparsed_section_heading` findings remain as registered follow-up.
* Production sync run `30608029865` (2026-07-31): `0 new, 0 changed,
  0 removed, 5914 unchanged` — verified no-op; the new
  corpus-integrity CI gate ran before push and passed.
