# Legal-time replay, phase 3: commencement per legal unit and across acts (2026-11-08)

Issue #744, after PR #833 (`ec961d86`) and PR #842 (`58fb4782`) merged and ADR-0020 was **accepted**
by the owner (lovspor-notebook PR #165, merge `1f857f22`, 2026-11-08): a separate, evidence-backed
legal-time axis with commencement modelled per legal unit (Decision 4), commencement instruments
including an instrument B for act A (Decision 5), and exceptions, staging, overlap, contradiction and
unknown scope represented and never defaulted across (Decision 6). Phase 3 implements those
decisions on two target acts and measures the result on the phase-2 set. Nothing is served,
published or written into `lovverk`; `recorded_at`, `valid_at`, `get_law_at`, the MCP surface and the
public JSON are untouched (Decision 10); the 2026 archive stays `research_only` (ADR-0019 R2,
Decision 12). Two pull requests, neither merged by the author: part 1 (grammar and structure,
PR #847) and part 2 (per-unit commencement and replay, stacked on part 1).

## 1. Inputs, pinned

| Input | Role | sha256 |
|---|---|---|
| `lovtidend-avd1-2001-2025.tar.bz2` | announcements 2001–2025 (NLOD 2.0, `stated_for_file`) | `d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0` |
| `lovtidend-avd1-2026.tar.bz2` | announced-not-in-force events only (ADR-0019 R2: `research_only`) | `600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2` |
| `gjeldende-lover.tar.bz2` (2026-11-04) | oracle: current consolidation | as pinned in the evidence files' `inputs` |

Replay version `legaltime-replay-3` (`src/lovspor/legaltime/run.py`); index version `legaltime-index-2`.
Evidence committed: `docs/research/data/legal-time-replay-2026-11-08-barnevernsloven.json` (the
staged ladder 2022-12-31, 2023-01-01, 2023-06-30, 2023-07-01, 2024-01-01, 2026-11-06; rebuilt twice,
sha256 `f27a1b110c963a94f5a2e488da281904f3617519dd2af1e3d2f22427d99a7ac0` both times) and the
phase-2 set reduced to `docs/research/data/legal-time-coverage-2026-11-08.json` (the phase-2 ladder
2008-01-01 … 2026-11-06; the eight per-act reports, 0.1–22 MB, are not committed). The four phase-1
evidence files were regenerated: every difference is the `replay_version` line and the additive
fields of §4 (`commencement`, `not_in_force`, `pending_commencement`, `unresolved_commencement`,
`ledd_counts`, `counts_before_blanket`); no date, verdict or count changed.

Reproduce (archives in `data/cache/legaltime/`, fetched by `lovspor ops legal-time fetch`):

```bash
uv run lovspor ops legal-time replay --act lov/2021-06-18-97 \
  --archive data/cache/legaltime/lovtidend-avd1-2001-2025.tar.bz2 \
  --archive data/cache/legaltime/lovtidend-avd1-2026.tar.bz2 \
  --at 2022-12-31 --at 2023-01-01 --at 2023-06-30 --at 2023-07-01 --at 2024-01-01 --at 2026-11-06 \
  --oracle data/cache/legaltime/gjeldende-lover.tar.bz2 \
  > docs/research/data/legal-time-replay-2026-11-08-barnevernsloven.json
for act in lov/2008-05-15-35 lov/2005-05-20-28 lov/2005-06-17-62 lov/2003-07-04-84 \
           lov/2005-06-17-90 lov/2009-06-19-58 lov/2009-06-19-100 lov/2021-06-18-97; do
  uv run lovspor ops legal-time replay --act "$act" \
    --archive data/cache/legaltime/lovtidend-avd1-2001-2025.tar.bz2 \
    --archive data/cache/legaltime/lovtidend-avd1-2026.tar.bz2 \
    --at 2008-01-01 --at 2012-01-01 --at 2016-01-01 --at 2020-01-01 --at 2024-01-01 --at 2026-11-06 \
    --oracle data/cache/legaltime/gjeldende-lover.tar.bz2 > "legal-time-replay-2026-11-08-<shortname>.json"
done
uv run python scripts/legal_time_coverage.py legal-time-replay-2026-11-08-*.json \
  > docs/research/data/legal-time-coverage-2026-11-08.json
```

`tests/integration/test_legaltime_phase3.py` rebuilds the barnevernsloven file from the archives and
compares it byte for byte with the committed one, and asserts the commencement facts of sections 2
and 3 of this note (the default days, § 2-6 pending, § 15-7 fjerde ledd, chapter 16 and its proofs,
the states before and after each day) directly from the archives;
`tests/integration/test_legaltime_pilot.py` still reproduces the four phase-1 files (9/9).

## 2. Target A — barnevernsloven (lov 18. juni 2021 nr. 97), staged commencement

**Evidence.** The act's header delegates (`Kongen bestemmer`). One decision cites it:
FOR-2022-10-14-1739 (`lti/2022/sf-20221014-1739.xml`, published 2022-10-14), *Delt ikraftsetting*,
body: "Loven trer i kraft 1. januar 2023, med unntak av § 2-6 og § 15-7 fjerde ledd. Loven § 15-7
fjerde ledd trer i kraft 1. juli 2023."

**Reading (Decisions 4 and 6).** The act's default commencement is `dated 2023-01-01`,
`known_from 2022-10-14`, proof the decision member and the digest of the first sentence. Two units
are dated apart: `§ 15-7 fjerde ledd` is `dated 2023-07-01` (proofs: the exception and the second
sentence); `§ 2-6` is excepted and stays `pending` — the header's delegation with the exception as
proof — because no instrument in the archives dates it. The enacting text has no § 2-6 and § 15-7
has three ledd: both units are text the amendments bring (ny § 2-6 by lov 25. mars 2022 nr. 13 del
III, whose own decision FOR-2022-03-25-465 excepts it too — "med unntak av ny § 2-6" — so the
insertion is `pending` on both counts; the fourth ledd by an amendment dated before 2023).

**States, proven before and after each date** (committed evidence; `applied` counts the
operations in the text at D, `withheld` the dated operations whose unit is not in force):

| D | status | applied | withheld | unresolved | `§ 15-7` ledd not in force | § 2-6 |
|---|---|---|---|---|---|---|
| 2022-12-31 | `not_yet_in_force` | 0 | 15 | 48 | (no text) | — |
| 2023-01-01 | `replayed` | 13 | 0 | 50 | `[4]` | absent, insertion withheld |
| 2023-06-30 | `replayed` | 14 | 0 | 51 | `[4]` | absent |
| 2023-07-01 | `replayed` | 14 | 0 | 51 | `[]` | absent |
| 2024-01-01 | `replayed` | 20 | 0 | 53 | `[]` | absent |
| 2026-11-06 | `replayed` | 101 | 0 | 66 | `[]` | absent |

The fourth ledd of § 15-7 exists in the text from 2023-01-01 (its insertion is dated before the act
commenced and applies the day the act does) and carries `ledd_not_in_force = [4]` until 2023-06-30;
on 2023-07-01 the flag is gone. An operation addressed to that ledd and dated between the two days
would be `withheld` until 2023-07-01 (unit test on the same sentences,
`tests/unit/test_legaltime_phase3_replay.py`); an operation that would renumber the flagged ledd (an
insertion or repeal below it, a section rewrite with a different ledd count) is refused with
`ledd-level commencement cannot follow a restructuring of the provision`, because the decision names
the ledd by its number.

**Reconciliation at 2026-11-06** against the pinned consolidation (Decision 8): every one of the 188
sections is `unresolved` — the act-level blanket of Decision 7, kept as decided and its refinement
deferred (Decision 13). Behind the blanket the verifier counts `exact_raw` 56, `exact_normalized` 66,
`editorial_omission` 1 (123 of 188 agree), `text_differs` 21, `unresolved` 32, `only_in_oracle` 12;
ledd 0 of 498 proven under the blanket. One operation holds the blanket from 2023-01-01 on:
`lov/2022-06-17-45#2:II:22.0`, "I § 18-2 nr. 16 skal endringen i lov 29. april 2005 nr. 20 … § 5 …" —
an amendment to the act's own closing list of amendments to other acts, `unsupported` and without a
provision to pin it to. **Sections proven: 0 of 188. Subunits proven: 0 of 498.** These are the
honest figures; the counts behind the blanket say what a refinement would be measured against.

**Unresolved timings (48 of 185 events)**: 45 `commencement sentence outside the grammar` — the
temporary Ukraine laws (lov 10. juni 2022 nr. 35, lov 25. juni 2024 nr. 53, lov 12. juni 2026 nr. 22)
whose clause repeals their own parts at a date ("Del I til X i loven oppheves 1. juli 2023", a sunset
the model does not hold, correctly refused), and lov 19. juni 2026 nr. 35 whose clause excepts units
by the amended act's short name ("Endringene i barnevernsloven § 1-2, § 5-1 første ledd bokstav e, …
trer i kraft straks"); 3 `partial decision outside the grammar` — FOR-2022-06-17-1037 (scopes stated
as the law's own "§ 6 nr. 2 om endring i lov …") and FOR-2025-06-10-967 (a prose exception,
"lovendringene om utlegg som i saker om utlegg kommer gradvis til anvendelse"). Phase 2 had 159 of 179
unresolved on timing for this act.

## 3. Target B — straffeloven (lov 20. mai 2005 nr. 28), commencement by another statute

**Evidence.** The act's header delegates. Two instruments reach its commencement:

- FOR-2008-03-07-225 (`lti/2008/sf-20080307-0225.xml`, published 2008-03-11), *Delvis ikraftsetting av
  lov 20. mai 2005 nr. 28*: "Lovvedtaket del I kapittel 16 trer i kraft straks." Chapter 16 is not in
  the enacting text; lov 7. mars 2008 nr. 4 del I inserts it ("Annen del skal lyde:" with the chapter
  titles and §§ 101–110), so the unit is reached through the act's chapter map (Decision 3a, text
  existence) and the part qualifier through the inserting part.
