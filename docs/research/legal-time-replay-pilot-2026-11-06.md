# Legal-time replay pilot — four acts from Norsk Lovtidend avd. I (2026-11-06)

Issue #744. Proposed ADR: lovspor-notebook `docs/adr/ADR-0020-legal-time-replay-from-lovtidend.md`
(status Proposed; it decides nothing until the owner accepts it). Code: `src/lovspor/legaltime/`,
operator commands `lovspor ops legal-time fetch|replay`. Evidence files:
`docs/research/data/legal-time-replay-2026-11-06-{arveloven,spraklova,sivilombudsloven,personopplysningsloven}.json`,
each the byte-identical output of one `replay` command over the pinned archives below, reproduced by
`tests/integration/test_legaltime_pilot.py` wherever the archives are cached.

Everything below is counted from those files or from the archive index; nothing is read from a README.

## 1. Source legality (Gate A)

Read on 2026-11-06 (see `docs/legal-and-sources.md`, "Norsk Lovtidend avd. I"):

| Fact | Where it says so |
|---|---|
| The archives are served by the publicData `/list` and `/get/` endpoints | `GET https://api.lovdata.no/v1/publicData/list` names `lovtidend-avd1-2001-2025.tar.bz2` (69,320,068 B, lastModified 2026-11-03T02:31:00Z) and `lovtidend-avd1-2026.tar.bz2` (1,663,298 B, 2026-11-06T02:30:00Z) |
| Reuse terms | `api.lovdata.no/om-api-tjenesten/`: "Datasettet til Norsk Lovtiden avd. 1 finner du på Data.norge.no … Det kan fritt benyttes til alle formål uten vederlag. Dataene er lisensiert under NLOD 2.0." |
| Contents | `api.lovdata.no/publicData`: "offisielle kunngjøringer av grunnlovsbestemmelser, lover, landsdekkende og lokale forskrifter, ikrafttredelses- og delegeringsvedtak, skatte-, avgifts- og tollvedtak m.m."; "komplett fra 1. januar 2001 og frem til dags dato" |
| Catalogue record | data.norge.no `c0c6a87c-f597-3735-965f-650be23426a0`, publisher Stiftelsen Lovdata, licence NLOD 2.0, direct download = the `/get/lovtidend-avd1-2001-2025.tar.bz2` URL, daily updates |
| Not found | an attribution wording specific to the series (the FAQ question exists, its answer was not retrievable); the 2026 file is described but not named on data.norge.no, so ADR-0019 R2 stands |

Posture taken: the 2001–2025 archive is the replay input (`stated_for_file` in
`src/lovspor/crosswalk/rights.py`); the 2026 archive is read only to detect announced events, and in
this pilot every event from it is `pending`. No lovdata.no HTML, no Lovdata Pro. Commencement dates
come only from the archives; Lovdata's `changesToParent` notes in the consolidation are a cross-check,
never an input.

## 2. Pinned inputs

Downloaded 2026-11-06 with `lovspor ops legal-time fetch` semantics (publicData `/get/`), kept in the
gitignored `data/cache/legaltime/`:

| File | sha256 | Members |
|---|---|---|
| `lovtidend-avd1-2001-2025.tar.bz2` | `d55f8a382bba3c0827da3df6041ed6e47fca855fbf685155abcf5d794fed0cb0` | 38,182 XML |
| `lovtidend-avd1-2026.tar.bz2` | `600c5e4191efa1c36190df66f967a14333111518bae48273abfea39c0c18f6d2` | 1,204 XML |
| `gjeldende-lover.tar.bz2` (oracle only, lastModified 2026-11-04) | `380632d48c82e48bb6fa37c6c6e6f13d55c77defa5f83400c5120ca6989593e9` | 753 acts |

Every evidence file repeats these pins under `inputs`, every member it used under `members` (path,
sha256, and for an amending law the count of operations it states without a recognisable act),
every member the index could not read under `archives[].skipped_members` (none in these archives),
and the replay parser version `legaltime-replay-2`. A timing's proof names the member and the
sha256 of the sentence it was read from; the sentence stays in the archive. Instruction addresses
("§ 89 skal lyde:") are carried verbatim; provision text never is.

## 3. What the archives hold (measured)

- Member naming `lti/<year>/nl-YYYYMMDD-NNN.xml` (law announcements) and `sf-YYYYMMDD-NNNN.xml`
  (regulations, commencement decisions, delegations). Same HTML-in-XML dialect as `gjeldende-lover`;
  `safe_parser()` parses every member tried.
- Structured change addresses (`article.change data-change-part="lov/…/§75/ledd/1/setning/3"`) appear
  on law members from 2022 (1 of 122), 2023 (32/116), 2024 (80/100), 2025 (117/130), 2026 (67/77).
  Before that the prose instruction is the only address.
