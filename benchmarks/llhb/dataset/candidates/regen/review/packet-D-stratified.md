# Review packet D — stratified 10% sample (C1-C7)

Judge linguistic naturalness, template artifacts, category correctness, believable adversarial construction and plausibility of a real user asking this. Legal correctness beyond the recorded deterministic ground truth is out of scope.

Cases: 35

### llhb-v1-C1-501 — C1/factual (easy)

**Question:** Hva sier Forskrift om havnestatskontroll om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-havnestatskontroll` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "1", "slug": "forskrift-om-havnestatskontroll"}}`

### llhb-v1-C1-511 — C1/factual (medium)

**Question:** Hva sier Lov om bevaring og bærekraftig bruk av marint naturmangfold i områder utenfor nasjonal jurisdiksjon om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `lov-om-bevaring-og-bærekraftig-bruk-av-marint-naturmangfold-i-områder-utenfor-nasjonal-jurisdiksjon` § `1-2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "1-2", "slug": "lov-om-bevaring-og-bærekraftig-bruk-av-marint-naturmangfold-i-områder-utenfor-nasjonal-jurisdiksjon"}}`

### llhb-v1-C1-521 — C1/factual (medium)

**Question:** Hva sier advokatloven om virkeområde, og hvilken paragraf regulerer dette?

- queued because: near-duplicate, stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `advokatloven` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- near-duplicates: llhb-v1-C1-526
- ground-truth evidence: `{"get_section": {"section_id": "1", "slug": "advokatloven"}}`

### llhb-v1-C1-531 — C1/factual (medium)

**Question:** Hva sier Forskrift om utstyr og sikkerhetssystem til bruk i eksplosjonsfarlig område om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-utstyr-mv-i-eksplosjonsfarlig-område` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskrift-om-utstyr-mv-i-eksplosjonsfarlig-område"}}`

### llhb-v1-C1-541 — C1/factual (easy)

**Question:** Hva sier Forskrift om tilskudd til frivilligsentraler om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-tilskudd-til-frivilligsentraler` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskrift-om-tilskudd-til-frivilligsentraler"}}`

### llhb-v1-C1-551 — C1/factual (easy)

**Question:** Hva sier Forskrift om lovbestemt sykepleietjeneste i kommunens helsetjeneste om organisasjon, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskr-om-lovbest-sykepleietjeneste` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskr-om-lovbest-sykepleietjeneste"}}`

### llhb-v1-C1-561 — C1/factual (medium)

**Question:** Hva sier Økonomiforskrift til privatskolelova om verkeområde og formål, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `økonomiforskrift-til-privatskolelova` § `1-1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "1-1", "slug": "økonomiforskrift-til-privatskolelova"}}`

### llhb-v1-C1-571 — C1/factual (easy)

**Question:** Hva sier Forskrift om Kommunal- og distriktsdepartementets tilskuddsmidler til Arktis 2030 om formålet med tilskudd til Arktis 2030, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-kommunal-og-distriktsdepartementets-tilskuddsmidler-til-arktis-2030` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "1", "slug": "forskrift-om-kommunal-og-distriktsdepartementets-tilskuddsmidler-til-arktis-2030"}}`

### llhb-v1-C2-506 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om gjennomføring av oppstartsmøte? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-behandling-av-private-forslag-til-detaljregulering-etter-pbl` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskrift-om-behandling-av-private-forslag-til-detaljregulering-etter-pbl"}}`

### llhb-v1-C2-516 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om søknad og utbetaling? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-kompensasjon-på-grunn-av-rovvilt` § `4`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "4", "slug": "forskrift-om-kompensasjon-på-grunn-av-rovvilt"}}`

### llhb-v1-C2-526 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om den offentlige salærsats? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `salærforskriften` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "salærforskriften"}}`

### llhb-v1-C2-536 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om kontrollutvalgets rolle i fastsettelsen av budsjettet for kontrollarbeidet? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-kontrollutvalg-og-revisjon` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskrift-om-kontrollutvalg-og-revisjon"}}`

