# Accepted architecture: source-backed Lovspor ↔ Politik legislative crosswalk

**Status:** source-of-truth architecture accepted 2026-10-14 by the project owner as ADR-0017; no crosswalk implementation deployed and its publication schema/location still require separate decisions  
**Tracking:** [Lovspor #610](https://github.com/bartoszkobylinski/lovspor/issues/610), [Politik #62](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/62), [Politik #88](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/88), [Politik #98](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/98)  
**Related planned sources:** `docs/roadmap.md` D-DIRECT-3 (forarbeider), D-DIRECT-4 (Stortinget).  
**Date:** 2026-10-14

## Goal and existing implementation

Build *legal git blame* as a **verified chain of official legal/parliamentary records**, not an AI attribution guess:

`amended act § → amending law (LOV id) → Lovvedtak (session + number) → parliamentary sak_id → votes / speeches / documents`.

This is **not** a greenfield project:

- Lovspor and Lovverk supply legal document identity, statute text, amendment annotations, `get_temporal_events` and source provenance.
- Politik already reads a sibling Lovverk checkout via `src/analysis/law_amendments.py` and `law_timeline.py`. Its `/laws` and `/law/{slug}` pages display legislative change history. `law_debate.py` surfaces contemporaneous speeches as **candidates only**.
- Politik already stores parliamentary cases, votes and representatives; [Politik #88](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/88#issuecomment-5526234233) documents a completed `vote → sak_id` join and explicitly leaves **`sak_id → sanctioned/amending law`** unresolved. Do not invent that missing edge.
- [Politik #98](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/98) defers cross-project legal strategy from its own ongoing political-history product cycle. This proposal is a separate cross-project integration, not a change in Politik product positioning.

## Source-of-truth boundary

| What the claim concerns | Authoritative upstream | Derived/presentation owner |
| --- | --- | --- |
| Norwegian law text, identifiers, amendment notes | Lovdata official primary sources | Lovspor derives, Lovverk publishes snapshots |
| Parliamentary cases, Lovvedtak and documentation references | Stortinget official case/publication data | Politik caches and presents |
| Votes, individual ballots, representatives, speeches | Stortinget official records | Politik caches and presents |
| Royal sanction (sanksjon), enacted law number ↔ parliamentary Lovvedtak | Official published `regjeringen.no` sanction communications and/or explicit Lovdata citations | Lovspor derives the **verified linking edge** |
| Cross-domain legal ↔ parliamentary mapping | Only the official documents explicitly establishing each edge | **One** proposed Lovspor-produced, independently reproducible *derived crosswalk*, consumed by Politik |
| Legal validity `valid_at` | Lovdata-sourced legal commencement evidence evaluated under Lovspor's accepted temporal rules | Existing Lovspor temporal tools — **never** inferred from votes or sanction alone |

**The official sources are the actual authorities.** A versioned crosswalk is an audit artifact, not authoritative legal content. `lovverk` Git is authoritative for what the corpus **recorded**, not for what law was **in force**. No LLM may generate or certify a linking edge.

## Proposed integration architecture

```text
Lovdata public source / amendment note
    └── amendment identity + affected provision ──┐
Regjeringen.no sanction notice / Lovdata citation  ├── Lovspor deterministic
    └── LOV id ↔ (session, Lovvedtak number) ──────┤    crosswalk producer
Stortinget official sak / publication references   │
    └── (session, Lovvedtak number) ↔ sak_id ───────┘
                     │
             immutable evidence-backed links
             (candidate optional Lovverk derived overlay;
              placement requires an owner-approved ADR)
                     │
           ┌─────────┴─────────────┐
           │                       │
   Lovspor read-only surface   Politik consumer adapter
   (legal provenance only)     joins to its own vote/speech stores
```

**Do not make Lovspor depend on Politik at runtime.** It must be independently installable and reproducible from official sources. Conversely, do not copy the full Politik votes/speeches database into Lovverk or build two independently-maintained crosswalks. An imported Politik cache may accelerate discovery, but a verified link must point to primary-source evidence.

**Publication location is not yet accepted.** Proposed possible namespace: `lovverk/enrichment/legislative-links/` as a separate opt-in generated, versioned artifact, with no changes to statute Markdown, core manifest membership or existing MCP semantics. Verify ADR-0004's existing engine/corpus boundary, licensing, re-buildability and repository churn before choosing/creating this namespace. Until that decision: pilot fixtures live only in Lovspor's development/tests.

## Stable identifiers and join gates

Use *typed* identities:
- enacted/amending statute: full Lovdata `LOV-YYYY-MM-DD-NNN` identity / LTI document ID;
- parliamentary law decision: `(session_id, lovvedtak_number)` — **not the number alone**;
- parliamentary case: `sak_id`; vote: the official division ID in Politik;
- affected provision: `(amended_act_doc_id, section_id, source-scope evidence)` rather than just a display label.

**Sufficient evidence, each edge separately:**

1. `provision → amending law`: a source-attributed amendment note/event. Scope and `Endret` vs `Endres` matter.
2. `amending law → Lovvedtak`: an explicit Lovdata LTI document reference (e.g. `Lovvedtak 85 (2021–2022)`) **or** a government sanction notice that names both `Lovvedtak` and the enacted law's date/number; title/date proximity alone is **not** sufficient.
3. `Lovvedtak → sak_id`: a matching, uniquely resolved official Stortinget case/publication reference in the correct parliamentary session. A free-text `henvisning` is a search hint until the publication's typed identity is checked.
4. `sak_id → divisions, speeches and document trail`: Politik uses its existing official Stortinget data, labels specific vote targets accurately, and does **not** treat a vote on a case as necessarily a vote on a specific §. `law_debate` keyword+date results are **candidate related speeches**, not proven debate causation.

A response must say `attested`, `unresolved`, `ambiguous`, `contradicted` or `unsupported` for each requested edge. No guessed IDs, proposal authors, votes, amendments or legal effective dates.

## Evidence record (illustrative schema — not stable yet)

```json
{
  "schema_version": "proposal-0",
  "subject": {"type": "lovdata_amending_act", "id": "LOV-2022-06-17-63"},
  "relation": "enacted_from_lovvedtak",
  "object": {"type": "storting_lovvedtak", "session": "2021-2022", "number": 85},
  "status": "attested",
  "evidence": [{
    "publisher": "Lovdata",
    "url": "https://lovdata.no/dokument/LTI/lov/2022-06-17-63",
    "source_document_id": "LOV-2022-06-17-63",
    "quoted_reference": "Lovvedtak 85 (2021–2022)",
    "source_sha256": "<sha256-of-captured-source>",
    "observed_at": "<RFC3339-timestamp>"
  }],
  "derivation": {
    "rule_id": "exact_lovvedtak_reference_v1",
    "parser_version": "<version>",
    "corpus_commit": "<lovverk-full-SHA>"
  }
}
```

The placeholders **must not** be published as real hashes or events. Actual records require captured source bytes, explicit provenance, versioned schema and validation. The example establishes a relation to Lovvedtak only — it does not assert a `sak_id`, a vote or legal validity.

## Concrete authoritative pilot evidence

- **Lovdata**: [LOV-2022-06-17-63](https://lovdata.no/dokument/LTI/lov/2022-06-17-63), amendment of forvaltningsloven §§ 10 and 46, lists `Prop. 81 L (2021–2022)`, `Innst. 386 L (2021–2022)` and `Lovvedtak 85 (2021–2022)`.
- **Stortinget**: [Lovvedtak 85 (2021–2022)](https://www.stortinget.no/no/Saker-og-publikasjoner/Vedtak/Beslutninger/Lovvedtak/2021-2022/vedtak-202122-085/) prints those provisions and references the proposal / committee recommendation.
- **Regjeringen**: [Official Council of State notice (June 2026)](https://www.regjeringen.no/no/aktuelt/offisielt-fra-statsrad-18.-juni-2026/id3165927/) pairs `Lovvedtak 47 (2025–2026)` with `Lov nr. 33` for amendments to arbeidsmiljøloven and folketrygdloven.
- **Stortinget API contract**: [`sak` publication-reference types](https://data.stortinget.no/dokumentasjon-og-hjelp/sak/) and [`saker` metadata](https://data.stortinget.no/dokumentasjon-og-hjelp/saker/) support the next source-checked link. This first design pass **does not claim to have resolved the pilot's sak_id**.

## First implementation slice: fixture-backed research spike (no production contract changes)

1. Resolve one official `LOV id → Lovvedtak(session, number) → sak_id` chain with primary-source URLs, source snapshots/hashes and deterministic test fixtures. Test a second contradictory/ambiguous candidate.
2. Find and document concrete upstream export endpoints, availability, event corrections, terms, identifier collisions and one-to-many shapes. In particular test multi-law cases and session reuse of Lovvedtak numbers.
3. Specify typed edge outcomes, evidence schema, and the build/exposure boundary. Do **not** silently convert an unresolved outcome to a best-effort title/date match.
4. Decide materialisation, licensing and API boundary in a separately accepted ADR before adding code that publishes the crosswalk or changes the Lovspor MCP surface.
5. On approval, implement the producer in Lovspor and a **small consuming adapter** in Politik; reuse `sak_store`, `vote_profile`, `law_timeline`, `law_debate`. Existing UI can retain its honest caveats until the new verified edge exists.

## Explicit non-goals

No full historical valid-time law reconstruction; no secondary voting database in Lovspor; no Politik runtime requirement to use Lovspor; no speculation that votes prove legislative authorship; no inference that a law is effective simply because adopted; no adoption of sejmDiff's implementation (it is product inspiration only).

**Governance:** the authority and ownership assignment is accepted in ADR-0017; this public document is the implementation-facing design and evidence-spike plan. It is NOT deployed code, a functioning crosswalk, permission for a corpus migration, or approval of an immutable output schema. The project's private ADR notebook cannot be a hidden dependency of reproducibility or user-facing documentation.
