# Real Stortinget sak XML

Exact response bodies of `https://data.stortinget.no/eksport/sak?sakid={id}`,
captured once by `scripts/pilot_e4.py` for the E4 pilot (ADR-0017, #610). URL,
retrieval time and SHA-256 of each file are in
`docs/research/data/pilot-e4-evidence-2026-11-01.json`. Never reformat these
files: the test suite compares their bytes to the recorded digests.

Source: Stortinget, data.stortinget.no, under the Norwegian Licence for Open
Government Data (NLOD). A digest proves which bytes were read, not their legal
effect. Raw Lovdata XML is not stored here or anywhere else in this repository.
