# #861 Phase 4B: evidence-backed attribution of amendment intros, research, 2026-11-10

This note covers attribution only: which act an amendment block's operations belong to. No ADR changed. No owner ruling was assumed: kap. 16, renumbering, reach sets and D7 narrowing stay out (#169–#173). Nothing is served, published or written into `lovverk`; the MCP/API/public JSON surfaces are untouched; no corpus-wide replay was run; ADR-0019 R2 is unchanged. ADR-0020 Decision 7 is applied as written: an unattributed operation in effect at D still blankets the state. Phase 4B only removes operations from the unattributed pool when their act is proven.

## 0. Baseline and inputs

| item | value |
|---|---|
| baseline | origin/main `73f7c805` (merge of #865, Phase 4A) |
| `lovtidend-avd1-2001-2025.tar.bz2` | sha256 `d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0` |
| `lovtidend-avd1-2026.tar.bz2` | sha256 `600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2` (ADR-0019 R2 `research_only`, unchanged) |
| oracle `gjeldende-lover.tar.bz2` | sha256 `380632d48c82e48bb6fa37c6c6e6f13d55c77defa5f83400c5120ca6989593e9` |
| `REPLAY_VERSION` | `legaltime-replay-4` → **`legaltime-replay-5`** |

Only the permitted publicData archives were read: no lovdata.no HTML, no Lovdata Pro. The baseline was rebuilt first, on a detached checkout of `73f7c805`. The eight-act coverage came out byte-identical to the committed `legal-time-coverage-2026-11-10.json` (sha256 `1c0b0b67…c153c7`), and the straffeloven ladder (`--at 2008-03-06 2008-03-07 2015-09-30 2015-10-01 2026-11-06`) to the Phase 4A digest `a0a9cfa5…6f9ea8`.

Note on the Phase 4A record: its §2.3 lists the coverage digest as `35b56473…`. That is the file as of `fd53095c`; review round 3 (`0b1111ef`) regenerated it, and the committed Phase 4A coverage file is `1c0b0b67…`. The counts in that note are the counts of `1c0b0b67…`.

The commands are those of the Phase 3 note §1 (eight acts at `--at 2008-01-01 … 2026-11-06`, reduced by `scripts/legal_time_coverage.py`), with the per-act files named `legal-time-replay-2026-11-10-<short>.json`.

## 1. Audit of the 1,392 unattributed operations at `73f7c805`

1,392 is a sum over the eight acts: one amending member that amends several of them is counted once per act. Read once per member, the pool is **451 distinct operations in 31 members**. Each was classified by the sentence that closed its block (the last intro at or before it in its article), and the classes were ranked by measured frequency, then by risk:

| intro shape at `73f7c805` (measured) | distinct ops | risk | example (archive sentence) |
|---|---|---|---|
| citation after the verb, behind a commencement clause or reordered | 224 | medium: the clause can name other acts by short title, so only a unique dated, numbered citation counts | `Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i lov 22. mai 1981 nr. 25 om rettergangsmåten i straffesaker:` (lov/2015-06-19-65 § 5, 145 ops; § 4 the same for `lov 22. mai 1902 nr. 13 militær straffelov`, 71); `Frå det tidspunktet lova tek til å gjelde, blir desse endringane i lov 8. november 1991 nr. 76 … gjort gjeldande:` |
| `I` + short or full title + citation + `gjøres følgende endringer:` | 98 | low | `I Almindelig borgerlig Straffelov 22. mai 1902 nr. 10 gjøres følgende endringer:` (lov/2013-06-21-85), `I straffegjennomføringsloven 18. mai 2001 nr. 21 …`, `33. I Lov 29. juni 2007 nr. 75 …`, `3) I lov 4. juli 2003 nr. 84 …` |
| title + citation + `endres slik:` / `vert endra slik:` | 20 | low | `Straffelova 22. mai 1902 nr. 10 vert endra slik:`, `Lov om rettergangsmåten i straffesaker 22. mai 1981 nr. 25 (straffeprosessloven) endres slik:` |
| intro behind another sentence in the same node (fused into the previous payload ledd, or after a list intro) | 17 | medium: only the node's last sentence binds | `… etter § 16-4. 17. I lov 13. juni 1997 nr. 45 om allmennaksjeselskaper gjøres følgende endringer:` (lov/2014-05-09-16); `Fra den tid loven trer i kraft, gjøres følgende endringer i andre lover: Lov 10. juni 1966 nr. 5 om toll (tolloven) oppheves. I lov 19. juni 1969 nr. 66 om merverdiavgift (merverdiavgiftsloven) gjøres følgende endringer:` (lov/2007-12-21-119) |
| numbered act heading, or heading fused with its instruction, under a list intro naming no act | 12 | highest: needs the list context, the item number and a closing rule for the next item | `1. Lov 25. september 1992 nr. 107 om kommuner og fylkeskommuner:`, `7. Lov 17. juni 2005 nr. 62 om arbeidsmiljø-, arbeidstid og stillingsvern`, `2. Lov 23. mai 1997 nr. 31 om eierseksjoner § 3 a skal lyde:` (lov/2017-06-16-51 § 44) |
| the intro sentence itself, counted as an `unsupported` operation | 7 | — | `1. Lov 25. september 1992 nr. 107 om kommuner og fylkeskommuner:` |
| no provable identity, or no intro at all | 73 | — | §4 |

The first six rows (378 operations) are the ones Phase 4B binds or removes; the counts are measured on the branch, not targeted.

The audit also measured the opposite failure: operations that Phase 4A still credits to the **wrong** act. A numbered list item naming another act (`2. Lov 13. desember 1946 nr. 14 om straff for utlendske krigsbrotsmenn oppheves.`) neither matched an intro nor closed the block, so it inherited the act of the block before it. At `73f7c805` **32 operations** were misattributed this way, one of them to straffeloven (lov/2009-06-19-74 I nr. 2, a whole-act repeal of lov/1946-12-13-14, counted as a dated straffeloven event). They are in §3.

## 2. The evidence chain and the grammar

`src/lovspor/legaltime/attribution.py` holds all act-binding intro grammar: the two Phase 4A canonical shapes (moved from `instructions.py` unchanged, still bound without further proof) and the Phase 4B shapes. A Phase 4B intro binds its act only when every link holds:

1. **Shape.** The node's last sentence is one of three closed shapes, ending in a colon: `I <title> <citation> … gjøres|skal|blir|vert følgende|følgjande|desse endring(er|ar…) [gjerast]:`; `<title> <citation> … endres|endrast|vert endra|blir endra slik:`; `… gjøres|gjerast|blir|vert følgende … endringer i [lov] <citation> … [gjort gjeldande]:`. An optional item mark (`17.`, `1 a.`, `3)`) may precede it. Two more shapes are numbered items: an act heading (`N. Lov <citation> [om …][:]`, no verb, only inside a list intro that names no act) and an act heading fused with its instruction (`N. Lov <citation> <title> [skal] § … lyde:`).
2. **Uniqueness.** The sentence (for a fused item, its part before the `§`) cites exactly one act by day, month, year **and number**. Two citations, a date without a number, or a day without its full stop cite nothing.
3. **Directness.** It amends that act, not an amendment of it: `om endring(er) i`, `(endringer i`, `romertall` and `endringslov` refuse. And the citation sits where the amended act is **named**: either right after the main verb's object `gjøres|gjerast|blir|vert følgende|følgjande|desse endring… i [lov]`, or right after a title that ends in an act noun (`… lov`, `… loven`, `… lova`) or reads `Lov om <title>`. A title holds no other act noun (`I straffeloven med hjemmel i lov …` names two) and no comma, bracket, `endring…`, `av`, `følger`, `etter`, `ved`, `jf.`, `lyder`, `lydde`, `endret` or `endra`. A citation in a clause about another text (`I straffeloven, slik den lyder etter lov …`, `… med de endringer som følger av lov …`) is the source of a wording, not the act amended, and proves nothing. This is a positive slot, not a list of forbidden words: review round 4 showed that a word list always misses a phrasing.
4. **Header proof.** The member's own `changesToDocuments` header lists the cited act. This is Lovdata's metadata about which documents the announcement changes; it is independent of the sentence.

A unit-led sentence (`I kapittel 3 i lov … gjøres følgende endringer:`) is not a Phase 4B shape: Phase 4A closes the block and that stays. A short title alone is never an identity. Nothing is matched by similarity.

**Block rules.** The Phase 4A reset rules stand: a new `legalArticle` closes a block; an intro the grammar cannot bind closes it; unknown, ambiguous or unproven intros leave what follows unattributed; nothing is inherited. Phase 4B adds one closing rule: a **numbered** list item led by an act citation (`2. Lov …`, `12. I lov … oppheves …`, `93 a. I lov …`) closes the open block, proven or not. An unnumbered `Lov 4. juli 2003 nr. 84 om private skolar … (privatskolelova)` closes nothing: in lov/2007-06-29-92 and lov/2015-06-19-75 it is the payload of `Ny tittel på lova skal lyde:`. A first version closed on every act-led node and lost 60 friskolelova operations to exactly that; the full rebuild audit caught it before commit, and `test_a_new_title_payload_naming_the_act_keeps_the_block` pins it.

**Payload references.** A payload ledd is consumed by its instruction and never reaches the block reader, so an act it cites cannot rebind anything. Transitional and cross-reference sentences that cite an act but do not end in an intro shape bind nothing. Both are pinned by tests.

**Phase 4A behaviour kept exactly.** A proven intro in a ledd is still read as loose text (in documents without provisions this is the clause path Phase 4A used); in a paragraph, the intro sentence is no longer an operation, a commencement-shaped intro sentence still goes to the clauses, and the sentences before it are read unattributed as before. The full op dump shows **0 clause differences** in the 403 members.

## 3. Every changed attribution

`scripts/legal_time_attribution_audit.py` dumps every operation of every amender of the eight acts at each commit, then classifies every operation whose act, kind, instruction or presence differs, with its governing sentence, the acts that sentence cites, and whether it cites exactly one act that the header lists (`identity_proven`). `tests/unit/test_legal_time_attribution_audit.py` pins that two citations, an unlisted act or an act the sentence does not cite are never classed as proven. Output: `docs/research/data/legal-time-attribution-audit-2026-11-10.json` (sha256 `d1c4e0d3…1a98ca`; inputs: the `73f7c805` dump `0f53261c…`, the branch dump `543268b8…`).

| class | ops | meaning |
|---|---|---|
| `new_proven` | 371 | unattributed before; now bound to the one act its governing sentence cites, which the header lists |
| `rebound_proven` | 9 | bound before to an act the sentence does not cite; now to the act it cites, header-listed |
| `unbound_was_false` | 23 | bound before to an act the sentence does not cite; now unattributed (proof missing or no unique citation) |
| `intro_not_an_operation` | 7 | a proven intro sentence that Phase 4A counted as an `unsupported` operation |
| `check` (anything else) | **0** | |
| **total** | **410** | in 28 members; no operation changed kind or instruction while keeping its act |

**Every one of the 410 was classified; none is incorrect.** The 380 bindings are source-proven. The 32 rows that had an act before and lost or changed it (9 + 23) were all incorrect attributions at `73f7c805`: in each, the sentence cites a different act than the one credited (e.g. `19. Lov 17. juni 2005 nr. 90 om mekling og rettergang i sivile tvister § 34-4 fjerde ledd annet punktum skal lyde:`, a tvisteloven amendment credited to lov/2004-03-26-17). No operation lost an attribution that its own block proves.

Newly attributed operations by act (source members in the audit file):

| act bound | ops | source members |
|---|---|---|
| lov/1981-05-22-25 straffeprosessloven | 168 | lov/2015-06-19-65 (145), lov/2008-06-20-47 (12), lov/2005-12-21-131 (5), lov/2007-04-13-14 (5), lov/2019-03-15-6 (1, rebound) |
| lov/1902-05-22-13 militær straffelov | 71 | lov/2015-06-19-65 § 4 |
| lov/1902-05-22-10 straffeloven 1902 | 62 | eleven members |
| lov/2001-05-18-21 straffegjennomføringsloven | 28 | lov/2007-06-29-83, -84, lov/2014-05-09-16 |
| lov/2005-05-20-28 **straffeloven** | 3 | lov/2007-06-29-83 III, lov/2007-06-29-84 IV |
| lov/2003-07-04-84 **friskolelova** | 2 | lov/2009-06-19-95 kap. 5 |
| lov/2005-06-17-62 **arbeidsmiljøloven** | 2 | lov/2017-06-16-51 § 44 nr. 7 |
| lov/2005-06-17-90 **tvisteloven** | 1 | lov/2019-03-15-6 kap. 12 nr. 19 (rebound) |
| 24 other acts | 43 | see the audit file |

Only the eight operations bound to the four bold acts enter any coverage act's events. All others leave the eight acts' unattributed pools, which is where the blanket changes come from.

## 4. What stays unattributed

After Phase 4B the pool is **96 distinct operations in 21 members** (175 summed over the acts). Each fails a link for a reason the sources state:

| reason | distinct ops | members | example |
|---|---|---|---|
| cited act not in the member's `changesToDocuments` | 36 | 4 | `45. I vegtrafikklov 18. juni 1965 nr. 4 …`, `10. I straffegjennomføringsloven 18. mai 2001 nr. 21 …` (lov/2007-01-26-3, lov/2008-03-07-4, lov/2009-06-19-74: their headers omit these acts); `I straffeloven 22. mai 2005 nr. 28 …` (lov/2008-06-20-47: the source misprints straffeloven's date, so `lov/2005-05-22-28` is cited and not listed) |
| short or full title only, no citation | 18 | 3 | `Lov om frittståande skolar (friskolelova) endres slik:`, `I straffeloven 2005 tredje del …`, `§ 13-3 nr. 6 om endringer i merverdiavgiftsloven:` |
| numbered whole-act repeal of another act | 17 | 9 | `1. Lov 4. februar 1977 nr. 4 om arbeidervern og arbeidsmiljø m.v. oppheves.` (the operation is a repeal with no unit; it is `unsupported` and binds no block) |
| amendment of an amending act | 17 | 5 | `I § 102 nr. 4 (endringer i lov 22. mai 1981 nr. 25 …) tilføyes:`, `I romertall II om endringer i lov 13. august 1915 nr. 5 …` |
| malformed citation | 4 | 1 | `I lov 15 mai 2008 nr. 35 …` (lov/2020-03-27-16: no full stop after the day) |
| article boundary, no intro | 3 | 3 | `§ 253 første ledd skal lyde:` |
| numbered item with a verb before its `§` | 1 | 1 | `28. I lov 28. mai 2010 nr. 16 … erstattes i § 40 …` |

