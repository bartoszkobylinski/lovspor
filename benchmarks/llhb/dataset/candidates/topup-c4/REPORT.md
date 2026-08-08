# LLHB v1 — Stage 3 candidate-pool report

Pre-freeze development artifact. No benchmark model has been run;
no final selection has been made; nothing here is frozen.

## Corpus pin

- lovverk commit: `6ec7059d53d25ddae99d8a64bf5157a90c4c166c`
- manifest generated_at: `2026-08-04T20:48:16.533742+00:00`
- lovspor generator commit: `c5a47f8300cd98d287a783048bedc22e3f4b0416`
- generated: 2026-08-08T09:22:01.781587+00:00 (seed 20260808)
- versions: {"abbreviations": "llhb-abbrev-v1", "stance_rules": "llhb-stance-v1", "templates": "llhb-templates-v3", "topic_filter": "llhb-topic-filter-v2"}

## Counts

- emitted: 50 ({"C4": 50})
- valid after validation + dedup: 50 ({"C4": 50})
- targets: {"C1": 0, "C2": 0, "C3": 0, "C4": 50, "C5": 0, "C6": 0, "C7": 0, "C8": 0}
- rejected/quarantined: 0 (codes: {})
- exact duplicates removed: 0
- near-duplicate flags: 0
- phrasing: {"llm_assisted": 0, "template": 50} (template-only; no LLM phrasing used)

## Diversity

- acts inventoried: 320
- unique acts in pool: 50
- top acts: [["sanksjonsforskrift-ukraina-territoriell-integritet-mv", 1], ["kryptoeiendelsloven", 1], ["forskrift-om-flyselskaper-med-driftsforbud", 1], ["forskrift-om-takseringsregler-til-bruk-ved-beskatning-ved-trekk-i-l\u00f8nn-mv-av-personer-som-skattlegges-p\u00e5-svalbard-i-inntekts\u00e5ret-2026-etter-lov-29-november-1996-nr-68-om-skatt-til-svalbard", 1], ["lov-om-gjennomf\u00f8ring-av-roma-vedtektene", 1], ["teknisk-og-operasjonell-forskrift", 1], ["forskrift-om-varef\u00f8rselskontroll-p\u00e5-svalbard", 1], ["forskrift-for-graden-philosophiae-doctor-i-kunstnerisk-utviklingsarbeid-ved-universitetet-i-bergen", 1]]
- section-id shapes: {"hyphen": 4, "plain": 46}
- max provision reuse within a category: 1

## C5 ambiguity population (real, not manufactured)

- duplicate-section-id documents in corpus: 7
- C5 candidates emitted: 0 (duplicate-id: 0, repealed-as-current: 0)

## Manual review

- queue size: 5
- C8 candidates (100% mandatory review): 0

## Act-name calibration

- {"collision_count": 154, "docs_without_short_name": 3957, "docs_without_short_name_sample": ["12-pax-forskriften", "admin-instruks-for-pensjonsordningen-for-apotekvirksomhet", "agnforsyningsloven", "aif-forskriften", "aif-loven", "alternativ-behandlingsloven-albhl", "andre-arter-forskriften", "anerkjennelse-av-norges-røde-kors-rett", "anerkjennelse-av-visse-typer-legitimasjonsdokumenter-ved-reise-til-svalbard-jf-forskrift-om-kontroll-av-reisende-til-og-fra-svalbard-4-annet-ledd", "ankringsforskriften-09", "ansvaret-for-samfunnssikkerhet-i-sivil-sektor-på-nasjonalt-nivå-og-justis-og-beredskapsdepartementets-samordningsrolle-innen-samfunnssikkerhet-og-ikt-sikkerhet", "ansvarlighetsloven-riksrl", "apotekpensjonsloven", "arbeidstakeroppfinnelsesloven", "atomenergiloven-atomenl", "auksjonsforskriften-2020", "auksjonsforskriften-2022", "auksjonsforskriften-2024", "auksjonsloven", "avgiftsregulativ-for-sterkstrømanlegg", "avtaleloven-avtl", "avvikling-av-nasjonalt-råd-for-spesialistutdanning", "barneombudsloven", "beitelova", "bemanningsforskriften-2009"], "documents": 5923, "keys": 11936}
- collision keys: 154
