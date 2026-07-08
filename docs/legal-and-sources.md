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

## Verification

Anyone can verify our output by:

1. Downloading the same tarball from Lovdata API.
2. Running this engine on it.
3. Comparing the resulting Markdown and SHA256 hashes against the corresponding `lovverk` commit.

Determinism is enforced by tests. See `tests/unit/test_rendering_markdown_renderer.py`.
