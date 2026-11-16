# #935: an amending member's own chapter is never the affected act's chapter, research, 2026-11-16

This note covers one structure defect. `document._enclosing` stamped every `futureLegalArticle`, the text an amending member carries for another act, with the `kapN` / `delN` sections of the member that carries it. That is the amending member's own outline. Three engine paths read it as the affected act's chapter:

- `run.chapter_map`, which places a section key in a chapter;
- `units._chapter_relation`, which relates a chapter-named commencement unit to an operation;
- `baseline.Commencement.section_timing`, through `replay._Working.unit_state`, which gives an inserted section its chapter's commencement.

The fix places an inserted section in a chapter of the affected act **only by its own source**: the structure sentence, or a chapter title read before the text (`Operation.stated`, ADR-0023 Phase B4a's provenance boundary). Without one, the placement is `UNPLACED`. That is "unknown", never "no chapter":
- a chapter unit's relation to an unplaced insertion is `unknown`;
- a chapter dated apart leaves an unplaced section's commencement `unresolved`.

Nothing is served, published or written into `lovverk`. The MCP, API and public JSON surfaces are untouched. No ADR changed. The chapter grammar is not extended. No chapter identity, outline history or temporal outline rule was added. No corpus-wide replay was run.

The change **removes unproven structure and adds uncertainty**:
- In the twelve replayed acts, no operation's `status`, `effective`, `known_from`, `retroactive`, reason or proofs changed.
- 24 `chapter_map` entries are removed in four of those acts: 21 inherited from an amending member's chapter, 3 refused because their printed chapter title is unreadable (§5).
- In straffeloven, 29 inserted sections leave `applied` and become `unresolved` at the end state. They were applied on the act's default commencement while chapter 16 is dated apart.
- D7's act-level problems are identical in every state, and no section verdict changed.

## 0. Baseline and inputs

| item | value |
|---|---|
| baseline | origin/main `d16f9192b81d70d46b471c5778e7c8f64dcdb97c` (PR #942 merged) |
| fix | `8c4e522a` + review fix `770e17dc` |
| `lovtidend-avd1-2001-2025.tar.bz2` | sha256 `d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0` |
| `lovtidend-avd1-2026.tar.bz2` | sha256 `600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2` (ADR-0019 R2 `research_only`, unchanged) |
| oracle `gjeldende-lover.tar.bz2` | sha256 `380632d48c82e48bb6fa37c6c6e6f13d55c77defa5f83400c5120ca6989593e9` |
| member | `lti/2011/nl-20110624-030.xml`, `lov/2011-06-24-30`, sha256 `557b1e9a61ea879d57052af58da6306e22f0752b78048cc64f1f90dd34bbc42e` |
| `REPLAY_VERSION` | `legaltime-replay-9` → **`legaltime-replay-10`** |
| governing record | ADR-0020 D3/D4/D7/D9, ADR-0023 R3/R8 and its placement state ("an insertion sets its pair from the inserting structure"), all read, unchanged |

The baseline was rebuilt first, as B0, from a frozen copy of `src/` at `d16f9192`. All 15 reports came out byte-identical to the replay-9 evidence. The coverage equals the committed `legal-time-coverage-2026-11-14-918.json`, which is kept as the historical baseline.

## 1. Root cause, from the source

`lov/2011-06-24-30` amends other acts inside its own *Kapittel 13 Ikrafttredelse, overgangsbestemmelser og endringer i andre lover*. Its payloads sit at `article:§13-3 > section:kap13 > section:kapI`. Eleven are plain insertions (`Ny § X skal lyde:`) stating no chapter of the act they insert into: nine into `lov/1999-07-02-61` and two into `lov/1999-07-02-64`. Two examples:

| op | act | section | instruction sha256 | replay-9 stamp | fix |
|---|---|---|---|---|---|
| `lov/2011-06-24-30#1:I:335.0` | `lov/1999-07-02-61` | § 2-1 e | `45f1b1f9…` | `kap13` | `UNPLACED` |
| `lov/2011-06-24-30#1:I:513.0` | `lov/1999-07-02-64` | § 38 a | `26bdc777…` | `kap13` | `UNPLACED` |

**Red, on `d16f9192`** (`scratchpad` script over the pinned member; the affected act's own structure is empty so only the amender can place):

- `chapter_map` for `lov/1999-07-02-61` holds **18** keys, all `kap13`. That is wider than the issue said: nine `replace_section` payloads (§§ 2-1 a, 2-2, 3-12 to 3-15, 6-2 to 6-4) were stamped too, beside the nine insertions. `lov/1999-07-02-64` holds 9, all `kap13`.
- `relation(kapittel 13, #335.0) == "covers"` and `relation(kapittel 2, #335.0) == "disjoint"`. `relation(nytt kapittel 13, #335.0) == "covers"`.
- With a commencement whose default is 2012-01-01 and whose `kapittel 13` is dated 2013-01-01, `unit_state(§ 2-1 e)` is `in_force` from **2013-01-01**: the amending member's chapter dated the affected act's section.

The new pinned and synthetic tests import `UNPLACED` and `inserted_chapter`, which `d16f9192` does not have. On main they fail at collection. The behavioural red is the script output above.

## 2. The fix

- `document.py`: `_provision(article, own=False)` for a `futureLegalArticle`. Its `chapter` and `part` stay `None`. The raw sections stay in `Provision.enclosures`. A member's own `legalArticle` keeps `_enclosing`'s reading: that is its act's structure, and #922's host timing reads it.
- `units.py`: `inserted_chapter(op)` returns the chapter the inserting structure read, or `UNPLACED`. It refuses:
  - no `stated`, which covers every plain insertion;
  - a printed chapter title the grammar cannot read;
  - a sentence naming one chapter above a title naming another;
  - a sentence naming several chapters ("Nytt kapittel 21 til 22") with no title read before the text.

  `inserted_where(op)` adds the part labels.
- `units._chapter_relation`: an `UNPLACED` insertion is `unknown` against a chapter unit. One case is unchanged: a plain insertion against a `nytt kapittel X` unit stays `disjoint`. The new chapter is exactly the text its own chapter insertion carries, and a separate `Ny §` instruction is not that text. See §5 for the evidence.
- `run.chapter_map`: places an insertion only through `inserted_chapter`.
- `baseline.Commencement.section_timing`: for `UNPLACED`, the section's own unit still wins. Otherwise the default applies only when no non-heading chapter unit is dated differently from it. If one is, the result is `unresolved`, with the reason "inserted section placed by no source, and a chapter is dated apart" and every proof. Part qualifiers are ignored for this test, because the section's own part is as unknown as its chapter. `None` still means a section proven to stand in no chapter, as it does for the act's own enacted provisions.
- `replay.py`: `_Working.unit_state` takes a `Where`, and insertions pass `inserted_where(op)`. `replay.py` stays at 699 lines.

## 3. Audit

`docs/research/data/legal-time-chapter-ledger-2026-11-16-935.json` (sha256 `c7c967448ec2a982846d8839629257f2023c7a1bc9cc3ebed5f84445e61d2e43`) is the machine-readable before/after ledger. Each row carries its source (archive and member sha256, op id, instruction sha256, the raw enclosures), the replay-9 stamp, the placement after the fix and the reason.

**Archive scan (affected operations, not replay results).** Both pinned archives, every member, `interpret()` at `d16f9192`:
- 877 operations carry a payload stamped with a `kapN` of the carrying member, with no structure sentence or title behind it. Another 2 carry only a `delN`.
- By kind: 663 `replace_section`, 126 `insert_section`, 56 `unsupported`, 31 `replace_heading`, 1 `replace_ledd`.
- 602 are bound to an act, across 155 acts in 106 members.
- 7 members raise `IndexError` in `interpret()` (#937). They are listed in the ledger, never dropped.
- 18 structure insertions sit inside a member chapter. Their placement is read from their own sentence or title, and none was found contradicting its title.

**Twelve acts (8 coverage acts and 4 pilots), event by event**, frozen copies at `d16f9192` and `770e17dc`:

| act | `chapter_map` | removed entries (replay-9 stamp) | events | timing changes |
|---|---|---|---|---|
| friskolelova `lov/2003-07-04-84` | 74 → 57 | 17 (`kap30` of `lov/2023-06-09-30`'s *Åttande del*) | 302 | 0 |
| straffeloven `lov/2005-05-20-28` | 363 → 360 | §§ 52a–52c (`kap8a`, see §5) | 624 | 0 |
| arbeidsmiljøloven `lov/2005-06-17-62` | 169 → 167 | §§ 9-5, 9-6 (`kap9` of `lov/2018-06-15-38`) | 358 | 0 |
| merverdiavgiftsloven `lov/2009-06-19-58` | 180 → 178 | §§ 14-4, 14-5 (`kap16` of `lov/2016-05-27-14`) | 324 | 0 |
| the other eight | unchanged | — | — | 0 |

No `chapter_map` entry was added or changed. No event's `status`, `effective`, `known_from`, `retroactive`, reason or proofs moved in any of the twelve acts, so every commencement-unit relation that decided a date is unchanged. The false entries existed, but no proven date depended on them.

**Replay diffs** (rebuild B0 against rebuild C):
- **straffeloven, coverage and ladder.** Its commencement is a 2015-10-01 default plus `kapittel 16` (Lovvedtaket del I), which is `unresolved` ("conflicting commencement decisions", `forskrift/2008-03-07-225` against `lov/2015-06-19-65`). Thirty plain insertions name no chapter. replay-9 gave them no chapter stamp either, so `section_timing` read the absent chapter as "in no chapter" and applied the default.
  - At 2015-10-01 the six inserted before then become unresolved: lov/2013-06-21-85 §§ 136a, 191a, 191b and lov/2015-06-19-65 §§ 176, 189a, 370. The ladder pin moves from 334/358 to 328/352.
  - At 2026-11-06 applied goes 423 → 394, unresolved 185 → 214 and provisions 381 → 352: 29 from `applied`.
  - The thirtieth, `lov/2023-12-20-113#1:I:5.0` (§ 270), was already refused ("key already present"). It is now refused earlier by the commencement gate.
  - Nothing is newly applied.
  - `counts_before_blanket` goes from 293 to 264 proven of 400.
  - Under D7 all 400 sections were and stay `unresolved`, so **no section verdict changed**.
- **D7 blanket.** In every state of every report, `act_level_problems` is identical before and after.
- **Unchanged.** arbeidsmiljøloven, barnevernsloven (coverage and ladder), friskolelova, merverdiavgiftsloven, naturmangfoldloven, tvisteloven, utlendingsloven and the four pilots are content-identical. Only their version line changed.

**Was any earlier result falsely dated or applied?**
- No date in the twelve acts came from an inherited chapter. The inherited stamps reached `chapter_map` and relations, but no reported date.
- straffeloven's 29 applied insertions were **applied without proof**. Their default date assumed they stand outside chapter 16, and no source says where they stand. The dates may well be right, but they were not proven, and ADR-0020 D4 makes an unproven unit `unresolved`.
- All of straffeloven's sections were already `unresolved` under D7, so no reported section verdict was false.

**Digests** (replay-9 → replay-10; two independent rebuilds from `git archive 770e17dc`, C and D, byte-identical, and identical to rebuild A from `8c4e522a`):

| report | replay-9 sha256 | replay-10 sha256 | content |
|---|---|---|---|
| cov-arbeidsmiljoloven.json | `f8d76ee4fe78150e84afc8fae7e5bd46aaf1f0fd541031561bcc5e2d81747ec2` | `95ac3cefcabf54e888f0ae9c8fea7f97f599e01f2bc244aa85fd55231ca069c7` | version line only |
| cov-barnevernsloven.json | `0611bc136421cb0cacc7a6f92ba1a648a5add04ed3545401c5b6f167363a3591` | `13e06a5ccf550bf6e8bc47681f76720c3cddb7523b0e239ae8fd7e55dcc0e8e9` | version line only |
| cov-friskolelova.json | `a319eab4a1ce7da28aec22188cd4fa146d59787217b4f69f622ef49e2170b47a` | `01bdb3ebef3e21c6ee7a6fd6822f9c2f0d5982aeae9b0b7b71f8c4e82b415ef7` | version line only |
| cov-merverdiavgiftsloven.json | `0d9dc49159344d7b4e76ad7c09f443878f1adada8e1bf6b6b1c492e596a0bd50` | `3039b67c992981a56967c282b474a27802312ca51e011b9b84a56077e91f31c7` | version line only |
| cov-naturmangfoldloven.json | `4dc9d1138100615d58175bff1ac3048c1168a0edd4b517ad042d554137548d1b` | `22a0ed63ddccd613f7a07c69951ad0cb3128eedc58556da13294e67517dc0552` | version line only |
| cov-straffeloven.json | `2e41d4ff0fa5af7b7bd42550b3e3e10aa2443ee7d4d80fb39d7be366b380e299` | `387e56a8abe0b732b137ddaefc0615f227bc341e1491c288813516f311e24b1d` | changed |
| cov-tvisteloven.json | `90a1480fc0f3dcc988b456e310cc7d55ca168f11016a5aad20298bd063921856` | `33bb175f406e78aa31e5d0522875110d38c9bc5fc0192bf661c271ca1622ee8a` | version line only |
| cov-utlendingsloven.json | `75c32f87f30946d52fd5b4683f19f864260255432c1d39b9b211e9341f1e81e2` | `b3501b6657f5ae1acd3c0b86caa3e7dcc9400e107c037b9769dcc2c5ec291471` | version line only |
| coverage.json | `08f0dc232dfc4fe968595f5aa6ec193ac31117bdf57e215414d57f5349393bc7` | `fea50d0514c853ca2d73981a419d80919573d8a324c07fbbdacc46149bad0b59` | changed |
| ladder-barnevernsloven.json | `cfae428dd3f5f63a03d063834ea4b69bf950b85753b1c61b2e7e19cf0f000686` | `d03cb768b842e50118d620356e481da22fd1b2c1eb04a4f58656aec514273119` | version line only |
| ladder-straffeloven.json | `158993686b2e8cfcbe2aaa0b9951492a4c81dfa7145abd0327f861b1e19c6036` | `37ade527bc68cc1850fd7a7de9c99bfb9530d0f1281d1f65dd9b9035af90a91f` | changed |
| pilot-arveloven.json | `a868ba3155d730e1a32108fe8c065f0b1917b66d8450f7754d0ed121d8a1756b` | `420f22e0d96e2540b4d2773fcd0c1d05e74ec59bdf539c7be3e440b77dc39c2e` | version line only |
| pilot-personopplysningsloven.json | `a0db118e9bb3f662cc809febe1a27e9b084b81b95e886df5ed0d24e283e0cff3` | `2ebbe6448edf1fd2f732a616c105c8e9df71dd26e93c1916d40f35bc5289a691` | version line only |
| pilot-sivilombudsloven.json | `2ec43b012e113f1e5119b7f248eeb566f32c6b236f8cceddb8e79c0803de41ac` | `5a3527dc9e6ebcfa6d146c7855c3e86dff1d3f0c663360db6496dbd64b17615c` | version line only |
| pilot-spraklova.json | `8094507126c10f90025a761d390488702e60b03aa2e438131dd7e1a227c06706` | `629f029542b99e736e119b92642ec6105544d136bbc5949a2c7a684b7db0fc7e` | version line only |

`coverage.json` at replay-10 is committed as `legal-time-coverage-2026-11-16-935.json`.

The five committed pilot and barnevernsloven evidence files change in their version line only.

## 4. Tests

- `tests/integration/test_legaltime_935_inherited_chapter_pinned.py` (11) covers the pinned member: all eleven plain insertions are found and place nothing, the `chapter_map` of both affected acts is empty, a chapter unit is `unknown` (`kap13`, `kap2`, `kap7`), and a chapter dated apart leaves § 2-1 e `unresolved`. Its positive control is straffeloven 2009 "Nye kapitler 21 til 31 plasseres i annen del": 174 sections placed from their titles, `kap21` covers, `kap31` is disjoint, and a `kap21` commencement still dates § 201 while § 410 keeps the default.
- `tests/unit/test_legaltime_935_inherited_chapter.py` (30) uses synthetic members through the real parser. It covers:
  - placements: a chapter named by the sentence; chapters read from titles; inherited only; a member chapter equal to the act's own chapter, which still places nothing; a `replace_section` payload; an insertion naming no act;
  - refused placements: a roman chapter, an unreadable title, a contradicting title, a chapter range without a title, and a title agreeing with its sentence as the control;
  - a payload nested in a host article's list (#893/#922): the host keeps its chapter, the payload has none;
  - `chapter_map` under all six orders of three amenders;
  - commencement: chapter dated apart, chapters dated with the default, a heading unit, the section's own unit, a stated chapter still dated by its chapter, a proven chapterless section, and the replay gate and application.
- Existing tests that built an inserted provision with a chapter but no `stated` now give it a `StatedPlacement`. The parser can no longer produce that state. Two expectations changed:
  - `test_legaltime_step_evidence_pinned` now has `payload.chapter is None`; the raw `kap13` stays in `enclosures`;
  - straffeloven's 2015-10-01 ladder pin is 328/352.
- The #893, #907/#909, #918/#922 unit and pinned suites pass unchanged.
- **Test teeth.** Each part of the fix was reverted alone, 12 reverts in all, and 10 are caught. One of the two survivors was a redundant `chapter_from is None` guard that no test could distinguish, so the guard was removed. The other adds `chapter is not None` to `chapter_map`, which is equivalent because `inserted_chapter` never returns `None`.

## 5. Decisions and gaps, for the owner

1. **"nytt kapittel" against a plain insertion stays `disjoint`.** An earlier draft made it `unknown`. In barnevernsloven that turned six insertions of `lov/2025-06-20-39` (§§ 5-12, 7-7, 8-7, 12-3a, 13-5a, 13-7) from `dated 2026-01-01` into `unresolved`. `forskrift/2025-12-19-2711` excepts "nytt kapittel 9 A" from part I, and those six are separate `Ny § …` instructions, not the text of the chapter insertion. Keeping `disjoint` rests on the operation's own identity, not on a label.
2. **An unreadable title refuses a sentence's chapter.** straffeloven §§ 52a–52c (`lov/2012-01-20-6`, "Nytt kapittel 8 a skal lyde:") carry the printed title "Kapitel 8 a Ungdomsstraff". `chapter_key` does not read the one-t spelling, so the placement is refused and leaves `chapter_map`. Replay is unaffected. Reading "Kapitel" would extend the grammar, which this change does not do.
3. **Part qualifiers.** For an unplaced section, every chapter unit dated apart counts, whatever its part qualifier. A finer rule would need the section's own part, which is as unknown as its chapter.
4. **Not done here:** chapter identity or outline history (ADR-0023 B4b, D-B4a-2), the #937 `IndexError`, and the D7 blanket, which stands as decided.