- 12,554 instruction paragraphs over 2019–2026 fall into 2,935 distinct normalised shapes; the 60
  most frequent cover about 61 %.
- The archive year is the document's own date, not its publication: 22 `sf-` members under
  `lti/2025/` carry a `dateOfPublication` in 2026 (latest 2026-06-23). The report's per-archive
  `horizon` is therefore the latest publication time in the archive, read from the members, never
  from the filename.
- Reach: 436 of 753 current acts (57.9 %) were enacted on or after 2001-01-01 and each has its
  enacting announcement in the archive; 317 acts have no replayable baseline in a permitted source.
  Amending-law counts among the 436: 154 none, 70 one, 32 two, 25 three, 22 four, 17 five, 11 six,
  34 twenty or more.

## 4. Acts and why

| Act | Enacted | Sections | Amending laws | Decisions | Chosen for |
|---|---|---|---|---|---|
| arveloven, `lov/2019-06-14-21` | 2019, in force 2021-01-01 by `res. 28 feb 2020 nr. 200` | 183 | 6 | 5 | #744's candidate: delegated commencement, a partial decision (`Delt ikrafttredelse`, ny § 99 a → 2022-01-01), a global term substitution (`fylkesmannen` → `statsforvalteren`, 15 sections), punktum-level and ledd-level operations, a 2026 amendment not yet in force |
| språklova, `lov/2021-05-21-42` | 2021, in force 2022-01-01 by `Ikrafttreden av lov …` | 22 | 2 | 2 | nynorsk drafting, an inline bokstav replacement, a repeal with renumbering, a closing "Endringar i andre lover" section |
| sivilombudsloven, `lov/2021-06-18-121` | 2021, in force 2021-07-01 | 35 | 2 | 2 | a punktum repeal with renumbering; an amendment carried inside another new act's closing section (arkivlova § 25) whose commencement is a full decision later narrowed by a `Delt` decision |
| personopplysningsloven, `lov/2018-06-15-38` | 2018, in force 2018-07-20 by `Meddelelse om ikrafttredelse …` | 35 (+99 annexed regulation articles in the consolidation) | 2 | 4 | a conditional first decision ("Når personvernforordningen trer i kraft") superseded by a dated one; a two-date `Delt ikraftsetting` scoped by part and section; an annex the replay must not be charged with |

## 5. Replay coverage and end-state reconciliation (Gate C, at 2026-11-06)

| Act | Oracle sections | exact_raw | exact_normalized | editorial_omission | text_differs | unresolved | pending ops | future ops | notes ⇄ events |
|---|---|---|---|---|---|---|---|---|---|
| arveloven | 183 | 55 | 127 | 1 (§ 181, `– – –`) | 0 | 0 | 3 (lov 19 juni 2026 nr. 48, "Kongen bestemmer") | 0 | 31 noted sections, all with a replay event; no event without a note |
| språklova | 22 | 9 | 12 | 1 (§ 22, closing amendment list) | 0 | 0 | 0 | 0 | agree |
| sivilombudsloven | 35 | 16 | 19 | 0 | 0 | 0 | 0 | 0 | agree |
| personopplysningsloven | 35 (+99 annex) | 10 | 24 | 1 (§ 34, closing amendment list) | 0 | 0 | 0 | 0 | agree |

Verdict classes: `exact_raw` = heading and ledd markup byte-identical; `exact_normalized` = identical
text after Lovdata's editorial markup (cross-reference `<a>` links, `ul`→`ol` list markup, footnote
marks, `changesToParent` notes) is set aside; `editorial_omission` = the consolidation prints
`– – –` or drops a closing "Endringer i andre lover" list where the replay carries the announced
text; `oracle_annex` = the consolidation's appended regulation articles (`gdpr/a1` …), which are not
provisions of the act.

