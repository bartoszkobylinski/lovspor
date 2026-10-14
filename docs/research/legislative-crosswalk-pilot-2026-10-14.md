# Legislative crosswalk pilot: LOV-2022-06-17-63 → Lovvedtak 85 → sak 89016

**Status:** exploratory proof of concept (2026-10-14), not a production export or an MCP contract.  
**Authority:** ADR-0017 (Accepted), [Lovspor #610](https://github.com/bartoszkobylinski/lovspor/issues/610), [Politik #177](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/177).

## Finding: the missing join exists in an official primary source

The [official Stortinget case page for sak 89016](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=89016) identifies, **on the same case page**:

- \`Prop. 81 L (2021–2022)\` and \`Innst. 386 L (2021–2022)\`;
- \`Lovvedtak 85 (2021–2022)\`;
- \`Sanksjonert 17.06.2022 (lov63)\`;
- the first reading/debate on 2 June 2022, voting on 3 June 2022 and second reading on 7 June 2022;
- the official [voting overview](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/Voteringsoversikt/?dnid=1&p=89016) with **two votes** in the case.

Independent confirmation on the **law side**: [Lovdata's published original text](https://lovdata.no/lov/2022-06-17-63/KAPITTEL_2) identifies \`LOV-2022-06-17-63\`, lists the same \`Lovvedtak 85 (2021–2022)\`, and states that it amends **forvaltningsloven §§ 10, 46**. [Stortinget's full Lovvedtak](https://www.stortinget.no/no/Saker-og-publikasjoner/Vedtak/Beslutninger/Lovvedtak/2021-2022/vedtak-202122-085/) describes both provisions. **These public web pages were manually checked**; the code does not scrape Lovdata or import its HTML. Production law acquisition continues to use Lovdata publicData, as required by \`CLAUDE.md\`.

Verified **identity chain for this one case**:

\`\`\`text
forvaltningsloven (LOV-1967-02-10) §§ 10, 46
  └── amended by LOV-2022-06-17-63
        └── Lovvedtak (2021-2022, 85)
              └── Stortinget sak_id 89016
                    ├── debate / case processing: 02.06.2022
                    └── official voting overview: 03.06.2022 (two votes)
\`\`\`

**Important:** A vote on a case or the law in its entirety does not establish a person's vote *on each sentence* of § 10 or § 46. An expression of support in a debate does not establish who actually authored the legal wording. No such attribution is made.

## What this PR implements

\`src/lovspor/legislative_crosswalk.py\` is a side-effect-free *probe*, separate from production temporal tools. It:

1. Accepts a law identity, session+Lovvedtak number and a caller-provided snapshot/excerpt of one **official** Stortinget case page;
2. Validates a strict HTTPS Stortinget case URL and derives the typed \`sak_id\` from its \`p\` parameter;
3. Requires explicit **both** \`Lovvedtak N (YYYY–YYYY)\` and \`Sanksjonert DD.MM.YYYY (lovN)\` evidence on that same case page;
4. Returns \`attested\` only when the identities agree, otherwise \`unresolved\`, \`ambiguous\`, \`contradicted\` or \`unsupported\`;
5. Hashes **only the bytes supplied by the caller**, with \`representation="excerpt"\` by default. This is **not** an original-site checksum or proof that the input was acquired from the stated URL.

\`tests/unit/test_legislative_crosswalk.py\` has 11 offline tests for the checked real-case excerpt and controlled negative/ambiguous/contradicted/malicious-URL cases. The negative cases are **synthetic test perturbations**, not real parliamentary records. There are no network requests in the tests.

### Executable example

\`\`\`python
from lovspor.legislative_crosswalk import CrosswalkRequest, resolve_case_page

result = resolve_case_page(
    CrosswalkRequest("LOV-2022-06-17-63", "2021-2022", 85),
    source_url="https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=89016",
    source_body=verified_official_case_page_html_or_excerpt,
)
assert result.status == "attested"
assert result.sak_id == 89016
\`\`\`

The test's checked excerpt hashes to **\`d452e746b719a1b08e46771ec1c3e005f7ee6b9cbd30b60d1a078395c5ed290f\`**. This is *only* the SHA-256 of the fixture snippet, **not** of the current upstream case page.

## Boundaries and operational blockers

- The pilot verified the official case page, but could **not fetch and archive the raw Stortinget \`/eksport/sak?sakid=89016\` API response** from this environment. Before claiming generalized API parity, separately validate JSON/XML shape against real response bytes and correct IDs, in particular one-to-many edges and source corrections.
- A caller can lie about \`source_body\`; a trusted URL string alone does not authenticate its bytes. This probe is therefore suitable for controlled, source-reviewed fixtures **only**. Production requires fetch provenance, an allowed-origin client, observed timestamp, immutable body digest and source retention under applicable terms, before returning an unqualified publicly attested claim.
- This implements **one deterministic \`LOV ↔ Lovvedtak ↔ sak_id\` path** where one case page explicitly gives both numbers. It does not implement generalized law-to-case discovery, an official source downloader, storage, versioning, vote/§ joins, MCP, or a Politik consumer.
- Page layout/wording variants, decisions that result in more than one law, and historical gaps require separate measurements. Multiple distinct identities currently fail closed as \`ambiguous\`, even if a human could resolve the pair.
- Publication location (\`lovverk/enrichment/...\` or separate sidecar), legal-source rights and crosswalk schema remain gated by ADR-0017 / #610; this PR changes none of them.

## Next scoped PRs

1. **Authenticated evidence capture**: source-allowed, rate-limited Stortinget client, complete raw response bytes, URL, digest, timestamp and durable source manifest; never scrape Lovdata HTML.
2. **API parser**: join \`Stortinget sak.publikasjon_referanse_liste\` and \`saker.henvisning\` against \`(session, Lovvedtak)\`; compare results with the official case page; test ambiguous/duplicate/multi-act cases. Reuse Politik knowledge without introducing a live Politik dependency.
3. **Crosswalk publication decision**: owner review of licences, artifact format and placement. Export only independently checked evidence; preserve explicit unknown.
4. **Politik adapter** under [#177](https://github.com/bartoszkobylinski/norwegian_parilamentary/issues/177), only after an accepted artifact contract.

## Test evidence

Offline local run on the exact pilot module/test fixture: **11 passed** (Python 3.13, pytest). Full repository gates (\`ruff\`, \`mypy\`, all unit/security/CI checks) were **not available locally**; GitHub PR checks remain authoritative. No claim of end-to-end or production readiness.
