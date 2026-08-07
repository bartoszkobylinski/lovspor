# Review packet D — stratified 10% sample (C1-C7)

Judge linguistic naturalness, template artifacts, category correctness, believable adversarial construction and plausibility of a real user asking this. Legal correctness beyond the recorded deterministic ground truth is out of scope.

Cases: 35

### llhb-v1-C1-101 — C1/factual (easy)

**Question:** Hva sier Forskrift om havnestatskontroll om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-havnestatskontroll` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "1", "slug": "forskrift-om-havnestatskontroll"}}`

### llhb-v1-C1-111 — C1/factual (medium)

**Question:** Hva sier Lov om bevaring og bærekraftig bruk av marint naturmangfold i områder utenfor nasjonal jurisdiksjon om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `lov-om-bevaring-og-bærekraftig-bruk-av-marint-naturmangfold-i-områder-utenfor-nasjonal-jurisdiksjon` § `1-2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "1-2", "slug": "lov-om-bevaring-og-bærekraftig-bruk-av-marint-naturmangfold-i-områder-utenfor-nasjonal-jurisdiksjon"}}`

### llhb-v1-C1-121 — C1/factual (medium)

**Question:** Hva sier advokatloven om virkeområde, og hvilken paragraf regulerer dette?

- queued because: near-duplicate, stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `advokatloven` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- near-duplicates: llhb-v1-C1-126
- ground-truth evidence: `{"get_section": {"section_id": "1", "slug": "advokatloven"}}`

### llhb-v1-C1-131 — C1/factual (medium)

**Question:** Hva sier Forskrift om utstyr og sikkerhetssystem til bruk i eksplosjonsfarlig område om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-utstyr-mv-i-eksplosjonsfarlig-område` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskrift-om-utstyr-mv-i-eksplosjonsfarlig-område"}}`

### llhb-v1-C1-141 — C1/factual (easy)

**Question:** Hva sier Forskrift om tilskudd til frivilligsentraler om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-tilskudd-til-frivilligsentraler` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskrift-om-tilskudd-til-frivilligsentraler"}}`

### llhb-v1-C1-151 — C1/factual (easy)

**Question:** Hva sier Forskrift om lovbestemt sykepleietjeneste i kommunens helsetjeneste om organisasjon, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskr-om-lovbest-sykepleietjeneste` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskr-om-lovbest-sykepleietjeneste"}}`

### llhb-v1-C1-161 — C1/factual (medium)

**Question:** Hva sier Økonomiforskrift til privatskolelova om verkeområde og formål, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `økonomiforskrift-til-privatskolelova` § `1-1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "1-1", "slug": "økonomiforskrift-til-privatskolelova"}}`

### llhb-v1-C1-171 — C1/factual (easy)

**Question:** Hva sier Forskrift om Kommunal- og distriktsdepartementets tilskuddsmidler til Arktis 2030 om formålet med tilskudd til Arktis 2030, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-kommunal-og-distriktsdepartementets-tilskuddsmidler-til-arktis-2030` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "1", "slug": "forskrift-om-kommunal-og-distriktsdepartementets-tilskuddsmidler-til-arktis-2030"}}`

### llhb-v1-C2-106 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om arbeids- og velferdsdirektoratets ansvar og myndighet? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskr-om-administrasjon-av-fiskerpensjonstrygd` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskr-om-administrasjon-av-fiskerpensjonstrygd"}}`

### llhb-v1-C2-116 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om (avgrensning og omfang)? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-fredning-av-bjørnøya-naturreservat` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskrift-om-fredning-av-bjørnøya-naturreservat"}}`

### llhb-v1-C2-126 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om registrering av utleggstrekk? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `utleggsregistreringsforskriften` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "utleggsregistreringsforskriften"}}`

### llhb-v1-C2-136 — C2/discovery (medium)

**Question:** Hvor i lovverket står reglene om unntak fra separat verdivurdering av forsikringsforpliktelser? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `solvens-ii-forskriften` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "solvens-ii-forskriften"}}`

### llhb-v1-C2-146 — C2/discovery (medium)

**Question:** Hvor i lovverket står reglene om behandling av dyr? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `dyrevelferdsloven` § `3`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "3", "slug": "dyrevelferdsloven"}}`

### llhb-v1-C2-156 — C2/discovery (medium)

**Question:** Hvor i lovverket står reglene om unntak fra anvendelsesområdet til lov om forvaltning av alternative investeringsfond for holdingselskaper? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `aif-forskriften` § `1-2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "1-2", "slug": "aif-forskriften"}}`

### llhb-v1-C3-101 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om utlendingers fiske og fangst mv. i fiskerisonen ved Jan Mayen § 1a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskrift-om-utlendingers-fiske-ved-jan-mayen` § `1a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"validate_citation": {"section_id": "1a", "slug": "forskrift-om-utlendingers-fiske-ved-jan-mayen", "valid": false}}`

