# LLHB v1 — Stage 3 candidate-pool report

Pre-freeze development artifact. No benchmark model has been run;
no final selection has been made; nothing here is frozen.

## Corpus pin

- lovverk commit: `6ec7059d53d25ddae99d8a64bf5157a90c4c166c`
- manifest generated_at: `2026-08-04T20:48:16.533742+00:00`
- lovspor generator commit: `ca546fbc81a1e4497af885244fee0a7911b09cd8`
- generated: 2026-08-05T22:28:43.981066+00:00 (seed 20260805)
- versions: {"abbreviations": "llhb-abbrev-v1", "stance_rules": "llhb-stance-v1", "templates": "llhb-templates-v2", "topic_filter": "llhb-topic-filter-v1"}

## Counts

- emitted: 381 ({"C1": 75, "C2": 65, "C3": 55, "C4": 50, "C5": 11, "C6": 55, "C7": 40, "C8": 30})
- valid after validation + dedup: 381 ({"C1": 75, "C2": 65, "C3": 55, "C4": 50, "C5": 11, "C6": 55, "C7": 40, "C8": 30})
- targets: {"C1": 75, "C2": 65, "C3": 55, "C4": 50, "C5": 30, "C6": 55, "C7": 40, "C8": 30}
- rejected/quarantined: 0 (codes: {})
- exact duplicates removed: 0
- near-duplicate flags: 4
- phrasing: {"llm_assisted": 0, "template": 381} (template-only; no LLM phrasing used)

## Diversity

- acts inventoried: 320
- unique acts in pool: 242
- top acts: [["-", 30], ["utleggsregistreringsforskriften", 3], ["forskrift-om-fordeling-av-norsk-tippings-overskudd-til-kulturform\u00e5l", 3], ["klimakvoteforskriften", 3], ["forskrift-om-avskrivning-p\u00e5-driftsmidler", 3], ["forskrift-om-erstatning-for-tap-av-tamrein", 3], ["forskrift-om-utskriving-m-v-av-terminskatt", 3], ["forskrift-om-naturfaretilskudd", 3]]
- section-id shapes: {"hyphen": 52, "letter": 2, "other": 2, "plain": 240}
- max provision reuse within a category: 1

## C5 ambiguity population (real, not manufactured)

- duplicate-section-id documents in corpus: 7
- C5 candidates emitted: 11 (duplicate-id: 11, repealed-as-current: 0)

## Manual review

- queue size: 81
- C8 candidates (100% mandatory review): 30

## Act-name calibration

- {"collision_count": 154, "docs_without_short_name": 5118, "docs_without_short_name_sample": ["12-pax-forskriften", "admin-instruks-for-pensjonsordningen-for-apotekvirksomhet", "agnforsyningsloven", "aif-forskriften", "aif-loven", "alternativ-behandlingsloven-albhl", "andre-arter-forskriften", "anerkjennelse-av-norges-røde-kors-rett", "anerkjennelse-av-visse-typer-legitimasjonsdokumenter-ved-reise-til-svalbard-jf-forskrift-om-kontroll-av-reisende-til-og-fra-svalbard-4-annet-ledd", "ankringsforskriften-09", "ansvaret-for-arbeidsforskningsinstituttet-as", "ansvaret-for-arbeidsmiljø-og-sikkerhetsavd", "ansvaret-for-samfunnssikkerhet-i-sivil-sektor-på-nasjonalt-nivå-og-justis-og-beredskapsdepartementets-samordningsrolle-innen-samfunnssikkerhet-og-ikt-sikkerhet", "ansvarlighetsloven-riksrl", "apotekpensjonsloven", "arbeidstakeroppfinnelsesloven", "atomenergiloven-atomenl", "atp-forskriften", "auksjonsforskriften-2020", "auksjonsforskriften-2022", "auksjonsforskriften-2024", "auksjonsloven", "autorisasjon-av-verksteder", "avgiftsregulativ-for-sterkstrømanlegg", "avhendingsinstruksen"], "documents": 5923, "keys": 11936}
- collision keys: 154
