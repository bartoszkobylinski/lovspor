# Lovverk-first 100-law audit: source-cited amendments to Storting proceedings

**Snapshot:** 2026-10-14 (UTC+02). **Status:** exploratory observational research, not production evidence or parser-coverage benchmark. **Project:** Lovspor #610 / ADR-0017. **Branch:** `docs/610-lovverk-first-sampling-20261014`.

## Design: unlike the earlier 20-act Storting-first sample

Before any selected legal texts were opened, the [preregistration](lovverk-first-100-preregistration-2026-10-14.md) and [selection ledger](data/lovverk-first-100-selection-inventory-2026-10-14.csv) were committed. The reproducible sample begins with **Lovverk**, not with parliamentary cases that are already easy to find.

- Source authority for the **derived corpus snapshot**: `bartoszkobylinski/lovverk`, Git commit `2ab982d253c9470ddb885172b36c10687539c910`, `manifest.json` generated at `2026-10-14T09:26:38.661084Z`. It contains **755** currently published `gjeldende-lover` entries (309 enactment years <=1999, 205 in 2000–2015, and 241 >=2016).
- Stratified, independently of any Storting evidence, with 32-bit FNV-1a of `lovspor-610-lovverk-first-2026-10-14:<document_id>`. **35 old / 30 mid / 35 new** selected; 50 additional document IDs preregistered as reserves only if initial eligibility fell below 50. **No reserves needed or substituted.**
- In each fixed selected **consolidated base law**, select the **latest** referenced instrument dated 2007-01-01 through 2026-10-14 from its published amendment notes (`> Endret/Endra/Endres/Tilføyd/Opphevet...`) and keep its exact Lovverk source path, blob SHA, note line and excerpt. Missing references **remain in the denominator**.
- All 100 outcomes are published in the [full outcome ledger](data/lovverk-first-100-audit-outcomes-2026-10-14.csv); extracted source references in the [source citation ledger](data/lovverk-first-source-extraction-2026-10-14.csv); distinct parliamentary cases in [proceedings ledger](data/lovverk-first-52-proceedings-2026-10-14.csv). Each includes official links and explicitly records whether the official legal/parliamentary API payloads were captured.

## Observed counts

| Measure | Observed | Meaning |
|---|---:|---|
| Eligible manifest base laws | **755** | All *current* consolidated laws in this Lovverk snapshot; does not include all historical repealed acts. |
| Base laws sampled (preregistered) | **100** | Denominator for source-extraction yield. |
| Base laws with an amendment-note citation in 2007–2026 | **83/100** | These contain an identifiable **derived Lovverk amendment source ref**, not proof of effective legal date. |
| Base laws with no eligible cite | **17/100** | Not evidence they were never amended: renderer notes, period limit and instrument type all matter. |
| Distinct sampled amending-instrument IDs | **52** | Deduplicated exact `LOV-YYYY-MM-DD-N` among the 83 references. |
| Distinct parliamentary cases manually linked to sampled instruments | **52/52** | Case URLs identified first, independently of the subsequent sanction evidence audit. This is manual evidence discovery, not automated join recall. |
| Standard parliamentary `Lovvedtak` references | **51/52** | Procedural type that PR #613/#614 plans to use. |
| **Legacy Odelsting `Besl. O.`** | **1/52** | Historical exception requiring a separate typed relationship. |
| **Official act-number ↔ decision-number pairs inspected** | **52/52** | All 43 former candidate pairs were individually revisited in official Storting case pages or government sanction notices (9 had already been checked). This is a **manual page-level** result, not API verification. |
| Documented **corrective re-sanction** of one numbered decision | **1/52** | `LOV-2025-06-20-67` is the corrected re-sanction of initially, incorrectly sanctioned `LOV-2025-04-25-13`. Both LTI instrument publications explicitly explain the error and the second sanction. Preserve **both events**; this is not an unexplained inconsistency. |
| Full API snapshot plus independent Lovdata publicData legal identity | **0/52** | No machine-replayable end-to-end input bytes/hashes for official source authority yet. |

### Eligibility by original law's enactment year

| Base-law stratum | Sampled | With eligible cited instrument | No eligible cited instrument | Population manifest size |
|---|---:|---:|---:|---:|
| Through 1999 | 35 | 31 | 4 | 309 |
| 2000–2015 | 30 | 27 | 3 | 205 |
| 2016–2026 | 35 | 25 | 10 | 241 |
| **Total** | **100** | **83** | **17** | **755** |

The sample is **balanced by era, not population-weighted**, so the `83/100` is descriptive only. Its exact original-law sampling weights are not the national law population weights. The 83 source references collapse to only 52 distinct instruments because one amendatory law often modifies multiple statutes. For example, `LOV-2015-06-19-65` is cited by **10** sampled base laws, while `LOV-2025-04-25-12` occurs **5** times. A naive per-mention coverage figure would count the same legislative record repeatedly.

## Source-linked confirmed examples

