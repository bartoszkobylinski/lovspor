# Legal and Sources

## Data sources

This project consumes only the Lovdata public-data API:

- `GET https://api.lovdata.no/v1/publicData/list` — lists available archives
- `GET https://api.lovdata.no/v1/publicData/get/gjeldende-lover.tar.bz2` — current laws
- `GET https://api.lovdata.no/v1/publicData/get/gjeldende-sentrale-forskrifter.tar.bz2` — current central regulations

## License

The data is provided under [Norsk lisens for offentlige data (NLOD) 2.0](https://data.norge.no/nlod/no/2.0/).

NLOD 2.0 grants the right to copy, use, modify, and redistribute the data, including commercially, provided that:

1. The licensor (Lovdata) is named.
2. Changes to the data are clearly indicated.
3. The license is referenced.

## Attribution in output

Every Markdown file produced by this engine carries attribution in YAML front matter:

```yaml
source_provider: "Lovdata"
source_dataset: "gjeldende-lover"
source_license: "NLOD 2.0"
```

The `lovverk` repo's README contains the full attribution notice.

## What we do NOT do

- We do **not** scrape `lovdata.no` website HTML. (Lovdata's regular brukervilkår forbid this for AI use.)
- We do **not** redistribute Lovdata's raw XML files in this repo or in `lovverk`.
- We do **not** consume content covered by Lovdata's regular brukervilkår.

## What we do

- We consume only the public-data tarballs licensed under NLOD 2.0.
- We render the consolidated XML into Markdown.
- We commit only the rendered Markdown derivative + a manifest of XML hashes for verifiability.
- We never commit raw XML to a public repo. Conservative posture: sidesteps any argument about Lovdata's editorial markup being copyrightable.

## Local law: the observatory (ADR-0010)

`Lokale forskrifter` are not in the free Lovdata dataset tier and no permitted bulk
source for Lovtidend Avdeling II is known, so they are captured — not ingested —
from the authorities that publish them. The posture is deliberately narrower than
the canonical pipeline's:

- **Eligible** sources are official kommune and fylkeskommune sites, plus the openly
  documented bulk Lovtidend **Avdeling I** dataset on data.norge.no.
- Eligibility is not permission. **Activating** a source requires a recorded
  per-source access-policy check — `robots.txt`, site terms, rate limit, identifying
  User-Agent, named reviewer — and the check records its *outcome*, not merely that
  someone looked. `src/lovspor/observatory/registry.py` refuses an active source
  without one.
- `lovdata.no` stays **denied for crawling**, centrally and unconditionally: its terms
  forbid *massenedlasting*, and registering the host cannot unlock it. The register
  may be consulted by hand; it is never harvested.
- Every fetch re-reads `robots.txt` live, waits out the source's own rate limit, and
  does not follow redirects off the authorised host. A refusal is recorded, so
  compliance is demonstrable from the log rather than asserted here.
- **May fetch ≠ may redistribute.** Observed bytes live outside this repo and outside
  `lovverk`, and are not published anywhere until a per-source redistribution basis
  exists. A municipality-hosted copy of a Lovtidend document carries its own
  provenance and is never relabelled as a Lovdata/NLOD artifact.
- Observed material is evidence that specific bytes were retrievable at a recorded
  time — not an assertion of law, and not even of legal publication. It reaches the
  canonical corpus only through an explicit per-artifact promotion step.

## Verification

Anyone can verify our output by:

1. Downloading the same tarball from Lovdata API.
2. Running this engine on it.
3. Comparing the resulting Markdown and SHA256 hashes against the corresponding `lovverk` commit.

Determinism is enforced by tests. See `tests/unit/test_rendering_markdown_renderer.py`.
