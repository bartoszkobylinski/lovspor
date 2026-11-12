# #893: a nested amendment is dated by its host provision, research, 2026-11-12

This note covers one timing defect. An amendment nested inside another act's amending provision was dated by the **outer** law's own commencement clause. The fix implements notebook ADR-0024 R4 (Accepted 2026-11-12) for the one nesting shape verified against its sources, and refuses every other shape.

Nothing is served, published or written into `lovverk`. The MCP, API and public JSON surfaces are untouched. No ADR changed, no commencement precedence rule was added, D7 is not narrowed, renumbering is not implemented, and no corpus-wide replay was run.

The change **adds uncertainty and proves one date**:
- 6 operations that were falsely dated are now `unresolved`.
- 1 operation moves to its proven date.
- No section verdict changes; every section was already behind the D7 blanket.

## 0. Baseline and inputs

| item | value |
|---|---|
| baseline | origin/main `ed804724aeb2b328314200320065db84c0fca7ff`; `src/lovspor/legaltime/` byte-identical to `d9b9789e` and `b96529ea` |
| `lovtidend-avd1-2001-2025.tar.bz2` | sha256 `d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0` |
| `lovtidend-avd1-2026.tar.bz2` | sha256 `600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2` (ADR-0019 R2 `research_only`, unchanged) |
| oracle `gjeldende-lover.tar.bz2` | sha256 `380632d48c82e48bb6fa37c6c6e6f13d55c77defa5f83400c5120ca6989593e9` |
| `REPLAY_VERSION` | `legaltime-replay-6` → **`legaltime-replay-7`** |
| governing record | notebook ADR-0024 (Accepted 2026-11-12) R1, R4, test N4; ADR-0020 D3, D6, D7, D9 |

The baseline was rebuilt first, from a frozen copy of `src/` at `ed804724`, and **every file came out byte-identical to its committed or noted digest**:
- the four pilots, the barnevernsloven ladder and the eight-act coverage `f82d0495…`;
- the straffeloven ladder `c3f7df65…`;
- the per-act reports named in the #872 note (`a520546a…`, `0bba894c…`, `86a7bd52…`, `8cdfb3d2…`).

Commands: those of the Phase 3 note §1. The coverage ladder is `--at 2008-01-01 2012-01-01 2016-01-01 2020-01-01 2024-01-01 2026-11-06`; the straffeloven ladder is `--at 2008-03-06 2008-03-07 2015-09-30 2015-10-01 2026-11-06`; every run has `--oracle`. The per-act reports are reduced by `scripts/legal_time_coverage.py`.

## 1. The defect, from the sources

`lti/2013/nl-20130405-012.xml` (sha256 `5f1d25e5c54c1fad5ce17ddec921a139602a77b8031277275337a17717d3d4ba`, `lov/2013-04-05-12`, published 2013-04-05 14:40). Sentence digests use the ADR-0024 evidence file's convention: tags stripped, whitespace collapsed.

- Part I: "I lov 26. mars 2010 nr. 9 om vergemål gjøres følgende endringer:" (`c2e5e9be…`) … "§ 102 skal lyde:".
- The replacement text is one ledd: "§ 102. Endringer i andre lover Fra den tid loven trer i kraft gjøres følgende endringer i andre lover:" (`1a03153e…`).
- Its items follow **as flat siblings** of the `futureLegalArticle`, not inside it. Items 1–56 run to the end of part I. Item 50 reads "50. I lov 20. mai 2005 nr. 28 om straff skal § 261 første ledd lyde:" (`a7f4094f…`).
- Part II resumes the law's own direct amendments ("I lov 4. juli 2003 nr. 74 om hundehold …"). Part III: "Loven trer i kraft straks." (`d36ed556…`).

`lti/2013/sf-20130405-0338.xml` (sha256 `46fdc736f81b4f04d469c71792cdbdf4add628d044c47d5fd97a213dee805f59`, published 2013-04-08 16:00): "Delt ikraftsetting av lov 26. mars 2010 nr. 9 om vergemål. Loven trer i kraft 1. juli 2013 med unntak av § 51 annet ledd, § 54 tredje ledd, § 65 og § 66 som trer i kraft straks." (`de517f3d…`).

