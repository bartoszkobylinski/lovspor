# Legal-time replay, phase 2: eight harder acts (2026-11-07)

Issue #744, after PR #833 merged (`ec961d86`). Phase 2 measures how the pilot generalises:
eight acts chosen by measured amendment count and instruction diversity, replayed at a ladder of
dates, reconciled against the pinned consolidation, every refusal shaped and counted. The aim is
an honest failure taxonomy and the next limiting factor, not a green percentage. A correctly
`unresolved` amendment counts as a success of the method; a falsely replayed provision would be
its failure, and none was found.

ADR-0020 (lovspor-notebook PR #165) stays **Proposed**; nothing here is served, published or
written into `lovverk`, and `recorded_at`, `valid_at`, `get_law_at`, the MCP surface and the
public JSON are untouched.

## 1. Inputs, pinned

| Input | Role | sha256 |
|---|---|---|
| `lovtidend-avd1-2001-2025.tar.bz2` | announcements 2001–2025 (NLOD 2.0, `stated_for_file`) | `d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0` |
| `lovtidend-avd1-2026.tar.bz2` | announced-not-in-force events only (ADR-0019 R2: `research_only`) | `600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2` |
| `gjeldende-lover.tar.bz2` (2026-11-04) | oracle: current consolidation | `380632d4…93e9` (see `pins.json`) |

Replay version `REPLAY_VERSION` as in `src/lovspor/legaltime/run.py`; index version `legaltime-index-2`.
Dates replayed: 2008-01-01, 2012-01-01, 2016-01-01, 2020-01-01, 2024-01-01, 2026-11-06.

Reproduce (archives in `data/cache/legaltime/`, fetched by `lovspor ops legal-time fetch`):

```bash
for act in lov/2008-05-15-35 lov/2005-05-20-28 lov/2005-06-17-62 lov/2003-07-04-84 \
           lov/2005-06-17-90 lov/2009-06-19-58 lov/2009-06-19-100 lov/2021-06-18-97; do
  uv run lovspor ops legal-time replay --act "$act" \
    --archive data/cache/legaltime/lovtidend-avd1-2001-2025.tar.bz2 \
    --archive data/cache/legaltime/lovtidend-avd1-2026.tar.bz2 \
    --at 2008-01-01 --at 2012-01-01 --at 2016-01-01 --at 2020-01-01 --at 2024-01-01 --at 2026-11-06 \
    --oracle data/cache/legaltime/gjeldende-lover.tar.bz2 > "legal-time-replay-2026-11-07-$act.json"
done
uv run python scripts/legal_time_coverage.py legal-time-replay-2026-11-07-*.json \
  > docs/research/data/legal-time-coverage-2026-11-07.json
```

The per-act reports (0.1–4 MB each) are not committed; the coverage report they reduce to is.

## 2. Act selection (measured, not chosen by hand)

From the archive index: amenders = members whose `changesToDocuments` names the act; decisions =
resolutions whose title cites it. All 436 acts in reach (enacted 2001+, present in the oracle)
were ranked; the eight below are the top of the distribution plus the two the task named.

| Act | refid | amenders | decisions | why |
|---|---|---|---|---|
| utlendingsloven | lov/2008-05-15-35 | 215 | 2 | most amended act in reach |
| straffeloven | lov/2005-05-20-28 | 140 | 4 | named; commenced by a statute, not a resolution |
| arbeidsmiljøloven | lov/2005-06-17-62 | 125 | 1 | named; `(1)`-numbered ledd dialect |
| friskolelova | lov/2003-07-04-84 | 86 | 0 | nynorsk, dated header, no commencement resolutions |
| tvisteloven | lov/2005-06-17-90 | 79 | 10 | most partial decisions |
| merverdiavgiftsloven | lov/2009-06-19-58 | 75 | 8 | numbered items, chapter repeals |
| naturmangfoldloven | lov/2009-06-19-100 | 45 | 7 | chapter commenced separately |
| barnevernsloven | lov/2021-06-18-97 | 34 | 3 | 2021 act, partial own commencement |

## 3. What the first run showed (code of `ec961d86`)

Seven of the eight acts stopped at `unresolved_baseline`: the act's own commencement could not be
read. Five of those (utlendingsloven, arbeidsmiljøloven, tvisteloven, merverdiavgiftsloven,
naturmangfoldloven) failed on the header alone, because `Kongen bestemmer.` with a trailing period
was outside the header grammar — 675 of the 3,089 enacting members in the archives carry exactly
that string. Straffeloven's baseline stayed `pending`: it was commenced by a statute (lov 19. juni
2015 nr. 65, *straffelovens ikraftsettingslov*), not by a resolution, and the grammar only knew
resolutions. Barnevernsloven's own `Delt ikraftsetting` ("Loven trer i kraft 1. januar 2023, med
unntak av § 2-6 og § 15-7 fjerde ledd") was, correctly, refused. Friskolelova, the one act with a
dated header, replayed, but every one of its 91 sections was blanketed `unresolved` by act-level
problems (operations whose target could not be named), so it proved nothing either.

