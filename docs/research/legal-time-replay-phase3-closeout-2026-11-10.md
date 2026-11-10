# #744 Phase 3 closeout and Phase 4 decision record (research, 2026-11-10)

Read-only verification and research session (2026-11-09/10). No replay code changed, no verdict changed, no lovverk write, no merge or deploy by the agent. Regenerated evidence and the five research digests live in the session scratchpad (not committed); every number below is reproducible from the pinned archives with the commands in the Phase 3 note.

## 0. Preconditions (read 2026-11-10, live)

| item | state | evidence |
|---|---|---|
| PR #847 `feat/744-phase3-commencement` | **MERGED** 2026-11-09T19:33:48Z | merge commit `fe93dca5795d7ea29ff1ec6a0902d0c508997f13`, head `194bd554`; origin/main tip = `fe93dca5` |
| PR #848 `feat/744-phase3-replay` | **MERGED** 2026-11-10T05:15:44Z by the owner | merge commit `97f5f7fe1b223c678a1bb6c460ea27545939b06d`, parents `fe93dca5` + `87ab1429`; head `87ab14290215da54c181a79964105692093d211b`; all checks SUCCESS on that head (fast-ci, test 3.12/3.13/3.14, audit, codex-author, codex-tests, mutation, ready; PR Pipeline run 42668682909) |
| ADR-0020 | **Accepted** 2026-11-08 | lovspor-notebook origin/main `1f857f2` (merge of #165), `docs/adr/ADR-0020-legal-time-replay-from-lovtidend.md` l.3 |

origin/main tip = `97f5f7fe`. `git diff --stat 87ab1429 97f5f7fe` = one file, `tests/unit/test_legaltime_heading_timing.py` (+57, #847's codex-tests commit `194bd554`); `src/`, `scripts/`, `docs/research/` and `mutation-equivalents/` are byte-identical to `87ab1429`. Every reproduction in §1 was run against `87ab1429` while #848 was open and therefore holds for `97f5f7fe`; the integration rebuild was re-run on `97f5f7fe` itself (result in §1).

## 1. Phase 3 evidence reproduced at `87ab1429`

Archives (pinned, digests match `data/cache/legaltime/SHA256SUMS`): `lovtidend-avd1-2001-2025.tar.bz2`, `lovtidend-avd1-2026.tar.bz2`, oracle `gjeldende-lover.tar.bz2`. `REPLAY_VERSION = legaltime-replay-3`.

**Integration rebuild**: `uv run pytest tests/integration/test_legaltime_pilot.py tests/integration/test_legaltime_phase3.py -q`: **12 passed** at `87ab1429` (282 s) and **12 passed** again on the merge commit `97f5f7fe` (274 s). These tests rebuild the four phase-1 evidence files and the barnevernsloven evidence byte-for-byte and pin straffeloven's four intermediate states.

**Barnevernsloven** (`lov/2021-06-18-97`), committed `docs/research/data/legal-time-replay-2026-11-08-barnevernsloven.json` sha256 `f27a1b110c963a94f5a2e488da281904f3617519dd2af1e3d2f22427d99a7ac0` (rebuilt identically by the test):
- `commencement.default`: dated 2023-01-01, known_from 2022-10-14, proof FOR-2022-10-14-1739.
- `commencement.units`: `§2-6` → **pending** (proofs: the act's header delegation + the 2022 decision's exception); `§15-7 ledd 4` → **dated 2023-07-01**.
- States: 2022-12-31 `not_yet_in_force` (0 applied, 15 withheld); 2023-01-01 `replayed`, 13 applied, § 15-7 `ledd_not_in_force=[4]`; 2023-06-30 same flag; 2023-07-01 flag gone; 2024-01-01 20 applied; 2026-11-06 101 applied. § 2-6 absent from the text at every date (insertion withheld; pending unit). Act-level problems: 1 (2023-01-01 to 2023-07-01), 3 (2024-01-01), 10 (2026-11-06).
- Verification: `unresolved` 188/188 sections at every date (blanket). Before blanket at 2026-11-06: exact_raw 56, exact_normalized 66, editorial_omission 1, text_differs 21, only_in_oracle 12, unresolved 32 → 123 of 188 agree behind the blanket; ledd 0 of 498 proven.

**Straffeloven** (`lov/2005-05-20-28`), regenerated (uncommitted, 11.6 MB), sha256 `169f7aaf6087b5f25a53a5fc4b0861a2abb45d019e4dd74412903d196eed3635` = the digest the Phase 3 note records for this ladder (byte-identical):
- `baseline_timing` / `commencement.default`: **dated 2015-10-01**, known_from 2015-06-19, proof `lov/2015-06-19-65` (`lti/2015/nl-20150619-065.xml`), D5(d).
- `commencement.units`: `kap16` part I → **unresolved: conflicting commencement decisions**, proofs `forskrift/2008-03-07-225` + `lov/2015-06-19-65`. Explicit, not silently resolved. (Correction to an earlier digest written against the stale `legaltime-replay-2` tree: at `87ab1429` res. 225 IS loaded and the conflict IS recorded.)
- Ladder: 2008-03-06 / 2008-03-07 / 2015-09-30 `not_yet_in_force` (725 / 624 / 115 future, 16 pending, 105 unresolved timing); 2015-10-01 `replayed` 376 applied, 343 unresolved ops, 128 act-level; 2026-11-06 463 applied, 371 unresolved, 143 act-level.
- Verification at 2026-11-06: `unresolved` 400/400 (blanket). Before blanket: exact_raw 97, exact_normalized 129, only_in_oracle 19, text_differs 34, unresolved 121 → 226 of 400 agree behind the blanket; ledd 0 of 700.

**Four Phase 1 acts** (arveloven, personopplysningsloven, sivilombudsloven, språklova): origin/main copies are `legaltime-replay-2`, the `87ab1429` copies `legaltime-replay-3`. Structural diff after removing `replay_version` and the documented additive fields (`commencement`; states `not_in_force`/`pending_commencement`/`unresolved_commencement`; verification `ledd_counts`/`counts_before_blanket`): **0 residual differences** for all four. No date, verdict or count changed. Terminal states: arveloven 183 sections / 464 of 464 ledd proven; personopplysningsloven 35 (+99 annex) / 76 of 76; sivilombudsloven 35 / 94 of 94; språklova 22 / 46 of 46; act-level 0 everywhere, so no blanket (these are the only proven sections in the project).

**Eight Phase 2 acts**, regenerated at `87ab1429` with the phase-2 ladder (6 dates, oracle), then reduced with `scripts/legal_time_coverage.py`: content-identical to the committed `docs/research/data/legal-time-coverage-2026-11-08.json` (0 differences per act; the file digest differs only because the script keys acts by input filename). Changes against the previous pinned baseline `legal-time-coverage-2026-11-07.json` (origin/main, `legaltime-replay-2`), verified from both JSONs:

| act | own commencement 11-07 → 11-09 | events | dated | pending | unresolved timing | applied | act-level | unattributed | proven | agree behind blanket |
|---|---|---|---|---|---|---|---|---|---|---|
| utlendingsloven | dated 2010-01-01 → same | 556 → 556 | 281 → 306 | 9 → 9 | 266 → 241 | 160 → 176 | 85 → 93 | 26 → 5 | 0/212 → 0/212 | n/a → 50/212 |
| straffeloven | unresolved → **dated 2015-10-01** | 523 → 850 | 136 → 729 | 16 → 16 | 371 → 105 | 0 → 463 | 0 → 143 | 155 → 106 | no text → 0/400 | n/a → 226/400 |
| arbeidsmiljøloven | unresolved → unresolved | 347 → 356 | 263 → 272 | 2 → 2 | 82 → 82 | 0 → 0 | 0 → 0 | 85 → 10 | no text | — |
| friskolelova | dated 2003-10-01 → same | 293 → 300 | 222 → 232 | 0 → 0 | 71 → 68 | 157 → 168 | 48 → 47 | 2 → 2 | 0/92 → 0/92 | n/a → 34/92 |
| tvisteloven | dated 2008-01-01 → same | 161 → 163 | 72 → 111 | 8 → 8 | 81 → 44 | 57 → 94 | 9 → 8 | 61 → 7 | 0/442 → 0/442 | n/a → 296/442 |
| merverdiavgiftsloven | dated 2010-01-01 → same | 324 → 324 | 160 → 162 | 0 → 0 | 164 → 162 | 103 → 105 | 42 → 42 | 0 → 0 | 0/206 → 0/206 | n/a → 89/206 |
| naturmangfoldloven | unresolved → unresolved | 49 → 49 | 44 → 44 | 0 → 0 | 5 → 5 | 0 → 0 | 0 → 0 | 4 → 4 | no text | — |
| barnevernsloven | unresolved → **dated 2023-01-01** (+2 units) | 179 → 185 | 18 → 134 | 2 → 3 | 159 → 48 | 0 → 101 | 0 → 10 | 4 → 4 | no text → 0/188 | n/a → 123/188 |
| **total** | 4 of 8 → **6 of 8** | 2,432 → 2,783 | 1,196 → 1,990 | 37 → 38 | 1,199 → 755 | 477 → 1,107 | 184 → 343 | 337 → 138 | **0 → 0** | — → **818 of 1,540** |

"Unsupported" per act is not derivable from the coverage JSON (its `unsupported_instructions` map is capped at 25 shapes); the note's 343 total comes from `end_state.act_level_reasons["unsupported instruction"]` summed over acts (straffeloven 74) and is reported as the note states it. The `agree behind blanket` column (`counts_before_blanket`) is an additive replay-3 field, so the 11-07 baseline has none. Every change is the one the Phase 3 note §5 explains (the `Lov … endres slik:` intro, partial decisions with unit lists, D5(d) for straffeloven, D4/D6 for barnevernsloven); nothing else moved.

**Reconciliation at 2026-11-06, six acts with text, verified**: sections 1,540; exact_raw 356; exact_normalized 460; editorial_omission 2; text_differs 114; only_in_oracle 148; only_in_replay 7; oracle_annex 103; unresolved 350. **Proven final: 0 of 1,540 sections, 0 of 3,217 ledd.** 818 of 1,540 sections agree with the oracle *behind* the act-level blanket. That is a measurement of the blanket's cost, not proven historical law.

**CI/Codex/mutation on the heads**: #847 @ `194bd554` all 16 checks green, mutation PASS (1241/1243, 2 registered equivalents); #848 @ `87ab1429` all 12 checks green, mutation PASS (1531/1533, 2 equivalents, `utf-8`/`UTF-8`), six Codex read-only reviews (`f7063843` NOT READY fixed, `0a69bbaf` NOT READY fixed, `4f80cd55`, `ad30391b`, `597eddca`, `87ab1429` READY).

## 2. Defects found and filed this session (all lovspor, engine)

| issue | finding | effect today | effect under any blanket narrowing |
|---|---|---|---|
| **#856** | `instructions._open_block` keeps the previous intro's act when the next intro is unrecognised. lov/2015-06-19-65 § 4 (`Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i lov 22. mai 1902 nr. 13 militær straffelov:`) → 71 militær-straffelov ops attributed to straffeloven 2005; **11 applied** into the straffeloven state (§13, §30, §34, §40, §59, §61, §67, §69, §71, §78, §89), 60 held only because their targets happen not to fit. | all 400 sections `unresolved` anyway (blanket) | §34, §59, §61, §71, §89 have no other problem: they would read as replayed law with military-law text |
| **#857** | `_INFORMATIONAL` drops `Fra samme tid …` sentences before the grammar sees them; FOR-2009-06-19-704 sentence 2 (`§ 78 nr. 1 til 16 … med unntak av nr. 7`) silently vanishes | masked: sentence 1 also refused | bypasses D6 once sentence 1 parses |
| **#858** | unknown-target op with `unresolved` timing never enters `act_level_problems` (`replay.py:605-612`); BV `lov/2024-06-25-53#11:XI:12.0/14.0` | masked by dated act-level ops | fail-open |
| **#859** | D7's "unattributed keeps the member's states unproven" leg not implemented; 138 unattributed ops are not an input to `verify()` | masked | fail-open |

Consequence for Phase 4: #856 is a correctness bug independent of any decision and must be fixed first; #857–#859 are preconditions of any D7 narrowing (blocker A). None of them changes a Phase 3 verdict.

## 3. Blockers A–E, decision record

### A. ADR-0020 D7 act-level blanket (D13 deferral)

- **Mechanism** (`verify.py:161-164`): one entry in `state.act_level_problems` rewrites every section verdict to `unresolved`. Act-level = handler failed and no named section is in the map (`replay.py:547-548`): every `unsupported` sentence, unabsorbed `renumber` with empty `Address`, `substitute_term` with unknown locations, and any addressed op whose key is absent from the map.
- **Impact**: 343 act-level problems hold 0 of 1,540 sections proven; 818 agree behind. Barnevernsloven is held 2023-01-01 to 2024-01-01 by **one** op (`lov/2022-06-17-45#2:II:22.0`); straffeloven by 128–143.
- **Can uncertainty be confined?** Only with a per-operation *reach set* R(op) derived from evidence. Today nothing supplies it: `Address.section=None` means "anywhere"; `host_section` and `change_part` are stamped (`document.py:245`) but unused for reach. For a unit U to be provable, every act-level op, every unresolved-timing unknown-target op (#858) and every unattributed op (#859) must have a proven R with U ∉ R. #856 shows the attribution itself can be wrong, so R must be computed from the amending member's structure, not from the sentence alone.
- **Safe alternative (stricter-first, no weakening)**: (1) fix #856–#859 so the blanket's input set is complete; (2) add R(op) as an explicit, evidence-backed field: for an `unsupported` sentence inside a `legalArticle`/`change` block whose intro names act A and whose preceding addressed ops all target §§ in chapter K, R = "act A, unknown" unless a structural rule proves narrower; the only rule worth proposing first is **"a sentence that names a § in its text reaches at most that § and its neighbours named"**, measured before adoption. (3) Keep the blanket unconditional until the measured false-exemption rate on the six acts is 0 against the oracle.
- **Risk**: any R rule that is wrong is exactly ADR's "silent-until-fixed" class; #856's 11 applied ops are the live example of what a narrower blanket would expose.
- **Dependencies**: #856, #857, #858, #859; B (renumbering) because unresolved renumberings are a large share of act-level reasons (straffeloven 6+3, BV 3).
- **ADR**: amendment to D7 (new decision "D7a reach sets"), not a new ADR. Owner must accept it before any code.
- **Falsifiable experiment**: implement R(op) as a report-only field (no verdict change), then for each of the six acts count sections S such that every act-level op has U ∉ R, and compare those S against the oracle: if any S is `text_differs`/`only_in_oracle`, the rule is refuted. Acceptance: 0 refutations across 1,540 sections, and the §34/§59/§61/§71/§89 case from #856 is caught (after #856 is fixed, re-run with the fix reverted as a mutation).

### B. Section renumbering (D7, D13, Open Q4)

- **Today**: keys are `data-name` labels (`§88a`, `§9A-1`); `renumber` applies only when absorbed (ledd/punktum shift implied by the immediately preceding insertion/repeal in the same §); `Nåværende § X blir § Y` → empty address → act-level; `§ 101 … blir § 401, og skal lyde slik:` → `unsupported`. No lineage field exists (`ProvisionState` has `key`, `applied`, `problems`; no `former_keys`).
- **Impact**: straffeloven cannot pass 2008 (§ 101→§ 401, § 102→§ 402, then 2009 § 401→§ 411): old `§101` key blocks the chapter-16 `§§ 101–110` insertions (`key already present` 7), then 52 `target provision missing`, 28 `target ledd range`, 74 unsupported. `kinds.renumber` 152 across the 8 acts (ledd/punktum/section mixed); barnevernsloven has 0 section renumberings.
- **Editorial label vs legal identity**: the announcement renumbers a *label*; the norm (the ikraftsetting rule) keeps its identity across § 101 → § 401 → § 411. A replay keyed by label can move the key only if the move is recorded as an event with the instrument id (lineage), and a served view must never present `§ 101` (2008, ikraftsetting) and `§ 101` (2015, folkemord) as one history. Chained amendments (2009 renumbers what 2008 renumbered) need the chain to resolve in publication order with each link proven.
- **Safe alternative**: model a `rename_section` op kind that (a) is applied only when the sentence names both keys explicitly, (b) records `former_keys` with the instrument id on the provision, (c) refuses if the target key exists or the source does not, (d) never rewrites cross-references (refs stay unresolved, as D7 says). Chains: apply strictly in order; a renumbering whose source was itself renumbered in the same member is refused unless the earlier rename applied.
- **Risk**: label reuse (ADR-0009 hazard): straffeloven `§101` 2005 (ikraftsetting) vs `§101` 2008 (folkemord) is the exact case; only lineage keeps them apart.
- **Dependencies**: none on A; A depends on B for straffeloven.
- **ADR**: new decision in ADR-0020 (D15 "section identity across renumbering: label lineage, no norm identity claim") or a small new ADR; D13 explicitly reserved it for the owner.
- **Experiment**: straffeloven ladder with `rename_section` enabled: 2008-03-07 must still be `not_yet_in_force` (D4), kap. 16 must stay `unresolved` (C), and at 2015-10-01 the sections 101–110 must exist with 2008 text, `former_keys` empty, while `§411` carries `former_keys=[§101@lov/2008-03-07-4, §401@lov/2009-06-19-74]`. Falsifier: any section reaching `exact_*` before blanket whose oracle text belongs to a different norm.

### C. Straffeloven chapter 16

- **Primary evidence** (pinned archive members; quoted verbatim in lovspor-notebook #169): 2005 § 101 "Loven trer i kraft fra den tid Kongen bestemmer. Kongen kan sette forskjellige bestemmelser i loven i kraft til ulik tid."; lov 2008-03-07-4 renumbers to § 401 and adds the kap. 16 ledd; **res. 7 mars 2008 nr. 225** (hjemmel § 401 første ledd): "Lovvedtaket del I kapittel 16 trer i kraft straks." (the 2008 *amending act's* del I, which inserted kap. 16); lov 2009-06-19-74 § 411: "Ikraftsetting … fastsettes ved lov. … Kapittel 16 kan settes i kraft fra den tid Kongen bestemmer. …"; lov 2015-06-19-65 § 1 replaces **§ 411 første ledd only** with "Loven trer i kraft 1. oktober 2015. …", § 8 "Loven her trer i kraft straks."; current § 411 annet ledd unchanged since 2009 with footnote "Fra 7 mars 2008 iflg. res. 7 mars 2008 nr. 225". The 2015 act's text contains no commencement sentence about kap. 16 (grep of the member: only cross-references in other acts' text).
- **Verdict on the law**: **legitimately staged**, not conflicting. The two instruments date disjoint unit sets (kap. 16 in 2008; the rest in 2015); the 2015 sentence says "Loven" but the same § 411's annet ledd carves kap. 16 out by delegation, and no instrument re-sets kap. 16. Complication: between 2008-03-07 and 2015-10-01 første del *applies to* kap. 16 without being in force generally (§ 401/411 annet ledd) — a scoped-applicability state a per-unit date cannot hold.
- **Engine today**: `unresolved: conflicting commencement decisions` with both proofs. Correct as fail-closed, but the *reason* asserts a disagreement the sources do not contain. Under D6 as written ("different days on the same unit → conflicting") the engine is right only because "Loven" is read as reaching kap. 16; the carve-out is in another sentence of the act's own § 411, which the default-clause reader does not consult.
- **Safe alternative**: owner ruling that a default clause of the act's own commencement section is **narrowed by a reservation in the same section** ("Kapittel N kan settes i kraft fra den tid Kongen bestemmer") when that reservation is in force at the default's `known_from`; the kap. 16 unit then dates 2008-03-07 by res. 225, the rest 2015-10-01; første del gets an explicit `applies_to` fact for [2008-03-07, 2015-10-01) and stays `pending` as a unit. Without the ruling: keep `unresolved`, change nothing. Do not silently resolve.
- **Dependencies**: B (chapter-16 §§ 101–110 cannot be placed until § 101→§ 401 renumbering applies); D (chapter scope `Lovvedtaket del I kapittel 16` is already parsed at `87ab1429`; the "del I of the amending act" reading must not be confused with the 2005 act's Første del).
- **ADR**: amendment to D6 (reservation in the act's own commencement section narrows its default clause) — owner decision, legal reading.
- **Experiment**: fixture act with clause "Loven trer i kraft D1. Kapittel N kan settes i kraft fra den tid Kongen bestemmer." plus a `Delvis ikraftsetting` decision "… kapittel N trer i kraft straks" at D0 < D1. Expected: kap. N units dated D0 (proof: decision), all others D1 (proof: clause), no `conflicting`. Falsifiers: kap. N dated D1; or any output labelled `conflicting`. Then the straffeloven ladder: 2008-03-07 must show §§ 101–110 (after B) in force and everything else `not_in_force`.

### D. Commencement scope grammar

Supported shapes at `87ab1429` (`scopes.py`): listed items with units and `med unntak av`, whole-with-exceptions, chapter of the act, units of the act, `Delt` sentence (part + one §), part/listed part scopes, remainder, own-section listings; `_RESERVATION` elsewhere refuses the block. Gaps, classified honestly against the Pydantic models (`Scope`, `Unit`, `Timing`):

| shape | count | class |
|---|---|---|
| multi-act act-named list (`Loven del I, endringene i straffeprosessloven § 84, …, del III, endringene i straffeloven § 27 …`) | 83 of straffeloven's 105 unresolved timings; `partial decision outside the grammar` 300 of 755 across acts | **parsing gap** (several `Scope(part, units, act)` are representable) + one semantic caveat: `reach` returns `unknown` when `scope.act != op.act` (`scopes.py:178-179`), so a part amending two acts blocks unless another act's item is treated as `disjoint` |
| part-only scope in the act's own commencement (`Loven del I trer i kraft …`) | 0 of 8 acts; found by Codex review, fail-closed `PART_UNPLACED` | **model gap**: no part Unit, no part → chapters map (`Provision.part` exists but `section_timing` uses it for chapters only) |
| chapter range `kapittel 3 til 5` | not counted | parsing gap |
| exceptions in other words (`unntatt`, `likevel`, `bortsett fra`) | aml item 5b | parsing gap (`excluded` holds them) |
| prose exception (`med unntak av lovendringene om utlegg som … gradvis`) | FOR-2025-06-10-967 | **model gap** (predicate, not units) |
| conditional / person / object (`For arbeidstakere som …`, `for utenlandske treslag`, `for saker som …`) | aml, nml | **model gap** (`Scope.when: date`, `Timing` has no condition) |
| sunset / repeal-at-date (`Del I til X oppheves 1. juli 2023`) | BV 45 of 48 unresolved timings | **model gap** (`Timing` has no end) |
| unit below ledd dated apart (`§ 3-2 første punktum`, bokstav) | parses, state refuses `BELOW_LEDD` | **replay-model gap** (`UnitState.flagged` ledd-only) |
| act cited by full title as subject (`Lov 17. juni 2005 nr. 62 om … trer i kraft …`) | aml, nml defaults | parsing gap (title → refid) |

- **Priority inside D**: multi-act lists (largest measured impact, parsing) and title-cited subject (unblocks E's regex part). Model gaps need an ADR decision each; the "part" model gap is small and well-bounded (part → chapter map from the act's own structure).
- **ADR**: none for parsing gaps (D5/D6 already cover the semantics); a D6 amendment for the part Unit; conditional/sunset need a new decision ("applicability statements are not commencement") — see E.
- **Experiment**: per shape, a fixture sentence + the expected `Scope` list; for multi-act lists, straffeloven's 83 must drop with no new `dated` op whose instrument does not name straffeloven (falsifier: any op dated by a scope whose `act` is another act).

### E. arbeidsmiljøloven and naturmangfoldloven baselines

Both refuse at `87ab1429` with `partial decision outside the grammar` (`commencement.default.reason`, `units: []`) because `baseline.py:376` stops at the first citing decision. Primary texts read (lovverk corpus + pinned resolution members; quoted verbatim in lovspor-notebook #171):

- **aml** § 20-1 "Loven trer i kraft fra den tid Kongen bestemmer."; **FOR-2005-06-17-609**: (1) act by full title "trer i kraft fra 1. januar 2006 med følgende unntak og presiseringer:"; (2) "§ 10-12 sjette ledd trer i kraft 1. juli 2005."; (3) "§ 14-9 femte ledd andre punktum **får ikke anvendelse overfor midlertidig arbeidsavtale som løper** …"; (4) "**For oppsigelser som finner sted før** lovens ikrafttreden, gjelder lov 4. februar 1977 nr. 4 … § 61 nr. 4."; (5) "**For arbeidstakere som** … har en særlig uavhengig stilling skal … lovens kapittel 10 tre i kraft 1. januar 2011. § 10-2 første, andre og fjerde ledd trer likevel i kraft 1. januar 2006."; (6) "Ikrafttreden for § 2-4 fastsettes senere."
  **Classification: b + c + d.** Items 1, 2, 5b, 6 are unit-level (regex + the existing per-unit fold: default 2006-01-01, § 10-12 (6) 2005-07-01, § 10-2 (1,2,4) 2006-01-01, § 2-4 pending). Items 3, 4, 5a are **applicability / transitional statements conditioned on contracts, events and persons** (two dates for kap. 10 keyed on a person predicate). No unit-date holds them. Dating aml at all requires an owner ruling that such statements are outside the commencement model (recorded as explicit `applicability` facts, never as dates) — that is person-conditional semantics, not regex. Until then the refusal is correct.
- **nml** § 76 "Loven gjelder fra den tid Kongen bestemmer. Kongen kan fastsette at loven skal tre i kraft til forskjellig tid for forskjellige bestemmelser **eller forskjellige grupper av organismer**."; **FOR-2009-06-19-704** "… (naturmangfoldloven), med unntak av kapittel IV i loven, trer i kraft fra 1. juli 2009. Fra samme tidspunkt trer endringene i § 78 nr. 1 til 16 i kraft, med unntak av nr. 7 …"; FOR-2011-01-07-10 (§ 78 nr. 15, 2011-02-01); FOR-2012-06-22-588 (§§ 28, 30, 31, 32 from 2012-07-01), **repealed and restated** by FOR-2013-01-25-72 "… gjelder fra 1. juli 2012 **for utenlandske treslag**"; FOR-2015-12-18-1583 "Kapittel IV samt § 78 nr. 8 og den delen av § 78 nr. 7 som gjelder oppheving av viltloven § 47 trer i kraft 1. januar 2016."
  **Classification: b + c (+ one object-conditional instrument).** Default 2009-07-01 less kap. IV is a pure regex gap (title-cited subject, exception before the verb); kap. IV 2016-01-01 is a chapter unit; both reachable with the per-unit fold. The § 78 `nr.` items, "den delen av nr. 7 som gjelder …", a decision repealing an earlier decision, and "for utenlandske treslag" (one unit, two dates keyed on the regulated object; the axis § 76 itself announces) are below-ledd / object-conditional: owner ruling (pending or conflict), not regex; they do not block the default or kap. IV.
- **Answer to the question**: neither act is a regex-only case. nml's *default* is; aml's is not. Both need the per-unit fold (#848) as floor; aml and nml's 2012/2013 instruments need **applicability semantics** (person/object/event-conditional) decided by the owner before any unit is dated. Proposed decision text (not accepted): "A commencement instrument's sentence that conditions a unit's application on a class of persons, contracts, events or objects is an *applicability statement*; it never dates a unit and never undates one; it is recorded verbatim with its unit, and a unit carrying one is reported `dated` with an `applicability` note, or `unresolved` if the owner so decides." Experiment: aml ladder at 2006-01-01 and 2011-01-01 with the ruling applied; falsifier: kap. 10 dated 2011-01-01 for everyone, or § 14-9 (5) absent from the 2006 text.
- Filed from this blocker: #857 (`_INFORMATIONAL` drops 704's second sentence).

## 4. Phase 4 proposal (bounded, for owner decision; nothing here is approved)

Priority by correctness and dependency, not by percentage:

1. **Fix #856** (attribution after an unrecognised intro). Pure bug; fail-closed fix (`self.act = None`, ops unattributed). Acceptance: straffeloven evidence regenerates with 0 ops hosted in the 2015 act's § 4 attributed to straffeloven; unattributed count rises accordingly; a unit test on the § 4 intro.
2. **Close the blanket's input set: #858, #859** (and #857 as a guard). Acceptance: unit tests that fail on current code; Phase 2 coverage unchanged in verdicts (still 0 proven), act-level counts may rise.
3. **Owner rulings** (ADR-0020 amendments, each a separate notebook decision; none is code until accepted): (a) D6 reservation-in-own-section narrows the default clause (C); (b) section label lineage `rename_section` with `former_keys`, no norm-identity claim (B); (c) applicability statements are not commencement (E, D-conditional); (d) part Unit with part → chapter map (D).
4. **Parsing-only grammar work** that needs no ruling: multi-act act-named lists (83 straffeloven timings), title-cited subject, exception synonyms, chapter ranges. Acceptance: for each, `unresolved timing` drops only where a proof names the act; no new `dated` op without a proof naming the act.
5. **D7 reach sets** (A) only after 1–3 and only as report-only until the falsification experiment passes on all six acts.

Out of Phase 4: serving, MCP/API, public JSON, materialisation, corpus-wide replay, licence basis (D13), ADR-0019 R2 (unchanged).

## 5. Governance recommendation

- **#744**: post the verified Phase 3 closeout (§0–§2 above, with SHAs) **after #848 merges**, with the real merge commit. Then **close #744** as the pilot record: its scope (replay axis on 2–3 acts, byte-for-byte reconciliation, measure what needs a human) is fulfilled; phase 3 delivered per-unit and cross-act commencement with evidence.
- **Follow-up issues** (lovspor, engine): #856, #857, #858, #859 exist. One umbrella "Phase 4" issue in lovspor referencing them and the owner rulings is sufficient; do not reopen the ADR discussion there.
- **Notebook issues opened 2026-11-10** (owner decisions, one each; no open notebook issue touched legal-time replay before): **#169** D6 reservation in the act's own commencement section (straffeloven kap. 16, staged reading); **#170** section label lineage across renumbering (D13/Q4); **#171** applicability statements vs commencement (aml, nml); **#172** part unit / part-only scope; **#173** D7 reach sets (A), stricter-only, report-only, gated on #856–#859. None accepts a decision; each carries the evidence and the falsifiable experiment.
- **lovspor umbrella**: **#861** (Phase 4 order, acceptance criteria, out-of-scope list, baseline numbers).
- **Not recommended**: opening a Phase 4 implementation branch before the rulings in #169–#172.

## 6. Limitations of this closeout

- All reproduction is against `87ab1429`, not a merge commit. If #848 merges with new commits, §1 must be re-run.
- The straffeloven evidence (11.6 MB) is uncommitted by design; its digest is pinned in the Phase 3 note and reproduced here.
- The "unsupported" per-act column cannot be re-derived from the coverage JSON (capped list); taken from the note.
- lovverk MCP `get_temporal_events` refused aml/nml (corpus head unattested, ADR-0012 Am. 1); resolution texts were read from the pinned Lovtidend archive instead.
- An intermediate research digest for blocker C was run on a stale `legaltime-replay-2` worktree; its grammar claims (`Delvis` title unread, res. 225 not loaded, 0 conflicts) are **false at `87ab1429`** and were corrected above from the regenerated report. Its primary-source reading stands.
