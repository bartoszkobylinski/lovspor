# Forarbeider edition evidence: contract (issue #784)

Status: proposed extension of ADR-0018 (Accepted 2026-11-01, kept in the notebook repository), for the owner's and the architecture reviewer's acceptance in the PR that implements it. ADR-0018 §3 already names landing-page verification ("Discover and verify a primary document landing page"); this document fixes how a saved landing page is used, and what it may and may not prove.

## Decision

1. **The landing page is a second, separate input, for edition evidence only.** It is never passed to the chapter reader and none of its body text is read. The chapter page `?ch=N` stays the only extraction source and the only input of the v1 metadata record.
2. **Sidecar, not v2.** The edition evidence is its own file with its own schema (`record_type: forarbeid-edition-evidence`, `schema_version: 1`), beside the v1 `ForarbeidRecord`. The v1 record is unchanged in schema and in bytes. This keeps release preflight's `build_record(page) == stored` reproduction check (PR3B contract) whole, and keeps every v1 reader working: #784 showed that even an optional field on the v1 record breaks compatibility in both directions. A v1 reader refuses the sidecar (`schema_invalid`), so it cannot be mistaken for a record.
3. **What the evidence asserts:**
   - `edition.kind`: `corrected` only when the source states it in markup observed on a real page; `unknown` otherwise. `original` is in the vocabulary and refused by the schema: no observed page states that it is the original edition, and the absence of a notice is not that statement.
   - `edition.corrected_on`: the date the source states in its correction notice. Never `DC.Date`, which is the statsråd date, and never the retrieval time or `last-modified`.
   - `edition.original_edition_url` and `edition.rettebrev_urls`: the source's own links, as stated. They are not fetched.
   - `landing_page_url`, `landing_sha256` and `landing_retrieved_at` (with the operator's offset): what was saved, its bytes and when. They are the operator's word (`landing_provenance: operator_saved_offline`); no request is made.
   - `chapter_record`: the `source_page_url`, `source_sha256` and `retrieved_at` of the v1 record the evidence was checked against, by identity (document id, type, session, number, canonical URL).
   - `chapter_edition`: always `unknown`. The two pages are separate captures, so nothing proves which edition the chapter bytes belong to, or which chapters a correction changed. Only a byte difference between two observations of the same chapter is evidence of that.
4. **Fail closed.** The page must be whole: exactly one `footer.page-footer`, a child of `body` after `main` (`incomplete_page` otherwise), because an absent notice may only read as `unknown` on a page proved complete. The edition list is consumed whole: the block is exactly `h3.edition` + `ul.link-list` and every item exactly `li > a`, or the page is refused (`edition_unrecognized`). Likewise, a notice without the edition block, or the block without the notice, two of either, an unfamiliar notice, date, heading or link, and edition wording or edition links anywhere else on the page are refused, never read as `unknown`. A notice we cannot read must not pass as "no notice".
5. **Offline and owner-only.** `lovspor ops forarbeider-edition` has no clock and no network. The evidence is written beside the v1 record as `<record>.edition.json`, under the metadata path rules (outside every repository), and it is written once, 0600, never replaced (`observation_exists`).
6. **No release consequence.** Nothing in review or release reads the evidence. It authorizes nothing and is not published.

## The markup, as observed

Read on one real landing page (Prop. 54 LS (2025–2026), a corrected proposition), saved privately with one HTTPS GET on 2026-11-05: status 200, no redirect, verified TLS. The bytes stay in private staging, outside Git. Only the structure was copied into the synthetic fixtures `tests/fixtures/forarbeider/landing-*.html`, with invented identities and text:

- `header.article-header > div[data-doc-meta="corrected-edition-date"]`, text `(Korrigert utgave per D.M.YYYY)`;
- `h3.edition` with the text `Opprinnelig utgave og rettebrev`, followed by `ul.link-list` linking `/contentassets/<32 hex>/no/opri/<name>.pdf` (`Opprinnelig utgave …`) and `/contentassets/<32 hex>/no/rett/<name>.pdf` (`Rettebrev …`). The block is a `div` holding only those two elements, and each list item is a bare `li > a`;
- one `footer.page-footer`, a direct child of `body`, after `main`.

On that page the stated correction date was 2026-05-13 and `DC.Date` was 2026-03-27. The two saved chapter pages carry none of this markup. No landing page of an uncorrected proposition has been saved, so `unknown` on such a page rests on the absence of the observed markup, and any other wording is refused.

## Not decided here

- Whether evidence of a correction should hold a chapter's review or release. Today it does neither.
- Whether a later capture of the same chapter, compared byte for byte with the first, should be recorded as evidence that a correction changed it.
