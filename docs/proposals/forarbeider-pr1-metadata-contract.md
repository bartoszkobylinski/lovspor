# Forarbeider: PR1 metadata-only implementation contract

**Status:** Proposed implementation contract; Claude may develop and open the isolated offline metadata code PR in parallel with review of [ADR-0018](https://github.com/bartoszkobylinski/lovspor-notebook/pull/150). Merge, any live fetching and any public data publication remain gated by the owner's acceptance of the relevant ADR decisions.  
**Date:** 2026-10-31  
**Owner:** Lovspor project owner (architecture); Claude / coding agent (implementation), independent reviewer (architecture + review)  
**Tracking:** [#738](https://github.com/bartoszkobylinski/lovspor/issues/738), [#737](https://github.com/bartoszkobylinski/lovspor/issues/737), [ADR-0017 public crosswalk contract](legislative-crosswalk-source-boundary.md).

## Why this slice

The existing `src/lovspor/storage/manifest.py` and root Lovverk `manifest.json` are for Lovdata-based legislation; the local-law implementation `src/lovspor/promotion/corpus.py` demonstrates a dedicated dataset manifest. `src/lovspor/rendering/document.py` hard-codes NLOD for the Lovdata pipeline; `src/lovspor/promotion/render.py` writes `source_license: "åndsverkloven § 14"` for local regulations. Government forarbeider must **not** inherit either field's assumption without making the distinction explicit.

PR1 establishes an isolated, testable metadata reader and writer **without network collection or public corpus publication**. It is an implementation PR for the coding agent, **not** a second ADR.

## Proposed PR1 boundaries

### In scope

1. Create a separately named module (e.g. `src/lovspor/forarbeider/`) with typed models and a deterministic parser for **regjeringen.no Prop. L / Prop. LS** landing-page metadata plus its explicit chapter navigation.
2. Parse from a supplied **offline HTML input**, not from a live network request in PR1. Source-page fixtures must be small, minimal, licensed/rights-safe excerpts: structural HTML only, not wholesale government texts or real private persons' submissions.
3. Official document reference as a typed tuple `(document_type, session, proposition_number)` with explicit source evidence. Source numeric `idNNNNNNN` from an allowlisted government URL is a separate stable upstream identity; do not derive an ID from a title or a content hash.
4. Chapter inventory has typed `(chapter_number, title, url)`, source document ID and deterministic ordering. A chapter URL must reference the same `idNNNNNNN` and host as its parent. Duplicate chapter numbers, conflicting identity and missing required identifiers fail closed.
5. Define a **forarbeider-specific** version-1 staging metadata record schema, separate from `ManifestRecord` and `LocalRecord`. The multi-document, authoritative `forarbeider/manifest.json` is deliberately **deferred until PR3**, when permitted corpus publication exists; do not mislabel a single staging record as a corpus manifest. Validate `source_document_type=forarbeid`, `normative=false`, `source_provider=regjeringen.no`, `source_license=null` and `rights_basis=åndsverkloven § 14` (legal qualification is recorded, not claimed to be a grant/licence).
6. Metadata includes canonical source URL, source document ID, typed official reference, title, ministry (if stated), date of publication (nullable if not proven), retrieval timestamp (explicit input), ordered chapter list, parser version, and source response SHA-256. **No** chapter body or published Markdown yet; no `valid_at`.
7. A supported Typer CLI path is required to feed the offline HTML, source URL, explicit retrieval time and a chosen output path, then write validated JSON metadata **outside both public repositories**. Refuse paths inside the engine repository and Lovverk checkout. The command must be documented and have end-to-end CLI tests: a model the operator can construct only in Python is insufficient.
8. Deterministic JSON formatting, sorted keys, UTF-8, stable chapter order and one trailing newline, with atomic file writes. The parser must depend on supplied inputs only; any clock/IO is confined to CLI boundary.
9. Test a sample against the documented structure of [Prop. 54 LS (2025–2026)](https://www.regjeringen.no/no/dokumenter/prop.-54-ls-20252026/id3154578/) and a distinct `Prop. L` structural fixture; the **real upstream sample** is evidence for expected fields but does not authorise hard-coding its IDs or content.
10. Provide a proof report in the PR: fields extracted from the sanitized sample, parser refusals, compatibility check, commands run and one known limitation.

### Out of scope / forbidden

* No `httpx` retrieval, sitemap crawling, cookies, authentication, PDF/OCR, bulk fetching, background jobs or automatic scheduling.
* No writing to `lovverk/forarbeider/` or root `lovverk/manifest.json` in PR1. The output is staging-only metadata. No public website, MCP tool/schema or default search changes.
* No automatic links to laws or Stortinget, no embeddings, legal-validity assertions, or interpretation text.
* No `source_license: "NLOD"` and no `source_license: "åndsverkloven § 14"`: statutory exclusion and an affirmative licence are distinct.
* No raw source HTML or scraped text committed to git, and no sensitive source text in logs, CI, issue comments, test fixtures or PR description.
* No speculative fallback when the source structure cannot prove a title, reference, chapter list or canonical host.
* No generic web scraper, no new broad infrastructure framework, no change to local regulation rendering solely to unify the schemas in this slice.

## Contract fields (illustrative, not fake source data)

```json
{
  "schema_version": 1,
  "id": "regjeringen-3154578",
  "source_document_type": "forarbeid",
  "normative": false,
  "document_type": "prop-ls",
  "session": "2025-2026",
  "proposition_number": 54,
  "title": "<source-extracted-title>",
  "ministry": "<source-extracted-ministry-or-null>",
  "published_at": null,
  "source_document_id": 3154578,
  "source_url": "https://www.regjeringen.no/no/dokumenter/prop.-54-ls-20252026/id3154578/",
  "retrieved_at": "<timestamp-supplied-by-operator>",
  "source_sha256": "<hash-of-supplied-raw-html>",
  "parser_version": 1,
  "source_provider": "regjeringen.no",
  "rights_basis": "åndsverkloven § 14",
  "source_license": null,
  "chapters": []
}
```

This is an **illustration**, not a record to be published; neither the empty chapters list nor the placeholder hashes represent successful extraction. The type of `published_at` may be a source-backed `YYYY-MM-DD`, otherwise null. If the site gives inconsistent information, emit a typed, counted refusal, not the illustrative values.

The eventual corpus layout is described by ADR-0018, but PR1 must not publish a new corpus dataset before the rights/privacy and extraction pilot gates.

## Test-first acceptance matrix (Claude implements; reviewer checks)

| Case | Expected result |
| --- | --- |
| Valid structured Prop. L fixture | Exact typed metadata and ordered chapters |
| Valid structured Prop. LS fixture | Same, with distinct type |
| `Prop.` without L/LS, `NOU`, `Ot.prp.`, `Rundskriv` | Unsupported, no output |
| Missing or contradictory official reference | HOLD/refusal, no guessed ID |
| Two chapters with same number but different URLs | HOLD/refusal |
| Chapter URL points to another document ID / host | HOLD/refusal |
| Non-allowlisted host, invalid source URL or unexpected query parameters | Rejected |
| HTML changed so main metadata cannot be identified | Rejected, with reason; no fallback to site-nav text |
| Missing source publication date | `published_at=null`; no inferred date |
| Same frozen input and retrieval timestamp twice | Identical output bytes |
| New schema omits `source_license` / fills with NLOD | Validation failure |
| `source_document_type` changes to enacted law or `normative=true` | Validation failure |
| Output targeted at either Git checkout | Refused before writing |
| Existing `lovspor` / `lovverk` codepaths without new command | Byte-identical behaviour; no new import-time side effects |
| Unsupported config field accessible only by hand-editing JSON | PR fails: every operator-settable field needs a supported writing command and test |

The tests for parser/staging-record schema/CLI are unit tests under `tests/unit/`, with representative structural fixtures in the repository's existing test-fixture convention; integration tests where needed must not call real websites. Include a real CLI subprocess or Typer runner test exercising the operator path end to end.

## Quality, workflow and handoff

* TDD: red test first, minimal implementation, refactor; each commit passes established repo gates.
* Run `scripts/quality/verify-fast.sh`, `/security-check`, `scripts/quality/verify-deep.sh` before push; preserve `CLAUDE.md` constraints. CI and the existing independent testing/mutation pipeline remain authoritative.
* One focused PR against `main`, no unrelated refactor and no migration. GitHub Actions owns independent Codex testing; **do not bypass checks or merge**.
* Coding agent writes code and fixes its own PR; architectural reviewer checks against accepted ADR-0018, this public contract, evidence, negatives, security and compatibility. Owner alone merges.
* An implementation that requires changing a numbered ADR decision must stop and request a reviewer-led ADR amendment/successor first, not silently choose a different policy. The coding agent may open a draft or ordinary PR for this isolated offline-only work before ADR-0018 acceptance; it must not be merged until the decision is accepted and reviewed.

## Following slices (not authorised by PR1)

PR2: bounded, rights-reviewed offline chapter-body extractor, normalization/section quality and per-chapter privacy HOLD before Git.

PR3: opt-in separate `lovverk/forarbeider/manifest.json` publication with explicit owner release approval, rights checks, manual review workflow and takedown ability.

PR4+: source-backed attested relation to law under ADR-0017, never title/year joins. Rundskriv, NOU, Ot.prp. and Stortinget Innstillinger are separate research/admission decisions.

## Evidence and legal limitations

The Ministry explicitly describes `lovforarbeider` as examples of official documents without copyright protection under the statutory rules: [government guidance](https://www.regjeringen.no/no/tema/kultur-idrett-og-frivillighet/opphavsrett/innsiktsartikler/Opphavsrett1/id2000944/). §14 does not itself license excluded third-party works or waive GDPR. §24 database rights and the [HR-2019-1725-A distinction](https://rettspraksis.no/wiki/HR-2019-1725-A) demand a separate, source-specific review before volume collection. `robots.txt` permissions are not republication rights. This PR does not purport to grant legal clearance.
