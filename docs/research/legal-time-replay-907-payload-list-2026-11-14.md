# #907: an item of a payload's list never inherits an act, research, 2026-11-14

This note covers one attribution defect. Numbered items printed after a replaced provision were read as top-level amendment intros. That provision's last ledd announces a list of amendments to other acts. One genuine straffeloven instruction then inherited the act an item cited.

The fix removes the wrong act. It does **not** bind the instruction to straffeloven: the source does not state where the list ends, and the same shape sits on both sides of that boundary (§ 2). So the instruction is refused, and its act is none.

Nothing is served, published or written into `lovverk`. The MCP, API and public JSON surfaces are untouched. No ADR changed, D7 is not narrowed outside such lists, the R3 grammar is not extended, no rename is applied, and no corpus-wide replay was run.

The change **removes one wrong act and adds uncertainty**:
- 8 of 10,761 operations in the eight acts' evidence change act, all to none.
- None is added, dropped or reclassified.
- No timing changes, and no section verdict changes.

## 0. Baseline and inputs

| item | value |
|---|---|
| baseline | origin/main `76d93b6905825ac13070ef376cc410bc41abf2a8` (PR #906 merged) |
| `lovtidend-avd1-2001-2025.tar.bz2` | sha256 `d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0` |
| `lovtidend-avd1-2026.tar.bz2` | sha256 `600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2` (ADR-0019 R2 `research_only`, unchanged) |
| oracle `gjeldende-lover.tar.bz2` | sha256 `380632d48c82e48bb6fa37c6c6e6f13d55c77defa5f83400c5120ca6989593e9` |
| `REPLAY_VERSION` | `legaltime-replay-7` → **`legaltime-replay-8`** |
| governing record | ADR-0020 D7 (an act binds only on proof; fail closed), D9; ADR-0023 R3-R5 (read, unchanged) |

The baseline was rebuilt first, as B0, from a frozen copy of `src/` at `76d93b69`. **All fifteen files came out byte-identical** to the #893 note's after column (rebuild C), including the committed pilots, the barnevernsloven ladder and the coverage `c47a129a…`. Commands are those of the #893 note §0.

## 1. The defect, from the source

The member is `lti/2009/nl-20090619-074.xml` (sha256 `7ec6a1b4ae319b86ae6735eb2e969022e827afad552c9f9e8a9e4e12db1304d4`, `lov/2009-06-19-74`). Part I opens "Lov 20. mai 2005 nr. 28 om straff endres slik:".

| node | markup | text |
|---|---|---|
| 239 | `defaultP` | "Nåværende § 401 blir § 411 som innleder tredje del og som skal lyde:" |
| 240 | `futureLegalArticle` § 411 | its last ledd: "… Fra den tid kapittel 16 tar til å gjelde, gjøres følgende endringer i andre lover:" |
| 241 | `defaultP` | "1. Militær straffelov 22. mai 1902 nr. 13 § 107 oppheves." |
| 242 | `defaultP` | "2. Lov 13. desember 1946 nr. 14 om straff for utlendske krigsbrotsmenn oppheves." |
| 243 | `defaultP` | "3. I lov 13. juni 1975 nr. 39 om utlevering av lovbrytere mv. skal § 9 annet punktum lyde:" |
| 244 | `legalP` | item 3's payload |
| 245 | `defaultP` | "Nåværende § 402 blir § 412 og skal lyde:" (sentence sha256 `57a9640b…47c9`) |
| 246 | `futureLegalArticle` § 412 | its only ledd: "Med virkning fra den tid hele straffeloven 20. mai 2005 nr. 28 settes i kraft, gjøres følgende endringer i annen lovgivning:" |
| 247-1430 | `defaultP` / `legalP` | § 412's items 1-268, to the end of part I |

The items are text of § 411 and § 412. They are **flat siblings** of the provision, not its children; the markup carries no nesting.

At main:
- `#1:I:241.0` (militær straffelov § 107) was **straffeloven's**: the item names no act the grammar binds, so it inherited the outer block.
- `#1:I:243.0` bound `lov/1975-06-13-39`, and `#1:I:245.0` **inherited it**: the defect of #907.
- Items "31. Utgår.", "180. Utgår." and "191. Utgår." inherited the act of the item before them.

## 2. Why the boundary is not proven

Inside § 412's list, item 18 (node 460) reads "18. I lov 17. juni 1932 nr. 6 om kvalitetskontroll med landbruksvarer m.v. skal § 4 annet ledd lyde:". Its payload is node 461. Then node 462 reads "§ 4 annet ledd annet punktum oppheves.", which is **item 18's own second instruction**.

So the same shape occurs on both sides of a list's end:

| | item | then | belongs to |
|---|---|---|---|
| § 411's list | "3. I lov … nr. 39 … skal § 9 annet punktum lyde:" + payload | "Nåværende § 402 blir § 412 og skal lyde:" | the outer block (straffeloven) |
| § 412's list | "18. I lov … nr. 6 … skal § 4 annet ledd lyde:" + payload | "§ 4 annet ledd annet punktum oppheves." | the item (lov/1932-06-17-6) |

Nothing tells the two apart:
- the markup is identical (`defaultP`, `margin-top`);
- the grammar is identical;
- the header (`changesToDocuments`) lists neither act;
- the item "I lov X skal § Y lyde:" does not announce how many instructions follow.

A first version of the fix restored the outer act after such a "closed" item. The audit found it then bound node 462 to straffeloven, which was wrong. That version was discarded before commit. Binding 245 to straffeloven would need evidence the member does not carry, such as the outer act's own sections (an oracle) or a numbering heuristic. The goal forbids both. So 245 stays an operation of no act. The positive binding is the blocker in §7.

## 3. The rule

`legaltime/payload_list.py`, called by `instructions._Reader` before each node's boundary:

- **Opens** when an instruction's consumed payload ends in a provision whose last ledd announces a list of amendments to other acts. That is the regex `nesting` already uses for the same shape (#893): "endring… i andre lover / anna lovgjeving / annan lovgjeving / annen lovgivning:".
- **A numbered item** (`^\d{1,3}\s?[a-z]?[.)]\s`) starts with no act and the block unbound. It binds only an act it proves itself (D7), never the outer act and never the previous item's.
- **After an open item** ("… følgende endring[er]:", "… endres slik:"), unnumbered instructions are that item's, as the D7 chain reads a block at the top level.
- **After any other item, or before the first**, an unnumbered instruction binds no act. It stays an operation and is counted as unattributed.
- **An unnumbered intro** inside the list binds its own block.
- **Closes** at the end of the part or in another article. The outer act is never restored inside the part.
- **A substitution list's** numbered entries are left to the substitution reader.
- A list opened inside a list behaves the same.

Outside such a list nothing changes: top-level numbered lists, standalone numbered intros and cross-act blocks read exactly as before. `commencement_provisions` moved verbatim from `instructions` to `attribution`, so that `instructions.py` stays under the 700-line ratchet.

## 4. Audit of every changed operation

Every amender and host member in the eight acts' evidence was interpreted from both frozen sources: 404 members, 10,761 operations. Operations were compared by id on (kind, act, instruction digest). **8 change act, and nothing else changes.** Each keeps its kind, instruction, address, timing status, effective date and `known_from`.

`docs/research/data/legal-time-attribution-audit-2026-11-14-907.json` (sha256 `1e9bc633f6a3b4421f9ac34179d667b8011c8efa35bc74bd5b61f6986d746536`) lists them with member sha256, sentence, sentence sha256 (D9 convention: whitespace collapsed) and their place in each report.

| op | kind | act before | act after | class |
|---|---|---|---|---|
| `lov/2009-06-19-74#1:I:241.0` | unsupported | `lov/2005-05-20-28` | none | item no longer inherits the outer act |
| `lov/2009-06-19-74#1:I:245.0` | renumber | `lov/1975-06-13-39` | none | wrong act removed; list end not stated (§2) |
| `lov/2009-06-19-74#1:I:462.0` | repeal_punktum § 4 | `lov/1932-06-17-6` | none | item continuation, list end not stated |
| `lov/2009-06-19-74#1:I:487.0` | unsupported ("31. Utgår.") | `lov/1950-07-14-2` | none | no longer inherits the previous item's act |
| `lov/2009-06-19-74#1:I:1095.0` | unsupported ("180. Utgår.") | `lov/1997-02-28-19` | none | no longer inherits the previous item's act |
| `lov/2009-06-19-74#1:I:1134.0` | unsupported ("191. Utgår.") | `lov/1997-06-19-82` | none | no longer inherits the previous item's act |
| `lov/2009-06-19-74#1:I:1332.0` | replace_section § 15 | `lov/2004-11-19-73` | none | item continuation, list end not stated |
| `lov/2024-06-21-46#1:I:64.0` | renumber § 65 | `lov/2010-03-26-9` | none | item continuation, list end not stated |

`lov/2024-06-21-46` (`lti/2024/nl-20240621-046.xml`, sha256 `3b3ba2b0ef3eaeaa4e2c360da7f97650304bad66e672995e49dce98cfcf5f85a`) replaces advokatloven 2022 § 74, whose last ledd is "Fra den tiden loven trer i kraft, gjøres følgende endringer i andre lover:". Its item "9. I lov 26. mars 2010 nr. 9 om vergemål skal § 65 nytt andre ledd lyde:" is followed by "§ 65 nåværende andre ledd blir tredje ledd.". This is the same class as node 462.

**Separation.**
- *Attribution correction*: 241, 487, 1095 and 1134 (four operations), plus 245, whose wrong act is gone.
- *Fail-closed cost*: 462, 1332 and the 2024 `#64.0` were bound by the D7 chain to the act of the item before them, plausibly correctly. They are now refused, because the same chain bound 245 wrongly.
- *Timing*: no changes.
- *Parser*: no operation added, dropped or reclassified; the member's (id, kind) list is pinned (`348c8bf4…`, 835 operations).
- `nesting()` finds no region in either member, before or after.

## 5. Before / after

**Coverage.** The new file is `legal-time-coverage-2026-11-14-907.json`, sha256 `70b2855c06245320358f9097964517cf16b8403490e40c9810b84a373c501ce3`; before is `legal-time-coverage-2026-11-12-893.json`, `c47a129a…`.
- Barnevernsloven, friskolelova, merverdiavgiftsloven and naturmangfoldloven change their `replay_version` line only.
- Every difference besides the version line is listed below. The 2009 member is in the evidence of straffeloven, arbeidsmiljøloven and utlendingsloven; the 2024 member is in straffeloven's and tvisteloven's.

| | straffeloven | arbeidsmiljøloven | utlendingsloven | tvisteloven |
|---|---|---|---|---|
| events | 625 → **624** | 358 | 556 | 164 |
| unsupported kind | 64 → **63** | — | — | — |
| dated timing | 505 → **504** | unchanged | unchanged | unchanged |
| unattributed operations | 116 → **124** | 80 → **87** | 39 → **46** | 62 → **63** |
| act-level problems, end state | 187 → **194** | 91 → **98** | 199 → **206** | 79 → **80** |
| unresolved at 2026-11-06 | 186 → **185** | unchanged | unchanged | unchanged |
| sections agreeing behind the blanket, sections proven | unchanged | unchanged | unchanged | unchanged |

- Every rung from 2012 on gains the same act-level problems in those reports. The 2024 operation only appears at the 2026-11-06 rung.
- Straffeloven's `unsupported_instructions` top-25 swaps "1. Militær straffelov … § 107 oppheves." for "§ 78 bokstav h og ny bokstav i skal lyde:". That is `most_common(25)` moving its window: the latter event is present, unchanged, in both reports.

**Straffeloven ladder** (not committed):

| state | withheld | applied | unresolved | act-level |
|---|---|---|---|---|
| 2008-03-06 | 4 | 0 | 104 | 62 |
| 2008-03-07 | 99 | 0 | 104 | 79 |
| 2015-09-30 | 390 → **389** | 0 | 104 | 119 → **126** |
| 2015-10-01 | 0 | 334 | 160 → **159** | 159 → **165** |
| 2026-11-06 | 0 | 423 | 186 → **185** | 187 → **194** |

`tests/integration/test_legaltime_phase3.py` pinned (4, 99, 390, 334). It now pins **(4, 99, 389, 334)**, with the reason in the test. The ladder's digest moves from `bac215f6…` to `d1ae21c8dbab366f4d09cf5b503322a049c6fec4e2f9dad3d5d9d6a1e54dde6b`.

**#872 / #893 evidence.**
- None of the 8 operations is a #872 date-only binding or a #893 nested region member: `nesting()` finds no region in either member.
- The #872 and #893 coverage and audit files are left as they were.
- The § 411 / § 412 lists are hosted by `renumber` operations, which `nesting` does not read, so their items keep the outer law's date. That is a separate, pre-existing timing defect; see §7.

**Committed evidence** (sha256):

| file | before | after |
|---|---|---|
| `legal-time-replay-2026-11-06-arveloven.json` | `b729e784…` | `88563a727c374f817449f967d5afd9080d3607bf5c82bf98ebec3ce4e0e3a0a5` (version line only) |
| `legal-time-replay-2026-11-06-personopplysningsloven.json` | `0c4e4e80…` | `f80ae2e22f080564eee1f4eaf9b60aa5f8a8fd02e29be8a82236654d6801b584` (version line only) |
| `legal-time-replay-2026-11-06-sivilombudsloven.json` | `7af5304f…` | `f2f02342fb5322d7856883cb04c560a1eb65f446414ae7e5d7fdb1a812fec8de` (version line only) |
| `legal-time-replay-2026-11-06-spraklova.json` | `6b5fdf7e…` | `59ec41f8904b38cd4bf13c40138342aa90d96023e655cac27a71b2f5f6e78959` (version line only) |
| `legal-time-replay-2026-11-08-barnevernsloven.json` | `c7548647…` | `eb8777cefca315a90b34bb50d72e57780a955d2e909c40423e89042ab2622abe` (version line only) |
| `legal-time-coverage-2026-11-14-907.json` (new) | — | `70b2855c06245320358f9097964517cf16b8403490e40c9810b84a373c501ce3` |
| `legal-time-attribution-audit-2026-11-14-907.json` (new) | — | `1e9bc633f6a3b4421f9ac34179d667b8011c8efa35bc74bd5b61f6986d746536` |

**The four phase-1 pilots keep their verdicts.** Rebuilt at the fix with the old version string, each pilot is byte-identical to its committed file. After the bump, each differs only in the `replay_version` key.

**Determinism.** Two independent, sequential rebuilds, E and F, ran from two frozen copies of the fix's `src/` (tree digest `cbd45438…`). Their fifteen files are identical byte for byte. A further rebuild at the fix with the replay-7 string isolates the content changes listed above.

| file | before (B0, `76d93b69`) | after (E = F) |
|---|---|---|
| `cov-arbeidsmiljoloven.json` | `fe964b68c5048f01…` | `55fc25c812a7864e789252fe0418256ff372219541836e45537541927c4ea7c4` |
| `cov-barnevernsloven.json` | `a968f7fe78da2108…` | `87ddcfb1f107c5abbb595f99ab54cd19de0a580493f5693d748f058f1341ecbc` |
| `cov-friskolelova.json` | `3d212a3ef2589f34…` | `e4e9e40b658429aed64ac1f16964440af72f0427bd79ab40f275f64ddc82cfcf` |
| `cov-merverdiavgiftsloven.json` | `9a51d3fa3e91cc6b…` | `d2137d16f4429ad7b519da748e7ab7ec62d56b5dac22db0df4e744608008d5ca` |
| `cov-naturmangfoldloven.json` | `87f3ce6efb1696a6…` | `9a0ca58a49f5f1a744c03a1cec83a9f8e6e378a74416ddc7e0b8bfc594eea83d` |
| `cov-straffeloven.json` | `7046760f037c5bbb…` | `842a517fbded266961371248b5146ef02451cc03902d197f96dc8d77894408f3` |
| `cov-tvisteloven.json` | `7b4176d041191911…` | `77e5107f2dd6e89b1a453299b13cdd2693647771f0efe1260d7be9d7cff2ca2e` |
| `cov-utlendingsloven.json` | `96c049522558e414…` | `73ae8c39e807eeab9235148f4b9357085523e46864bd7f38a0f3cc759176fd05` |
| `coverage.json` | `c47a129a480e6cde…` | `70b2855c06245320358f9097964517cf16b8403490e40c9810b84a373c501ce3` |
| `ladder-barnevernsloven.json` | `c7548647c4b7bf9d…` | `eb8777cefca315a90b34bb50d72e57780a955d2e909c40423e89042ab2622abe` |
| `ladder-straffeloven.json` | `bac215f61775ae13…` | `d1ae21c8dbab366f4d09cf5b503322a049c6fec4e2f9dad3d5d9d6a1e54dde6b` |
| `pilot-arveloven.json` | `b729e784a4027db3…` | `88563a727c374f817449f967d5afd9080d3607bf5c82bf98ebec3ce4e0e3a0a5` |
| `pilot-personopplysningsloven.json` | `0c4e4e805f8a612b…` | `f80ae2e22f080564eee1f4eaf9b60aa5f8a8fd02e29be8a82236654d6801b584` |
| `pilot-sivilombudsloven.json` | `7af5304f1d71d257…` | `f2f02342fb5322d7856883cb04c560a1eb65f446414ae7e5d7fdb1a812fec8de` |
| `pilot-spraklova.json` | `6b5fdf7efb7f7ada…` | `59ec41f8904b38cd4bf13c40138342aa90d96023e655cac27a71b2f5f6e78959` |

## 6. Tests

- **`tests/unit/test_legaltime_payload_list.py`**: 20 cases on synthetic members, using the source's own sentences. They cover:
  - the pinned shape;
  - item 18's same-shape continuation;
  - that same shape outside a list, where it is still bound by D7;
  - two provisions announcing no list: no listing;
  - open items;
  - per-item reset;
  - items of unproven extent (substitution sentence, act heading, repeal);
  - an instruction before the first item;
  - no return of the outer act;
  - a list inside a list;
  - an unnumbered intro;
  - the end of the part;
  - no operation lost;
  - a top-level numbered list left untouched;
  - `host_list`;
  - a change of article;
  - a substitution list inside an open item.
- **`tests/integration/test_legaltime_payload_list_pinned.py`**: 6 cases on the pinned member. They cover:
  - member sha256, boundary node texts and the sentence digest of 245;
  - 239 = straffeloven, 245 = none;
  - 460 = `lov/1932-06-17-6`, 462 = none;
  - 241, 242 = none and 243 = `lov/1975-06-13-39`;
  - the full (id, kind) digest;
  - `nesting()` empty.
- **Red first.** The final test files were run against main's `src/` with only `payload_list.py` added and the reader unwired: **11 failed, 11 passed**. The 11 that pass are guards that must hold on both sides. Against the fix: 22 passed, and 26 after the review additions.
- **Teeth.** Each rule was disabled in turn by monkeypatching, without mutmut:

  | rule disabled | unit cases failing (of 20) |
  |---|---|
  | list never opens | 8 |
  | items keep the previous act | 5 |
  | open items fail closed | 3 |
  | unproven items inherit | 5 |
  | no article check | 1 |
  | reader hook off | 8 |
  | substitution guard off | 1 |
  | `host_list` accepts any colon | 1 |
- **Preserved.** These files pass **350 of 350** against main's `src/` and the fix's alike: the phase 4 attribution, blanket, commencement and mutant files, the phase 4B and #872 files, the #893 nested-timing file, and the instructions, phase 2 and phase 3 instruction files. The legaltime unit suite: 1,519 passed before the new file.
- **Updated, with the reason in each test:**
  - `test_legaltime_phase3.py` (390 → 389);
  - `test_legaltime_rename_research_pinned.py`: 245.0 is now refused as unbound, and its sentence digest is pinned;
  - `test_legaltime_mutants.py`: the version string.

## 7. Not done, and blockers

- **Binding 245 to straffeloven is blocked.** The member does not state where § 411's list ends (§2). A proof would need structure Lovdata's markup does not carry, or an owner decision admitting another kind of evidence. Until then 245 is unattributed, and the B2 research adapter refuses it as `act not bound by the D7/phase-4B chain`.
- **Timing of renamed hosts' lists (filed separately).** Items of § 411's and § 412's lists are dated by the 2009 law's "straks" (2009-06-19), not by their host:
  - arbeidsmiljøloven: 4 events, `#1:I:1361.0`-`#1:I:1366.0`;
  - utlendingsloven: 10 events, `#1:I:1417.0`-`#1:I:1428.1`.

  `nesting` reads only `replace_section` hosts, and both hosts here are `renumber`. This PR does not change it.
- **Numbered act headings inside a payload list** still bind only where phase 4B's `in_list` already holds. Widening that would add bindings, which is out of scope here.