Of 2,432 operations aimed at the eight acts, 0 applied in a provable state. The refusals, shaped
and counted (`scripts/…/diag.py`, kept in the session scratchpad, is a one-off; the shapes are
quoted in §4), split into: header shapes (above); the amending laws' own clauses, where one sentence
outside the grammar unsettles every operation of that law; commencement resolutions whose body
covers several laws, or whose body names an exception; and instruction shapes the pilot's four acts
never used (numbered list items above all).

## 4. Grammar extensions, each from a counted sentence shape

Each extension below is one sentence shape seen in the archives, quoted in the regression tests
(`tests/unit/test_legaltime_phase2_commencement.py`, `test_legaltime_phase2_instructions.py`,
`test_legaltime_phase2_items.py`). None adds fuzzy matching, a guessed date or an act-specific patch;
each refuses everything it does not literally read. Commits, in order:

1. `ca5f75f5` — **commencement**: delegation headers with a trailing period, `fastsetter`,
   `Departementet bestemmer`, `Kongen avgjer` → `pending`; staggering and transitional sentences
   ("De ulike bestemmelsene kan settes i kraft til ulik tid", "gjelder bare … etter at loven har
   trådt i kraft") state no commencement; a leading `(1)` ledd number is stripped; `Lovens del I` in
   the Delt grammar; `Delvis`, `Forskrift om`, `Vedtak om` as decision titles (tvisteloven's
   resolution is "Forskrift om ikraftsetting av lov …"); a resolution commencing several laws is read
   block by block — the block whose title cites the law gives its date or scopes, a shared block
   needs its own sentence, `straks` is the resolution's own day, and a decision is the law's only
   when its title or a block title cites it (the header's changed-document list is the superset
   read from the index, because it lists every act a commenced law amends). **Instructions**: a
   leading `23.` item number no longer splits an intro; intro variants (`lov av`, `gjer ein
   følgjande endringar`, inline `oppheves § N`); heading variants; `I kapittel K skal ny § N lyde`;
   `siste punktum`; `(1)` numbered ledd (arbeidsmiljøloven's dialect); `§§ 14 og 15 oppheves` and
   numeric ranges as one repeal per section; `§ 6A-4` keys; `Tidlegare`; `strykes`, `sletta`,
   `utgår`.
2. `1239777e` — **partial decisions**: part lists in every spelling seen ("Romertall I, II og III",
   "Lovens del I og del III", "Følgende trer i kraft D: Endringsloven del I (barnevernsloven)",
   "Følgjande blir sett i kraft D: …"), a part narrowed to listed bare sections, `straks`, and a
   remainder ("De andre bestemmelsene i endringsloven trer i kraft D") covering what no other scope
   of the decision reaches. Exceptions, ledd qualifiers, sections under several parts, nested
   parentheses and a lone remainder stay foreign. The scope grammar moved to `scopes.py`.
3. `c9be0aa7` — **fail-closed fix**: a bokstav replacement whose rewritten markup does not parse
   is refused with a reason; utlendingsloven exposed a nested article inside a list item that made
   the regex rewrite raise out of the replay.
4. `7162e484` — **list items**: `nr. N` and `bokstav x` replace, insert (after the predecessor
   label) and repeal, edited as XML through lxml, with or without a ledd on one-ledd provisions;
   `Nytt kapittel 6A skal lyde:` as one insertion per provision the chapter carries.
5. `240370c8` — a sentence that only **refers** to the commencement ("… etter at loven har trådt i
   kraft") states none; a delegation always does.
6. `f01f11f9` — **part-scoped clauses** inside the amending law ("Endringene under II trer i kraft
   1. januar 2015", "Del I og III trer i kraft straks", "Loven del I til III gjelder fra den tiden
   Kongen bestemmer"), default-clause spellings ("Denne loven …", "Lova blir sett i verk …", "Loven
   her trer i kraft straks" — straffeloven's commencement statute), short-title citations whose
   dates are not commencement dates ("straffeloven 22. mai 1902 nr. 10"), section-scoped exceptions
   under a part, and amendment intros sharing an act's commencement provision.

## 5. Coverage after the extensions

Code of `8dc65359` (seven commits on `feat/744-phase2-stress`), archives as pinned in §1, replay at
2026-11-06 against the oracle of 2026-11-04. Full report: `docs/research/data/legal-time-coverage-2026-11-07.json`
(every count below, per act, plus the state ladder and the unsupported instruction shapes).

| Act | own commencement | events | dated | pending | unresolved timing | applied | unresolved ops | act-level | unsupported | unattributed | sections proven |
|---|---|---|---|---|---|---|---|---|---|---|---|
| utlendingsloven | dated 2010-01-01 | 556 | 281 | 9 | 266 | 160 | 387 | 85 | 114 | 26 | 0 of 212 |
| straffeloven | unresolved (partial own commencement) | 523 | 136 | 16 | 371 | 0 | 371 | 0 | 67 | 155 | no text |
| arbeidsmiljøloven | unresolved (partial own commencement) | 347 | 263 | 2 | 82 | 0 | 82 | 0 | 34 | 85 | no text |
| friskolelova | dated 2003-10-01 | 293 | 222 | 0 | 71 | 157 | 136 | 48 | 30 | 2 | 0 of 92 |
| tvisteloven | dated 2008-01-01 | 161 | 72 | 8 | 81 | 57 | 96 | 9 | 17 | 61 | 0 of 442 |
| merverdiavgiftsloven | dated 2010-01-01 | 324 | 160 | 0 | 164 | 103 | 220 | 42 | 49 | 0 | 0 of 206 |
| naturmangfoldloven | unresolved (partial own commencement) | 49 | 44 | 0 | 5 | 0 | 5 | 0 | 2 | 4 | no text |
| barnevernsloven | unresolved (partial own commencement) | 179 | 18 | 2 | 159 | 0 | 159 | 0 | 11 | 4 | no text |
| **total** | 4 of 8 dated | 2,432 | 1,196 | 37 | 1,199 | 477 | 1,456 | 184 | 324 | 337 | **0** |

Reading the columns: *events* are operations aimed at the act; *dated / pending / unresolved
timing* is what the commencement evidence says about each; *applied* are operations that reached
the text at 2026-11-06; *unresolved ops* are the operations that did not (timing or application);
*act-level* are failed operations with no provision to pin them to; *unsupported* are instruction
sentences outside the grammar (still counted, never guessed); *unattributed* are operations in the
amending laws that no intro assigned to any act (counted per member, invisible per provision —
see §8). *Sections proven* is the verification verdict: every replayed state carries act-level
problems, and a state with an act-level problem has every section `unresolved` by design (ADR-0020
decision 5), so no section of any phase-2 act is reported proven, even where the text agrees with
the oracle. That is the intended reading: the method refuses to certify what it cannot attribute.

Intermediate dates: the ladder in the coverage report shows the expected monotone picture for the
four replayed acts (applied grows, future shrinks, unresolved grows as later events are reached);
nothing applies before an act's own commencement (`not_yet_in_force` for utlendingsloven and
merverdiavgiftsloven at 2008-01-01). The four acts with an unresolved own commencement have no
text at any date, and 461 dated operations aimed at them are `withheld`, never applied.

Determinism: the eight reports were rebuilt twice from the same inputs and compared by sha256;
see the PR description for the digests. The four pilot evidence files reproduce byte for byte at
every commit of this branch (`tests/integration/test_legaltime_pilot.py`, 9/9 each time).

## 6. Failure taxonomy

Every failure is one of the classes below; the counts are operations across the eight acts at the
final head. Nothing in these classes is a wrong date or a wrong text: each is a refusal with its
reason recorded on the operation and, where it has one, on the provision.

**Commencement of the act itself (4 of 8 acts, no text at any date)**
- `partial commencement of the act itself` — straffeloven (chapter 16 commenced 2008 by
  FOR-2008-03-07-225, the rest 2015 by statute), arbeidsmiljøloven (FOR-2005-06-17-609 with
  exceptions), naturmangfoldloven (chapter IV commenced 2012 by FOR-2012-06-22-588),
  barnevernsloven (FOR-2022-10-14-1739: "Loven trer i kraft 1. januar 2023, med unntak av § 2-6 og
  § 15-7 fjerde ledd"). The replay's baseline is one timing for the whole act; these acts need a
  **per-section baseline**. This is the single largest limit found.
- `commencement stated in another act` — straffelovens ikraftsettingslov (lov 19. juni 2015 nr. 65)
  says "Loven her trer i kraft straks" of itself and, in another sentence, when straffeloven takes
  effect. The replay reads an amending law's clause as that law's own commencement, so the two
  sentences are `two default commencement clauses` (259 operations). A clause that commences a
  *different* act is a shape the model does not have.

**Commencement of amendments (1,199 operations unresolved on timing)**
- `partial decision outside the grammar` — 497. Mostly `Delt` sentences with exceptions
  ("Følgende trer i kraft D: Endringsloven del I (barnevernsloven) med unntak av § 3-1 …"), ledd
  qualifiers under a section, sections listed under several parts, or section lists with nested
  parentheses. Refused by design: an exception the grammar cannot scope would otherwise date the
  excepted provision.
- `two default commencement clauses` — 276 (259 of them the straffeloven case above).
- `commencement sentence outside the grammar` — 250. The remaining clause shapes after this round:
  transitional prose with a date ("For avgiftsoppgjør truffet før ikrafttredelsen … gjelder
  søksmålsfristen fra 1. januar 2011"), "Ikraftsetting av straffeloven … reguleres av § 411 i
  lovvedtaket del I" shapes, and clauses that commence the amended act rather than the amending one.
- `full decision body carries an exception` — 68; `full decision body names several dates` — 21;
  `decision body has no commencement sentence for this law` — 58 (a decision names the law among
  the documents it changes but has no block for it: usually the law is amended by the commenced
  law, not commenced itself — read as evidence that says nothing, now that the body is the source);
  `conflicting commencement decisions` — 14; `dateInForce outside the grammar` — 12.

**Instructions**
- `unsupported` — 324 of 2,432 (13%). Top remaining shapes: `§ N innledningen skal lyde:` (lead-in
  text of a list-carrying ledd), `Kapittel N skal lyde:` / `Kapittel N oppheves.` / chapter and act
  title headings, `§ N første ledd nr. N første punktum skal lyde:` (a punktum inside an item),
  `§ 67 første ledd bokstav c og d skal lyde:` (several items in one sentence), `§ 15-1 åttande
  ledd` (ordinals above the table), `I § 28-A1 …` (keys with a letter before the hyphen).
- `unattributed` — 337 operations in the amending laws that no intro sentence assigned to an act
  (155 in straffeloven's amenders). They are counted per member and never reach a provision. The
  intro shapes behind them are known ("erstattes i § N nr. N bokstav e … ordet «…» av «…»",
  "I lov … oppheves del II", intros without a date citation); whether any of them aims at one of
  the eight acts is not known, which is why a state is never reported proven while its members
  carry unattributed operations (see §8).

**Application (184 act-level, the rest per provision)**
- `target provision missing …` / `insertion point does not exist` / `target ledd range does not
  exist` — consequences: an earlier operation on the same provision was unsupported or unresolved,
  so the text the later one expects is not there. `renumbering not implied by the operation before
  it` — 24, the same mechanism. `bokstav X matched 0 list items`, `item without ledd on a
  provision with several ledd`, `punktum operation on a ledd that carries a list`, `bokstav
  replacement leaves the ledd markup ill-formed` — each a refusal where the pilot would once have
  applied or crashed.

## 7. Comparison with the four-act pilot

| | four-act pilot (2026-11-06) | eight-act phase 2 (2026-11-07) |
|---|---|---|
| acts with a dated own commencement | 4 of 4 | 4 of 8 |
| operations aimed at the acts | 36 | 2,432 |
| applied at the current date | 32 (89%) | 477 (20%) |
| pending | 3 | 37 |
| unresolved | 0 | 1,199 timing + 257 application |
| unsupported instructions | 0 | 324 (13%) |
| sections proven at the current date | 275 of 275 | 0 |

The pilot's acts were young (2018–2021 enactments), each with a handful of amenders and plain
commencement resolutions. The phase-2 acts are the most amended statutes in reach, with 34–215
amenders each, multi-law resolutions, partial own commencement and two dialects (nynorsk,
arbeidsmiljøloven's `(1)` ledd). The grammar extensions of this branch moved the eight acts from 0
applied operations and 7 unreadable own commencements to 477 applied and 4 readable; the pilot's
four evidence files did not change by a byte. The gap between 20% applied and 0 sections proven is
not a defect of the verification: it is the act-level blanket doing what ADR-0020 says it must.

## 8. The next limiting factor

1. **Commencement resolution, at section granularity.** Four of eight acts have no text at any
   date because their own commencement is partial (one decision, several dates, exceptions), and
   497 amendment operations are unresolved because their `Delt` decisions carry exceptions or ledd
   qualifiers. Both need the same thing: a baseline and a scope model that can hold one date per
   section (and, for exceptions, per ledd), not one per act or per part. ADR-0020's decision 3
   ("the state of act A at legal date D is A's enacting text, in force from A's own commencement")
   is where this would change, and it is an owner decision, not a grammar extension.
2. **Commencement stated in another act** (straffeloven's ikraftsettingslov): a clause that
   commences the amended act, not the amending one. 259 operations.
3. **Instruction grammar**: 324 unsupported sentences (13%), with the shapes listed in §6; and 337
   unattributed operations, whose intro shapes are listed too. Each is a per-pattern parser with
   a fixture, as before.
4. **Verification semantics**: once 1–3 move, the act-level blanket will still hide every section
   of a state that carries one unknown-target operation. A finer rule (which provisions an
   unsupported sentence *could* reach) is a semantics decision, and a stricter one than today's is
   the only safe direction.

Source coverage is not the limit for these acts: every member needed was in the pinned archives,
no member was skipped, and the 2026 archive contributed only announced-not-in-force events, as
ADR-0019 R2 requires.