**Pre-2009 parliamentary procedure (unanticipated boundary):** The parliamentary case [sak 43206](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=43206) itself explicitly links `Sanksjonert 19.06.2009 (lov107)` to **`Besl. O. nr. 87 (2008–2009)`**, not any `Lovvedtak`. A schema mandating `Lovvedtak` for the **2007–2009** portion of the preregistered range would silently create an artificial missing result or conflate distinct types. The data contract needs a typed `parliamentary_decision_kind`, with Odelsting/Lagting readings and not a hardcoded Lovvedtak assumption.

**Older modern `Lovvedtak`:** [sak 50431](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=50431) directly identifies `20.01.2012 (lov7)` and `Lovvedtak 28 (2011–2012)`. [sak 61801](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=61801) identifies `19.06.2015 (lov65)` with `Lovvedtak 90 (2014–2015)`. [sak 200231](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=200231) identifies `19.06.2026 (lov42)` with `Lovvedtak 65 (2025–2026)`. These are explicit act/decision/case relationships, not title-proximity guesses.

**Cross-authority government confirmation:** Official government notices independently pair:
- `LOV-2023-06-16-34 ↔ Lovvedtak 98 (2022–2023)` in [Offisielt fra statsråd 16 June 2023](https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-16.-juni-2023/id2984858/); [sak 93692](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=93692).
- `LOV-2025-12-22-117 ↔ Lovvedtak 9` and `LOV-2025-12-22-124 ↔ Lovvedtak 17 (2025–2026)` in [Offisielt fra statsråd 22 December 2025](https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-22.-desember-2025/id3144030/); [saker 105211](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=105211) and [104897](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=104897).
- `LOV-2026-03-06-8 ↔ Lovvedtak 26 (2025–2026)` in [Offisielt fra statsråd 6 March 2026](https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-6.-mars-2026/id3151187/); parliamentary [sak 103533](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=103533) references the same numbered decision.
- `LOV-2026-06-19-36 ↔ Lovvedtak 83 (2025–2026)` in [Offisielt fra statsråd 19 June 2026](https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-18.-juni-2026/id3165927/); parliamentary [sak 107976](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=107976).

**A genuine official-source discrepancy:** Parliamentary [sak 103533](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=103533) still says the final `Lov` step has *"Ikke gjennomført ennå"*, while the [March 6 government sanction announcement](https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-6.-mars-2026/id3151187/) explicitly states `Lovvedtak 26` was sanctioned as **`Lov nr. 8`**. The official government/legal authority is decisive for sanction, but this is an **observed disagreement between displayed source pages**. The implementation must retain source type and timestamps/claim specificity and never take a missing web-page state as a legal non-event.

### Follow-up verification of the remaining 43 pairs (same sampling frame)

**2026-10-14 amendment to this audit:** without changing the pre-registered 100 base-law sample or the 52 distinct amending instruments, we revisited **all 43 previously candidate-only instrument pairs** against direct **official sanction evidence**. For 17 we read the Storting case page's `Sanksjonert DD.MM.YYYY (lovNN)` line directly next to its numbered `Lovvedtak`. For 26 we inspected an explicit government `Lovvedtak N (session) Lov nr. M` sanction notice; the candidate Storting case page carries the same session/decision identity. Evidence URL/type, verification date and any conflict appear in the [52-instrument ledger](data/lovverk-first-52-proceedings-2026-10-14.csv) and propagate to all [100 sampled outcomes](data/lovverk-first-100-audit-outcomes-2026-10-14.csv).

The earlier **9 reviewed instruments plus 43 follow-ups equal 52 reviewed**, not 52 independent new government notices. Not all 52 have **two independent institutions** confirming them; a Storting-only case is still only one official source. The audit keeps `raw_storting_xml_captured=no` and `lovdata_publicdata_independently_checked=no` throughout.

