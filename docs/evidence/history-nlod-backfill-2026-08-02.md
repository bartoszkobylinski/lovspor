# History Markdown NLOD Backfill — Evidence Record (2026-08-02)

One-off publication-format migration bringing every legacy ``history/*.md``
onto the frontmatter/NLOD attribution contract (defect register item 4 in
``lovspor-notebook/docs/adr/evidence/ADR-0004-evidence.md``; engine PR #183,
merged ``4fea9d7``).

**Provenance:** hand-assembled by the session that analysed, implemented,
executed and verified the migration; every figure measured with the commands
in Verification Method. No pipeline wrote this file.

## The defect and the decision

2,580 history Markdown files (150 lover + 2,430 forskrifter) predated the
history frontmatter and carried no NLOD attribution — including 2 tombstoned
documents and 468 histories whose manifest records predate the tombstone era
(pre-tombstone removals: document file and record gone, history preserved as
the audit trail). ``history/*.json`` remains the structured source of truth;
the Markdown is the published human-readable view and therefore had to meet
the publication contract rather than be removed.

The original register count was 2,581: one legacy forskrift history
self-healed through a normal upstream-driven history rewrite between the
original audit and the 2026-08-02 read-only migration analysis — a real sync
regenerates both files of a pair on any document event.

## Mechanism

``scripts/backfill_history_frontmatter.py`` (PR #183): fail-closed,
all-or-nothing, dry-run by default. Preflight re-derives every legacy file
from its paired JSON through the production renderer
(``render_history_markdown`` — format never reimplemented) and proves, for
the complete set, that the only change is the frontmatter block and that the
JSON round-trips byte-identically; any anomaly aborts the whole run with
zero writes. Execution stages the complete set (``.nlod-stage``) before
renaming anything, so a mid-run I/O failure unwinds to zero visible changes
(Codex-reviewed; two findings fixed in ``74e8374`` before merge).

## Runs and commits

| Step | Result |
|---|---|
| Final dry-run (pre-execution) | 6,390 scanned: 3,810 current, 2,580 to migrate, 0 anomalies |
| Execution | ``Migrated 2580 files (Markdown only; JSON untouched)`` |
| Idempotence proof | second ``--execute`` → ``Migrated 0 files`` |
| ``lovverk`` commit | ``0075a22`` ``migration: backfill NLOD attribution in history markdown (2580 files)`` — single forward-only commit, owner-pushed |
| No-op proof sync | ``30738731178`` (workflow_dispatch, 2026-08-02T07:56Z) — ``0 new, 0 changed, 0 removed, 5878 unchanged``, no new corpus commits |

## Measured outcomes

* Changed paths: exactly **2,580**, all matching ``*/history/*.md``; zero
  outside the pattern [FACT, ``git status --porcelain``].
* Every diff **+7/−0** — the six frontmatter lines plus separator; total
  18,060 insertions, 0 deletions [FACT, ``--numstat`` filter].
* Publication contract: **6,390/6,390** ``history/*.md`` now open with a
  closed frontmatter block carrying ``type: "history"`` and NLOD 2.0 [FACT].
* Category SHA256 pins byte-identical before/after: ``manifest.json``,
  document Markdown (5,880 incl. INDEX), ``history/*.json`` (6,390),
  ``embeddings/*.bin`` (5,878), INDEX files. Only the history-Markdown
  digest moved [FACT, pinned digests].
* Corpus membership, ``xml_hash``, ``last_changed``, ``total_changes``:
  unchanged — the manifest was not touched at all [FACT, manifest pin].
* ``extract_history`` before vs after the migration commit, sampled across
  all three populations — current (``eos-kontrolloven``), tombstoned
  (``endringslov-til-barnehageloven``), pre-tombstone-era orphan
  (``arkivlova-arkl``): **identical** ×3. ADR-0003 history extraction walks
  document paths only, so the history-only commit is structurally invisible
  [FACT].
* Audit: exit 0; advisory inventory unchanged (11× ``unsupported_section_range``,
  the registered class C follow-up); zero new findings [FACT].
* Droplet corpus refreshed to ``0075a22`` (``lovspor-fetch-corpus.service``);
  ``get_law_history`` serves JSON and is unaffected [FACT].
* Git history not rewritten: forward commit only, fast-forward push.

## Verification method

```bash
uv run python scripts/backfill_history_frontmatter.py --corpus-path <lovverk>            # dry-run
uv run python scripts/backfill_history_frontmatter.py --corpus-path <lovverk> --execute  # ×2, second migrates 0
git -C lovverk status --porcelain | grep -cE '^ M "?(lover|forskrifter)/history/.*\.md"?$'  # 2580, no others
git -C lovverk diff --numstat | awk '$1!="7" || $2!="0"'   # empty
# category pins: sha256 over (relpath, blob-sha256) per set, before vs after
# extract_history(model_dump) for the three samples, before vs after commit
uv run lovspor audit --corpus-path <lovverk>               # exit 0, advisories unchanged
gh run view 30738731178 --log | grep 'Sync complete'       # 0/0/0/5878
```
