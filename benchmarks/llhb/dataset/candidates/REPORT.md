# LLHB v1 — Stage 3 candidate-pool report

Pre-freeze development artifact. No benchmark model has been run;
no final selection has been made; nothing here is frozen.

## Corpus pin

- lovverk commit: `6ec7059d53d25ddae99d8a64bf5157a90c4c166c`
- manifest generated_at: `2026-08-04T20:48:16.533742+00:00`
- lovspor generator commit: `8137cec7f0a1e33dbb04477149bbb2eeaa2b16aa`
- generated: 2026-08-05T07:09:19.495229+00:00 (seed 20260805)
- versions: {"abbreviations": "llhb-abbrev-v1", "stance_rules": "llhb-stance-v1", "templates": "llhb-templates-v1"}

## Counts

- emitted: 400 ({"C1": 75, "C2": 65, "C3": 55, "C4": 50, "C5": 30, "C6": 55, "C7": 40, "C8": 30})
- valid after validation + dedup: 390 ({"C1": 75, "C2": 60, "C3": 55, "C4": 50, "C5": 30, "C6": 55, "C7": 40, "C8": 25})
- targets: {"C1": 75, "C2": 65, "C3": 55, "C4": 50, "C5": 30, "C6": 55, "C7": 40, "C8": 30}
- rejected/quarantined: 0 (codes: {})
- exact duplicates removed: 10
- near-duplicate flags: 61
- phrasing: {"llm_assisted": 0, "template": 400} (template-only; no LLM phrasing used)

## Diversity

- acts inventoried: 320
- unique acts in pool: 270
- top acts: [["-", 25], ["forskrift-om-tilskudd-til-frivilligsentraler", 2], ["forskrift-om-tvistel\u00f8sningsnemnd-etter-aml", 2], ["forskrift-om-midler-til-satsing-p\u00e5-b\u00e6rekraftig-matproduksjon-og-verdiskaping-i-nord", 2], ["ikrafttr-mv-av-lov-2003-43-endr-i-vpfl", 2], ["priips-forskriften", 2], ["forskrift-om-et-r\u00e5d-for-drivstoffberedskap-og-drivstoffn\u00e6ringens-beredskapsplikter", 2], ["otp-loven", 2]]
- section-id shapes: {"hyphen": 48, "letter": 1, "other": 1, "plain": 243}
- max provision reuse within a category: 1

## C5 ambiguity population (real, not manufactured)

- duplicate-section-id documents in corpus: 8
- C5 candidates emitted: 30 (duplicate-id: 13, repealed-as-current: 17)

## Manual review

- queue size: 119
- C8 candidates (100% mandatory review): 25

## Act-name calibration

- {"collision_count": 154, "docs_without_short_name": 5118, "docs_without_short_name_sample": ["12-pax-forskriften", "admin-instruks-for-pensjonsordningen-for-apotekvirksomhet", "agnforsyningsloven", "aif-forskriften", "aif-loven", "alternativ-behandlingsloven-albhl", "andre-arter-forskriften", "anerkjennelse-av-norges-røde-kors-rett", "anerkjennelse-av-visse-typer-legitimasjonsdokumenter-ved-reise-til-svalbard-jf-forskrift-om-kontroll-av-reisende-til-og-fra-svalbard-4-annet-ledd", "ankringsforskriften-09", "ansvaret-for-arbeidsforskningsinstituttet-as", "ansvaret-for-arbeidsmiljø-og-sikkerhetsavd", "ansvaret-for-samfunnssikkerhet-i-sivil-sektor-på-nasjonalt-nivå-og-justis-og-beredskapsdepartementets-samordningsrolle-innen-samfunnssikkerhet-og-ikt-sikkerhet", "ansvarlighetsloven-riksrl", "apotekpensjonsloven", "arbeidstakeroppfinnelsesloven", "atomenergiloven-atomenl", "atp-forskriften", "auksjonsforskriften-2020", "auksjonsforskriften-2022", "auksjonsforskriften-2024", "auksjonsloven", "autorisasjon-av-verksteder", "avgiftsregulativ-for-sterkstrømanlegg", "avhendingsinstruksen"], "documents": 5923, "keys": 11936}
- collision keys: 154