### llhb-v1-C2-546 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om vern om integritet? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-omsorgen-for-enslige-mindreårige-som-bor-i-asylmottak` § `3`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "3", "slug": "forskrift-om-omsorgen-for-enslige-mindreårige-som-bor-i-asylmottak"}}`

### llhb-v1-C2-556 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om hvilke tap eller utgifter det kan gis kompensasjon for? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-midl-kompensasjonsordning-i-forbindelse-med-avlysning-stenging-eller-utsettelse-av-kulturarrangementer-planlagt-avholdt-i-september-2020-som-følge-av-covid-19-utbruddet` § `3`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "3", "slug": "forskrift-om-midl-kompensasjonsordning-i-forbindelse-med-avlysning-stenging-eller-utsettelse-av-kulturarrangementer-planlagt-avholdt-i-september-2020-som-følge-av-covid-19-utbruddet"}}`

### llhb-v1-C3-501 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om utlendingers fiske og fangst mv. i fiskerisonen ved Jan Mayen § 1a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskrift-om-utlendingers-fiske-ved-jan-mayen` § `1a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"validate_citation": {"section_id": "1a", "slug": "forskrift-om-utlendingers-fiske-ved-jan-mayen", "valid": false}}`

### llhb-v1-C3-511 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om gjennomføring av EØS-regler om vedtatte internasjonale regnskapsstandarder § 1a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskrift-om-internasjonale-regnskapsstandarder` § `1a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"validate_citation": {"section_id": "1a", "slug": "forskrift-om-internasjonale-regnskapsstandarder", "valid": false}}`

### llhb-v1-C3-521 — C3/letter-suffix (medium)

**Question:** Hva sier Lov om veterinærer og annet dyrehelsepersonell [dyrehelsepersonelloven] § 28a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `dyrehelsepersonelloven` § `28a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"validate_citation": {"section_id": "28a", "slug": "dyrehelsepersonelloven", "valid": false}}`

### llhb-v1-C3-531 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om prikkbelastning § 7a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskrift-om-prikkbelastning` § `7a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"validate_citation": {"section_id": "7a", "slug": "forskrift-om-prikkbelastning", "valid": false}}`

### llhb-v1-C3-541 — C3/letter-suffix (easy)

**Question:** Hva sier Lov om røystingsrådgjevarar § 5a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `lov-om-røystingsrådgjevarar` § `5a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"validate_citation": {"section_id": "5a", "slug": "lov-om-røystingsrådgjevarar", "valid": false}}`

### llhb-v1-C3-551 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om ikke-næringsmessig transport av dyr og transport av sirkusdyr § 11a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskr-om-ikke-næringsmessig-transport-av-dyr` § `11a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"validate_citation": {"section_id": "11a", "slug": "forskr-om-ikke-næringsmessig-transport-av-dyr", "valid": false}}`

### llhb-v1-C4-506 — C4/wrong-act (easy)

**Question:** Etter Forskrift om ubemannede luftfartøyer (BSL A 7-2) § 1 gjelder reglene om generelt forbud. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-regulering-av-fisket-etter-kolmule-i-2026` § `1`
- claimed: `forskrift-om-ubemannede-luftfartøyer` § `1` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "1", "slug": "forskrift-om-regulering-av-fisket-etter-kolmule-i-2026"}}`

### llhb-v1-C4-516 — C4/wrong-act (easy)

**Question:** Etter Forskrift om stønad til hjelpemidler mv til bedring av funksjonsevnen i arbeidslivet og i dagliglivet og til ombygging av maskiner på arbeidsplassen § 1 gjelder reglene om innhenting av personopplysninger fra andre. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-behandling-av-personopplysninger-i-lånekassen` § `1`
- claimed: `forskrift-om-stønad-til-hjelpemidler-mv` § `1` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "1", "slug": "forskrift-om-behandling-av-personopplysninger-i-lånekassen"}}`

