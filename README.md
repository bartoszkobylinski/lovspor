# lovspor

Norwegian law change tracker. Engine that produces the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) corpus from Lovdata's public-data API.

## Status

Early scaffold. Not functional yet.

## Sources

- `https://api.lovdata.no/v1/publicData/get/gjeldende-lover.tar.bz2` — current Norwegian laws
- `https://api.lovdata.no/v1/publicData/get/gjeldende-sentrale-forskrifter.tar.bz2` — current central regulations

Data is licensed under [Norsk lisens for offentlige data (NLOD) 2.0](https://data.norge.no/nlod/no/2.0/).

## License

The engine code in this repository is licensed under MIT. See [LICENSE](LICENSE).

The legal text produced by this engine is published in the [`lovverk`](https://github.com/bartoszkobylinski/lovverk) repository under NLOD 2.0, with attribution to Lovdata.

## Related work

- [`cloveras/lovdata2`](https://github.com/cloveras/lovdata2) — JSON tooling and MCP server for the same Lovdata public data. `lovspor` is complementary, focused on Markdown rendering and Git-based change tracking.