The friskolelova block of lov/2003-07-04-85 (13 operations) is the largest remaining one aimed at a coverage act. Binding it needs the target's official title as evidence; the parser does not receive titles, and similarity is not evidence. That is left as is.

## 5. Before / after (`73f7c805` → this branch)

### 5.1 Eight-act coverage (`legal-time-coverage-2026-11-10.json` → `legal-time-coverage-2026-11-10-phase4b.json`, end state 2026-11-06)

| act | events | dated | pending | unres. timing | applied | unres. ops | act-level | unattributed | proven | agree behind blanket |
|---|---|---|---|---|---|---|---|---|---|---|
| arbeidsmiljøloven | 356 → 358 | 272 → 274 | 2 | 82 | 0 | 82 | 268 → 41 | 257 → 30 | no text | — |
| barnevernsloven | 185 | 134 | 3 | 48 | 101 | 66 | 17 → 15 | 4 → 2 | 0/188 | 123/188 |
| friskolelova | 300 → 302 | 232 | 0 | 68 → 70 | 168 | 132 → 134 | 78 → 75 | 21 → 18 | 0/92 | 34/92 |
| merverdiavgiftsloven | 324 | 162 | 0 | 162 | 105 | 218 | 293 → 72 | 228 → 7 | 0/206 | 89/206 |
| naturmangfoldloven | 49 | 44 | 0 | 5 | 0 | 5 | 4 → 2 | 4 → 2 | no text | — |
| straffeloven | 623 → 625 | 507 → 506 | 16 | 100 → 103 | 424 | 183 → 185 | 461 → 137 | 388 → 65 | 0/400 | 294 → **293**/400 |
| tvisteloven | 163 → 164 | 111 → 112 | 8 | 44 | 94 → 95 | 61 | 262 → 43 | 245 → 26 | 0/442 | 296/442 |
| utlendingsloven | 556 | 306 | 9 | 241 | 176 | 371 | 405 → 185 | 245 → 25 | 0/212 | 50/212 |
| **total** | 2,556 → 2,563 | 1,768 → 1,770 | 38 | 750 → 755 | 1,068 → 1,069 | 1,118 → 1,122 | **1,788 → 570** | **1,392 → 175** | **0 → 0** | 886 → 885 of 1,540 |