Item 50 is text of vergemålsloven § 102. It takes effect "fra den tid loven [vergemålsloven] trer i kraft", that is 2013-07-01. replay-6 dated `lov/2013-04-05-12#1:I:253.0` at **2013-04-05**, from part III. That date comes from the wrong instrument (ADR-0020 D9).

**Where the wrong date entered.** `instructions` reads the items as the 2013 law's own instructions, because the markup carries no nesting. `run._events_of` then gives every operation of the member `resolve_timings(amender, …)`: the member's own clause, folded with the decisions citing the member. Vergemålsloven, its enacting member and its decision were never in the act's evidence: `lov/2010-03-26-9` does not list straffeloven in `changesToDocuments`, and `forskrift/2013-04-05-338` cites vergemålsloven, not the 2013 law.

**Scan.** Every amender of the eight acts was scanned for the host shape (a `replace_section` whose last ledd introduces a list of amendments to other acts): 384 members in the 2001–2025 archive and 19 in the 2026 archive. Two hosts exist:

| member | host op | host | host list (verbatim) |
|---|---|---|---|
| `lov/2013-04-05-12` | `#1:I:24.0` | vergemålsloven `lov/2010-03-26-9` § 102 | "Fra den tid loven trer i kraft gjøres følgende endringer i andre lover:" |
| `lov/2020-12-18-149` (sha256 `ac838006…8e61`) | `#1:I:12.0` | arveloven `lov/2019-06-14-21` § 181 | the same sentence |

The second host's region holds `#1:I:183.0`, tvisteloven § 6-2 første ledd bokstav a. Tvisteloven was in force, so replay-6's date (2020-12-18, the outer law's "Loven trer i kraft straks.") **showed the amended text in states between 2020-12-18 and 2020-12-31**. Arveloven commenced 2021-01-01 by `forskrift/2020-02-28-200` (sha256 `4cef74e5…8543`, published 2020-03-02 14:30): "Lova gjeld frå 1. januar 2021." (`11a99b4c…`). That date is not a ladder date, so no committed count shows the wrong text. The defect changed history, not only metadata.

## 2. The rule

A post-pass, `legaltime/nesting.py`, runs after `instructions`, which is unchanged. It reads regions from the operations' own tags (`#<part>:<label>:<node>`) and the part's nodes. `legaltime/hosts.py` dates them.

- **Region.** A `replace_section` whose replacement's last ledd introduces a list of amendments to other acts opens a region. The region runs from that node to the end of its part. Every operation after the host in that part sits in it, unattributed ones included (D7 counts them from their own date).
- **Dated only when every link is proven:**
  1. the host text is the **verified wording**: exactly one ledd, "Fra den tid loven trer i kraft gjøres følgende endringer i andre lover:";
  2. every act intro inside the region is a **numbered** item;
  3. the region carries no commencement sentence of its own;
  4. the region holds no second host;
  5. the outer law's replacement of the host section is `dated`, and **no later** than the host section commences;
  6. the host act's enacting member is in the archives, and the host section is in its enacted text;
  7. the host section's commencement, read by the existing per-unit machinery (`baseline_commencement` on the host's enacting member and the decisions citing the host), is `dated` for that section, whole: no ledd or finer unit, and no part-qualified unit.
- **Date assigned:** effective = the host section's commencement. `known_from` = the later publication of the outer law and the host's dating instrument. Proofs = the outer law's proof, then the host's.
- **Otherwise `unresolved`**, with a reason starting `nested amendment: `, and known from the outer law's publication. Without that `known_from`, #858's rule would count an unproven 2013 operation from the start of time. Never `pending`: ADR-0024 R4 says a nested operation is dated or unresolved.
- **Never used as a date source:** the target act's commencement, the consolidation, or the outer law's own clause.
- **No precedence.** A commencement sentence inside a region is removed from the outer law's clauses and refuses the region (ADR-0020 D6).
- **Host evidence.** `collect()` gathers each host's enacting member and the decisions citing it into `Evidence.hosts` and `Evidence.host_decisions`, apart from the act's own evidence. They are carried on `Decisions` (`host_acts`, `host_decisions`) to `_events_of`. The report schema is unchanged: the proofs name the host instruments by member and sha256.

