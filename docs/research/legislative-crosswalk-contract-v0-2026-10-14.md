# ADR-0017 — draft legislative crosswalk evidence contract v0

**Status: research proposal only, not accepted for publishing or runtime use.**
Tracking: [Lovspor #610](https://github.com/bartoszkobylinski/lovspor/issues/610),
[Lovspor #618](https://github.com/bartoszkobylinski/lovspor/issues/618),
[Politik #177](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/177).
Depends conceptually on the accepted ADR-0017 ownership rules, but does **not**
decide artifact placement, licensing, release schema, or launch a Politik consumer.

## Why a separate draft contract

The frozen Lovverk-first audit in PR #617, head
`e63713017dc4f1a42663731d1d805b37f18f9660`, reviewed **52 distinct**
amending-law/decision/case associations on official web pages, drawn from
100 sampled laws (83 references, 17 missing/unqualified). Not a single one of
those 52 records has *both* captured original Storting API bytes and an
independently captured Lovdata publicData identity. **52/52 manually reviewed
does not mean 52/52 API-attested or 52/52 automated recall.**

A Politik adapter must not silently upgrade research URLs to independently
verified legal facts. This draft gives source and consumer a common language
to reject that unsafe upgrade while the evidence-acquisition PR #614 is gated.

## File boundaries

- `docs/research/schemas/legislative-crosswalk-v0.schema.json`: structurally
  testable **proposed** JSON Schema Draft 2020-12 for one candidate link record.
- `tests/fixtures/legislative-crosswalk-52-web-reviewed.csv`: byte-for-byte
  copy of PR #617's 52-proceeding research ledger, pinned by original Git blob
  SHA `956bbcba172bbb5a8d670b332c05437ab3e6cd2a`, not an ingestion feed.
- `tests/unit/test_legislative_crosswalk_contract_v0.py`: validates all
  52 derived research records and adverse contract examples.

Every record has an explicit `release_state=research_only`, source Lovverk
and research commit, observation **date**, typed parliamentary decision,
`sak_id`, separately typed sanction events, and edge-level evidence. No
provision-to-vote, politician-to-wording, or legal effectiveness claims.

## Evidence semantics — never infer a stronger status

- `reviewed_web_only`: both links have separately identified official web
  URLs reviewed by a person. No claim to immutable original remote bytes.
  This is the status of **all 52** regression cases.
- `attested`: syntactic eligibility *only* after independently archived
  source bodies for `law_identity`, `law_decision_pair` and `decision_case`,
  each with upstream ID, raw-body SHA-256, retrieval time, parser version.
  The schema **cannot** authenticate source origin, prove signatures,
  confirm permissions or legal validity; these remain producer checks.
  No such attestation is asserted by this change.
- `unresolved`, `ambiguous`, `contradicted`, `unsupported`: explicit
  partial result, with required reason; no title/date fuzzy joins.

The law/decision edge and decision/case edge must be attested *separately*.
A webpage reference or digest of a manually typed excerpt is not proof of
remote source bytes. An evidence URL never implies a particular paragraph
was voted on. The `recorded_at` of legal corpus history, legal `valid_at`,
parliamentary vote date, original sanction date, and evidence retrieval time
are **different axes**; this record encodes none as a surrogate for another.

## Cases that determine the model

1. **Historical Odelsting**: `LOV-2009-06-19-107` uses
   `Besl. O. 87 (2008–2009)`, not `Lovvedtak`. A decision is uniquely
   identified by its kind, session and number; a number alone is not a key.
2. **Corrected re-sanction**: `Lovvedtak 41 (2024–2025)` has an initially
   incorrect sanction `LOV-2025-04-25-13` and subsequent corrected
   `LOV-2025-06-20-67`. Both distinct events remain, with the second
   explicitly `corrects_law_id` referencing the first, based on the
   official original publications. No effective-date inference is encoded.
3. **Case-to-vote scope**: many laws may share a `sak_id`; Politik owns
   existing case/vote stores and must not infer that a division in that case
   individually adopted a specified `§`.
4. **Source mismatch**: when government sanction notice and a Storting
   webpage's completion indicator disagree, keep both claims/evidence,
   do not silently assert absence of sanction.

## Owner gates left open

Before promoting this research proposal into a stable, consumable release:
approve storage namespace and retention/licence policy (including Lovdata
publicData and Stortinget); prove the official XML+legal source capture; add
source authentication and independently replayable hashes; decide versioned
artifact lifecycle and correction updates; then implement Politik #177's
optional adapter. No existing MCP schema, law text, Lovverk manifest or
Politik runtime is modified by this PR. Mac mini-dependent Codex/mutation
gates and owner-only merge remain mandatory.