Every changed number, explained:

- **Unattributed 1,392 → 175** and **act-level 1,788 → 570**: −1,217 unattributed entries and −1 other entry (straffeloven's `unsupported instruction` for lov/2009-06-19-74 I nr. 2, below); `target unknown` entries are unchanged in every act. The 371 proven bindings and the 7 intro sentences leave the pools, the 23 operations whose false attribution was removed join them (distinct: 451 → 96). lov/2015-06-19-65 alone sits in five acts' pools (utlendingsloven, straffeloven, arbeidsmiljøloven, tvisteloven, merverdiavgiftsloven), so its 216 §§ 4–5 operations account for 1,080 of the removed entries.
- **straffeloven, events +2:** +3 proven (lov/2007-06-29-83 III `§ 45 nytt sjette ledd skal lyde:`, intro `I lov om straff 20. mai 2005 nr. 28 (straffeloven) gjøres følgende endring:`; lov/2007-06-29-84 IV `§ 37 bokstav f skal lyde:` and `§ 51 bokstav a skal lyde:`, intro `I straffeloven 20. mai 2005 nr. 28 gjøres følgende endringer:`), −1 false (lov/2009-06-19-74 I nr. 2, the 1946 act's repeal, a dated `unsupported` operation). All three new operations have unresolved timing (`commencement sentence outside the grammar` / `partial decision outside the grammar`): dated −1, unresolved timing +3, unresolved operations +3 −1 = +2. Applied unchanged, 424.
- **straffeloven, agree behind the blanket 294 → 293:** before the blanket `exact_normalized` 166 → 165 and `unresolved` 66 → 67. The only straffeloven section whose own problem list went from empty to non-empty is § 51, which now carries `lov/2007-06-29-84#4:IV:3.0` (`§ 51 bokstav a`, timing unresolved). Its earlier agreement rested on a missing operation. §§ 37 and 45 gain the same kind of problem but already had others.
- **tvisteloven, applied 94 → 95:** `lov/2019-03-15-6` kap. 12 nr. 19, `§ 34-4 fjerde ledd annet punktum skal lyde:`, dated, now applied. tvisteloven's counts behind the blanket are unchanged (296 of 442).
- **friskolelova, +2 events:** lov/2009-06-19-95 `3) I lov 4. juli 2003 nr. 84 om private skolar med rett til statstilskot gjøres følgende endringer:`: `§ 1-2a annet ledd` and `§ 6-1 femte ledd siste punktum`, both unresolved timing; § 6-1 gains a problem (already unresolved).
- **arbeidsmiljøloven, +2 events:** lov/2017-06-16-51 § 44 nr. 7 (`§ 13-1 fjerde ledd`, `§ 13-4 tredje ledd`), dated. The act has no baseline text, so nothing applies.
- **barnevernsloven, act-level 17 → 15 at 2026-11-06:** lov/2025-04-25-12's four tvangsfullbyrdelse operations (`Fra den tid Kongen fastsetter, gjøres følgende endringer i lov 26. juni 1992 nr. 86 om tvangsfullbyrdelse:`) are now bound to lov/1992-06-26-86; two of its numbered repeal items (`45. Lov 17. juni 2005 nr. 89 …`, `57. Lov 11. januar 2013 nr. 3 …`) lose their false attribution to other acts and join the pool. Earlier states and verification are unchanged.

### 5.2 Straffeloven ladder

| state | withheld | applied | unresolved ops | act-level |
|---|---|---|---|---|
| 2008-03-06 | 4 | 0 | 100 → 103 | 78 → 26 |
| 2008-03-07 | 99 | 0 | 100 → 103 | 94 → 43 |
| 2015-09-30 | 392 → **391** | 0 | 100 → 103 | 393 → 69 |
| 2015-10-01 | 0 | 335 | 157 → 159 | 434 → 109 |
| 2026-11-06 | 0 | 424 | 183 → 185 | 461 → 137 |

Withheld −1 at 2015-09-30: the false lov/2009-06-19-74 I nr. 2 operation. `test_legaltime_phase3.py` pins (99, 391, 335) with that reason. Behind the blanket at 2015-10-01, `exact_normalized` 129 → 128 and `unresolved` 60 → 61. Nothing from lov/2015-06-19-65 §§ 4–5 is a straffeloven event, an unattributed event or a straffeloven act-level entry. Ladder digest `a0a9cfa5…` → `71fb2fb5732079688e898ac3d40808a1a330c9660e48376d0b07fba2a254e7b3` (regenerated, not committed).

### 5.3 Phase-1 pilots

Arveloven, personopplysningsloven, sivilombudsloven and språklova: **no content change**. Apart from the `replay_version` line, each regenerated file is byte-identical to `73f7c805`; the terminal states keep 183/35/35/22 sections and 464/76/94/46 ledd proven, and act-level stays 0.

### 5.4 Verdicts

**Proven: 0 of 1,540 coverage sections before and after.** Every coverage act still has act-level problems at every replayed date (Decision 7 unchanged), so no verdict changed. The pilots' proven verdicts are unchanged.

### 5.5 Committed evidence (sha256)

| file | digest |
|---|---|
| `legal-time-replay-2026-11-06-arveloven.json` | `2232fb565f120caeb7bae17ade8d618670bc520ce1ce2d1c2e47669453c8a028` |
| `legal-time-replay-2026-11-06-personopplysningsloven.json` | `55b9b349205960bd200661eed9b9775c2fc599898608cd050bec5c9d7811e18a` |
| `legal-time-replay-2026-11-06-sivilombudsloven.json` | `8803d23b8b835510de6da60511fb30a6d919496d7471e1f740f8e3758d1195b7` |
| `legal-time-replay-2026-11-06-spraklova.json` | `1d6604ae012bbad798e3036bfc2a46fc2f8d1b2accd7307d14af35180d243374` |
| `legal-time-replay-2026-11-08-barnevernsloven.json` | `f0a83234e4deef9342efc8f70f705162f4e1292f3202eca3715b8153934ea89f` |
| `legal-time-coverage-2026-11-10-phase4b.json` | `fe51cae5266f963e9d3bf2db80e55b4f6e61ca68dbf178512553b718508b7737` |
| `legal-time-attribution-audit-2026-11-10.json` | `d1c4e0d3ef991a4e0e3ff59093ecb292de3689ff2db30c3e9f12832c5c1a98ca` |

The Phase 4A coverage file `legal-time-coverage-2026-11-10.json` is left as it was, so the Phase 4A note still points at its own evidence. **Determinism:** two independent rebuilds of the eight per-act reports, both ladders and the five evidence files (15 files) produced identical digests.

## 6. Tests

`tests/unit/test_legaltime_phase4b_attribution.py`, 46 cases, each with a real archived sentence or a negative near-match of one. Against `73f7c805` **28 fail and 18 pass**. The 18 that pass are guards that must stay true on both sides: without the header proof § 4 stays unattributed; misprinted, short-title, two-citation, unit-led and amendment-of-amendment intros bind nothing; a new-title payload keeps its block; a numbered repeal is not a heading. Required cases:

- lov/2015-06-19-65 §§ 4–5 bind militær straffelov and straffeprosessloven, never straffeloven; without header proof both stay unattributed.
- Several acts amended in one member: §§ 3/4/5 and the list of lov/2017-06-16-51 § 44; several acts in one ledd (lov/2007-12-21-119).
- Short titles with ambiguous identity: `I straffeloven 2005 tredje del …`, `Lov om frittståande skolar (friskolelova) …`.
- Intro fused with an instruction: `2. Lov 23. mai 1997 nr. 31 om eierseksjoner § 3 a skal lyde:`, `19. Lov 17. juni 2005 nr. 90 … § 34-4 …`, and the fused-into-payload intro of lov/2014-05-09-16.
- Article boundaries followed by unknown intros.
- Payload references that are not act bindings, and transitional sentences.

`tests/integration/test_legaltime_phase4b.py` (pinned archives): the ikraftsettingslov's §§ 4/5 bind 71/145 operations to the two acts its header lists; none is a straffeloven event, unattributed event, applied operation or act-level entry on the straffeloven ladder; two real members bind every operation of their block, with no operation of another act or of none inside that run; lov/2007-06-15-21 part IV reads lov/2005-06-17-58 § 10 and then four operations of the eldreråd act under the reordered intro; the misprinted straffeloven date stays unattributed. `test_legaltime_phase4.py` keeps its 71/216 counts and now asserts that none is straffeloven's (Phase 4A asserted "unattributed"); `test_legaltime_phase3.py` pins the straffeloven ladder at (4, 99, 391, 335).

## 6a. Independent review (Codex, read-only, `codex exec -s read-only`)

| round | head reviewed | verdict | findings | resolution |
|---|---|---|---|---|
| 1 | `2ae6f009` | NOT READY | major: a canonical intro binds a date-only citation (`I lov 20. mai 2005 gjøres …` → `lov/2005-05-20`); major: the audit tool classed a two-citation sentence as proven when the first citation matched; minor: the archive tests counted operations without pinning the block | the audit now requires exactly one citation that the header lists, with unit tests; the archive tests pin the run and the multi-act part IV. The date-only binding predates Phase 4A and is unchanged here: measured and filed as #872 (128 operations in the eight acts' amenders, 77 header-proven, none bound to a coverage act) |

| 2 | `b43b56c0` | READY | none | — |
| 3 | `74a7d6dd` | NOT READY | major: one citation inside a reference clause bound as the amended act (`I straffeloven, slik den lyder etter lov 20. desember 2023 nr. 110, gjøres følgende endringer:` → `lov/2023-12-20-110`); minor: the audit called an attribution false when the sentence cited the old act second. The new equivalents were confirmed provable | first a referring-word rule (superseded in round 4), three negative tests; the audit compares against the whole citation set, with a test. On the archives: 0 operations changed |
| 4 | `3ed531e7` | NOT READY | major: `… med de endringer som følger av lov 20. desember 2023 nr. 110 …` passed the word list; major: the audit's proof counted citations and header membership but not the engine's directness rule; minor: the audit's `_governing` forced `in_list` | the positive slot rule of §2 (link 3) replaces the word list, with two more negative tests; the audit's `identity_proven` now also requires `attribution.proven` (the engine's whole chain) and `_governing` no longer forces `in_list`. On the archives: 0 operations changed, evidence byte-identical; one audit row (`17. I lov 20. desember 2018 nr. 115 om endringer i sjøloven …`, already `unbound_was_false`) now reads `identity_proven: false` |
| 5 | `88dbcef1` | NOT READY | major: `I straffeloven med hjemmel i lov 20. desember 2023 nr. 110 gjøres følgende endringer:` passed the title slot (free words ending in `lov`); the same class reaches the verb slot through `… med endringer i lov …, gjøres …`; major: the audit trusts the engine's chain | the verb slot now requires the main verb's own object (`gjøres følgende endringer i [lov]`) and the title slot exactly one act noun, with three more negative tests; on the archives 0 operations changed. The audit finding is answered rather than changed: `identity_proven` restates the engine's chain on purpose, so that the audit flags any operation whose new act differs from what the chain proves for its governing sentence. It is not an independent oracle. Independence comes from the manual reading of every distinct governing sentence (§1, §3) and from this review |

## 6b. Mutation (CI, PR-scoped, mutmut 3.8.0)

The first run, at `6f422981`, killed 320 of 339 mutants. 19 survived. Two of those match the Phase 4A registered equivalents (`bound_host` `""`, `unbound` `None` in `_Reader.__init__`); 0 timed out, 0 suspicious, 0 without tests. The other 17 were handled as follows:

- **One survivor exposed a real defect.** `bind`'s `opens=False` mutant survived because no test had an unproven Phase 4B intro that `_unbound_intro` does not also catch. Writing that test (`I tolltariffen 1. januar 2000 nr. 1 skal desse endringane gjerast:` after a straffeloven block) showed the intro swallowed as the **payload** of the instruction before it: `_next_instruction` ended a payload only at Phase 4A's unbound shapes. The next instruction then inherited straffeloven. A payload now also ends at a Phase 4B block shape (`attribution.opens_block`), but never at a numbered heading or item, because a payload provision may list acts. On the archives this changes **0 operations, payload sizes or clauses** in the 403 members, and the evidence files regenerate byte-identically.
- **13 killed**, the two `opens` mutants above among them, by `tests/unit/test_legaltime_phase4b_mutants.py`, one test per mutated guard:
  - part start outside a list;
  - `seen_instruction` after a proven intro;
  - `unbound` after a proven or inline binding, which leaks into the next part;
  - a proven intro inside a substitution list;
  - a commencement-shaped intro kept as a clause;
  - every sentence before a fused intro;
  - an unproven inline item read as one unsupported operation.
- **2 removed as dead code:** `bind`'s `sentences[-1] if sentences else ""`. `split_sentences` never returns an empty list.
- **2 registered as provably equivalent:** `_Reader.__init__` `in_list` `True`/`None`. `_read_part` assigns `in_list = False` before `_boundary`, its only reader, can run. The Phase 4A `bound_host` entry was re-argued for the new `_boundary`.

## 7. Limitations

- **The header proof is incomplete in some members.** Lovdata's `changesToDocuments` omits acts a few members plainly amend (lov/2008-03-07-4 for straffegjennomføringsloven and militær straffelov; lov/2007-01-26-3 and lov/2009-06-19-74 for several). Those blocks stay unattributed. That is the fail-closed choice: the sentence alone is not enough proof.
- **A proven intro binds a block, not a sentence.** Within it, the Phase 4A rules decide where the block ends. A block whose end the grammar misses would misattribute its tail, as at Phase 4A for canonical intros. The audit found none in the eight acts' amenders.
- **Whole-act repeals** (`N. Lov … oppheves.`) close the block but are not bound to the repealed act: they have no unit to apply and stay unattributed operations.
- **Date-only canonical citations** bind without the header proof Phase 4B requires of its own shapes (#872).
- **Titles are not evidence yet.** Binding a short title (friskolelova, 13 operations) would need the act's official title as an input to the parser.