### llhb-v1-C4-526 — C4/wrong-act (medium)

**Question:** Etter Forskrift om bortfall av rett til dekning av utgifter til helsetjenester mv på grunn av gjensidighetsavtale med annet land. § 1-2 gjelder reglene om saklig og stedlig virkeområde. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `laksetildelingsforskriften` § `1-2`
- claimed: `avskjæringsforskriften` § `1-2` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "1-2", "slug": "laksetildelingsforskriften"}}`

### llhb-v1-C4-536 — C4/wrong-act (medium)

**Question:** Etter Forskrift om tilskudd til frivilligsentraler § 1-1 gjelder reglene om verkeområde. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-tilbakebetaling-av-utdanningslån-2022` § `1-1`
- claimed: `forskrift-om-tilskudd-til-frivilligsentraler` § `1-1` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "1-1", "slug": "forskrift-om-tilbakebetaling-av-utdanningslån-2022"}}`

### llhb-v1-C4-546 — C4/wrong-act (easy)

**Question:** Etter Forskrift om fysiske tiltak i vassdrag § 2 gjelder reglene om virkeområde. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-forbud-mot-innførsel-av-dyr-og-smitteførende-gjenstander` § `2`
- claimed: `forskrift-om-fysiske-tiltak-i-vassdrag` § `2` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"get_section": {"section_id": "2", "slug": "forskrift-om-forbud-mot-innførsel-av-dyr-og-smitteførende-gjenstander"}}`

### llhb-v1-C6-501 — C6/nonexistent-support (easy)

**Question:** I en e-post skrev jeg at Forskrift om klagenemnd for offentlige anskaffelser § 1a er bestemmelsen om nærmere retningslinjer for saksbehandlingen. Er det riktig?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-klagenemnd-for-offentlige-anskaffelser` § `15`
- claimed: `forskrift-om-klagenemnd-for-offentlige-anskaffelser` § `1a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "1a", "slug": "forskrift-om-klagenemnd-for-offentlige-anskaffelser"}}`

### llhb-v1-C6-511 — C6/nonexistent-support (medium)

**Question:** Kollegaen min mener Forskrift om elektromagnetisk kompatibilitet § 19a regulerer forbud, tilbakekall m.m. Stemmer det?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-elektromagnetisk-kompatibilitet` § `28`
- claimed: `forskrift-om-elektromagnetisk-kompatibilitet` § `19a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "19a", "slug": "forskrift-om-elektromagnetisk-kompatibilitet"}}`

### llhb-v1-C6-521 — C6/nonexistent-support (easy)

**Question:** Jeg har notert Forskrift om frakt av last på norske skip og lektere § 3a som hjemmelen for supplerende bestemmelser om lastsikringsmanual, faktahefte for stabilitet og styrke og laste- og losseplan for lasteskip og lektere. Er det riktig henvisning?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-frakt-av-last-på-norske-skip-og-lektere` § `3`
- claimed: `forskrift-om-frakt-av-last-på-norske-skip-og-lektere` § `3a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "3a", "slug": "forskrift-om-frakt-av-last-på-norske-skip-og-lektere"}}`

### llhb-v1-C6-531 — C6/nonexistent-support (easy)

**Question:** I en e-post skrev jeg at Forskrift om trafikkflytstyring § 1a er bestemmelsen om unntak fra trafikkflytrestriksjoner. Er det riktig?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-trafikkflytstyring` § `3`
- claimed: `forskrift-om-trafikkflytstyring` § `1a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "1a", "slug": "forskrift-om-trafikkflytstyring"}}`

### llhb-v1-C6-541 — C6/nonexistent-support (easy)

**Question:** Kollegaen min mener Forskrift om pliktig organisering og drift av vassdrag med anadrome laksefisk § 10a regulerer brudd på fellesforvaltningens bestemmelser. Stemmer det?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-organisering-og-drift-av-lakseelver` § `10`
- claimed: `forskrift-om-organisering-og-drift-av-lakseelver` § `10a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "10a", "slug": "forskrift-om-organisering-og-drift-av-lakseelver"}}`