Operations applied at the end state: arveloven 19 of 22 events (3 pending), språklova 8 of 8,
sivilombudsloven 3 of 3, personopplysningsloven 2 of 2. Operation kinds exercised on real data:
replace_section, insert_section, replace_ledd, insert_ledd (including the "§ 118 syvende ledd skal
lyde" + "Nåværende syvende ledd blir nytt åttende ledd" insertion shape), replace_punktum,
insert_punktum, replace_bokstav (inline payload), repeal_ledd, repeal_punktum, move_ledd,
substitute_term, renumber.

**What the end-state match does and does not say.** It says the replay reaches the same text as
Lovdata's consolidation for every section of the four acts at 2026-11-06, with every difference
classed and counted. It does not certify any intermediate state. Those are covered by:

- the state ladder in each evidence file (`states[]`), where the number of `text_differs` sections
  at an earlier date equals the number of sections later amended (arveloven at 2021-01-01: 21
  differ, 2 only in oracle = § 88 a and § 99 a not yet inserted; at 2021-06-01 after the term
  substitution: 7; at 2023-10-01: 0);
- the day/day-before readback (`test_day_before_each_event_differs_only_in_its_targets`): for every
  dated event of arveloven, the state the day before differs from the state on the day in exactly
  the sections the event targets;
- unit tests for same-day ordering (publication time, then document order), retroactive dates
  (`known_from` later than `effective`, applied at the legal day), partial decisions dating one part
  while the rest stays pending, future events listed and not applied, and every fail-closed path.

## 6. Unresolved cases, counterexamples and limits

- **Pending, correctly.** `lov 19 juni 2026 nr. 48` amends arveloven §§ 103 and 178; its header is
  "2026-06-19, Kongen bestemmer", its clause puts only part V (rekonstruksjonsloven) in force at
  once. The replay dates nothing for arveloven, lists the three operations as `pending`, and the
  consolidation's own notes say the same ("i kraft fra den tid Kongen bestemmer").
- **Grammar refusals seen on the way** (each fixed by a per-pattern parser with a fixture, none by
  a heuristic): a replacement followed by a shift renumbering is an insertion (arveloven § 118);
  "Ikrafttreden" and "Meddelelse om ikrafttredelse" are decision titles; a `Delt` decision may date
  several parts and name the amended act's section; a `likevel` exception may be scoped by a named
  act and section ("Endringene i offentleglova § 5 i del I i loven trer likevel i kraft straks"); a
  new act's closing section carries amendments to other acts in 2025 change-block markup; the
  sentence after an enumerated list is a sibling paragraph in Lovtidend and a `leddfortsettelse`
  inside the ledd in the consolidation (språklova § 3).
- **Still refused, by design.** Chapter and title replacements ("Kapittel 15 skal lyde:", "Lovens
  tittel skal lyde:") are recorded as `unsupported` act-level operations; a punktum operation on a
  ledd that carries a list; any commencement sentence outside the grammar; partial commencement of
  an act's own sections (`unresolved_baseline`). None of these occurred in the four acts' effective
  events; they will on other acts, and the counts will show it.
- **Decision contradiction not detected.** arkivlova has a full decision (2026-01-01) followed by a
  `Delt iverksetjing` that lists §§ 1–10 and 12–25; consistent here, but a later partial decision
  that narrowed an earlier full one would not be noticed because only still-pending operations
  are re-dated. Open question in ADR-0020.
- **Reach.** 317 of 753 current acts have no enacting announcement in the archives. For them the
  outcome is `no_baseline`; the replay never approximates from the current text.

## 6a. Independent review before the pipeline verdict

A reviewer with no part in the build read the package and reported 10 red and 24 amber findings.
The substantive ones were fail-open paths and are fixed in the same PR, each with a test: a ledd
range repeal deleted one ledd; a moved ledd ignored its destination ordinal; a global term
substitution ignored the ledd qualifier of a location and replaced inside longer words and inside
markup; a replacement text headed with another section was applied; a default commencement clause
with a reservation ("… unntatt § 2 a") dated everything; a dated sentence the grammar did not know
("Loven får virkning fra …") was treated as prose and the header date used; two default clauses
overwrote each other; an unparseable archive member vanished from the index; two enacting members
for one act were chosen between; "I lov … oppheves." was dropped; a commencement sentence among
instructions became an unsupported operation; dated events before the act's own commencement
fell in no bucket (now `withheld`); the report order depended on argument order; the preposition
"i" counted as a section letter; ledd text with `&` or `<` produced ill-formed markup; a
non-adjacent "første og tredje ledd" was applied to the first ledd only (now refused). Two findings
were left as documented limits: `editorial_omission` cannot verify the omitted tail against anything,
and a later partial decision narrowing an earlier full one is not detected (ADR-0020 open question).

## 7. Reproducibility

- `lovspor ops legal-time replay` with the same `--act`, `--archive` and `--at` arguments over the
  pinned archives emits byte-identical JSON (unit test on the synthetic act; integration test over
  the real archives against the committed evidence files).
- Header indexes are cached beside the archives under the archive's sha256 and `INDEX_VERSION`; a
  stale or foreign cache is rebuilt, never trusted.
- The report pins inputs and members by digest and quotes the clause each date was read from with
  the member it came from.

## 8. Next

See ADR-0020 "Follow-Up Work": a second wave on acts with 7–20 amending laws to measure the long
tail of instruction shapes, and a separate ADR if the axis is ever to be served.
