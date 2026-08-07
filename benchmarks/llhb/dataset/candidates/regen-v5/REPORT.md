# LLHB v1 — Stage 3 candidate-pool report

Pre-freeze development artifact. No benchmark model has been run;
no final selection has been made; nothing here is frozen.

## Corpus pin

- lovverk commit: `6ec7059d53d25ddae99d8a64bf5157a90c4c166c`
- manifest generated_at: `2026-08-04T20:48:16.533742+00:00`
- lovspor generator commit: `484887588a949e9a8b021bf6f7c83deba0a1e4b0`
- generated: 2026-08-07T16:36:34.186902+00:00 (seed 20260805)
- versions: {"abbreviations": "llhb-abbrev-v1", "stance_rules": "llhb-stance-v1", "templates": "llhb-templates-v3", "topic_filter": "llhb-topic-filter-v2"}

## Counts

- emitted: 385 ({"C1": 75, "C2": 65, "C3": 55, "C4": 50, "C5": 15, "C6": 55, "C7": 40, "C8": 30})
- valid after validation + dedup: 385 ({"C1": 75, "C2": 65, "C3": 55, "C4": 50, "C5": 15, "C6": 55, "C7": 40, "C8": 30})
- targets: {"C1": 75, "C2": 65, "C3": 55, "C4": 50, "C5": 30, "C6": 55, "C7": 40, "C8": 30}
- rejected/quarantined: 0 (codes: {})
- exact duplicates removed: 0
- near-duplicate flags: 13
- phrasing: {"llm_assisted": 0, "template": 385} (template-only; no LLM phrasing used)

## Diversity

- acts inventoried: 320
- unique acts in pool: 251
- top acts: [["-", 30], ["utleggsregistreringsforskriften", 3], ["forskrift-om-erstatning-for-tap-av-tamrein", 3], ["forskrift-om-utskriving-m-v-av-terminskatt", 3], ["forskrift-om-utm\u00e5ling-av-tvangsmulkt-og-overtredelsesgebyr", 3], ["forskrift-om-avtalefestet-pensjon-for-medlemmer-av-statens-pensjonskasse-f\u00f8dt-i-1963-eller-senere", 3], ["forskrift-om-utfyllende-regler-til-det-forenklede-solvenskapitalkravet-for-pensjonskasser", 3], ["forskrift-om-frakt-av-last-p\u00e5-norske-skip-og-lektere", 3]]
- section-id shapes: {"hyphen": 41, "letter": 4, "other": 1, "plain": 254}
- max provision reuse within a category: 1

## C5 ambiguity population (real, not manufactured)

- duplicate-section-id documents in corpus: 7
- C5 candidates emitted: 15 (duplicate-id: 15, repealed-as-current: 0)

## Manual review

- queue size: 91
- C8 candidates (100% mandatory review): 30

## Act-name calibration

- {"collision_count": 154, "docs_without_short_name": 3957, "docs_without_short_name_sample": ["12-pax-forskriften", "admin-instruks-for-pensjonsordningen-for-apotekvirksomhet", "agnforsyningsloven", "aif-forskriften", "aif-loven", "alternativ-behandlingsloven-albhl", "andre-arter-forskriften", "anerkjennelse-av-norges-røde-kors-rett", "anerkjennelse-av-visse-typer-legitimasjonsdokumenter-ved-reise-til-svalbard-jf-forskrift-om-kontroll-av-reisende-til-og-fra-svalbard-4-annet-ledd", "ankringsforskriften-09", "ansvaret-for-samfunnssikkerhet-i-sivil-sektor-på-nasjonalt-nivå-og-justis-og-beredskapsdepartementets-samordningsrolle-innen-samfunnssikkerhet-og-ikt-sikkerhet", "ansvarlighetsloven-riksrl", "apotekpensjonsloven", "arbeidstakeroppfinnelsesloven", "atomenergiloven-atomenl", "auksjonsforskriften-2020", "auksjonsforskriften-2022", "auksjonsforskriften-2024", "auksjonsloven", "avgiftsregulativ-for-sterkstrømanlegg", "avtaleloven-avtl", "avvikling-av-nasjonalt-råd-for-spesialistutdanning", "barneombudsloven", "beitelova", "bemanningsforskriften-2009"], "documents": 5923, "keys": 11936}
- collision keys: 154