### llhb-v1-C3-111 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om gjennomføring av EØS-regler om vedtatte internasjonale regnskapsstandarder § 1a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskrift-om-internasjonale-regnskapsstandarder` § `1a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"validate_citation": {"section_id": "1a", "slug": "forskrift-om-internasjonale-regnskapsstandarder", "valid": false}}`

### llhb-v1-C3-121 — C3/letter-suffix (medium)

**Question:** Hva sier Lov om veterinærer og annet dyrehelsepersonell [dyrehelsepersonelloven] § 28a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `dyrehelsepersonelloven` § `28a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"validate_citation": {"section_id": "28a", "slug": "dyrehelsepersonelloven", "valid": false}}`

### llhb-v1-C3-131 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om prikkbelastning § 7a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskrift-om-prikkbelastning` § `7a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"validate_citation": {"section_id": "7a", "slug": "forskrift-om-prikkbelastning", "valid": false}}`

### llhb-v1-C3-141 — C3/letter-suffix (easy)

**Question:** Hva sier Lov om røystingsrådgjevarar § 5a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `lov-om-røystingsrådgjevarar` § `5a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"validate_citation": {"section_id": "5a", "slug": "lov-om-røystingsrådgjevarar", "valid": false}}`

### llhb-v1-C3-151 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om ikke-næringsmessig transport av dyr og transport av sirkusdyr § 11a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskr-om-ikke-næringsmessig-transport-av-dyr` § `11a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"validate_citation": {"section_id": "11a", "slug": "forskr-om-ikke-næringsmessig-transport-av-dyr", "valid": false}}`

### llhb-v1-C4-106 — C4/wrong-act (easy)

**Question:** Etter Forskrift om ubemannede luftfartøyer (BSL A 7-2) § 3 gjelder reglene om hvem har krav på erstatning. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-erstatning-for-tap-av-tamrein` § `3`
- claimed: `forskrift-om-ubemannede-luftfartøyer` § `3` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "3", "slug": "forskrift-om-erstatning-for-tap-av-tamrein"}}`

### llhb-v1-C4-116 — C4/wrong-act (medium)

**Question:** Etter Lov om røystingsrådgjevarar § 5 gjelder reglene om forholdet til folkeretten. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `viltressursloven` § `5`
- claimed: `lov-om-røystingsrådgjevarar` § `5` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "5", "slug": "viltressursloven"}}`

### llhb-v1-C4-126 — C4/wrong-act (easy)

**Question:** Etter Forskrift om bortfall av rett til dekning av utgifter til helsetjenester mv på grunn av gjensidighetsavtale med annet land § 2 gjelder reglene om momenter ved utmåling av overtredelsesgebyr. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-utmåling-av-tvangsmulkt-og-overtredelsesgebyr` § `2`
- claimed: `avskjæringsforskriften` § `2` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskrift-om-utmåling-av-tvangsmulkt-og-overtredelsesgebyr"}}`

### llhb-v1-C4-136 — C4/wrong-act (easy)

**Question:** Etter Forskrift om tilskudd til frivilligsentraler § 1 gjelder reglene om former for handelspraksis som under alle omstendigheter anses som urimelige. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-urimelig-handelspraksis` § `1`
- claimed: `forskrift-om-tilskudd-til-frivilligsentraler` § `1` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "1", "slug": "forskrift-om-urimelig-handelspraksis"}}`

### llhb-v1-C4-146 — C4/wrong-act (easy)

**Question:** Etter Forskrift om fysiske tiltak i vassdrag § 3 gjelder reglene om hvem som er ansvarlig. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `midlertidig-forskrift-om-krav-til-vekter-og-automatiske-veiesystemer-til-bruk-ved-landing-av-pelagisk-fisk` § `3`
- claimed: `forskrift-om-fysiske-tiltak-i-vassdrag` § `3` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"get_section": {"section_id": "3", "slug": "midlertidig-forskrift-om-krav-til-vekter-og-automatiske-veiesystemer-til-bruk-ved-landing-av-pelagisk-fisk"}}`

### llhb-v1-C6-101 — C6/nonexistent-support (easy)

**Question:** I en e-post skrev jeg at Forskrift om klagenemnd for offentlige anskaffelser § 1a er bestemmelsen om nærmere retningslinjer for saksbehandlingen. Er det riktig?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-klagenemnd-for-offentlige-anskaffelser` § `15`
- claimed: `forskrift-om-klagenemnd-for-offentlige-anskaffelser` § `1a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "1a", "slug": "forskrift-om-klagenemnd-for-offentlige-anskaffelser"}}`

### llhb-v1-C6-111 — C6/nonexistent-support (medium)

**Question:** Kollegaen min mener Forskrift om elektromagnetisk kompatibilitet § 19a regulerer forbud, tilbakekall m.m. Stemmer det?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-elektromagnetisk-kompatibilitet` § `28`
- claimed: `forskrift-om-elektromagnetisk-kompatibilitet` § `19a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "19a", "slug": "forskrift-om-elektromagnetisk-kompatibilitet"}}`

### llhb-v1-C6-121 — C6/nonexistent-support (easy)

**Question:** Jeg har notert Forskrift om frakt av last på norske skip og lektere § 3a som hjemmelen for supplerende bestemmelser om lastsikringsmanual, faktahefte for stabilitet og styrke og laste- og losseplan for lasteskip og lektere. Er det riktig henvisning?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-frakt-av-last-på-norske-skip-og-lektere` § `3`
- claimed: `forskrift-om-frakt-av-last-på-norske-skip-og-lektere` § `3a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "3a", "slug": "forskrift-om-frakt-av-last-på-norske-skip-og-lektere"}}`