**Documented corrected re-sanction: `LOV-2025-06-20-67`.** Both officially published original legal instruments clarify the apparent government-notice discrepancy: [the first law, `LOV-2025-04-25-13`](https://lovdata.no/lov/2025-04-25-13), directly states that the wrong version of `Lovvedtak 41 (2024–2025)` was sanctioned and published and that the act was therefore re-sanctioned as law 67. [The later `LOV-2025-06-20-67` original publication](https://lovdata.no/dokument/LTI/lov/2025-06-20-67) confirms that the wrong decision version was used for the first sanction, and the correction was officially sanctioned and published again. The [25 April government notice](https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-25.-april-2025/id3098066/) accordingly says `Lovvedtak 41 → lov13`, while the [20 June notice](https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsradet-20.-juni-2025/id3110091/) and [Storting sak 101001](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=101001) say `Lovvedtak 41 → lov67`. **Both represent real sanction/publication events for the same numbered decision, with the latter explicitly a correction**. Store them as distinct events with an explicit supersession/correction relationship rather than silently dropping the April history. This clarification is a human-reviewed official-law-document observation, not a programmatic Lovdata HTML ingestion or an archived Lovdata publicData payload.

**Representative directly checked sources:** [sak 50936](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=50936) identifies `LOV-2012-01-27-9 ↔ Lovvedtak 36`; [sak 79685](https://stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=79685) gives `LOV-2020-12-18-157 ↔ Lovvedtak 22`; [sak 93718](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=93718) gives `LOV-2023-06-09-32 ↔ Lovvedtak 66`; [government notice 20 June 2025](https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsradet-20.-juni-2025/id3110091/) enumerates numerous distinct act/decision pairs; and [government notice 19 June 2026](https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-18.-juni-2026/id3165927/) likewise lists the exact numbers.

## Interpreting the 52 case records

- All 52 source-first instruments have a **specific located Storting proceeding page**. That is a useful **manual discoverability finding** despite starting from Lovverk; it does not prove the *automation* in PR #613/#614 could find them, nor the case page independently states the exact published law number.
- **All 52** distinct instruments now have a specifically checked official act-number/decision-number pairing, including the 43 formerly pending checks. The primary evidence is an official Storting `sak` page or an official government sanction notice; the full per-case evidence URL and type are in the CSV. **One pair has two successive law numbers, now explained by an expressly documented corrective re-sanction (see below).** This remains a manual, webpage-based, not a byte-attested API crosswalk.
- The reference period **2007–2026** is intentionally challenging: it spans a real parliamentary procedural regime change, requiring a distinct **Besl. O.** node rather than pretending no decision exists in 2009.
- Many modern instruments also create standalone laws that **consequentially amend older acts**: e.g. the 2015 penal code commencement law and the 2026 data governance statute. An instrument can be a 'new law' by title and an **amender** by cross-reference; distinguish `instrument_type` from an `amends` edge.
- Parliamentary voting data is **not** an exact paragraph voting result. For multi-act cases there can be several Lovvedtak and different votes, phases and scopes. Preserve `vote_id` and reading stage separately; do not fill a `law ↔ legislator` edge from mere case membership.
- **No source pages were stored with remote-body hash** here. Observed Storting URLs are public navigational evidence; primary law text from Lovdata publicData must be independently acquired before authoritative release. Lovverk Git SHA is a *published-corpus-state checksum*, never legal valid time.

## Biases and unresolved claims

1. The **source sampling frame** is current Lovverk base laws, not the entire population of historical amending statutes; removed/repealed statutes are not represented. New law enactment years may occur in a dataset generated as of 2026 even when a reference has not yet taken effect.
2. The **extraction rule** is a simple deterministic recognizer of Markdown legal notes; it might miss another form of note, and the 17 not-eligible base laws are *not* evidence of no amendments. The original corpus rendering is lossy and includes future `Endres` notes; candidate source IDs should not be mistaken for effective past legal changes.
3. A single **latest** qualifying reference per base document biases toward 2025–2026 and does not estimate all historic amendments. The source input includes 83 correlated mentions of 52 distinct instruments.
4. The 52 Storting case records and sanction pair checks were found through **targeted manual official-web lookup**, not `data.stortinget.no` API replay or an evaluated programmatic discovery index. **52/52 manual source-pair observation is not system recall nor a population coverage rate**; one real correction/re-sanction event is present.
5. The exact `LOV...` IDs are cited by an unofficial **Lovdata-derived** Markdown publication. In this experiment **0** law IDs have freshly checked Lovdata **publicData API** bytes and **0** Storting cases have newly archived official source XML; **even 52 manually verified web-source pairs do not authorize a production-attested API cache**.
6. The self-hosted independent Codex runner is unavailable by owner decision (Mac mini off). This documentation/measurement branch must stay pending; no bypass or merging without owner's explicit action and repo rules.

## Implementation consequences / follow-ups

1. ADR-0017 public export schema needs typed `decision_kind: lovvedtak | besl_o | ...`, with parliamentary `session`, number, distinct `sak_id`, and separate official evidence URLs for legal sanction and Storting case. The 2009 case proves this is a real requirement, not theoretical.
2. Build a *Lovverk-first discovery index* for all amending-law IDs from publisher-provided amendment notes, with an evidence log and typed candidate/unresolved. Start with the **52 fixed known instrument IDs** in the CSV as a regression set; do not use case titles as join keys.
3. On a host that can retrieve Storting API XML (PR #614 after approved merge), independently cross-check the exact parliamentary case publication references. Link the legal sanction through official Govt notices or Lovdata publicData, preserving unavailable and conflicting evidence.
4. For Politik consumer #177, use only individually attested law/decision/case links; its existing vote→sak dataset remains its own authoritative parliamentary store.
5. Run a separate random **amending-instrument-first** study from Lovdata publicData (not available in this environment) to estimate true national amendment coverage including repealed and obsolete laws. Do not present this 100-current-base-act audit as a measurement of all amendments.

**Interpretation:** all 52 amending instruments selected from 100 preregistered Lovverk base laws have now undergone **individual official webpage-level** sanction/decision pair review, with one independently explained corrective re-sanction event explicitly retained. This demonstrates feasibility of **manual** tracing, but **does not establish production-quality 52/52 attested API joins, automatic parser recall, or fine-grained voting attribution**.
