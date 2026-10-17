# Preregistered Lovverk-first crosswalk audit — 2026-10-14

**Registration checkpoint:** committed **before examining any selected act's body text or Storting results** in this audit. Supersedes neither ADR-0017 nor the separate Storting-first study (#616).

## Frozen input and sampling unit

- Source: **Lovverk**, commit \`2ab982d253c9470ddb885172b36c10687539c910\`, root \`manifest.json\` generated **2026-10-14T09:26:38.661084Z**. This particular snapshot lists **755** current \`doc_type="lov"\`, \`source_dataset="gjeldende-lover"\` records with \`markdown_path="lover/<slug>.md"\` (309 originally enacted through 1999, 205 from 2000–2015, 241 from 2016 onward).
- Sampling unit **first**: a **currently valid consolidated base act** from that manifest, not a \`Lovvedtak\`/Storting case. Removed/tombstoned acts and regulations are out-of-frame, never searched to enrich successes.
- Strata based **only on the manifest document ID's four-digit original enactment year**, independent of Storting outcomes: \`old <=1999\`, \`mid=2000..2015\`, \`new >=2016\`.
- Take **35 / 30 / 35** base-act IDs, respectively. All 100 count as sampled, even if no suitable amendment reference is found. If there are fewer than 50 *eligible* base acts with an amendment instrument dated 2007–2026, take the **next** 15/15/20 by precisely the same ordering, yielding a predeclared maximum of 150 sampled base acts. Stop after that and report any shortfall; do not search for replacement winners.
- Reproducible, environment-independent order: for each ID, apply unsigned 32-bit **FNV-1a** to the UTF-8 bytes of \`lovspor-610-lovverk-first-2026-10-14:\` + exact document ID; sort by (\`hash_number\`, \`document_id\`) ascending within each stratum, select first N. (Reference JS: start \`2166136261\`; for each UTF-8 byte \`h = Math.imul(h ^ byte, 16777619) >>> 0\`.) Fix all selected paths and manifest IDs in a committed inventory **before** conducting any Storting lookups.
- One candidate amendment per sampled base act: take the **most recent dated** instrument \`lov/YYYY-MM-DD-N\` explicitly linked in an amendment-note block in its **current** Markdown rendering, restricted to amendment dates \`2007-01-01..2026-10-14\`; tie-break ascending canonical \`LOV-YYYY-MM-DD-N\`. Keep the oldest/currently repealed note only if it qualifies as a source act in the current body; **do not infer that it is in force**. Recognize Norwegian notes starting (after optional Markdown formatting) with \`Endret/Endra/Endres/Vert endra/Blir endret/Tilføyd/Føyd til/Opphevet\` and multiple links in one note. A ref in body legal text without an amendment note is not eligible. Keep source doc path, line number, exact short note excerpt, source hash and all not-eligible reasons.
- Eligibility and results denominators are distinct: **100 (or 150) base acts sampled**; **N** with an eligible cited amending act; **M** distinct amending instruments after exact dedup. The **law-first lookup metric** is over N eligible source references, not 100 current base laws and not N unique cases. Duplicate amending laws cited by multiple base acts stay in N, with M given separately.

## Explicit evidence tiers, all fail-closed

1. **L**: Lovverk Published Rendering contains dated \`lov/...\` in an amendment note. This is a traceable *derivative* of Lovdata, not an independently verified primary legal record. Without a fresh Lovdata publicData source snapshot, record \`primary_legal_source_verified=no\` even when a source amendment link is found.
2. **P**: For the sampled instrument, an official Storting case page explicitly matches sanction **both date and number** plus the exact \`Lovvedtak\` (session/number) **on the same case page**. Check against a specific official page. A broad search result, duplicate law title, date coincidence or mere case vote page link **does not** justify an \`attested\` crosswalk.
3. **V**: When P is proven, a case vote overview is linked; count only opened/read overview content as **vote overview checked**. Never attribute votes to a particular law/provision if vote scope is not explicit; \`sak_id\` may cover multiple laws.
4. **A**: API snapshot: full Storting official \`/eksport/sak\` response with bytes/digest + independent Lovdata publicData identity capture. Mark \`not_checked\` until both have been independently retrieved. No claims of A in a web-only experiment.

All rows, including failures and unverified results, **must be published**, with \`unknown\` distinct from \`no parliamentary proceeding\`.

## Analysis/reporting plan

- First create \`sampling-inventory.csv\` containing fixed selected IDs, paths and selection ranks **before fetching selected document bodies**. Do not change the seed, cutoff or selected population after seeing results.
- Then read only those selected Lovverk document bodies at the fixed commit; extract candidate links deterministically. Records that fail retrieval or parsing stay in the denominator with an explicit error.
- Finally search official Storting sources for every extracted source amendment identity. Report **confirmed / ambiguous / unresolved / not_checked**, with exact URLs for confirmed links and the exact tested query / reason for others. No fabricated \`sak_id\` and no positive link based on unverified snippets.
- For unverified cases, preserve the distinction between **not found in searched surface** and **no match in authoritative Storting records**. The latter requires an exhaustive authoritative index, which is **not** available here.
- The previous 20-case study was **Storting-first**, intentionally selected visible joins, and its 20/20 is **not** baseline recall. Never use it to replace, filter or label these source-first samples.

## Limitations registered in advance

- The resulting sample covers **cited amendments in currently valid consolidated base laws**, not every historical amendment, abolished act or Lovdata law instrument ever published. One selected amendment per base act does **not** estimate legislation-wide coverage directly.
- Fixed, deliberately balanced strata (35/30/35) are **not proportionate** to the manifest (309/205/241); do not quote unweighted aggregate as an unbiased national share. Provide per-stratum counts and, if meaningful, poststratification with exact manifest weights and clear scope.
- Access to the raw Storting API and Lovdata source feeds may be unavailable; their independent evidence fields are explicit \`not_checked\`.
- No Lovdata HTML scraping, and no edits to Lovverk published corpus. Code/implementation PRs #613/#614 remain blocked by owner-confirmed offline Mac mini. This study is documentation-only and does not bypass agentic CI.