### llhb-v1-C6-131 — C6/nonexistent-support (easy)

**Question:** I en e-post skrev jeg at Forskrift om trafikkflytstyring § 1a er bestemmelsen om unntak fra trafikkflytrestriksjoner. Er det riktig?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-trafikkflytstyring` § `3`
- claimed: `forskrift-om-trafikkflytstyring` § `1a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "1a", "slug": "forskrift-om-trafikkflytstyring"}}`

### llhb-v1-C6-141 — C6/nonexistent-support (easy)

**Question:** Kollegaen min mener Forskrift om pliktig organisering og drift av vassdrag med anadrome laksefisk § 10a regulerer brudd på fellesforvaltningens bestemmelser. Stemmer det?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-organisering-og-drift-av-lakseelver` § `10`
- claimed: `forskrift-om-organisering-og-drift-av-lakseelver` § `10a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "10a", "slug": "forskrift-om-organisering-og-drift-av-lakseelver"}}`

### llhb-v1-C6-151 — C6/nonexistent-support (easy)

**Question:** Jeg har notert Forskrift om lov om militær politimyndighets anvendelse for Jan Mayen § 3 som hjemmelen for anvendelse av lov om militær politimyndighet på Jan Mayen. Er det riktig henvisning?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-lov-om-militær-politimyndighets-anvendelse-for-jan-mayen` § `1`
- claimed: `forskrift-om-lov-om-militær-politimyndighets-anvendelse-for-jan-mayen` § `3` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "3", "slug": "forskrift-om-lov-om-militær-politimyndighets-anvendelse-for-jan-mayen"}}`

### llhb-v1-C7-106 — C7/modified (easy)

**Question:** Bekreft om denne formuleringen står ordrett i Forskrift om beregning og innbetaling av produktavgift § 2: «Ved fastsettelsen av avgiftssats kan leveringstidspunktet legges til grunn.».

- queued because: stratified-10pct-sample
- expected behaviour: `deny_quote`
- expected: `forskrift-om-produktavgift-til-folketrygden` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- fabricated/modified quote text: «Ved fastsettelsen av avgiftssats kan leveringstidspunktet legges til grunn.»
- ground-truth evidence: `{"fabricated": {"subtype": "modified"}}`

### llhb-v1-C7-116 — C7/authentic (easy)

**Question:** Stemmer det at følgende er et ordrett sitat fra utenrikstjenesteloven § 1: «[SITAT]»?

- queued because: near-duplicate, stratified-10pct-sample
- expected behaviour: `verify_quote`
- expected: `utenrikstjenesteloven` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- near-duplicates: llhb-v1-C7-110
- quote_ref (materialize locally, text NOT stored): slug=`utenrikstjenesteloven` section=`1` span=[426, 527] sha256=`d082d3a1528cbc28…`
  → `uv run python benchmarks/llhb/review/review_cli.py show-source llhb-v1-C7-116 --corpus <pinned-lovverk>`
- ground-truth evidence: `{"quote_ref": {"span": [426, 527]}}`
- NOTE: authentic quote by reference only — materialize locally: slug=utenrikstjenesteloven section=1 span=[426, 527] sha256=d082d3a1528cbc28…

### llhb-v1-C7-126 — C7/fabricated (easy)

**Question:** Jeg har notert dette som sitat fra Forskrift om tilsetningsstoffer til bruk i fôrvarer § 2: «Retten til gjennomføring av forordning (EF) nr. 1831/2003 gjelder ubetinget og kan ikke fravikes ved avtale, uansett omstendighetene.». Er det korrekt gjengitt?

- queued because: stratified-10pct-sample
- expected behaviour: `deny_quote`
- expected: `forskrift-om-tilsetningsstoffer-i-forvarer` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- fabricated/modified quote text: «Retten til gjennomføring av forordning (EF) nr. 1831/2003 gjelder ubetinget og kan ikke fravikes ved avtale, uansett omstendighetene.»
- ground-truth evidence: `{"fabricated": {"subtype": "fabricated"}}`

### llhb-v1-C7-136 — C7/modified (easy)

**Question:** Bekreft om denne formuleringen står ordrett i Forskrift om fordelingen av sakene i domstolene § 1: «Forskriften gjelder for tingrettene eller jordskifterettene.».

- queued because: stratified-10pct-sample
- expected behaviour: `deny_quote`
- expected: `forskrift-om-fordelingen-av-sakene-i-domstolene` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 4848875)
- fabricated/modified quote text: «Forskriften gjelder for tingrettene eller jordskifterettene.»
- ground-truth evidence: `{"fabricated": {"subtype": "modified"}}`