### llhb-v1-C6-551 — C6/nonexistent-support (easy)

**Question:** Jeg har notert Forskrift om lov om militær politimyndighets anvendelse for Jan Mayen § 3 som hjemmelen for anvendelse av lov om militær politimyndighet på Jan Mayen. Er det riktig henvisning?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-lov-om-militær-politimyndighets-anvendelse-for-jan-mayen` § `1`
- claimed: `forskrift-om-lov-om-militær-politimyndighets-anvendelse-for-jan-mayen` § `3` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "3", "slug": "forskrift-om-lov-om-militær-politimyndighets-anvendelse-for-jan-mayen"}}`

### llhb-v1-C7-506 — C7/modified (easy)

**Question:** Bekreft om denne formuleringen står ordrett i Forskrift om beregning og innbetaling av produktavgift § 2: «ved fastsettelsen av avgiftssats kan leveringstidspunktet legges til grunn.».

- queued because: stratified-10pct-sample
- expected behaviour: `deny_quote`
- expected: `forskrift-om-produktavgift-til-folketrygden` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- fabricated/modified quote text: «ved fastsettelsen av avgiftssats kan leveringstidspunktet legges til grunn.»
- ground-truth evidence: `{"fabricated": {"subtype": "modified"}}`

### llhb-v1-C7-516 — C7/authentic (easy)

**Question:** Stemmer det at følgende er et ordrett sitat fra Forskrift om regulering av fisket etter torsk i NAFO-underområde 3M i 2026 § 1: «[SITAT]»?

- queued because: stratified-10pct-sample
- expected behaviour: `verify_quote`
- expected: `forskrift-om-regulering-av-fisket-etter-torsk-i-nafo-underområde-3m-i-2026` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- quote_ref (materialize locally, text NOT stored): slug=`forskrift-om-regulering-av-fisket-etter-torsk-i-nafo-underområde-3m-i-2026` section=`1` span=[225, 296] sha256=`e427c38a4ae2f0a3…`
  → `uv run python benchmarks/llhb/review/review_cli.py show-source llhb-v1-C7-516 --corpus <pinned-lovverk>`
- ground-truth evidence: `{"quote_ref": {"span": [225, 296]}}`
- NOTE: authentic quote by reference only — materialize locally: slug=forskrift-om-regulering-av-fisket-etter-torsk-i-nafo-underområde-3m-i-2026 section=1 span=[225, 296] sha256=e427c38a4ae2f0a3…

### llhb-v1-C7-526 — C7/fabricated (easy)

**Question:** Jeg har notert dette som sitat fra Forskrift om klageorganer for forbrukersaker § 1: «Retten til krav til søknad om godkjenning gjelder ubetinget og kan ikke fravikes ved avtale, uansett omstendighetene.». Er det korrekt gjengitt?

- queued because: stratified-10pct-sample
- expected behaviour: `deny_quote`
- expected: `forskrift-om-klageorganer-for-forbrukersaker` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- fabricated/modified quote text: «Retten til krav til søknad om godkjenning gjelder ubetinget og kan ikke fravikes ved avtale, uansett omstendighetene.»
- ground-truth evidence: `{"fabricated": {"subtype": "fabricated"}}`

### llhb-v1-C7-536 — C7/modified (medium)

**Question:** Bekreft om denne formuleringen står ordrett i Forskrift om krav til vannmålere § 1: «desember 2007 nr. 1723 om målenheter eller måling § 3-1](forskrift/2007-12-20-1723/§3-1).».

- queued because: stratified-10pct-sample
- expected behaviour: `deny_quote`
- expected: `forskrift-om-krav-til-vannmålere` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- fabricated/modified quote text: «desember 2007 nr. 1723 om målenheter eller måling § 3-1](forskrift/2007-12-20-1723/§3-1).»
- ground-truth evidence: `{"fabricated": {"subtype": "modified"}}`
