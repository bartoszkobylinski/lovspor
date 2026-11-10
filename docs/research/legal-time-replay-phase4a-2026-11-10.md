# #861 Phase 4A: four legal-time engine defects fixed (#856–#859), research, 2026-11-10

This note covers engine correctness only. No ADR changed. No owner ruling was assumed: kap. 16, renumbering and reach sets stay out (#169–#173). Nothing is served, published or written into `lovverk`, the MCP/API/public JSON surfaces are untouched, and no corpus-wide replay was run. ADR-0020 (Accepted, lovspor-notebook `1f857f2`) Decision 7 is applied as written: the blanket is not narrowed. It now sees two inputs it missed before.

## 0. Baseline and inputs

| item | value |
|---|---|
| baseline | origin/main `51c5d702` (contains #847 `fe93dca5`, #848 `97f5f7fe`, #863 `51c5d702`) |
| `lovtidend-avd1-2001-2025.tar.bz2` | sha256 `d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0` |
| `lovtidend-avd1-2026.tar.bz2` | sha256 `600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2` (current-year archive: ADR-0019 R2 `research_only`, unchanged) |
| oracle `gjeldende-lover.tar.bz2` | sha256 `380632d48c82e48bb6fa37c6c6e6f13d55c77defa5f83400c5120ca6989593e9` |
| `REPLAY_VERSION` | `legaltime-replay-3` → **`legaltime-replay-4`** |

Only permitted publicData archives were read; no lovdata.no HTML and no Lovdata Pro. The baseline was rebuilt first, on a detached checkout of `51c5d702`. The straffeloven ladder (`--at 2008-03-06 2008-03-07 2015-09-30 2015-10-01 2026-11-06`) came out byte-identical to the Phase 3 digest `169f7aaf…eed3635`. The four phase-1 files, barnevernsloven and the eight-act coverage were also reproduced. The commands are the ones in the Phase 3 note §1, with the coverage ladder files named `legal-time-replay-2026-11-10-<short>.json`.

## 1. The defects and the fixes

### #856: an unrecognised intro left the previous act bound

`instructions._Reader` kept `self.act` from the last recognised intro. It reset it only at a new part. The sentence that opens lov/2015-06-19-65 § 4 is `Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i lov 22. mai 1902 nr. 13 militær straffelov:`. It is a `legalP` that neither intro pattern matches, so it was dropped as a loose node. Its payload slice was not cut either.

**Wider than filed.** § 5 of the same act opens the same way for straffeprosessloven. At `51c5d702` it contributed **145** more operations to straffeloven, **30** of them applied. That is in addition to § 4's 71 operations and 11 applications. The extension is recorded on #856 with the evidence. The full-corpus audit (§3) also found the same mechanism in other members, attributing operations to the wrong *other* act.

**Fix.** An act is bound only by an intro that names it, and only inside the article where that intro sits:
- a new `legalArticle` (`host_section` change) closes the block;
- so does an intro shape the grammar cannot bind (`… gjøres følgende endringer …:` / `… endres slik:`), anywhere in a node, including one fused into the end of a payload ledd;
- an instruction's payload ends at the next instruction, at such an intro, or at an article boundary;
- an intro that opens only a unit of the act already named keeps the block (`§ 4-2 endres slik:`, `I § 15-11 gjøres følgende endringer:`, `§ 102 nr. 1 vert endra slik:`, `Kapittel 6 endres slik:`), unless it names an act in any way: by date (`I § 3 i lov 22. mai 1902 nr. 13 …`), by short title (`I § 3 i straffeloven …`, review round 2), or as `lov …`/`forskrift …`. This had no measured effect on the evidence;
- inside a block opened by an unbindable intro, an `unsupported` sentence is counted as an unattributed operation, not dropped as prose.

What follows a closed block is **unattributed**. No act is inferred from the intro's free text. Binding `… i lov 22. mai 1902 nr. 13 militær straffelov:` positively would need a grammar extension; that is left for later work (#861 item 3).

### #857: `_INFORMATIONAL` dropped commencement sentences

`Fra samme tid…`, `Kongen kan…`, `Dersom…`, `Når…` and similar openings were discarded before any grammar saw them. **Fix:** a sentence with such an opening is still informational, *unless* both of the following hold:
- it states a commencement phrase (`i kraft`, `tek … til å gjelde`, once references like `når loven trer i kraft` are removed);
- it carries a date, `straks`, a reservation or exception (`med unntak`, `unntatt`, `likevel`, `bortsett`), or the `Fra samme tid` anaphora;
- or it names a unit (§, kapittel, del, ledd) and its main clause states the commencement (`Dersom § 2-6 ikke er satt i kraft, trer den i kraft.`).

The unit case was added after the independent review. A commencement phrase confined to a leading reference clause does not count: `Er arvelateren død før ny § 99 a i arveloven trer i kraft, regnes fristen …` (lov/2020-12-18-149) and `Fra den tid endringene … i del I i loven her trer i kraft, oppheves midlertidig lov …` (lov/2026-06-19-48) stay informational. A first version without this main-clause rule refused both, and cost the arveloven pilot 10 proven sections; that version was not committed.

Such a sentence reaches the decision parser and the clause reader, and is refused there if unread (`partial decision outside the grammar` / `commencement sentence outside the grammar`). FOR-2009-06-19-704 sentence 2 (`Fra samme tidspunkt trer endringene i § 78 nr. 1 til 16 i kraft, med unntak av nr. 7.`) is now in the decision's block. Plain prose (`Fra samme tid oppheves lov …`, `Kongen kan sette de enkelte bestemmelsene i kraft til forskjellig tid.`) stays informational.

**Measured effect on the evidence:** the pilots, barnevernsloven, straffeloven and seven of the eight coverage acts are unchanged. naturmangfoldloven's default is still refused by sentence 1: the defect was latent, and it is now closed before the title-cited-subject grammar can expose it.

merverdiavgiftsloven changes. lov/2017-06-16-55's clause `Kongen kan sette i kraft del III og IV til ulik tid for ulike deler av riket.` now reaches the grammar and is refused. Its three operations aimed at the act were already `unresolved`; they now carry `commencement sentence outside the grammar` with no `known_from`. One of them is an unknown-target operation, so under the #858 rule it blankets every state. Act-level counts at 2008-01-01, 2012-01-01 and 2016-01-01 rise by one (17 → 18, 30 → 31, 261 → 262); the end state is unchanged.

### #858: an unknown target with unresolved timing bypassed the blanket

An operation that names no provision (`unsupported`, or a `renumber` with an empty address) and whose timing is `unresolved` went only into `state.unresolved`. **Fix:** it is now an act-level problem `"<id>: target unknown, commencement <reason>"` of every state at or after its timing's `known_from`, and of every state when `known_from` is unknown. That is the usual case: `_unresolved` timings carry none. Operations with a named target keep their per-section problem. `pending` and future-dated operations are listed, as before, and are not blanketed.

### #859: Decision 7's unattributed leg was not implemented

Unattributed operations (`op.act is None`) were counted per member and otherwise ignored. **Fix:**
- `run.unattributed_for(evidence)` collects them with their timings and the report lists them as `unattributed_events`;
- the replay never applies one;
- one in effect at D (dated ≤ D, or `unresolved` under the #858 `known_from` rule) is an act-level problem `"<id>: unattributed operation, act unknown (<member>)"`, which puts it in `verification.act_level_problems` and in every blanketed section's detail;
- a pending or future one leaves the state alone.

Member identity and timing are carried, never a guessed act.

## 2. Before / after (`51c5d702` → this branch)

### 2.1 Straffeloven ladder and the #856 acceptance numbers

lov/2015-06-19-65 operations attributed to straffeloven: **259 → 43** (§ 3 42, § 1 1). Hosted in § 4: **71 → 0**. Hosted in § 5: **145 → 0**. All 216 are now `unattributed_events`, and the member reports 219 unattributed (0 before). The other 3 were attributed to *other* acts at `51c5d702` (§ 3.2).

| state | status | withheld | applied | unresolved ops | act-level | §§ 4–5 ops applied |
|---|---|---|---|---|---|---|
| 2008-03-06 | not_yet_in_force | 4 → 4 | 0 | 105 → 100 | 0 → 78 | 0 |
| 2008-03-07 | not_yet_in_force | 105 → 99 | 0 | 105 → 100 | 0 → 94 | 0 |
| 2015-09-30 | not_yet_in_force | 614 → 392 | 0 | 105 → 100 | 0 → 393 | 0 |
| 2015-10-01 | replayed | 0 | 376 → **335** | 343 → 157 | 128 → 434 | **41 → 0** (§ 4 11 → 0, § 5 30 → 0) |
| 2026-11-06 | replayed | 0 | 463 → **424** | 371 → 183 | 143 → 461 | **41 → 0** |

Every one of the 41 applied operations at `51c5d702` is gone from `applied` and from every provision's `applied`. Of the 11 § 4 operations (§13 l.1, §30 l.1, §34 l.3, §40, §59 l.1, §61 l.1, §67 l.1, §69 l.1, §71 l.2, §78 l.1, §89 l.1), none applies. Verification at 2026-11-06: still `unresolved` 400/400. Behind the blanket, sections agreeing with the oracle rise from **226 to 294 of 400**. The 68 sections that military-law and procedure-law text had overwritten, or that carried their problems, now match the consolidated text. The blanket hides that number; it is never shown as a verdict. Withheld 105 → 99 at 2008-03-07 comes from 6 unsupported sentences of lov/2008-03-07-4 that are now unattributed: `I straffeloven 2005 tredje del gjøres følgende endringer:` and the two sentences after it, plus `1. I militær straffelov 22. mai 1902 nr. 13 gjøres følgende endringer:`, `§§ 80-86 og §§ 90-94 a oppheves.` and `2. Lov 18. august 1914 nr. 3 om forsvarshemmeligheter oppheves.`. The last three were misattributed militær straffelov and forsvarshemmeligheter text. Unresolved 105 → 100 at every date: five straffeprosessloven operations of lov/2007-04-13-14 part IV (§ 3).

Ladder digest: `169f7aaf…eed3635` → `a0a9cfa5c9bb41e50a07d9fb7d84d87e846ffe9c90b1f3a8f823a036b59f6ea8`. The file is 5.4 MB, regenerated and not committed, as in Phase 3.

### 2.2 Eight-act coverage (`legal-time-coverage-2026-11-08.json` → `legal-time-coverage-2026-11-10.json`, end state 2026-11-06)

| act | events | dated | pending | unres. timing | applied | unres. ops | act-level | unattributed (members) | unattributed events | proven | agree behind blanket |
|---|---|---|---|---|---|---|---|---|---|---|---|
| utlendingsloven | 556 | 306 | 9 | 241 | 176 | 371 | 93 → 405 | 5 → 245 | 0 → 245 | 0/212 | 50/212 |
| straffeloven | 850 → 623 | 729 → 507 | 16 | 105 → 100 | 463 → 424 | 371 → 183 | 143 → 461 | 106 → 388 | 0 → 388 | 0/400 | 226 → 294/400 |
| arbeidsmiljøloven | 356 | 272 | 2 | 82 | 0 | 82 | 0 → 268 | 10 → 257 | 0 → 257 | no text | — |
| friskolelova | 300 | 232 | 0 | 68 | 168 | 132 | 47 → 78 | 2 → 21 | 0 → 21 | 0/92 | 34/92 |
| tvisteloven | 163 | 111 | 8 | 44 | 94 | 61 | 8 → 262 | 7 → 245 | 0 → 245 | 0/442 | 296/442 |
| merverdiavgiftsloven | 324 | 162 | 0 | 162 | 105 | 218 | 42 → 293 | 0 → 228 | 0 → 228 | 0/206 | 89/206 |
| naturmangfoldloven | 49 | 44 | 0 | 5 | 0 | 5 | 0 → 4 | 4 | 0 → 4 | no text | — |
| barnevernsloven | 185 | 134 | 3 | 48 | 101 | 66 | 10 → 17 | 4 | 0 → 4 | 0/188 | 123/188 |
| **total** | 2,783 → 2,556 | 1,990 → 1,768 | 38 | 755 → 750 | 1,107 → 1,068 | 1,306 → 1,118 | 343 → 1,788 | 138 → 1,392 | 0 → 1,392 | **0 → 0** | 818 → 886 of 1,540 |

Own commencement: 6 of 8, unchanged; no default or unit timing changed. **Proven: 0 of 1,540 sections before and after**, so no verdict changed. The 1,788 act-level entries at the eight end states split into three classes:
- **unattributed (#859): 1,392.** At 2026-11-06 every unattributed operation is in effect or unresolved, and known. Most come from lov/2015-06-19-65, which amends utlendingsloven, tvisteloven and merverdiavgiftsloven in its § 6, so under D7 its 219 unattributed operations keep every state of those acts built from it unproven.
- **target unknown with unresolved timing (#858): 140.**
- **other: 256**, down from 343. These are the handler failures the old act-level set already held; 87 fewer because straffeloven's §§ 4–5 failures are no longer straffeloven's.

### 2.3 Phase-1 pilots and barnevernsloven

**Pilots** (arveloven, personopplysningsloven, sivilombudsloven, språklova): no behaviour change. With `replay_version` and the new `unattributed_events: []` removed, they are content-identical to `51c5d702` (0 residual differences). Under the #856 fix alone they were byte-identical. Terminal states keep 183/35/35/22 sections and 464/76/94/46 ledd proven; act-level stays 0.

**Barnevernsloven:** events, timings and applied are unchanged. Act-level is 0 → 3 at 2022-12-31; 1 → 4 from 2023-01-01 to 2023-07-01; 3 → 6 at 2024-01-01; 10 → 17 at 2026-11-06. The added entries come from two sources:
- **three #858 entries, in every state.** They are `lov/2024-06-25-53#11:XI:12.0` and `…:XI:14.0` (named in #858) and `lov/2026-06-19-35#1:I:30.0`. All three are `unsupported`, have timing `commencement sentence outside the grammar` and no `known_from`, so they blanket every state.
- **four unattributed operations of lov/2025-04-25-12 (#859), at 2026-11-06 only.** Their timing is `partial decision outside the grammar`, refused by FOR-2025-06-10-967, with `known_from` 2025-04-25.

Verification is unchanged: 188/188 `unresolved` at every replayed date, 123/188 agree behind the blanket.

Committed evidence digests (sha256):

| file | digest |
|---|---|
| `legal-time-replay-2026-11-06-arveloven.json` | `91266bb137a9cfeb623a7b5976613e84f420fd78fa760ee578b8c3226e91edb8` |
| `legal-time-replay-2026-11-06-personopplysningsloven.json` | `facec56ce6899c4bc8e6833313ca0f8766f807b1ef6488ffcd1f3b3fc7e39bca` |
| `legal-time-replay-2026-11-06-sivilombudsloven.json` | `decb09e06d3733338a6fc6956ea73526b155196fe9ab38514ee510aac8c370c2` |
| `legal-time-replay-2026-11-06-spraklova.json` | `d8caf6beffcbf924f366bc9e7bff7f2c2e49db04dae608c89874c049f3e61f68` |
| `legal-time-replay-2026-11-08-barnevernsloven.json` | `55ea120e045f4cc31b212e0de8de9f608b9f4f098cd88bc53b517e096e848296` |
| `legal-time-coverage-2026-11-10.json` | `35b56473af42788d7a7c63f048c2895921c54cce50c859cfcfe2e07eae3542e2` |

## 3. Audit of every operation whose attribution changed

Across all rebuilt reports, 451 operations are unattributed that were not unattributed before. Each one was classified against its attribution at `51c5d702`:

1. **227 were straffeloven's.** 216 are the lov/2015-06-19-65 § 4/§ 5 blocks (militær straffelov, straffeprosessloven). 6 come from lov/2008-03-07-4 (§ 2.1). 5 come from lov/2007-04-13-14 part IV, which opens `I § 102 nr. 4 (endringer i lov 22. mai 1981 nr. 25 om rettegangsmåten i straffesaker) tilføyes:`: they are straffeprosessloven provisions (§§ 68, 76, 332, 333, 455) embedded in straffeloven § 102 nr. 4.
2. **126 were already unattributed.** They are only newly *listed* as `unattributed_events`.
3. **56 were attributed to another act that their own block does not name.** Each sat after an intro the grammar cannot bind:
   - an act cited by short title: `84. I plan- og bygningslov 14. juni 1985 nr. 77 …`, `I vegtrafikklov …`, `I sjømannslov …`, `I straffegjennomføringsloven …`, `I vegloven …`, `2. I Almindelig borgerlig Straffelov …`;
   - a capitalised `I Lov 29. juni 2007 nr. 75 …`;
   - a reordered or nynorsk intro naming the act: `Frå det tidspunktet lova tek til å gjelde, blir desse endringane i lov 8. november 1991 nr. 76 om kommunale og fylkeskommunale eldreråd gjort gjeldande:` (lov/2007-06-15-21, credited to lov/2005-06-17-58), `§ 13-3 nr. 5 om endringer i skattebetalingsloven:` (lov/2022-12-20-110: an amendment to an amendment list, every operation `unsupported`);
   - an intro fused into the end of the previous payload ledd: `… 17. I lov 13. juni 1997 nr. 45 om allmennaksjeselskaper gjøres følgende endringer:`, `196. I lov 27. juni 2003 nr. 58 …`, `1 a. I lov 6. juni 1891 nr. 2 …`.

   At `51c5d702` these were attributed to the preceding block's act. For example, allmennaksjeloven's and straffegjennomføringsloven's operations in lov/2014-05-09-16 went to sjøloven (lov/1994-06-24-39).
4. **42 were silently dropped** as "prose outside any act's amendment block". They are `unsupported` sentences inside a block whose intro the grammar cannot bind, for example lov/2003-07-04-85's whole friskolelova block, 18 of lov/2015-06-19-65 §§ 4–5 and lov/2012-06-22-49's `I almindelig borgerlig straffelov 22. mai 1902 nr. 10 skal desse endringane gjerast:`. They are now counted.

No operation lost an attribution that a recognised intro of its own block proves. Three unsupported sentences about straffeloven itself (lov/2008-03-07-4 I:111/112/114, after `I straffeloven 2005 tredje del gjøres følgende endringer:`, a short-title intro) moved from act-level `unsupported` to unattributed. Both block every state, so nothing became provable. A first version of the fix also closed blocks at unit-level sub-intros: it lost 19 friskolelova, 6 arbeidsmiljøloven and 4 straffeloven operations. The audit caught this, the unit-led exemption fixed it, and the regression tests pin it. A second over-reach came from the round-3 word-order rule: payload instructions citing an earlier amending act as the source of their wording (`I § 52 a, slik ny § 52 a lyder etter endringer ved lov 20. januar 2012 nr. 6 del X, skal … lyde:`, lov/2014-06-20-49) closed straffeloven's block and lost a valid § 83 insertion. Sentences that open a payload are now excluded from that rule, and a test pins the case.

## 4. Tests and gates

- **Red-first.** The 51 new unit tests (`tests/unit/test_legaltime_phase4_{attribution,commencement,blanket}.py`) were run against `51c5d702`: **30 fail, 21 pass**. Every defect has failing tests on the old code (#856 10, #857 11, #858 3, #859 6). Later additions (review rounds 1–3: unit-conditional and reference-clause cases, short-title and reordered intros, payload instructions citing an earlier amendment, substitution and payload negatives, the `_named` branches, `state_at`) bring the phase-4 unit set to 73. The 21 that pass are preservation and negative guards: valid multi-paragraph blocks, unit-level sub-intros, truly informational prose, named targets, pending and future operations.
- `tests/integration/test_legaltime_phase4.py` (pinned archives) checks four things; the naturmangfoldloven check also asserts that the act's default stays `unresolved` with FOR-2009-06-19-704 among its proofs. On the real member: 216 §§ 4–5 operations, none straffeloven's. On the straffeloven ladder: nothing from them applied, and the blanket names the member. The real FOR-2009-06-19-704 block keeps sentence 2. The barnevernsloven XI:12.0/14.0 operations blanket every state.
- `tests/integration/test_legaltime_phase3.py`: the straffeloven pins move from (105, 614, 376) to (99, 392, 335), with the reason in a comment. The barnevernsloven and pilot evidence regenerate byte-for-byte from the committed files.
- **Determinism:** two independent rebuilds of all 14 reports and the coverage file (15 files) produced identical sha256 digests.

## 4a. Independent review (Codex, read-only, `codex exec -s read-only`)

| round | head reviewed | verdict | major finding | resolution |
|---|---|---|---|---|
| 1 | `fbe8db77` | NOT READY | `Dersom § 2-6 ikke er satt i kraft, trer den i kraft.` still dropped | fixed in `1fd85c26` (unit named + commencement in the main clause) |
| 2 | `1fd85c26` | NOT READY | short-title unit intro `I § 3 i straffeloven gjøres følgende endringer:` kept the block | fixed in `42ddf1c7` |
| 3 | `42ddf1c7` | NOT READY | intros worded outside the fixed shapes kept the block | fixed in `0b1111ef`. The round's own example (`I lov … skal følgende endringer gjøres:`) is read by `_INTRO` and binds its act; round 4 confirmed this |
| 4 | `0b1111ef` | NOT READY | `Kongen kan sette endringene i kraft.` dropped as informational | **declined**, see below; minor test-strength finding fixed |

Round 4's major is declined. `Kongen kan sette … i kraft` grants the power to commence. It states no commencement, date, exception or unit, which is the class of sentence the task says to keep as informational. The unit's timing still comes from the default clause, the `dateInForce` header and the decisions: with `Kongen bestemmer` it is `pending`, and every decision citing the law is folded in. Refusing every such delegation would refuse the standard delegation clause most acts carry, without evidence that any of them dates a unit. A delegation that names a unit, a date or an exception (`Kongen kan likevel sette § 2-6 i kraft 1. juli 2023.`) does reach the grammar, and a test pins that. Owner may overrule.

The sandbox could not run pytest or `gh` in any round; each review was static.

## 5. Limitations

- Operations after an unbindable intro are unattributed even where a human can read the act from the sentence (militær straffelov, plan- og bygningsloven by short title). That is the fail-closed choice: binding them is grammar work under #861 item 3 and needs its own tests.
- The #858 lower bound is `known_from`, as the task specifies. An unresolved-timing operation whose effective date could be retroactive (earlier than `known_from`) is not blanketed in states before `known_from`. In practice `_unresolved` timings carry no `known_from`, so these operations blanket every state. Only timings first dated and later unsettled keep one.
- An `unresolved`-timing operation whose named target is absent from the state is not act-level. It names its target, and D7 places unknown *targets* in the blanket.
- Act-level counts are now dominated by unattributed operations. This is Decision 7's leg as written, and it makes any D7 reach-set work (#173) depend even more on attribution grammar.