- lov 19. juni 2015 nr. 65 (`lti/2015/nl-20150619-065.xml`, published 2015-06-19, *straffelovens
  ikraftsettingslov*), § 1: "I lov 20. mai 2005 nr. 28 om straff skal § 411 første ledd lyde: Loven
  trer i kraft 1. oktober 2015. …"; § 8: "Loven her trer i kraft straks." — two facts about two acts
  (Decision 5), no longer `two default commencement clauses`.

**Reading (Decisions 5(d) and 6).** The act's default commencement is `dated 2015-10-01`,
`known_from 2015-06-19`, proof the instrument's member (`sha256` of the member, digest of the
sentence) — provenance names both acts, and the instrument's own commencement (`straks`, header
2015-06-19) is proven first; had it not been, the act would be `unresolved`
(`commencement instrument's own commencement is not proven`). Chapter 16 under part I is
`dated 2008-03-07` by the decision and then reached by the act-wide statement of 2015: two
instruments, two days, one unit → `unresolved: conflicting commencement decisions` with both proofs
attached. Decision 6 asserts no precedence rule, so the earlier partial commencement is recorded as a
contradiction, not as a stage. The act's own § 411 as rewritten by lov 19. juni 2009 nr. 74 reads
"Kapittel 16 kan settes i kraft fra den tid Kongen bestemmer" (read from the 2009 law's payload; the
renumbering that carries it is refused, below) — a textual carve-out a later owner ruling could use
to read the two instruments as staged. Not decided here.