The 2013 host's date is **not proven**. `forskrift/2013-04-05-338`, "Delt ikraftsetting … med unntak av …", is a partial decision the grammar does not read (`partial decision outside the grammar`, the same refusal the replay gives for vergemålsloven's own commencement). So its items are `unresolved`. 2013-07-01 would be accepted only once that sentence is read. 2013-04-05 is never accepted. Reading that decision shape is grammar work and is not part of this change.

## 3. Audit of every changed operation

`docs/research/data/legal-time-nested-audit-2026-11-12-893.json` (sha256 `a21ea5e101fac861ed06b74f864cbfc8b75c43cc0cb804d54564d97dad44f5c8`) compares every event and unattributed event of the before and after reports, keyed by list and op id. Status, effective date, `known_from` and reason are compared. Operations differ in neither presence nor kind.

| op | act (report) | list | before | after | known_from | class |
|---|---|---|---|---|---|---|
| `lov/2013-04-05-12#1:I:253.0` | straffeloven § 261 ledd 1 | events | dated 2013-04-05 | **unresolved** | 2013-04-05 | newly unresolved |
| `lov/2013-04-05-12#1:I:256.0` | tvisteloven § 2-2 | events | dated 2013-04-05 | **unresolved** | 2013-04-05 | newly unresolved |
| `lov/2013-04-05-12#1:I:259.0` | tvisteloven § 2-3 | events | dated 2013-04-05 | **unresolved** | 2013-04-05 | newly unresolved |
| `lov/2013-04-05-12#1:I:261.0` | tvisteloven § 2-4 | events | dated 2013-04-05 | **unresolved** | 2013-04-05 | newly unresolved |
| `lov/2013-04-05-12#1:I:26.0` | (both acts' reports) | unattributed | dated 2013-04-05 | **unresolved** | 2013-04-05 | newly unresolved |
| `lov/2013-04-05-12#1:I:43.0` | (both acts' reports) | unattributed | dated 2013-04-05 | **unresolved** | 2013-04-05 | newly unresolved |
| `lov/2020-12-18-149#1:I:183.0` | tvisteloven § 6-2 ledd 1 bokstav a | events | dated 2020-12-18 | **dated 2021-01-01** | 2020-12-18 | newly proven date |

7 distinct operations: **1 newly proven date, 6 newly unresolved, 0 other**. Every other operation of the eight acts' reports is unchanged.

## 4. Before / after

**Coverage.** `legal-time-coverage-2026-11-12-893.json`, sha256 `c47a129a480e6cde66e73ad10f32f42f10f4d3dcec93cfbf3f6c9d16cbac62c2`; before = `legal-time-coverage-2026-11-11-872.json`, `f82d0495…`. Six acts change their `replay_version` line only: arbeidsmiljøloven, barnevernsloven, friskolelova, merverdiavgiftsloven, naturmangfoldloven and utlendingsloven.

| | straffeloven before → after | tvisteloven before → after |
|---|---|---|
| events | 625 → 625 | 164 → 164 |
| dated / pending / unresolved timing | 506 / 16 / 103 → **505** / 16 / **104** | 112 / 8 / 44 → **109** / 8 / **47** |
| applied at 2026-11-06 | 424 → **423** | 95 → **90** |
| unresolved ops at 2026-11-06 | 185 → **186** | 61 → **66** |
| act-level problems, every rung | unchanged (reason text of the 2 unattributed ops now names the nested reason) | unchanged (same) |
| sections agreeing behind the blanket | 293 of 400, unchanged (counts before the blanket identical) | 296 → **293** of 442 (`exact_normalized` 189 → 186, `unresolved` 34 → 37) |
| sections proven | 0 → 0 | 0 → 0 |

- **Tvisteloven, unresolved +5 for 3 refused operations.** The 2 extra are `lov/2021-05-07-34#1:I:100.0` and `#4:IV:18.0`, `term not found in ['§2-4']`. Those substitutions address the 2013 text of § 2-4, which is no longer applied. This is fail-closed cascading, not new refusals.
- **The 3 sections that leave agreement behind the blanket** are §§ 2-2, 2-3 and 2-4. The oracle carries the 2013 text, but an end-state match never supplies a date (ADR-0020 D6, D8).

**Straffeloven ladder.** It is not committed.

| state | withheld | applied | unresolved | act-level |
|---|---|---|---|---|
| 2015-09-30 | 391 → **390** | 0 | 103 → 104 | 119 |
| 2015-10-01 | 0 | 335 → **334** | 159 → 160 | 159 |
| 2026-11-06 | 0 | 424 → **423** | 185 → 186 | 187 |

The ladder's digest is `c3f7df65…` → `bac215f61775ae1324169630f923bf9019cd0518001475cf8375a21f4854c8a8`. `tests/integration/test_legaltime_phase3.py` pins (4, 99, 391, 335). It now pins **(4, 99, 390, 334)**, with the reason in the test. The one operation is `#1:I:253.0`: withheld or applied before, unresolved after.

**Committed evidence** (sha256):

| file | before | after |
|---|---|---|
| `legal-time-replay-2026-11-06-arveloven.json` | `5b101e52…` | `b729e784a4027db323d24f175fca85e1798c08dd80ece17254aa603ed3c800ca` (version line only) |
| `legal-time-replay-2026-11-06-personopplysningsloven.json` | `0d42b5c3…` | `0c4e4e805f8a612bea92bb5687002e50c97e3acb7c75ce0d62a4e49245a582f4` (version line only) |
| `legal-time-replay-2026-11-06-sivilombudsloven.json` | `1684d54c…` | `7af5304f1d71d25713a521c3333c9676b43a0ec80e8fb6d3946dccf52d88e365` (version line only) |
| `legal-time-replay-2026-11-06-spraklova.json` | `123e3279…` | `6b5fdf7efb7f7ada163eefd00ddbd3d57ff3c785ed560335e4bc019129e25bf5` (version line only) |
| `legal-time-replay-2026-11-08-barnevernsloven.json` | `a559bec6…` | `c7548647c4b7bf9dcab069ab2f6ac8519b70d5e6c892f6f2a1e9a48e5fabd1ae` (version line only) |
| `legal-time-coverage-2026-11-12-893.json` (new) | — | `c47a129a480e6cde66e73ad10f32f42f10f4d3dcec93cfbf3f6c9d16cbac62c2` |
| `legal-time-nested-audit-2026-11-12-893.json` (new) | — | `a21ea5e101fac861ed06b74f864cbfc8b75c43cc0cb804d54564d97dad44f5c8` |

- Arveloven's own amender `lov/2020-12-18-149` hosts the § 181 region, but every item in it amends another act. Arveloven's events are therefore unchanged.
- Not committed (regenerated): the eight per-act reports. They change content for straffeloven (`7046760f…`) and tvisteloven (`7b4176d0…`); the other six differ by the version line only. The straffeloven ladder (`bac215f6…`) is also regenerated.
- The #872 coverage and audit files are left as they were.

**Determinism.** Three rebuilds of the fifteen files (eight per-act reports, the coverage reduction, two ladders, four pilots), each from its own frozen copy of `src/`:
- **B0** at `ed804724`: reproduces every before digest.
- **C and D**, run independently and sequentially at `c50f975d`'s `src/`: identical digests for all fifteen files. **E and F**, at the reviewed source `bf54c13d`, are identical to C and D.
**The fifteen digests**, one line each. C, D, E and F agree byte for byte; the before column is B0:

| file | before (B0, `ed804724`) | after (C = D at `c50f975d` = E = F at `bf54c13d`) |
|---|---|---|
| `cov-arbeidsmiljoloven.json` | `a520546acaeca106…` | `fe964b68c5048f01a064effa6c20fe5b34fe01286d4f475bb4d89781c17d2a74` |
| `cov-barnevernsloven.json` | `fd9c7e86659b1c09…` | `a968f7fe78da210891bd77464677df4f41095f35b74ef592df93ecd84f416c91` |
| `cov-friskolelova.json` | `f42acee8d8104df6…` | `3d212a3ef2589f34fb47d237ae1c2a154af998836f061650b3d6a4957d5427a9` |
| `cov-merverdiavgiftsloven.json` | `6e1c416596fc58c0…` | `9a51d3fa3e91cc6bfcff28eba7255351880ce557ddf617a0e9e90206dd822967` |
| `cov-naturmangfoldloven.json` | `ee4ac2ddddf1147f…` | `87f3ce6efb1696a6ecbe2d71aa826f66c1dc49330c37ee80ba40c75a221d7d41` |
| `cov-straffeloven.json` | `0bba894ce97990e3…` | `7046760f037c5bbb239a5fcb5b48a78761457b7f3583de85a1f9b85939f2b5dc` |
| `cov-tvisteloven.json` | `86a7bd522df8fcdd…` | `7b4176d0411919116917189acf53247592cadde9cad708187791429cde49070a` |
| `cov-utlendingsloven.json` | `8cdfb3d277f04b7c…` | `96c049522558e414df9e4cce99a07f04e8ac0aed9abaca6def8c939942f29bee` |
| `coverage.json` | `f82d04959afb7fbf…` | `c47a129a480e6cde66e73ad10f32f42f10f4d3dcec93cfbf3f6c9d16cbac62c2` |
| `ladder-barnevernsloven.json` | `a559bec6222e2f5f…` | `c7548647c4b7bf9dcab069ab2f6ac8519b70d5e6c892f6f2a1e9a48e5fabd1ae` |
| `ladder-straffeloven.json` | `c3f7df6538b853b5…` | `bac215f61775ae1324169630f923bf9019cd0518001475cf8375a21f4854c8a8` |
| `pilot-arveloven.json` | `5b101e528230d1a2…` | `b729e784a4027db323d24f175fca85e1798c08dd80ece17254aa603ed3c800ca` |
| `pilot-personopplysningsloven.json` | `0d42b5c39b964ad5…` | `0c4e4e805f8a612bea92bb5687002e50c97e3acb7c75ce0d62a4e49245a582f4` |
| `pilot-sivilombudsloven.json` | `1684d54cb77c8a36…` | `7af5304f1d71d25713a521c3333c9676b43a0ec80e8fb6d3946dccf52d88e365` |
| `pilot-spraklova.json` | `123e32790b8f9d2c…` | `6b5fdf7efb7f7ada163eefd00ddbd3d57ff3c785ed560335e4bc019129e25bf5` |

- An earlier pair, A and B, ran at a first version whose refusals carried no `known_from`. They were identical to each other, but raised act-level problems by 2 at the 2008 and 2012 rungs, a #858 knowledge leak. The fix (refusals known from the outer law's publication, tested) removed it before C and D.

## 5. Tests

**`tests/unit/test_legaltime_893_nested_timing.py`**, on synthetic members: 24 cases at `c50f975d`, 33 after review, **51** after the mutation evidence (§6). The 18 added for mutation pin refusal reasons, proofs and edge paths of the fix itself; they were not run against `main`. The markup is synthetic; the host sentence is the archive's (S27).
- **Against `ed804724`, the final 33-case file fails to collect**: `lovspor.legaltime.nesting` does not exist there.
- **With that one import guarded, 29 fail and 4 pass.** The 4 that pass:
  - 2 guards that must hold on both sides: the outer law's direct amendment after the region, and the host replacement itself, both keep the outer date;
  - the 2 knowledge cases (known-from, and no D7 problem before publication). They pass on `main` because `main` dates the item from the outer publication. They exist to catch the first fix version's leak: that version left `known_from` empty on refusals. That they fail against it is inferred from the code and was not run.
- **The required cases:**
  - the outer date never survives;
  - the host's proven date with both proofs and the later `known_from`;
  - no host decision → unresolved;
  - no host member → unresolved;
  - host section absent → unresolved;
  - a partial decision dating the host section itself → dated; one dating another section → unresolved;
  - a conditional host decision → unresolved;
  - a host replaced after it commenced → unresolved;
  - an undated replacement → unresolved;
  - three wordings outside the verified one → unresolved;
  - an unnumbered intro inside the region → unresolved;
  - a commencement sentence inside the region → unresolved, and the outer law's clause stays intact (no precedence);
  - an unattributed operation in the region → dated by the host;
  - two hosts in two members never share a date, and two target acts in one region share only the host date;
  - an unresolved item is known from the outer publication, and is no D7 problem before it;
  - **a history fixture**: target act in force, nested item applied at 2010-03-01 under the old reading, not applied under the fix, applied from the host date 2010-07-01.

**`tests/integration/test_legaltime_893_nested_pinned.py`**, 5 cases on the pinned archives:
- `#1:I:253.0` is never 2013-04-05, is `unresolved` with the host reason (`partial decision outside the grammar`), and names `forskrift/2013-04-05-338` among its proofs;
- tvisteloven's three 2013 items are `unresolved`;
- `lov/2020-12-18-149#1:I:183.0` is `dated` 2021-01-01, known 2020-12-18, with proofs `[lov/2020-12-18-149, forskrift/2020-02-28-200]`.

**Named regression run** at head `4a0a38c8`, after the Codex test author's commit:
- the #856–#859, Phase 4/4B and #872 unit files: `test_legaltime_phase4_attribution.py`, `…_phase4_blanket.py`, `…_phase4_commencement.py`, `…_phase4_mutants.py`, `…_phase4b_attribution.py`, `…_phase4b_mutants.py`, `…_872_date_only_attribution.py`: **210 passed**;
- the Phase-1 pilot reproduction `tests/integration/test_legaltime_pilot.py`: **9 passed**, byte-for-byte against the regenerated files;
- `tests/unit/test_legaltime_893_nested_timing.py`: **55 passed**, including the 3 cases the Codex test author added in `4a0a38c8`.

**The same seven files against the pre-fix source** (frozen `src/` of `ed804724`): **210 passed**. They hold on both sides, as preservation tests must.

**Pilots, field by field.** For each of arveloven, personopplysningsloven, sivilombudsloven and språklova, the B0 report and the after report differ in exactly one top-level key, `replay_version` (`legaltime-replay-6` → `legaltime-replay-7`). Every other key is equal: states, events, verification, commencement, members, inputs, archives and oracle.

**Preserved:**
- the #856–#859, Phase 4B and #872 tests (the legaltime unit suite: 1,249 before this file, all passing);
- the four Phase-1 pilots (byte-identical apart from the version line);
- `test_build_report_pins_oracle_sorted_inputs_and_json_mode`, updated to `legaltime-replay-7`.

**Review fixes** (`bf54c13d`, see §6a) added 9 cases. The mutation evidence (`4793b0d9`, `808b2f9b`) added 18 more: 51 in total.

**Gates, at `bf54c13d`:**
- `scripts/quality/verify-fast.sh`: all checks passed. That covers gitleaks, conflict markers, ruff, ruff format, mypy over `src/`, the equivalents register, the ratchets (no baseline entry added or raised), the boundaries, dependency policy and the release contracts.
- `VERIFY_DEEP_FULL=1 scripts/quality/verify-deep.sh`: all checks passed; full unit suite **20,817 passed**, 2 skipped, 10 xfailed.
- Pinned integration (`pytest tests/integration -k legaltime`, archives at the pins): **31 passed**. These include the Phase-1 pilot reproduction, the barnevernsloven byte-for-byte test against the regenerated files, and the Phase 3 straffeloven ladder at its new pin.
- `/security-check`, clean for this diff:
  - bandit: 0 unreadable files; the 2 HIGH B324 findings are pre-existing, in the untouched `tests/unit/test_r2_client_equivalence_assumptions.py`;
  - pip-audit: production and all dependencies clean;
  - gitleaks: clean;
  - no tar, XML or shell hits in the diff.

### 6a. Independent review

A read-only Codex review (`codex exec -s read-only`) ran on `75ee92b2`. Two major findings, both fixed in `bf54c13d` with tests:
1. **Region membership was "everything after the host to the end of the part".** A region now opens only when the first instruction paragraph after the host is a numbered item. It is refused when an item names the host act itself.
2. **Clauses were filtered by substring.** A clause now counts as read inside a region only by exact sentence equality on instruction paragraphs; payload ledd never state a clause. A copy also read outside a region stays the outer law's, so a shared "Loven trer i kraft straks." is neither silenced nor duplicated into a "two default clauses" refusal.
- Also fixed: operation tags are placed with `rsplit`, so a part label holding a colon cannot misplace a node.
- The review found nothing on the host-date guards, `known_from`, the proofs, the evidence separation in `collect()`, or the audit's digests and counts.

**Rebuilds E and F**, from `git archive` of `bf54c13d`, are identical to each other and to C, for all fifteen files. The review fixes change no output on the pinned inputs.

## 6. Mutation

The PR's `mutation` check is a trivial pass kept for the ruleset (#877). **It measures nothing and is not evidence.**

The evidence below comes from local runs of `scripts/mutmut-pr.sh origin/main`: mutmut 3.8.0, scoped to the 24 changed functions of `archive.py`, `hosts.py`, `nesting.py` and `run.py`. Unit tests only judge mutants (`[tool.mutmut]`).

| run | source | mutants | killed | survived | timed out | suspicious |
|---|---|---|---|---|---|---|
| 0 | `bf54c13d` | — | — | — | — | — (tool failure, see below) |
| 1 | `bf54c13d` | 481 | 423 | 58 | 0 | 0 |
| 2 | `4793b0d9` (+ survivor-killing tests) | 481 | **463** | **18** | 0 | 0 |

**Run 0 is a tool failure, not a score.** It was started with `MUTMUT_PR_TOTAL_BUDGET_SECONDS=3600`. Mutmut's stats pass failed on `tests/unit/test_mutmut_pr_script.py::TestBudgetKillBeforeFirstTally::test_is_reported_as_the_budget_not_as_a_missing_progress_line`, because that test inherits the variable and so never sees the 1 s budget it asserts. Run 1 and run 2 ran with no budget variables (defaults).
- **Out-of-scope finding (lovspor CI):** the nightly lane sets this same variable on its mutmut step (`nightly-mutation.yml:111`). #888 isolated one test from it; this sibling is not isolated. The issue text is drafted, but creating it was refused by this session's permission policy. Action taken: none.

Between run 1 and run 2, the 40 killed survivors were:
- host evidence collection (`archive._host_evidence`, `collect`), by `collect()` on a synthetic archive with and without a host;
- the SPLIT, unproven and late refusal paths, by their reasons and proofs;
- a chapter-dated host;
- a replacement on the host commencement day;
- retroactivity on both sides;
- an absent `known_from`;
- a skipped inner host followed by a later host (`continue`, not `break`);
- a clause shared with a later part;
- `_wording(None)`;
- `_place` on part labels holding "#".

**Of run 2's 18 survivors, one was a real gap:** `_region` `not nodes and not …` for `or`, which crashes on an empty region. `808b2f9b` kills it. Its two tests fail with the mutant applied by hand to a copy of the source. mutmut was not re-run for it: a re-run recomputes the ~25 min stats pass.

The other 17 are equivalent; each is argued below and registered.

| function | mutants | argument |
|---|---|---|
| `nesting._place` | `split(":", )[0]`, `split(":", 2)[0]`, `split(".", )[0]`, `split(".", 2)[0]` | Python semantics: `maxsplit` never changes the first field. |
| `nesting._place` | five mutations of `.split("-", 1)[0]` (`None`, no maxsplit, `rsplit`, `"XX-XX"`, maxsplit 2) | The input is the node field cut at its first "." (`"18"` of `"18.0-40"`). Every operation id `instructions` writes carries a `.offset`, and the structure suffix `-N` follows it, so no "-" is left. The split is defensive and dead on every produced id. |
| `nesting._region` | `nodes[start + 2 :]` | Node `start + 1` is the host's payload (`kind == "provision"`), which the paragraph filter drops anyway. |
| `nesting._region_nodes` | `start - 1`, `start + 2` | Only paragraphs' sentences are compared with the clauses. Node `start + 1` is the payload. Node `start` (the host's "§ N skal lyde:") opens a payload and is never a clause. Node `start - 1` (the block's intro) is never captured as a clause, checked on a commencement-shaped intro. |
| `nesting._outside_nodes` | `position >= start` | Removes only node `start`, the host's instruction, from "outside": never a clause, as above. |
| `nesting._sentences` | `"XX XX".join(...)` on the whole-node entry | Clauses are single sentences, and `split_sentences` yields every single-sentence node's text unchanged. The whole-node entry only matters for a clause spanning several sentences of one paragraph. None was seen; the argument rests on how `instructions` captures clauses, not on Python alone. |
| `hosts._from_host` | `parts=None` | `section_timing` reads `parts` only for a part-qualified unit, and those are refused (SPLIT) before this line. |
| `hosts._from_host` | `Decisions(...)` without `chapter_of` | Equivalent by construction. The map passed is `{p.key: p.chapter for p in host.provisions if p.chapter}`. `_Fold.__init__` (`baseline.py:244-248`) unions exactly that map from the act it is given, so `chapter_of`, `chapters` and `sections` come out identical. `_Decision.chapter_of` is read only by `_Decision.answer(op)` (`commencement.py:598`), which `baseline_commencement` never calls (no `.answer(` in `baseline.py`). |
| `archive._host_evidence` | `if refids or True` | `_decision_names(index, set())` returns `[]` for any index: no title cites an empty set of laws, and no `changesToDocuments` intersects it. |

So the measured surface ends at 463 of 481 killed by mutmut. One further mutant is killed by a test confirmed by hand. The other **17 are argued equivalent; none is open**.

All 17 are **registered** in `mutation-equivalents/`, one file each, under `legaltime.nesting/`, `legaltime.hosts/` and `legaltime.archive/`. Each entry is keyed by its `-`/`+` lines and carries the argument above. `scripts/ci/mutation_to_json.py --check-equivalents`: 406 registered, 0 refused, 0 stale.
- Four entries argue from Python semantics alone: the `maxsplit` cases.
- The others argue from this code's own invariants: the operation-id format `instructions` writes; how it captures clauses; `_Fold`'s merge; the SPLIT guard before `section_timing`. They cite those lines.

A registered entry moves only the nightly gate's verdict: the mutants are still counted and listed.

## 7. Limits and follow-ups

- **2013-07-01 is not proven.** The vergemålsloven partial decision ("Loven trer i kraft 1. juli 2013 med unntak av § 51 annet ledd, § 54 tredje ledd, § 65 og § 66 som trer i kraft straks") is outside the partial-decision grammar. Reading it would date § 102, and with it the 2013 items, at 2013-07-01. That is grammar work under #861 step 3, not this change.
- **Instruments of other acts that commence the host** (ADR-0020 D5(d), `instrument_statements`) are not gathered for hosts. `baseline_commencement` runs on the host with no events. Such a statement could only add a conflict, which would refuse more, never date differently.
- **Only one host wording is dated.** Others open a region and are refused. Nested shapes that are not a host replacement are untouched: they were refused before. Examples are barnevernsloven § 18-2 nr. 16 ("I § 18-2 nr. 16 skal endringen i lov … lyde", `unsupported`) and lov/2022-06-17-45 part III ("I lovens del III skal endringen i …").
- **A region runs to the end of its part.** Both verified regions end exactly there. An unnumbered intro inside a region refuses the whole region instead of guessing a boundary.

## 8. Recommendation

**MERGE.**

- The defect is real, source-proven and history-changing. tvisteloven § 6-2 showed amended text for 13 days before it took effect.
- The fix:
  - dates only what the sources prove (1 operation);
  - refuses the rest with stated reasons (6);
  - changes no section verdict;
  - is byte-reproducible;
  - is red-first tested, on synthetic and pinned inputs;
  - passed review.
- It adds no precedence rule and does not narrow D7. It touches no public surface.

**Conditions:** the PR Pipeline (fast-ci, Test matrix, codex-tests) is green on the final head.

**Not blocking:**
- the vergemålsloven partial-decision grammar (§7), which would turn 6 refusals into 2013-07-01 dates once read;
- the mutation-harness environment leak (§6, out of scope).
