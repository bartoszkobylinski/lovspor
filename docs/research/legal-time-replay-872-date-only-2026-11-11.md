# #872: date-only canonical citations bind only on header proof, research, 2026-11-11

This note covers one attribution defect: a canonical intro (`INTRO`, `INTRO_SLIK`) bound its operations to an act cited by date alone, without proof that the date names one act. No ADR changed. No owner ruling was assumed: kap. 16, renumbering, reach sets and D7 narrowing stay out. Nothing is served, published or written into `lovverk`; the MCP/API/public JSON surfaces are untouched; no corpus-wide replay was run; ADR-0019 R2 is unchanged. ADR-0020 Decision 7 is applied as written.

The change **adds uncertainty and claims no coverage**. 51 operations that were bound without proof are now unattributed. No operation of a coverage act changed, and no verdict changed.

## 0. Baseline and inputs

| item | value |
|---|---|
| baseline | origin/main `7031b041` (merge of #873, Phase 4B; #865, Phase 4A, merged at `73f7c805`) |
| `lovtidend-avd1-2001-2025.tar.bz2` | sha256 `d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0` |
| `lovtidend-avd1-2026.tar.bz2` | sha256 `600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2` (ADR-0019 R2 `research_only`, unchanged) |
| oracle `gjeldende-lover.tar.bz2` | sha256 `380632d48c82e48bb6fa37c6c6e6f13d55c77defa5f83400c5120ca6989593e9` |
| `REPLAY_VERSION` | `legaltime-replay-5` → **`legaltime-replay-6`** |

Only the permitted publicData archives were read. The baseline was rebuilt first, from a detached checkout of `7031b041`: the eight-act coverage came out byte-identical to the committed `legal-time-coverage-2026-11-10-phase4b.json` (`fe51cae5…`), the barnevernsloven ladder and the four pilot files to their committed files, and the straffeloven ladder (`--at 2008-03-06 2008-03-07 2015-09-30 2015-10-01 2026-11-06`) to the Phase 4B digest `71fb2fb5…`. The operation dump of the 403 amenders of the eight acts came out as `543268b8…`, the Phase 4B branch dump.

Commands: those of the Phase 3 note §1, with the per-act files named `legal-time-replay-2026-11-11-<short>.json`, reduced by `scripts/legal_time_coverage.py`.

## 1. The defect and the rule

`INTRO` and `INTRO_SLIK` capture `(?P<date>\d{1,2}\.\s*\w+\s+\d{4}(?:\s+nr\.\s*\d+)?)`: the number is optional. `14. I lov 21. februar 1930 om skifte gjøres følgende endringer:` bound the following operations to `lov/1930-02-21`. Lovdata does identify an unnumbered act by such a date-only refid, but a date alone does not prove that the sentence means that act: the number may be left out (`I lov 20. april 2001 om erstatning frå staten …`, lov/2008-12-19-115, whose header lists `lov/2001-04-20-13`) or misprinted (`I lov 20. mai 2005 nr 28 om straff …`, no full stop, lov/2021-05-07-33), or the date may name an amending act (`I lov 15. desember 2006 om endringer i lov 26. mars 1999 nr. 14 …`, lov/2007-06-15-27).

**Rule.** A canonical intro citing an act by date alone binds only when the member's own `changesToDocuments` lists **exactly that date-only refid and no numbered act of the same date**. Otherwise the intro closes the block before it, and what follows is unattributed; an unsupported sentence in it is counted as an unattributed operation, not dropped. Identity is never taken from a matching date, a title, a nearby reference or the previous block.

`attribution.header_proves(refid, changes)` is the one header-identity check. Phase 4B's `_proven` uses it, and so does `attribution.identified`, which names the act of a canonical intro (`canonical_act`) and of a term-substitution list item (`7. lov 10. februar 1967 om behandlingsmåten i forvaltningssaker`, then its locations), and of a node's Lovdata change address (`data-change-part`, `_addressed_act`), the other places a date-only refid could become an operation's act. For a numbered refid it is membership, exactly as Phase 4B had it; for a date-only refid it requires that no other listed refid shares the date, so a numbered proof is never unsettled by another act of its date. An unproven substitution item keeps its locations as `substitute_term` operations with no act: counted, and it ends the act pending from the item before it, so a following location-only line inherits nothing (a proven inline item keeps the pending act, as `test_explicit_substitution_act_does_not_steal_pending_act` pinned before this PR; that is unchanged). An unproven date-only change address leaves its operation unattributed; it never falls back to the open block's act. Numbered change addresses bind as before. A fully numbered canonical citation binds as printed, as before: that behaviour is preserved by the task's terms and is pinned by tests. The block rules are unchanged: a new `legalArticle`, an unbound intro and a numbered act-led item close a block; a unit-only sub-intro (`§ 4-2 endres slik:`) keeps it.

## 2. Audit of the 128 date-only operations

At `7031b041`, 128 operations of the eight acts' amenders were bound to a date-only refid. Each is classed against its member's header (`scripts/legal_time_attribution_audit.py`, dumps `543268b8…` at `7031b041` and `d9f48241…` on this branch; output `docs/research/data/legal-time-attribution-audit-2026-11-11-872.json`, sha256 `49859122e47b01967e474f1f9cbe24de68392dc2db34b24b67a0aaa13422ade4`):

| class at `7031b041` | ops | members | outcome on this branch |
|---|---|---|---|
| header lists exactly the date-only refid | 77 | 18 | **retained**, same act |
| header lists no act of that date | 50 | 2 | **unattributed** |
| header lists only the numbered act of that date | 1 | 1 | **unattributed** |
| **total** | **128** | 21 | 77 retained, 51 unattributed, 0 other |

The audit reports **51 changed operations, all `unbound_date_only_unproven`, 0 `check`**. Nothing else changed: 10,750 operations in 403 members on both sides, no change of kind, instruction or presence. The audit gained the class `unbound_date_only_unproven` for this PR, because its existing `unbound_was_false` would have called these attributions false; they were not shown false, they were never proven.

The 51, by member and by the act they lost:

| member | intro (archive sentence) | lost act | ops | header |
|---|---|---|---|---|
| lov/2007-01-26-3 | `2. I lov 28. november 1898 om Umyndiggjørelse gjøres følgende endringer:` | lov/1898-11-28 | 12 | no act of that date |
| lov/2007-01-26-3 | `14. I lov 21. februar 1930 om skifte gjøres følgende endringer:` | lov/1930-02-21 | 19 | no act of that date |
| lov/2007-01-26-3 | `40. I lov 5. mai 1961 om grannegjerde gjøres følgende endringer:` | lov/1961-05-05 | 2 | no act of that date |
| lov/2007-01-26-3 | `50. I lov 10. februar 1967 om behandlingsmåten i forvaltningssaker gjøres følgende endringer:` | lov/1967-02-10 | 3 | no act of that date |
| lov/2009-06-19-74 | `1. I lov 2. oktober 1751 Første Codicill … (Lappekodisillen) skal § 19 siste punktum lyde:` | lov/1751-10-02 | 1 | no act of that date |
| lov/2009-06-19-74 | `8. I lov 17. mars 1916 om straffebestemmelser for utenlandske militærpersoner … gjøres følgende endringer:` | lov/1916-03-17 | 4 | no act of that date |
| lov/2009-06-19-74 | `53. I lov 3. februar 1961 om ansvar for skade som motorvogner gjer gjøres følgende endring:` | lov/1961-02-03 | 1 | no act of that date |
| lov/2009-06-19-74 | `62. I lov 15. november 1963 om fullbyrding av nordiske dommer på straff m.v. gjøres følgende endringer:` | lov/1963-11-15 | 6 | no act of that date |
| lov/2009-06-19-74 | `71. I lov 10. februar 1967 om behandlingsmåten i forvaltningssaker gjøres følgende endringer:` | lov/1967-02-10 | 2 | no act of that date |
| lov/2021-05-07-33 | `I lov 20. mai 2005 nr 28 om straff gjøres følgende endring:` | lov/2005-05-20 | 1 | `lov/2005-05-20-28` only |

**Against the expected outcome** (retain 77, unbind 51): met exactly. One note on the single numbered case. Issue #872 classed it as "the header lists a numbered refid instead"; reading the sentence shows why: it **does** print straffeloven's number, as `nr 28` without the full stop, so the canonical grammar captured only the date. The old reading bound it to `lov/2005-05-20`, an act identifier that matches nothing in its header. Binding it to straffeloven would need the grammar to accept `nr` without its full stop; Phase 4B deliberately refuses malformed citations (`I lov 15 mai 2008 nr. 35 …`), so it stays unattributed here. The primary evidence does not contradict the classification; it explains it.

The two headers without the act (lov/2007-01-26-3, lov/2009-06-19-74) are the same two members Phase 4B §7 records as omitting acts they plainly amend. Their date-only blocks are now treated as their numbered blocks already were when unlisted.

The 77 retained, by member: lov/2010-12-10-76 (2), lov/2013-04-05-12 (11), lov/2013-06-21-100 (20), lov/2014-05-09-16 (1), lov/2015-06-19-65 (14), lov/2016-04-22-3 (1), lov/2017-05-11-26 (3), lov/2017-06-16-63 (1), lov/2018-04-20-6 (1), lov/2018-06-01-25 (6), lov/2018-06-15-37 (4), lov/2018-06-22-83 (2), lov/2020-12-18-149 (1), lov/2021-05-07-34 (1, through its substitution list, header-proven), lov/2021-06-18-127 (3), lov/2024-06-21-46 (1), lov/2025-04-25-12 (2), lov/2026-06-19-45 (3). `tests/integration/test_legaltime_872.py` pins every member's count of both groups on the archives.

None of the 128 was ever bound to one of the eight coverage acts (all carry numbers), so none was ever a coverage event.

## 3. Before / after (`7031b041` → this branch)

### 3.1 Eight-act coverage (`legal-time-coverage-2026-11-10-phase4b.json` → `legal-time-coverage-2026-11-11-872.json`, end state 2026-11-06)

| act | events | dated | applied | unres. ops | act-level | unattributed | proven | agree behind blanket |
|---|---|---|---|---|---|---|---|---|
| arbeidsmiljøloven | 358 | 274 | 0 | 82 | 41 → **91** | 30 → **80** | no text | — |
| barnevernsloven | 185 | 134 | 101 | 66 | 15 | 2 | 0/188 | 123/188 |
| friskolelova | 302 | 232 | 168 | 134 | 75 | 18 | 0/92 | 34/92 |
| merverdiavgiftsloven | 324 | 162 | 105 | 218 | 72 | 7 | 0/206 | 89/206 |
| naturmangfoldloven | 49 | 44 | 0 | 5 | 2 | 2 | no text | — |
| straffeloven | 625 | 506 | 424 | 185 | 137 → **187** | 65 → **116** | 0/400 | 293/400 |
| tvisteloven | 164 | 112 | 95 | 61 | 43 → **79** | 26 → **62** | 0/442 | 296/442 |
| utlendingsloven | 556 | 306 | 176 | 371 | 185 → **199** | 25 → **39** | 0/212 | 50/212 |
| **total** | 2,563 | 1,770 | 1,069 | 1,122 | **570 → 720** | **175 → 326** | **0 → 0** | 885 of 1,540 |

Every changed number, explained:

- **Unattributed 175 → 326 (+151).** Read once per member, the pool goes from 96 to **147 distinct operations** (+51) and from 21 to 22 members (+lov/2021-05-07-33). lov/2007-01-26-3's header lists straffeloven, arbeidsmiljøloven and tvisteloven, so its 36 operations count once in each (+108); lov/2009-06-19-74's lists straffeloven, arbeidsmiljøloven and utlendingsloven (+42); lov/2021-05-07-33's lists straffeloven (+1). Per act: arbeidsmiljøloven +36 +14 = +50, straffeloven +36 +14 +1 = +51, tvisteloven +36, utlendingsloven +14.
- **Act-level 570 → 720 (+150).** Each newly unattributed operation in effect at the replayed date is an act-level problem (Decision 7, unchanged). The reasons `unattributed operation, act unknown (lti/2007/nl-20070126-003.xml)` 12 → 48 and `(lti/2009/nl-20090619-074.xml)` 16 → 30 account for all of it. straffeloven gains 51 unattributed but 50 act-level: lov/2021-05-07-33's operation has `pending` timing, so it is in effect at no replayed date.
- **Events, dated, pending, unresolved timing, applied, unresolved operations, verification counts: unchanged** in every act and every ladder state. Not one of the 51 was a coverage-act event before or after.
- **Proven 0 → 0; agree behind the blanket unchanged.** The coverage acts already carried act-level problems at every date, so the added ones change no verdict. Per-state `counts`, `counts_before_blanket`, `ledd_counts` and every section verdict are identical in all fifteen rebuilt files.

The ladder rows of each act change only in `act_level_problems`: arbeidsmiljøloven +36/+50/+50/+50/+50/+50 at 2008…2026, straffeloven +36/+50/+50/+50/+50/+50, tvisteloven +36 at every date, utlendingsloven +0/+14/+14/+14/+14/+14 (lov/2007-01-26-3's operations count from their effective dates, lov/2009-06-19-74's from 2012 on).

### 3.2 Straffeloven ladder

| state | withheld | applied | unresolved ops | act-level |
|---|---|---|---|---|
| 2008-03-06 | 4 | 0 | 103 | 26 → 62 |
| 2008-03-07 | 99 | 0 | 103 | 43 → 79 |
| 2015-09-30 | 391 | 0 | 103 | 69 → 119 |
| 2015-10-01 | 0 | 335 | 159 | 109 → 159 |
| 2026-11-06 | 0 | 424 | 185 | 137 → 187 |

Events 625 and unattributed events 65 → 116. `test_legaltime_phase3.py`'s (4, 99, 391, 335) pin is unchanged. Ladder digest `71fb2fb5…` → ``c3f7df6538b853b595bbc9c79893e8548cd973f7dd8b14691a658a22143f0a87`` (regenerated, not committed).

### 3.3 Phase-1 pilots and barnevernsloven

Arveloven, personopplysningsloven, sivilombudsloven, språklova and the barnevernsloven ladder: **no content change**. Apart from the `replay_version` line each regenerated file is byte-identical to `7031b041`; act-level stays 0 for the pilots and their proven verdicts are unchanged.

### 3.4 Committed evidence (sha256)

| file | before (`7031b041`) | after |
|---|---|---|
| `legal-time-replay-2026-11-06-arveloven.json` | `2232fb56…c8a028` | `5b101e528230d1a2205b973d11d47901ecce1358ce44a062f755eefaa984372f` (version line only) |
| `legal-time-replay-2026-11-06-personopplysningsloven.json` | `55b9b349…11e18a` | `0d42b5c39b964ad58f90ac08a0fd4250fb68904e95171b86bf7f1c664e2226e0` (version line only) |
| `legal-time-replay-2026-11-06-sivilombudsloven.json` | `8803d23b…1195b7` | `1684d54cb77c8a36a24fa6accfd3b812bb88082aed0c7fb8bf6eac53e0e378e6` (version line only) |
| `legal-time-replay-2026-11-06-spraklova.json` | `1d6604ae…243374` | `123e32790b8f9d2cbab5c87bc3d167371e1624ca3658b7849ffa89cd3bf8543a` (version line only) |
| `legal-time-replay-2026-11-08-barnevernsloven.json` | `f0a83234…4ea89f` | `a559bec6222e2f5f74cb0c301568b7b5fba330e2199954e88d072cdab6497f32` (version line only) |
| `legal-time-coverage-2026-11-11-872.json` (new; before = `…-2026-11-10-phase4b.json`) | `fe51cae5…8b7737` | `f82d04959afb7fbfd555ef0d21d6268855958d10c4303f42e7bba6882dff02c3` |
| `legal-time-attribution-audit-2026-11-11-872.json` (new) | — | `49859122e47b01967e474f1f9cbe24de68392dc2db34b24b67a0aaa13422ade4` |

Not committed (regenerated): the eight per-act reports (`legal-time-replay-2026-11-11-<short>.json`; content changes in arbeidsmiljøloven `a520546a…`, straffeloven `0bba894c…`, tvisteloven `86a7bd52…`, utlendingsloven `8cdfb3d2…`; the other four differ from `7031b041` by the version line only) and the straffeloven ladder.

`legal-time-coverage-2026-11-10-phase4b.json` (`fe51cae5…`) and `legal-time-attribution-audit-2026-11-10.json` are left as they were, so the Phase 4B note still points at its own evidence and the before snapshot stays in the tree. **Determinism:** four rebuilds of the fifteen files (eight per-act reports, the coverage reduction, the straffeloven ladder, the barnevernsloven ladder, the four pilots), each from its own frozen copy of `src/`: A from the first working version, B at the source of `bd6231bc`, and E and F, run independently and in parallel, at the final source `32bbb1e6`. All four produced identical digests for all fifteen files. The review fixes after B (substitution items, change addresses) change no operation on the archives: the op dump is `d9f48241…` at every head.

## 4. Tests

`tests/unit/test_legaltime_872_date_only_attribution.py`, 37 cases on archived sentences (member named beside each), `INTRO` and `INTRO_SLIK` tested separately. Against `7031b041` **23 fail and 14 pass**; the 14 that pass are guards that must hold on both sides (numbered citations, including a numbered proof beside other numbered acts of its date in lov/2015-06-19-65's real header; article boundaries; unit-only sub-intros; #856; a header-proven substitution item; proven and numbered change addresses). The required cases:

1. exact header identity binds (`INTRO`, `INTRO_SLIK`, and the inline `INTRO` form `2. I lov 21. februar 1930 om skifte skal § 86 annet ledd lyde:`);
2. absent from the header: unattributed, the previous block closed, unsupported prose counted (both shapes);
3. a numbered refid of the same date proves nothing: lov/2021-05-07-33 and lov/2007-06-15-27 with their real headers, `INTRO_SLIK` with a numbered same-date header, lov/2008-12-19-115's number-less citation;
4. several acts of one date (the unnumbered act plus a numbered one, or two numbered ones): no binding, both shapes;
5. an unbound intro, and an unproven date-only intro, after a proven date-only block clear it; a unit-only sub-intro keeps it;
6. fully numbered citations bind as before, with an empty header, the exact header, or only a date-only refid of the same date;
7. proven, unproven and numbered blocks in one article stay apart; a block ends at the next article; two proven date-only acts in two articles;
8. #856: lov/2015-06-19-65 § 4 (militær straffelov) binds its own act with header proof and is unattributed without it, never straffeloven, beside an unproven date-only article.

Plus lov/2021-05-07-34's substitution list (items 6–8): the date-only item binds on exact header proof, and with the refid absent, only numbered, or ambiguous its locations are unattributed while the numbered items around it keep their acts; an unproven item with its locations on the same line ends the pending act before it. A `data-change-part` address naming an unproven date-only act binds neither that act nor the open block's; a proven or numbered address binds as before.

`test_legaltime_phase2_instructions.py::test_an_intro_with_an_inline_repeal_yields_that_repeal` asserted the unproven binding (`3. I lov 21. februar 1930 om skifte oppheves …` with no header); it now asserts the repeal is read either way and the act binds only with the header. `test_legal_time_attribution_audit.py` gains three cases for the new class (an unproven lost date-only act; a lost act the header does prove, a rebind, another act, and a never-bound operation are not that class). `tests/integration/test_legaltime_872.py` pins on the archives: the 77 per member and act, the 51 now unattributed, lov/2007-01-26-3's unproven items bounded by the numbered neighbours (`lov/1893-07-20-2`, `lov/1902-05-22-10`, `lov/1927-05-05-1`, `lov/1931-06-12-1`), and lov/2021-05-07-33.

## 5. Independent review and mutation

Codex, read-only (`codex exec -s read-only`), given the specification and the code, not this note's conclusions:

| round | head | verdict | findings | resolution |
|---|---|---|---|---|
| 1 | `dd29d19f` + working tree | NOT READY | major: a term-substitution list item (`7. lov 10. februar 1967 om …`) binds a date-only act without header proof; minor: no test for it | `attribution.identified` names the item's act too; an unproven item's locations are operations of no act (`fdbd567f`), with tests. 0 operations changed on the archives |
| 2 | `fdbd567f` | NOT READY | major: an unproven item with its locations on the same line left the previous item's pending act, so a following location-only line inherited it | an unproven item ends the pending act (`8b1ec7df`), with a test. A *proven* inline item keeps it, as the pre-existing `test_explicit_substitution_act_does_not_steal_pending_act` pins; not changed here. 0 operations changed |
| 3 | `8b1ec7df` | NOT READY | major: a node's `data-change-part` address (`_addressed_act`) overrides the reader's act, so an unproven date-only address still binds | the address's act goes through `identified`; unproven, the operation is unattributed and never falls back to the open block (`32bbb1e6`), with tests. 0 operations changed |
| 4 | `32bbb1e6` | NOT READY | no production fail-open path; minor: the audit test passes strings to `_row` | false positive: the test calls its own helper `_row`, which builds the operation records; the module called directly with them returns `unbound_date_only_unproven`. No change |
| 5 | `32bbb1e6` | **READY** | none | — |

After round 5 the CI test author (`3d755a61`, `[agent:codex-tests]`) added 8 cases: another act of a neighbouring date, or the same date-only refid listed twice, never makes a proof ambiguous; and an inline date-only substitution item keeps its locations and needs the header's proof. All pass. The test file has 45 cases.

**Mutation** (CI `mutation` job, PR-scoped, mutmut 3.8.0, head `3d755a61`; targets `_proven`, `header_proves`, `identified`, `item_act`, `canonical_act`, `is_intro`, `_Reader._open_block`, `_Reader._substitution_item`, `_Reader._instruction`): **201 of 201 killed**. 0 survived, 0 timed out, 0 suspicious, 0 without tests, and no equivalent mutant was registered. Two changed lines sit at module level, where mutmut does not measure: `_DATE_ONLY` in `attribution.py`, whose pattern every date-only test exercises, and `REPLAY_VERSION` in `run.py`, which `test_legaltime_mutants.py` pins.

**CI** at `3d755a61`: everything passed. That covers fast-ci, the Test matrix (3.12, 3.13, 3.14), codex-author, codex-tests, mutation, the security audit, publish determinism (digest on three platforms, compare), GitGuardian and ready. Locally: the full unit suite (20,364 passed, 2 skipped, 10 xfailed), the pinned legaltime integration suite (26 passed), the deep gate on push, the fast gate on every commit, and `/security-check` (0 vulnerabilities, 0 leaks, 0 unreadable files; the bandit MEDIUM findings are pre-existing and in files this change does not touch).

### 5a. The audit classifier (codex-tests, run 43015286940, head `6bb25053`)

On the note-only push, the CI test author wrote two blocking cases. Each loses a date-only act that the header *had* proven (`14. I lov 21. februar 1930 om skifte …`, and the `INTRO_SLIK` form `Lov 21. februar 1930 om skifte endres slik:`, with `lov/1930-02-21` in the header) and expects `check`. The audit returned `unbound_was_false`. That was a classifier defect, not a wrong expectation. `_class` decided "false" from `attribution.citations()`, which reads only citations with a number, so the date-only act the canonical intro *does* name looked uncited, and the loss of a proven attribution passed as the removal of a false one. The engine was never affected: no operation of the 128 lost a header-proven act (the integration test pins the 77).

The fix (`4b5b5923`) is in `scripts/legal_time_attribution_audit.py` only:
- `_named` adds the canonical intro's date-only refid to the acts the sentence names.
- `_date_only_proof` counts the canonical shapes' own header proof as identity proof (review round 6).

A lost attribution is now classed as follows:

| before | the header | class |
|---|---|---|
| the date-only act the intro names | proves it | `check` (a proven attribution lost) |
| the date-only act the intro names | does not prove it | `unbound_date_only_unproven` |
| an act the sentence does not name at all | any | `unbound_was_false` |

The converse cases changed with it. A header-proven date-only binding is `new_proven` (or `rebound_proven`), and a header-proven date-only intro sentence that stops being an operation is `intro_not_an_operation`, where before both fell to `check`. `cited` keeps its documented meaning, citations by day, month, year and number.

**No classification changed in any committed audit.** With the fixed classifier, the #872 audit re-runs byte-identical (`49859122…`, 51 × `unbound_date_only_unproven`, 0 `check`): each of the 51 rows already took the date-only path first. The Phase 4B audit `legal-time-attribution-audit-2026-11-10.json` also re-runs byte-identical (`d1c4e0d3…`, from a fresh `73f7c805` dump `0f53261c…`, the Phase 4B input). The 128-operation result stands at 77 retained and 51 unbound. Because `src/` is unchanged, no report digest changed: two more independent rebuilds (G, H) of the fifteen files match each other, rebuild E and every committed evidence file byte for byte.

Tests: the two blocking cases and three other generated cases are adopted verbatim from the run's `codex-tests.patch` artifact. `test_legal_time_attribution_audit.py` adds the three lost-attribution classes for `INTRO`, `INTRO_SLIK` and the inline intro, a numbered intro, and the converse classes `new_proven`, `rebound_proven` and `intro_not_an_operation`. Against the classifier at `6bb25053`, 11 of the file's 47 cases fail.

| round | head | verdict | findings | resolution |
|---|---|---|---|---|
| 6 | working tree | NOT READY | major: the date-only header proof did not count as proof (a proven binding was `check`, not `new_proven`); minor: `cited` stays numbered-only; minor: inline and positive cases untested | `_date_only_proof`, with tests; `cited` kept as documented, because changing it would rewrite both committed audits |
| 7 | working tree | NOT READY | minor: no positive inline-intro cases | tests added |
| 8 | `4b5b5923` | **READY** | none | — |

## 6. Limitations

- **A numbered canonical citation still binds without header proof**, as before: the task preserves it. lov/2007-01-26-3 shows the cost, its header omits acts whose numbered blocks still bind (e.g. `lov/1893-07-20-2`). Requiring header proof there would be a separate, measured change.
- **`nr` without its full stop is not a number.** lov/2021-05-07-33's one straffeloven operation stays unattributed (timing `pending`, so no act-level effect). Accepting the misprint would be a grammar change with its own audit.
- Header omissions (Phase 4B §7) now leave date-only blocks unattributed too; that is the fail-closed choice.

## 7. Recommendation

**MERGE**, for the owner to decide. The engine (`src/`) is final at `3edf7fec` and the audit classifier at `4b5b5923`; the commit after that changes only this note, and PR #876 names the final head. The change is strictly fail-closed. It retains every header-proven date-only binding (77), removes the 51 unproven ones, and closes the same hole in substitution items and change addresses. It changes no coverage-act event, applied operation or verdict. The evidence reproduces deterministically, the independent review ended READY, and CI is green with mutation at 201/201. The result is 151 more unattributed entries and 150 more act-level problems. That is the cost of not guessing, not a loss of correct coverage.
