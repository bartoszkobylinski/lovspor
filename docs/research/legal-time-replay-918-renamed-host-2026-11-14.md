# #918: the list of a renamed and replaced host provision is never "straks", research, 2026-11-14

This note covers one timing defect. In `lov/2009-06-19-74`, straffeloven § 401 and § 402 are each renamed and replaced in one sentence. Each replacement ends in a list of amendments to other acts. The engine dated every operation of those lists by the member's own "Loven trer i kraft straks.", which made them 2009-06-19. #893's nesting analysis read only `replace_section` hosts, while these two are parsed as `renumber` operations.

The fix makes such a host open a nested region, as `replace_section` already does. The region is then **refused**, so its operations become `unresolved`. No new date is given. ADR-0024 R4 dates a nested operation only when the grammar proves its host provision's effective date. Here neither host's wording is the verified one, and the host's unit is not followed across the renaming.

Nothing is served, published or written into `lovverk`. The MCP, API and public JSON surfaces are untouched. No ADR changed, no act attribution changed, the R3 grammar is not extended, no provision lineage is integrated, and no corpus-wide replay was run.

The change **removes a false date and adds uncertainty**:
- 51 operations of one member change timing, from `dated 2009-06-19` to `unresolved`: 4 arbeidsmiljøloven, 10 utlendingsloven and 37 naming no act.
- No operation is added, dropped, or changes act, kind, section, `known_from` or proofs.
- D7's act-level blanket covers the same operations in every state as before.
- The four phase-1 pilots and the barnevernsloven ladder change in their version line only.

## 0. Baseline and inputs