**States.** 2008-03-06 and 2008-03-07: `not_yet_in_force` (default 2015-10-01), 4 then 105 dated
operations withheld; 2015-09-30: `not_yet_in_force`, 614 withheld; 2015-10-01: `replayed`, 376
applied, 343 unresolved, 128 act-level problems, 358 provisions; 2026-11-06: 463 applied, 371
unresolved, 143 act-level, 381 provisions against 400 in the oracle. **Sections proven: 0 of 400
(blanket); behind the blanket 226 of 400; ledd 0 of 700.** The full report at this ladder plus 2026-11-06 (same command as §1 with `--act lov/2005-05-20-28` and `--at 2008-03-06 --at 2008-03-07 --at 2015-09-30 --at 2015-10-01 --at 2026-11-06`) is 11.6 MB and is not committed; its sha256 is `169f7aaf6087b5f25a53a5fc4b0861a2abb45d019e4dd74412903d196eed3635`, and `tests/integration/test_legaltime_phase3.py` rebuilds the four intermediate states and asserts their status, withheld, applied and provision counts on every run against the pinned archives.

**Why the text does not reach 2015.** The 2008 and 2009 laws renumber the enacting §§ 101–102 ("§
101 i lov 20. mai 2005 nr. 28 blir § 401, og skal lyde slik:", "Nåværende § 401 blir § 411 som
innleder tredje del og som skal lyde:"). A renumbering of sections is `unresolved` by Decision 7 and
norm identity across renumbering is deferred by Decision 13, so the old § 101 keeps its key, the
chapter-16 insertions of §§ 101–110 fail on `key already present`, and the chain of later operations
fails on `target provision missing` (52), `target ledd range does not exist` (28) and `unsupported`
(74). The commencement reading does not depend on that chain: the instrument's sentence is read as a
statement about the act it is written into, wherever its § is addressed.

**Unresolved timings (105 of 850 events)**: 83 `partial decision outside the grammar`, almost all
one shape the unit grammar does not hold — scopes stated as act-named lists across several acts in
one sentence ("Loven del I, endringene i straffeprosessloven § 84, § 94, …, del III, endringene i
straffeloven § 27, § 67 …", FOR-2020-12-04-2592, 29 operations; FOR-2024-05-24-811, 15;
FOR-2012-01-20-41, 12; FOR-2024-11-29-2890, 9); 19 `commencement sentence outside the grammar`;
2 `decision body has no commencement sentence for this law`; 1 `dateInForce outside the grammar`.
Phase 2 had 371 of 523 unresolved on timing for this act and 155 unattributed operations; now 850
events (the 2008 and 2009 laws' `Lov … endres slik:` intros are read) and 106 unattributed.

## 4. What changed in the model (both PRs)

Part 1 (PR #847, grammar and structure, no replay-semantics change): provisions carry their
enclosing chapter and part (`Provision.chapter`, `Provision.part`); the intro `Lov … endres slik:`;
`Annen del skal lyde:` / `Nye kapitler X til Y plasseres i … del, og skal lyde:` / `Nytt kapittel 9 A
skal lyde:` as chapter-stamped insertions; a commencement provision that amends another act
contributes no clause of its own; the unit grammar (`units.py`: section, ledd, punktum, bokstav or
item, heading, chapter; `covers` / `spans` / `disjoint` / `unknown` against an operation's address;
chapter units placed through the act's own chapter map); scopes with exceptions and narrowing in the
decisions' own shapes (`scopes.py`); decision bodies printed as a lead-in with a list read one item
per statement; `§ 9 A-1` keyed `§9A-1`; two phase-2 refusals that are units now (`med unntak av ny
§ 2 a`, `§ 1 annet ledd`).

Part 2 (stacked): `baseline.py` folds the act's commencement per unit (`Commencement`: a default
`Timing` plus `UnitTiming`s) from its header, the decisions citing it and the statements other
instruments write into it, in publication order, with the fold rules of Decision 6 (same day: all
cited; different days: `conflicting commencement decisions`; an excepted unit keeps the header's
state; an unreadable scope unsettles every unit); `replay.py` gates every dated operation on its
target unit's effectiveness at D (`withheld`, or `unresolved` with the unit's reason), keeps
ledd-level flags and refuses restructurings under them; each provision carries `in_force`,
`effective` and `ledd_not_in_force`; each state lists `not_in_force`, `pending_commencement`,
`unresolved_commencement`; `verify.py` adds the class `not_in_force` (text exists at D, no legal
effect yet), ledd counts per class, and `counts_before_blanket`; `run.py` writes the `commencement`
record and bumps `REPLAY_VERSION`; two more instruction shapes (a range shift after a first-ledd
rewrite is an insertion; `Ordet «X» skal endres til «Y» i § …` is a scoped substitution). Operation
kinds, the act-level blanket, `recorded_at`/`valid_at`/`get_law_at` and the public surfaces are
unchanged.

## 5. Phase-2 set regenerated on the phase-3 code

Code of part 2's head, archives as pinned, replay at the phase-2 ladder, oracle of 2026-11-04. Full
reduction: `docs/research/data/legal-time-coverage-2026-11-08.json`.

| Act | own commencement | events | dated | pending | unresolved timing | applied | unresolved ops | act-level | unsupported | unattributed | proven | behind blanket |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| utlendingsloven | dated 2010-01-01 | 556 | 306 | 9 | 241 | 176 | 371 | 93 | 114 | 5 | 0 of 212 | 50 of 212 |
| straffeloven | **dated 2015-10-01** (B); kap. 16 unresolved | 850 | 729 | 16 | 105 | 463 | 371 | 143 | 92 | 106 | 0 of 400 | 226 of 400 |
| arbeidsmiljøloven | unresolved | 356 | 272 | 2 | 82 | 0 | 82 | 0 | 31 | 10 | no text | — |
| friskolelova | dated 2003-10-01 | 300 | 232 | 0 | 68 | 168 | 132 | 47 | 29 | 2 | 0 of 92 | 34 of 92 |
| tvisteloven | dated 2008-01-01 | 163 | 111 | 8 | 44 | 94 | 61 | 8 | 17 | 7 | 0 of 442 | 296 of 442 |
| merverdiavgiftsloven | dated 2010-01-01 | 324 | 162 | 0 | 162 | 105 | 218 | 42 | 49 | 0 | 0 of 206 | 89 of 206 |
| naturmangfoldloven | unresolved | 49 | 44 | 0 | 5 | 0 | 5 | 0 | 2 | 4 | no text | — |
| barnevernsloven | **dated 2023-01-01**; § 2-6 pending; § 15-7 ledd 4 2023-07-01 | 185 | 134 | 3 | 48 | 101 | 66 | 10 | 9 | 4 | 0 of 188 | 123 of 188 |
| **total** | **6 of 8 dated** | 2,783 | 1,990 | 38 | 755 | 1,107 | 1,306 | 343 | 343 | 138 | **0** | 818 of 1,540 |

Every count change against phase 2 (2,432 events; 1,196 / 37 / 1,199; 477 applied; 184 act-level;
324 unsupported; 337 unattributed; 4 of 8 dated; 0 proven), explained:

- **events +351**: straffeloven +327 (the `Lov … endres slik:` intro — the 2008 and 2009 laws alone
  carry 100 and 214 operations aimed at the act — and the `Annen del skal lyde:` / `Nye kapitler …`
  insertions), arbeidsmiljøloven +9, friskolelova +7, barnevernsloven +6, tvisteloven +2, all from the
  same intro shape; utlendingsloven, merverdiavgiftsloven and naturmangfoldloven unchanged.
- **unattributed −199** (337 → 138): the same intro, per act straffeloven 155 → 106, arbeidsmiljøloven
  85 → 10, tvisteloven 61 → 7, utlendingsloven 26 → 5; the 138 left are intro shapes still outside the
  grammar (listed per member in the coverage report).
- **dated +794, unresolved timing −444**: partial decisions with exceptions and unit lists now read
  (barnevernsloven 159 → 48, straffeloven 371 → 105, utlendingsloven 266 → 241, tvisteloven 81 → 44,
  merverdiavgiftsloven 164 → 162, friskolelova 71 → 68); the 755 left are the shapes of §2 and §3 and,
  for arbeidsmiljøloven and naturmangfoldloven, their own commencement (below).
- **own commencement 4 → 6 of 8**: straffeloven by Decision 5(d), barnevernsloven by Decisions 4 and
  6. arbeidsmiljøloven's FOR-2005-06-17-609 states "trer i kraft fra 1. januar 2006 med følgende unntak
  og presiseringer:" followed by a dated ledd, a dated ledd exception and a person-conditional
  exception ("For arbeidstakere som … har en særlig uavhengig stilling skal … lovens kapittel 10 tre i
  kraft 1. januar 2011") that no unit can hold — refused whole, correctly. naturmangfoldloven's
  FOR-2009-06-19-704 states the act "med unntak av kapittel IV i loven" with the act cited by title,
  and FOR-2015-12-18-1583 commences "Kapittel IV samt § 78 nr. 8 og den delen av § 78 nr. 7 som gjelder
  oppheving av viltloven § 47" — prose scope, refused whole. Both are the next sentence shapes.
- **applied +630** (477 → 1,107): the two newly dated acts (straffeloven 463, barnevernsloven 101)
  and more dated operations on the others (utlendingsloven 160 → 176, friskolelova 157 → 168,
  tvisteloven 57 → 94, merverdiavgiftsloven 103 → 105).
- **act-level +159, unsupported +19**: operations that are now attributed and dated reach the text
  and fail there (straffeloven alone 128 at 2015-10-01: the renumbering chain of §3). Unsupported
  sentences are counted per shape in the coverage report.
- **sections proven 0 → 0**: every replayed state still carries at least one act-level problem, and
  Decision 7's blanket stands. `counts_before_blanket` (818 of 1,540 sections agree with the oracle
  at 2026-11-06 behind it) is reported so the blanket's cost is visible, never to replace the verdict.
- phase-1 acts: unchanged apart from the version line and the additive fields (§1).

**Reconciliation against the pinned consolidation (oracle 2026-11-04, replay at 2026-11-06), by
verdict.** The final verdict is `unresolved` for every section of every replayed act: each state
carries an act-level problem and Decision 7's blanket covers the whole act. The split below is
`counts_before_blanket`, what the same comparison says section by section with the blanket lifted,
reported so the blanket's cost is visible. Proven = `exact_raw` + `exact_normalized` +
`editorial_omission`; `only_in_oracle` is a section the oracle has and the replay lacks (unattributed
or unsupported insertions), `only_in_replay` the reverse, `oracle_annex` a non-section oracle
heading (tvisteloven's and utlendingsloven's annex lists). Two acts have no row: arbeidsmiljøloven
and naturmangfoldloven have no readable commencement, so no text to compare.

| Act | sections | exact_raw | exact_normalized | editorial_omission | text_differs | only_in_oracle | only_in_replay | oracle_annex | unresolved | proven before blanket | proven final | ledd proven |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| utlendingsloven | 212 | 17 | 33 | 0 | 5 | 70 | 0 | 15 | 72 | 50 of 212 | 0 of 212 | 0 of 461 |
| straffeloven | 400 | 97 | 129 | 0 | 34 | 19 | 0 | 0 | 121 | 226 of 400 | 0 of 400 | 0 of 700 |
| friskolelova | 92 | 24 | 10 | 0 | 9 | 22 | 1 | 0 | 26 | 34 of 92 | 0 of 92 | 0 of 186 |
| tvisteloven | 442 | 107 | 189 | 0 | 17 | 7 | 0 | 88 | 34 | 296 of 442 | 0 of 442 | 0 of 1,002 |
| merverdiavgiftsloven | 206 | 55 | 33 | 1 | 28 | 18 | 6 | 0 | 65 | 89 of 206 | 0 of 206 | 0 of 370 |
| barnevernsloven | 188 | 56 | 66 | 1 | 21 | 12 | 0 | 0 | 32 | 123 of 188 | 0 of 188 | 0 of 498 |
| **total** | 1,540 | 356 | 460 | 2 | 114 | 148 | 7 | 103 | 350 | **818 of 1,540** | **0 of 1,540** | **0 of 3,217** |

Ledd are counted under the section's verdict (`ledd_counts`): with the blanket every ledd is
`ledd_unresolved`, so no subunit is proven either; the ledd denominators are the oracle's ledd per
act. The per-section `unresolved` before the blanket is a section whose own operations carry an
unresolved timing or an unresolved renumbering, not the act-level reason.

## 6. The next limiting factors

1. **The act-level blanket** (Decision 7, refinement deferred by Decision 13): one unsupported
   operation without a provision to pin to turns every section of a state `unresolved`. On the two
   target acts the blanket holds from a single operation each at the early dates. A finer rule — which
   provisions an unsupported sentence *could* reach — is the semantics decision the ADR deferred, and
   a stricter one than today's is the only safe direction.
2. **Section renumbering** (Decision 7, Decision 13): straffeloven's text cannot pass 2008 without
   it. Whether a literal "§ 101 blir § 401" may move a key in the replay, and what that means for norm
   identity, is an owner decision.
3. **Act-named scopes across several acts in one sentence** ("Loven del I, endringene i
   straffeprosessloven § 84, …, del III, endringene i straffeloven § 27 …"): 83 of straffeloven's 105
   unresolved timings, and the amending law's own clause in the same shape (lov 19. juni 2026 nr. 35).
   A unit grammar keyed by the amended act's short name, resolved through the intro that names it.
4. **Chapter 16 of straffeloven**: contradictory under Decision 6 as written; the act's own § 411
   carve-out is evidence for a staged reading, which only an owner ruling can adopt.
5. The two remaining unreadable own commencements (arbeidsmiljøloven, naturmangfoldloven), the
   temporary laws' sunset clauses, and the instruction shapes listed per act in the coverage report.
6. **A part named without units of the act in the act's own commencement** ("Loven del I trer i
   kraft …", "Endringsloven del I § 2 …"): the sentence does not say whose part — the act's own,
   which the structure could place, or the amending law's, which only its operations could — so
   the fold refuses the whole decision
   (`partial decision names a part that cannot be placed against the act's units`). None of the eight
   acts' own commencements has the shape; placing it needs a grammar that tells "Loven(s) del" from
   "Endringsloven del", and for the act's own part a rule mapping a part to its chapters (independent
   review of #848, 2026-11-09).

Source coverage is not the limit: every member needed was in the pinned archives, no member was
skipped, and the 2026 archive contributed only announced-not-in-force events.
