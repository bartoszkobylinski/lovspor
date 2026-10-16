# Stortinget sak XML capture — ADR-0017, Lovspor #610

**Status:** experimental source-ingestion slice (2026-10-14). This is not a production crosswalk, public API, MCP tool or vote attribution layer.

## Why a second slice?

[Lovspor PR #613](https://github.com/bartoszkobylinski/lovspor/pull/613) showed that the official [Stortinget case page for sak 89016](https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=89016) directly identifies `Lovvedtak 85 (2021–2022)` together with `Sanksjonert 17.06.2022 (lov63)`. That pilot accepted a **caller-supplied excerpt** rather than independently capturing the government response. This slice adds the independently usable Stortinget **XML** retrieval/storage boundary.

Official [`sak` API documentation](https://data.stortinget.no/dokumentasjon-og-hjelp/sak/) specifies `https://data.stortinget.no/eksport/sak?sakid={id}` and a namespaced `detaljert_sak` document with `id`, `sak_sesjon`, `henvisning` and typed `publikasjon_referanse_liste/publikasjon_referanse` (`type=lovvedtak` is a documented type). The [data-use terms](https://data.stortinget.no/om-datatjenesten/bruksvilkar/) invoke NLOD, mandate attribution to Stortinget and set 100 API calls/min. A single read does not implement bulk-rate orchestration: all future bulk fetching must honor that limit.

## Implemented in this slice

- `fetch_sak(client, sak_id, observed_at=...)` makes **one** strictly named HTTPS request to `data.stortinget.no/eksport/sak` (no redirects). It accepts a supplied `httpx.Client` for explicit timeout/retry policies and offline `MockTransport` tests; 429 and failures do not masquerade as empty results.
- The returned `CaseSnapshot` contains a full **HTTP-decoded XML response body**, SHA-256 of exactly those bytes, official request URL, acquisition timestamp, requested case id and returned content type. Never use a web-rendered excerpt digest as if it were a source payload digest.
- The reader enforces a **2,000,000-byte decoded response limit**, fails closed on HTML/non-XML, mismatched IDs, unexpected namespaces and DTDs; XML parsing disables network/DTD entity loading.
- `parse_sak` extracts only typed `lovvedtak` publication references from the official XML tree, with exact parliamentary `(session, number)` identity; the title and free-text `henvisning` alone never establish a link. Missing/multiple/conflicting decisions have explicit outcomes.
- `archive_sak(snapshot, directory)` stores immutable content-addressed XML and a metadata file, plus per-observation timestamp manifests. This is an **opt-in local cache path**, not a new directory in `lovverk` or public publication format.
- Offline unit tests use an **invented but schema-shaped XML fixture**, deliberately *not* a forged claim that the actual raw XML for case 89016 has been captured. Tests check source URL, bytes/digest, strict typed reference, wrong case, missing/conflicting references, malformed/DTD payloads, redirect, 429, HTML, body bounds and immutable local archive.

## Trust and coverage limitations

1. A `CaseSnapshot` from a trusted `fetch_sak` execution pins bytes fetched from the configured official endpoint at one time. Snapshots constructed by a caller or returned by mocked HTTP transport do **not** independently prove origin. Storage alone adds no authentication.
2. This environment has **not** retrieved the real case-89016 XML export. Actual `publikasjon_referanse` values for that case remain **unconfirmed in the API**; coverage on a captured real response is still a required integration proof. The HTML page from PR #613 remains separate, verified source evidence.
3. A typed `Lovvedtak` reference supports `sak_id ↔ (session, Lovvedtak)`, **not** the `Lovdata LOV-id ↔ Lovvedtak` half, `vote ↔ exact paragraph`, legal validity, or anyone's authorship of a legal provision.
4. No scheduled crawler, rate limiter for batches, backfill, corpus publication, evidence-signature mechanism or Politik adapter is included. Choose the artifact placement/schema only after owner review and measured real XML fixtures; ADR-0017 remains controlling.
5. The local snapshot directory is chosen by the operator and must be access-controlled outside the Git-tracked legal corpus. Stortinget is the attribution source; a cache digest is not a legal citation.

## Next step once the two PRs pass review

Run a controlled **single-case integration capture** on a host with working access to `data.stortinget.no`:

```python
import httpx
from pathlib import Path
from lovspor.stortinget_case_source import fetch_sak, parse_sak, archive_sak, LawDecision, resolve_publication

with httpx.Client(timeout=20, follow_redirects=False) as client:
    snapshot = fetch_sak(client, 89016)
case = parse_sak(snapshot)
print(case.sak_id, case.session_id, case.lovvedtak)
print(resolve_publication(case, LawDecision("2021-2022", 85)))
archive_sak(snapshot, Path("data/cache/stortinget-saker"))
```

After this succeeds, check the raw case publication references against [the official Lovvedtak](https://www.stortinget.no/no/Saker-og-publikasjoner/Vedtak/Beslutninger/Lovvedtak/2021-2022/vedtak-202122-085/) and the Lovdata-source identity from the existing legal corpus, then introduce the complete law↔decision↔case chain with no fuzzy joins.