| item | value |
|---|---|
| baseline | origin/main `f23a3c04178f861d8658cba6cc00908a7763a14b` (PR #909 merged as `db68cca1`) |
| `lovtidend-avd1-2001-2025.tar.bz2` | sha256 `d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0` |
| `lovtidend-avd1-2026.tar.bz2` | sha256 `600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2` (ADR-0019 R2 `research_only`, unchanged) |
| oracle `gjeldende-lover.tar.bz2` | sha256 `380632d48c82e48bb6fa37c6c6e6f13d55c77defa5f83400c5120ca6989593e9` |
| member | `lti/2009/nl-20090619-074.xml`, `lov/2009-06-19-74`, sha256 `7ec6a1b4ae319b86ae6735eb2e969022e827afad552c9f9e8a9e4e12db1304d4` |
| `REPLAY_VERSION` | `legaltime-replay-8` → **`legaltime-replay-9`** |
| governing record | ADR-0024 R4/R5/R7 (accepted 2026-11-12), ADR-0020 D3/D4/D7/D9, ADR-0023 R3/R7 (read, unchanged) |

The baseline was rebuilt first, as B0, from a frozen copy of `src/` at `f23a3c04`. The coverage (`70b2855c…`), the four pilots and the barnevernsloven ladder **came out byte-identical** to the committed evidence. The commands are those of the #893 note §0.

## 1. Source to host: the proof matrix

Part II of the member reads "Loven trer i kraft straks. Ikraftsetting av straffeloven 20. mai 2005 nr. 28 reguleres av § 411 i lovvedtaket del I." The header's `dateInForce` is 2009-06-19.

| | § 411 | § 412 |
|---|---|---|
| sentence (part I) | node 239, "Nåværende § 401 blir § 411 som innleder tredje del og som skal lyde:" | node 245, "Nåværende § 402 blir § 412 og skal lyde:" |
| operation | `lov/2009-06-19-74#1:I:239.0`, `renumber`, act straffeloven | `#1:I:245.0`, `renumber`, act none (#909, kept) |
| payload | `futureLegalArticle` § 411 with two ledd | `futureLegalArticle` § 412 with one ledd |
| list-announcing ledd | "… Fra den tid kapittel 16 tar til å gjelde, gjøres følgende endringer i andre lover:" | "Med virkning fra den tid hele straffeloven 20. mai 2005 nr. 28 settes i kraft, gjøres følgende endringer i annen lovgivning:" |
| what the source states the items depend on | chapter 16's commencement, a delegation to the King ("Kapittel 16 kan settes i kraft fra den tid Kongen bestemmer.") | the commencement of the whole of straffeloven |
| what the engine proves about that | chapter 16 is `unresolved`, "conflicting commencement decisions": `forskrift/2008-03-07-225` against `lov/2015-06-19-65` (phase-3 test) | the act's default is dated 2015-10-01 by `lov/2015-06-19-65`, but chapter 16 is `unresolved`, so the whole act's moment is not proven by this engine |
| wording verified against a host's commencement (#893 S27) | no (two ledd, conditional wording) | no (wording outside the grammar) |
| host's unit across the renaming | not followed (ADR-0023 lineage is not integrated) | not followed |
| result | refused: `unresolved` | inside § 411's region (see below): `unresolved` |

**Where each list ends is not stated by the source.** #909 established this: the same shape sits on both sides of node 245. So the region runs, as #893's does, from its host to the end of the part. § 411's region (nodes 241 to 1430) therefore holds § 412's renaming and its whole list, and `nesting` reports one region, refused for its wording. `nesting` does not decide which of the two lists an item belongs to. Neither list can be dated, so the uniform refusal is the honest answer.

The operations the issue reported sit at nodes 1361-1366 (arbeidsmiljøloven) and 1417-1428 (utlendingsloven). Both are after node 246, so they are items of § 412's list if that list runs to the end of the part. They are not straffeloven's: #909 binds no item to the outer act, and no member of the region has act straffeloven (pinned).

## 2. The fix

`nesting.py`: an operation of kind `replace_section` **or `renumber`** whose replacement provision's last ledd announces a list (`_LIST_INTRO`, unchanged) opens a region. A region whose host is a `renumber` is refused, with the new reason `RENAMED` ("nested amendment: host provision renamed: its unit in the host act is not followed (#918)"). The existing checks run first, so the pinned regions report `WORDING`. The refusal reaches `hosts.nested_timings` unchanged: `unresolved`, `known_from` the outer law's publication (#858), proofs the outer law's own. No date of the host, the target or the outer law is given.

`archive.py`: `collect` no longer reads host evidence (the host's enacting member and its decisions) for a region `nesting` has already refused. Such a region never consults its host (`hosts._nest_timing` returns the refusal first), so this is output-neutral: every report unaffected by the region is content-identical (§ 4). Without it, every act amended by `lov/2009-06-19-74` would read all of straffeloven's decisions for nothing.

What stays apart (ADR-0024 R1/R4):
- **the outer law's own commencement** still dates the renaming itself (`239.0` stays `dated 2009-06-19`). This is a fact about the text of § 411, not about its list;
- **the host provision's commencement** is never consulted for a refused region;
- **text existence** is unchanged: no operation moves, and `known_from` is the announcement's day.

## 3. Red first

Both runs used frozen copies of `src/`. The red run was at `f23a3c04`, the green run at the fix.

| suite | main `f23a3c04` | fix |
|---|---|---|
| `tests/unit/test_legaltime_918_renamed_host.py` (17) | 11 failed, 6 passed (the controls) | 17 passed |
| `tests/integration/test_legaltime_918_renamed_host_pinned.py` | 8 tests at red time: 5 failed, 3 passed (the source-shape tests) | 9 passed (the 9th, added after review, pins that straffeloven's own operations in the member keep 2009-06-19) |
| `tests/integration/test_legaltime_payload_list_pinned.py` (#907) | its `test_no_nesting_region_is_found_for_renaming_hosts` stated main's behaviour | replaced by `test_the_renaming_hosts_open_one_refused_region`; its act assertions are unchanged; 9 passed |

The main-side unit run defines `RENAMED` locally, because main has no such name. Every main failure is a date or a reason. A representative line: `assert datetime.date(2009, 6, 19) != datetime.date(2009, 6, 19)`.

The synthetic controls, from #893's helpers:
- a rename without payload, and a rename whose payload announces no list: no region;
- a `replace_section` host: still dated 2010-07-01 by its proven commencement;
- several items: one provenance for all;
- no host decision, no host act, and a dated host decision: always `unresolved`, never "straks" and never the host's date;
- a dated `replace_section` host and a renamed host in two members: they never share a date;
- an item naming no act: stays unbound and undated;
- an ordinary "straks" amendment outside the list keeps its date;
- no operation is dropped and none changes act or kind;
- amenders in either order and a rerun: identical timings;
- `collect`: a refused region reads no host evidence.

## 4. Audit: the eight acts, operation by operation

`docs/research/data/legal-time-timing-ledger-2026-11-14-918.json` (sha256 `c86f7487f6bcb24d977e2baf67528c4e0701a382a124808e3a48c366881a89b3`) is the machine-readable before/after ledger.

**What was compared.** The ledger compares 2,911 event records over the eight coverage acts, attributed and unattributed (main and fix events from frozen copies). The fields compared are role, kind, act, section, status, effective, known_from, reason, proofs and member.

**Results.**
- 0 records are added and 0 dropped.
- 125 records change. They are 51 distinct operations: an operation naming no act is listed in each of the three acts its member amends.
- On every changed record only `status`, `effective` and `reason` move: `dated` / `2009-06-19` / `""` becomes `unresolved` / `None` / `WORDING`.
- Every changed operation is in § 411's region, `host_op` `#1:I:239.0`.

| act | attributed operations changed | ids (`lov/2009-06-19-74#1:I:`) |
|---|---|---|
| arbeidsmiljøloven `lov/2005-06-17-62` | 4 | 1361.0, 1362.0, 1364.0, 1366.0 |
| utlendingsloven `lov/2008-05-15-35` | 10 | 1417.0, 1418.0, 1419.0, 1420.0, 1422.0, 1424.0, 1425.0, 1426.0, 1428.0, 1428.1 |
| no act (D7), in straffeloven's, arbeidsmiljøloven's and utlendingsloven's reports | 37 | 241.0 to 721.1, 1078.0, 1095.0, 1134.0, 1332.0 (full list in the ledger) |

Both reported counts (4 + 10) are confirmed exactly. No operation of any other member changed.

**Replay diffs** (rebuild B0 against rebuild E, with `replay_version` ignored):
- **arbeidsmiljøloven.** At every date the 4 move to `unresolved` (82 → 86). They come from `withheld` (274 → 270 at 2026-11-06) or from `future` at 2008-01-01. The `applied` count is unchanged at every date.
- **utlendingsloven.** 10 move to `unresolved` (371 → 377 at the end state): 6 from `applied` (176 → 170), and 4 that were refused on application anyway now carry their own timing refusal. Before the blanket, § 97 (op 1422.0) goes from `replayed` to `unresolved`, so `sections_proven_before_blanket` moves 50 → 49 of 212 and `exact_normalized` 33 → 32. § 97 had matched the oracle only because of a date the source does not prove. After the blanket the section verdicts are unchanged.
- **straffeloven** (coverage and the ladder). No attributed operation changes. The 37 unattributed operations stay under D7's blanket, and their act-level line gains the commencement reason. The ladder's withheld, applied and provision counts are unchanged.
- **D7 blanket.** In every state of every report, the set of operation ids in `act_level_problems` is identical before and after.
- **unchanged.** friskolelova, merverdiavgiftsloven, naturmangfoldloven, tvisteloven (which holds #893's dated and refused regions), barnevernsloven, and the four pilots: content-identical, version line only. Their supported verdicts are unchanged.

## 5. Committed evidence and determinism

| file | before (sha256) | after (sha256) |
|---|---|---|
| `legal-time-coverage-2026-11-14-918.json` (new) | — (`legal-time-coverage-2026-11-14-907.json` `70b2855c…` kept) | `08f0dc232dfc4fe968595f5aa6ec193ac31117bdf57e215414d57f5349393bc7` |
| `legal-time-timing-ledger-2026-11-14-918.json` (new) | — | `c86f7487f6bcb24d977e2baf67528c4e0701a382a124808e3a48c366881a89b3` |
| `legal-time-replay-2026-11-06-arveloven.json` (version line) | `88563a72…` | `a868ba3155d730e1a32108fe8c065f0b1917b66d8450f7754d0ed121d8a1756b` |
| `legal-time-replay-2026-11-06-personopplysningsloven.json` (version line) | `f80ae2e2…` | `a0db118e9bb3f662cc809febe1a27e9b084b81b95e886df5ed0d24e283e0cff3` |
| `legal-time-replay-2026-11-06-sivilombudsloven.json` (version line) | `f2f02342…` | `2ec43b012e113f1e5119b7f248eeb566f32c6b236f8cceddb8e79c0803de41ac` |
| `legal-time-replay-2026-11-06-spraklova.json` (version line) | `59ec41f8…` | `8094507126c10f90025a761d390488702e60b03aa2e438131dd7e1a227c06706` |
| `legal-time-replay-2026-11-08-barnevernsloven.json` (version line) | `eb8777ce…` | `cfae428dd3f5f63a03d063834ea4b69bf950b85753b1c61b2e7e19cf0f000686` |

The #872, #893 and #907 coverage and audit files are left as they were. No attribution audit is produced, because no act changed.

The uncommitted rebuild outputs have these after hashes:
- `cov-arbeidsmiljoloven` `f8d76ee4…`
- `cov-utlendingsloven` `75c32f87…`
- `cov-straffeloven` `2e41d4ff…`
- `ladder-straffeloven` `15899368…`

**Determinism.** Two sequential rebuilds, E and F, ran from two separate frozen copies of the fix's `src/` (tree digest `e1c5529c…` for both). Their fifteen files are identical byte for byte (E = F, the after column above). The frozen tree differs from the committed `src/` only in the comment beside `REPLAY_VERSION`, shortened afterwards for line length; no output depends on it. Input ordering is covered by `test_timings_do_not_depend_on_the_amenders_order_or_on_a_rerun`.

## 6. What stays unknowable (blockers, not guesses)

- **§ 411's items** depend on chapter 16's commencement. The engine reads it as conflicting (`forskrift/2008-03-07-225` against `lov/2015-06-19-65`, ADR-0020 D6), and the wording sits outside the verified grammar. They stay `unresolved` until a commencement for chapter 16 is proven and the wording is admitted.
- **§ 412's items** depend on "hele straffeloven" commencing. Dating them would need two things this PR may not add:
  - a verified grammar for "Med virkning fra den tid hele [act] settes i kraft". That is an R3/R4 grammar expansion;
  - a whole-act commencement that includes chapter 16, which is unresolved. The default date of 2015-10-01 is not that proof.
- **The boundary between the two lists**, and between § 412's list and the end of part I, is not stated by the source (#909). No item is assigned to one list or the other.
- **A renamed host's unit** (straffeloven § 401 → § 411) would have to be followed through ADR-0023's lineage before any host commencement could be read for it. That integration is out of scope, and `RENAMED` refuses it explicitly.
