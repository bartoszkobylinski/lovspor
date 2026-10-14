# Source-backed legislative crosswalk — 20-act manual feasibility audit

**Date:** 2026-10-14 (Europe/Oslo)  
**Scope:** 20 distinct **amending law instruments**, seven calendar years (2015, 2016, 2022–2026), 18 distinct Storting cases (`sak_id`).  
**Tracking:** [Lovspor #610](https://github.com/bartoszkobylinski/lovspor/issues/610), [ADR-0017](https://github.com/bartoszkobylinski/lovspor-notebook/blob/main/docs/adr/ADR-0017-source-backed-legislative-crosswalk.md), [Politik #177](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/177).  
**Machine-readable sample:** [CSV](data/legislative-crosswalk-20-case-audit-2026-10-14.csv).

> This is a **purposeful, source-first feasibility sample**, **not** a random or representative sample of all Norwegian amendments. The denominator is 20 hand-selected sanctioned acts found on official Storting case pages. **Do not extrapolate its 20/20 result to national coverage.** No Lovdata HTML was ingested or scraped.

## How to read the result

Treat the crosswalk as **four separately validated joins**, not one generic “verified” flag:

- **E1 — Act identity from Storting**: the official *sak* page pairs `Sanksjonert DD.MM.YYYY (lovNN)` with a particular `Lovvedtak NN (YYYY–YYYY)`. The sample CSV normalizes the date and `lovNN` into a **candidate** `LOV-YYYY-MM-DD-NN` identity. The Storting case page is official and directly attests the sanction/date/number pairing. Normalization **does not substitute for an independent Lovdata publicData document identity check**.
- **E2 — Parliamentary case**: `sak_id` comes directly from the official `Saker/Sak/?p=N` page carrying those exact references. The same official page attests `Lovvedtak ↔ sak_id`; it may describe *many* separate enacted statutes.
- **E3 — Voting overview**: the official *sak* page links a **case-level** `Voteringsoversikt`. The linked overview was separately opened and inspected in **6 distinct cases / 8 rows** (because two cases in that inspected subset cover two acts each). Other 12 rows have the link visible on the official case page, **but the detailed page was not reviewed**. Vote counts in the table below refer to the checked **case's first-reading vote overview**, not votes on a specific LOV or paragraph.
- **E4 — Authenticated structured API + Lovdata legal provenance**: **not completed**. No raw `data.stortinget.no/eksport/sak` case XML or independent `api.lovdata.no/v1/publicData/` source snapshots were obtained in this environment. Thus **0/20 meet the intended production-grade two-source, byte-hashed, reproducible API criterion**. PR #614 implements the source-capture logic but has only synthetic API-shaped offline fixtures.

Statuses are about **evidence actually examined**, not an assertion about legal validity. A sanction date is **not** an in-force date, and a case vote is **not** a legislator's vote on a particular provision.

## Results

| Test | Observed | What it proves |
| --- | ---: | --- |
| Distinct amending-law instruments | **20** | Sample size, not an estimated population size. |
| Distinct Storting `sak_id` values | **18** | At least one many-acts-per-case pattern exists. |
| E1+E2 explicit official Storting case-page sanction → Lovvedtak → case pair | **20/20** | Direct source presentation in this **selected** set, not generalized API coverage. |
| Cases where actual voting-overview body was read (E3) | **6/18** | Official numbered case divisions exist for these cases. |
| Rows whose case voting-overview body was read | **8/20** | They share the same case-level overview when `sak_id` repeats. |
| Rows with voting-overview link visible in case page | **20/20** | A navigable link exists; it does **not** demonstrate all divisions/ballots were retrieved. |
| Rows in a **multi-act** parliamentary case | **11/20** | Non-injective `sak_id → law`; independent law/decision identifiers are mandatory. |
| Independent raw Storting XML and Lovdata publicData cross-validation (E4) | **0/20** | Cannot assert a functioning production crosswalk or a general coverage rate. |
| Validated per-representative, **paragraph-specific** vote attribution | **0/20** | Such an assertion needs separately sourced division scope / ballot evidence. |

## The 20 source-backed cases

Each `LOV-...` is a **normalized identity derived from the sanction entry on the linked official Storting case page**. It is **not yet an independently checked Lovdata API object**. `Yes` in the “Overview” column means the actual numbered voting page was opened; `Link` means the link was found but the page itself was not opened in this audit.

| # | Normalized amended-law ID | Lovvedtak (session: #) | `sak_id` | Case shape | Vote overview |
| ---: | --- | --- | ---: | --- | --- |
| 1 | `LOV-2015-12-18-122` | 2015-2016: 13 | [63056](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=63056) | multi-act | [Yes, 5 case votes](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=63056) |
| 2 | `LOV-2015-12-18-123` | 2015-2016: 14 | [63056](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=63056) | multi-act | [Yes, 5 case votes](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=63056) |
| 3 | `LOV-2015-12-18-125` | 2015-2016: 21 | [63655](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=63655) | single-act | Link |
| 4 | `LOV-2016-05-27-15` | 2015-2016: 64 | [64495](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=64495) | single-act | Link |
| 5 | `LOV-2016-06-17-57` | 2015-2016: 104 | [65616](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=65616) | single-act | Link |
| 6 | `LOV-2016-06-17-62` | 2015-2016: 107 | [64513](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=64513) | single-act | Link |
| 7 | `LOV-2016-06-17-32` | 2015-2016: 110 | [65608](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=65608) | multi-act | Link |
| 8 | `LOV-2022-06-17-63` | 2021-2022: 85 | [89016](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=89016) | single-act | [Yes, 2 case votes](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=89016) |
| 9 | `LOV-2022-12-20-117` | 2022-2023: 15 | [88530](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=88530) | multi-act | Link |
| 10 | `LOV-2022-12-20-102` | 2022-2023: 28 | [90787](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=90787) | multi-act | Link |
| 11 | `LOV-2023-12-20-96` | 2023-2024: 24 | [95560](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=95560) | multi-act | Link |
| 12 | `LOV-2024-06-25-61` | 2023-2024: 97 | [98917](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=98917) | multi-act | Link |
| 13 | `LOV-2024-12-20-84` | 2024-2025: 15 | [100212](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=100212) | multi-act | Link |
| 14 | `LOV-2025-06-20-48` | 2024-2025: 92 | [102903](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=102903) | multi-act | [Yes, 2 case votes](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=102903) |
| 15 | `LOV-2025-06-20-53` | 2024-2025: 98 | [102903](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=102903) | multi-act | [Yes, 2 case votes](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=102903) |
| 16 | `LOV-2026-05-07-17` | 2025-2026: 34 | [104478](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=104478) | single-act | Link |
| 17 | `LOV-2026-06-19-54` | 2025-2026: 75 | [200266](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=200266) | single-act | [Yes, 2 case votes](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=200266) |
| 18 | `LOV-2026-06-19-59` | 2025-2026: 67 | [200250](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=200250) | single-act | Link |
| 19 | `LOV-2026-06-23-73` | 2025-2026: 84 | [200235](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=200235) | single-act | [Yes, 3 case votes](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=200235) |
| 20 | `LOV-2026-06-23-65` | 2025-2026: 91 | [200312](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=200312) | multi-act | [Yes, 8 case votes](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=200312) |

### Critical observations and negative-scope controls

**A. One case is not one statute.** [Sak 63056](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=63056) pairs **two** sanctioned acts (`LOV-2015-12-18-122` and `LOV-2015-12-18-123`) with **two** separately numbered Lovvedtak, but its [voting overview](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=63056) contains **five** voting items, including different proposals and whole-law votes. Linking all five to *both* acts would be false attribution. Other observed multi-act cases include [2016 sak 65608](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=65608) (12 sanctioned acts), [2022 sak 90787](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=90787) (10), [2025 sak 102903](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=102903) (10) and [2026 sak 200312](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=200312) (6).

**B. A case-level division is not an individual paragraph vote.** [Sak 89016](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=89016) explicitly maps `LOV-2022-06-17-63` to `Lovvedtak 85 (2021–2022)` and records [two votes](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=89016) on the recommendation and the entire act. Those votes do **not** establish who voted for each separate change to forvaltningsloven §§ 10 and 46. In [sak 200312](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=200312), [eight case votes](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=200312) mix rejected alternatives, parts identified by section and an overall multi-act adoption vote. A reliable product must retain both **vote object** and **scope text**, and leave act/paragraph matching unresolved until an explicit law-part cross-reference exists.

**C. More than two readings can matter.** [Sak 200235](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=200235) has **three readings** and a `Lovanmerkning 3 (2025–2026)` plus the sanctioned `LOV-2026-06-23-73`/Lovvedtak 84. The [first-reading vote page](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=200235) lists three votes; the case page also separately links voting overviews for the later readings. A design that merely joins all votes on `sak_id` to one final law without phase/type would conflate different events. The case report notes that other elements of the original proposal were **not** enacted.

**D. Mixed source coverage is not a parser defect.** The actual XML export, its `publikasjon_referanse` values, the representative-by-representative ballot files, and the legal-corpus instrument identities are **not** covered by this observational web audit. Missing snapshots are recorded as *not checked*, not “API fails” or “unsupported law.”

**E. New acts were deliberately excluded when the case page described a new standalone law rather than an amendment.** Examples of an **excluded type** are [sak 200240](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=200240) (new law on negative social control) and [sak 103474](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=103474) (barnevoldsundersøkelsesloven). This is an audit of **amending instruments**, not any `Lovvedtak` regardless of legal effect.

## Method and limitations (important)

1. **Selection:** browsed official Stortinget case pages, then deliberately selected 20 distinct sanctioned amending-law references spanning 2015–2026 and simple/budget/multiple-reading cases; excluded standalone new acts. This is **Storting-first selection**, not a Lovverk-to-Storting discovery recall test. It cannot measure the share of amendments in Lovverk for which Storting evidence is obtainable.
2. **Observation:** read the official pages and the directly displayed sanction + Lovvedtak pairs. These are accessible public **pages**, not independently stored evidence with exact remote-body digests. “Sourced” means the links are inspectable, **not** cryptographically verified captures. Stortinget's own official site is authoritative for its case history and public parliamentary references, but original Lovdata legal state still needs its separate source verification.
3. **URLs:** `case_url` is the primary inspectable official evidence; `checked_vote_page_url` is given **only when that URL's contents were opened and inspected**. Empty URL does **not** mean no vote page exists. The CSV explicitly records `raw_storting_api_xml_captured=no` and `independent_lovdata_publicdata_id_checked=no` for all 20.
4. **Correlated sample:** 20 acts come from 18 distinct parliamentary cases, with some deliberate shared-case duplicates; rows are **not** independent statistical observations. Included years skip 2017–2021, so neither temporal coverage nor modern API coverage can be inferred for those years.
5. **Terms/access:** Stortinget NLOD attribution/source-use review and Lovdata publicData contractual boundaries remain prerequisites for publishing source snapshots. Never fetch/scrape Lovdata HTML as a substitute for its licensed public-data API.
6. **Code status:** [PoC PR #613](https://github.com/bartoszkobylinski/lovspor/pull/613) and [Storting XML capture PR #614](https://github.com/bartoszkobylinski/lovspor/pull/614) have hosted tests green, but an owner-confirmed offline Mac mini leaves the independent `codex-author` lane queued. **Do not merge/bypass the gate**; documentation research can progress independently.

## Architecture implications, already supported by the evidence

- Maintain `amending_lov_id ↔ lovvedtak(session, number) ↔ sak_id` as separately evidenced edges with `source_url`, raw/parsed evidence type, and observation date. Never collapse it to `law ↔ sak ↔ all case votes`.
- Model `sak_id → divisions` independently (Politik already owns those). Divisions require their own `vote_id`, `reading_stage`, `proposal_or_adoption_scope`, and exact case/decision link **before** emitting a provision-specific voting claim. A representative's stance is tied to the official individual division result; an act's validity is not inferred from this data.
- Allow many `LOV` acts per case; many `Lovvedtak` per case and distinct law numbers; three-stage proceedings and annotations; partial effective dates; missing source or unpublished results as first-class unknowns.
- A future user-facing confidence badge like “attested” must apply **to a named edge** and named source, never imply the entire law-to-paragraph-to-voter chain is verified.

## Next reproducible experiment (separate from CI runner)

1. Draw a **Lovverk-first**, pre-registered, stratified random sample of 50–100 amending-act references from the licensed Lovdata-derived corpus (periods, fields, older acts, single/multiple amendments); keep **all** selected acts including failures. Only then estimate lookup coverage/recall with a denominator independent from a Storting search.
2. Run a **single-case live XML capture** of `sakid=89016` when a permitted network host is available. Archive the entire official Storting response body with its hash and observation time, compare typed publication references to case-page observations. Add complex `sakid=63056`, `200235`, `200312` before concluding API parity.
3. Verify normalized `LOV-...` identities against **Lovdata publicData** originals; preserve legal validity and observed-at as distinct time axes.
4. Decide published evidence schema, storage/retention/licence and the precise consumer contract in a separate owner-reviewed ADR amendment/implementation PR; then connect [Politik #177](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/177) to the vetted artifact.

**Bottom line:** there is substantial, official, non-fuzzy evidence for the **case-level** legislative crosswalk in this selected set. **Population coverage, API parity, per-division law-scope matching, and law/provision/voter causation remain unmeasured or unverified.**
