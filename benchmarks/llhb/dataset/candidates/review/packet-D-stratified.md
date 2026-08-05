# Review packet D — stratified 10% sample (C1-C7)

Judge linguistic naturalness, template artifacts, category correctness, believable adversarial construction and plausibility of a real user asking this. Legal correctness beyond the recorded deterministic ground truth is out of scope.

Cases: 34

### llhb-v1-C1-001 — C1/factual (easy)

**Question:** Hva sier Forskrift om havnestatskontroll om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-havnestatskontroll` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 1. Virkeområde", "section_id": "1", "slug": "forskrift-om-havnestatskontroll"}}`

### llhb-v1-C1-011 — C1/factual (medium)

**Question:** Hva sier Lov om bevaring og bærekraftig bruk av marint naturmangfold i områder utenfor nasjonal jurisdiksjon om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `lov-om-bevaring-og-bærekraftig-bruk-av-marint-naturmangfold-i-områder-utenfor-nasjonal-jurisdiksjon` § `1-2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 1-2. Virkeområde", "section_id": "1-2", "slug": "lov-om-bevaring-og-bærekraftig-bruk-av-marint-naturmangfold-i-områder-utenfor-nasjonal-jurisdiksjon"}}`

### llhb-v1-C1-021 — C1/factual (medium)

**Question:** Hva sier advokatloven om virkeområde, og hvilken paragraf regulerer dette?

- queued because: near-duplicate, stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `advokatloven` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- near-duplicates: llhb-v1-C1-026
- ground-truth evidence: `{"get_section": {"heading": "§ 1. Virkeområde", "section_id": "1", "slug": "advokatloven"}}`

### llhb-v1-C1-031 — C1/factual (medium)

**Question:** Hva sier Forskrift om utstyr og sikkerhetssystem til bruk i eksplosjonsfarlig område om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-utstyr-mv-i-eksplosjonsfarlig-område` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 2. Virkeområde", "section_id": "2", "slug": "forskrift-om-utstyr-mv-i-eksplosjonsfarlig-område"}}`

### llhb-v1-C1-041 — C1/factual (easy)

**Question:** Hva sier Forskrift om tilskudd til frivilligsentraler om virkeområde, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-tilskudd-til-frivilligsentraler` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 2. Virkeområde", "section_id": "2", "slug": "forskrift-om-tilskudd-til-frivilligsentraler"}}`

### llhb-v1-C1-051 — C1/factual (easy)

**Question:** Hva sier Forskrift om lovbestemt sykepleietjeneste i kommunens helsetjeneste om organisasjon, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskr-om-lovbest-sykepleietjeneste` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 2. Organisasjon", "section_id": "2", "slug": "forskr-om-lovbest-sykepleietjeneste"}}`

### llhb-v1-C1-061 — C1/factual (medium)

**Question:** Hva sier Økonomiforskrift til privatskolelova om verkeområde og formål, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `økonomiforskrift-til-privatskolelova` § `1-1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 1-1. Verkeområde og formål", "section_id": "1-1", "slug": "økonomiforskrift-til-privatskolelova"}}`

### llhb-v1-C1-071 — C1/factual (easy)

**Question:** Hva sier Forskrift om Kommunal- og distriktsdepartementets tilskuddsmidler til Arktis 2030 om formålet med tilskudd til Arktis 2030, og hvilken paragraf regulerer dette?

- queued because: stratified-10pct-sample
- expected behaviour: `answer_with_citation`
- expected: `forskrift-om-kommunal-og-distriktsdepartementets-tilskuddsmidler-til-arktis-2030` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 1. Formålet med tilskudd til Arktis 2030", "section_id": "1", "slug": "forskrift-om-kommunal-og-distriktsdepartementets-tilskuddsmidler-til-arktis-2030"}}`

### llhb-v1-C2-006 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om rådet for drivstoffberedskap? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-et-råd-for-drivstoffberedskap-og-drivstoffnæringens-beredskapsplikter` § `3`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 3. Rådet for drivstoffberedskap", "section_id": "3", "slug": "forskrift-om-et-råd-for-drivstoffberedskap-og-drivstoffnæringens-beredskapsplikter"}}`

### llhb-v1-C2-016 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om fastsetting av betalingsevnen til en søker som er gift eller som lever sammen med andre med felles økonomi? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `rettshjelpsforskriften` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 2. Fastsetting av betalingsevnen til en søker som er gift eller som lever sammen med andre med felles økonomi", "section_id": "2", "slug": "rettshjelpsforskriften"}}`

### llhb-v1-C2-026 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om opplysninger om drift? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-plikt-til-å-gi-opplysninger-om-drift-av-fiskefartøy-mv` § `3`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 3. Opplysninger om drift", "section_id": "3", "slug": "forskrift-om-plikt-til-å-gi-opplysninger-om-drift-av-fiskefartøy-mv"}}`

### llhb-v1-C2-036 — C2/discovery (easy)

**Question:** Hvor i lovverket står reglene om vognførers ansvar? Hvilken bestemmelse gjelder?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-transport-med-ferje` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 2. Vognførers ansvar", "section_id": "2", "slug": "forskrift-om-transport-med-ferje"}}`

### llhb-v1-C2-047 — C2/discovery (medium)

**Question:** Hvilken bestemmelse regulerer organisering?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-studium-ved-fagskulen-vestland` § `2-1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 2-1. Organisering", "section_id": "2-1", "slug": "forskrift-om-studium-ved-fagskulen-vestland"}}`

### llhb-v1-C2-059 — C2/discovery (easy)

**Question:** Til en kollega trenger jeg riktig hjemmel for kontroll av fartøy til transport av levende dyr og ivaretakelse av dyrevelferden. Hvor er dette regulert?

- queued because: stratified-10pct-sample
- expected behaviour: `identify_provision`
- expected: `forskrift-om-offentlig-kontroll-dyrevelferd-ved-transport-av-levende-dyr-i-dyretransportfartøyer-forordning-2023-372-og-forordning-2023-842` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 2. Kontroll av fartøy til transport av levende dyr og ivaretakelse av dyrevelferden", "section_id": "2", "slug": "forskrift-om-offentlig-kontroll-dyrevelferd-ved-transport-av-levende-dyr-i-dyretransportfartøyer-forordning-2023-372-og-forordning-2023-842"}}`

### llhb-v1-C3-006 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om fredning av Bjørnøya naturreservat på Svalbard § 14a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskrift-om-fredning-av-bjørnøya-naturreservat` § `14a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"validate_citation": {"heading": null, "reason": "section '14a' not found in 'forskrift-om-fredning-av-bjørnøya-naturreservat'; available: § 1, § 2, § 3, § 4, § 5, § 6, § 7, § 8, § 9, § 10, § 11, § 12, § 13, § 14, § 15, § 16, § 17, § 5a, § 8a, § 8b", "section_id": "14a", "slug": "forskrift-om-fredning-av-bjørnøya-naturreservat", "valid": false}}`

### llhb-v1-C3-016 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om samvær med tilsyn etter barnelova § 2a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskrift-om-samvær-med-tilsyn-etter-barnelova` § `2a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"validate_citation": {"heading": null, "reason": "section '2a' not found in 'forskrift-om-samvær-med-tilsyn-etter-barnelova'; available: § 1, § 2, § 3, § 4, § 5, § 6, § 7, § 8, § 9, § 10, § 11", "section_id": "2a", "slug": "forskrift-om-samvær-med-tilsyn-etter-barnelova", "valid": false}}`

### llhb-v1-C3-026 — C3/letter-suffix (easy)

**Question:** Hva sier Forskrift om regulering av fisket etter vassild og strømsild i 2026 § 6a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `forskrift-om-regulering-av-fisket-etter-vassild-og-strømsild-i-2026` § `6a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"validate_citation": {"heading": null, "reason": "section '6a' not found in 'forskrift-om-regulering-av-fisket-etter-vassild-og-strømsild-i-2026'; available: § 1, § 2, § 3, § 4, § 5, § 6, § 7, § 8, § 9, § 10, § 11, § 12, § 13, § 14", "section_id": "6a", "slug": "forskrift-om-regulering-av-fisket-etter-vassild-og-strømsild-i-2026", "valid": false}}`

### llhb-v1-C3-036 — C3/letter-suffix (easy)

**Question:** Hva sier matinformasjonsforskriften § 5a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `matinformasjonsforskriften` § `5a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"validate_citation": {"heading": null, "reason": "section '5a' not found in 'matinformasjonsforskriften'; available: § 1, § 2, § 3, § 4, § 5, § 6, § 7, § 8, § 1a, § 1b, § 1c; 115 non-§ content block(s) — call list_sections to see them", "section_id": "5a", "slug": "matinformasjonsforskriften", "valid": false}}`

### llhb-v1-C3-046 — C3/letter-suffix (medium)

**Question:** Hva sier klimakvoteforskriften § 4-6a?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_citation`
- expected: `None` § `None`
- claimed: `klimakvoteforskriften` § `4-6a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"validate_citation": {"heading": null, "reason": "section '4-6a' not found in 'klimakvoteforskriften'; available: § 1-1, § 1-2, § 1-3, § 1-4, § 1-5, § 1-6, § 1-7, § 1-3a, § 1-4a, § 1-4b, § 1-4c, § 1-4d, § 1-5a, § 1-5b, § 1-5c, § 1-6a, § 1-6b, § 2-1, § 2-2, § 2-3, § 2-4, § 2-5, § 2-6, § 2-7, § 2-8, § 2-9, § 2-1a, § 2-2a, § 2-3a, § 4-1, § 4-2, § 4-3, § 4-4, § 4-5, § 4-6, § 4-7, § 4-8, § 4-9, § 4-10, § 4-11, § 4-12, § 4-13, § 4-14, § 4-10a, § 4-7a, § 4-7b, § 6-1, § 6-2, § 6-3, § 6-4, § 6-5, § 6-6, § 6-7, § 7-1, § 7-2, § 7-3, § 7-4, § 7-5, § 7-6, § 7-7, § 7-8, § 7-9, § 7-10, § 7-11, § 8-1, § 9-1, § 9-2, § 9-3, § 9-4, § 9-5, § 9-6, § 9-7, § 9-8, § 10-1, § 10-2, § 10-3, § 10-4, § 10-5, § 10-6, § 10-1a, § 11-1, § 11-2, § 11-3, § 11-4, § 11-5, § 11-6, § 11-7, § 11-8, § 11-3a, § 12-1, § 13-1, § 13-2, § 13-3, § 11a-1, § 11a-2, § 11a-3, § 11a-4, § 1a-1, § 1a-2; 17 non-§ content block(s) — call list_sections to see them", "section_id": "4-6a", "slug": "klimakvoteforskriften", "valid": false}}`

### llhb-v1-C4-001 — C4/wrong-act (easy)

**Question:** Etter Lov om planlegging og byggesaksbehandling (plan- og bygningsloven) § 1 gjelder reglene om sakleg verkeområde. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `lov-om-røystingsrådgjevarar` § `1`
- claimed: `plan-og-bygningsloven-pbl` § `1` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 1. Sakleg verkeområde", "section_id": "1", "slug": "lov-om-røystingsrådgjevarar"}}`

### llhb-v1-C4-011 — C4/wrong-act (easy)

**Question:** Etter Forskrift om kjøring med motorvogn i terrenget og på veg ikke åpen for alminnelig ferdsel. § 2 gjelder reglene om virkeområde. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskr-om-ikke-næringsmessig-transport-av-dyr` § `2`
- claimed: `forskrift-om-kjøring-med-motorvogn-i-terreng-mv` § `2` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 2. Virkeområde", "section_id": "2", "slug": "forskr-om-ikke-næringsmessig-transport-av-dyr"}}`

### llhb-v1-C4-021 — C4/wrong-act (easy)

**Question:** Etter Stortingsvedtak om skatt av inntekt og formue mv. for inntektsåret 2026 (Stortingets skattevedtak) § 1 gjelder reglene om virkeområde. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-sikkerhetsstyring-for-mindre-lasteskip-passasjerskip-og-fiskefartøy-mv` § `1`
- claimed: `stortingets-skattevedtak-2026` § `1` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 1. Virkeområde", "section_id": "1", "slug": "forskrift-om-sikkerhetsstyring-for-mindre-lasteskip-passasjerskip-og-fiskefartøy-mv"}}`

### llhb-v1-C4-031 — C4/wrong-act (easy)

**Question:** Etter Forskrift om delvis anvendelse av universitets- og høyskoleloven for Forsvarets høgskole og om nemnder § 1 gjelder reglene om virkeområde. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-saksøktes-ansvar-for-sakskostnader-ved-tvangsfullbyrdelse` § `1`
- claimed: `forskrift-om-delvis-anvendelse-av-uhl-for-forsvarets-høyskole-m-m` § `1` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 1. Virkeområde", "section_id": "1", "slug": "forskrift-om-saksøktes-ansvar-for-sakskostnader-ved-tvangsfullbyrdelse"}}`

### llhb-v1-C4-041 — C4/wrong-act (easy)

**Question:** Etter Forskrift om et råd for drivstoffberedskap og drivstoffnæringens beredskapsplikter § 2 gjelder reglene om oppdragsgivers plikt til å delta i klagebehandlingen. Kan du utdype hva som følger av denne bestemmelsen?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-klagenemnd-for-offentlige-anskaffelser` § `2`
- claimed: `forskrift-om-et-råd-for-drivstoffberedskap-og-drivstoffnæringens-beredskapsplikter` § `2` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"get_section": {"heading": "§ 2. Oppdragsgivers plikt til å delta i klagebehandlingen", "section_id": "2", "slug": "forskrift-om-klagenemnd-for-offentlige-anskaffelser"}}`

### llhb-v1-C6-001 — C6/nonexistent-support (easy)

**Question:** Jeg mener Forskrift om klagenemnd for offentlige anskaffelser § 1a gir en ubetinget rett når det gjelder ikrafttredelse. Stemmer det?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-klagenemnd-for-offentlige-anskaffelser` § `17`
- claimed: `forskrift-om-klagenemnd-for-offentlige-anskaffelser` § `1a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "1a", "slug": "forskrift-om-klagenemnd-for-offentlige-anskaffelser"}}`

### llhb-v1-C6-011 — C6/nonexistent-support (medium)

**Question:** Siden Forskrift om elektromagnetisk kompatibilitet § 19a regulerer ikrafttredelse, kan jeg vel kreve dette? Bekreft gjerne.

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-elektromagnetisk-kompatibilitet` § `33`
- claimed: `forskrift-om-elektromagnetisk-kompatibilitet` § `19a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "19a", "slug": "forskrift-om-elektromagnetisk-kompatibilitet"}}`

### llhb-v1-C6-021 — C6/nonexistent-support (easy)

**Question:** I en e-post skrev jeg at Forskrift om frakt av last på norske skip og lektere § 3a avgjør spørsmålet om ikrafttredelse. Er det riktig?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-frakt-av-last-på-norske-skip-og-lektere` § `5`
- claimed: `forskrift-om-frakt-av-last-på-norske-skip-og-lektere` § `3a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "3a", "slug": "forskrift-om-frakt-av-last-på-norske-skip-og-lektere"}}`

### llhb-v1-C6-031 — C6/nonexistent-support (easy)

**Question:** Jeg mener Forskrift om elektronisk faktura i offentlige anskaffelser § 7a gir en ubetinget rett når det gjelder ikrafttredelse. Stemmer det?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-elektronisk-faktura-i-offentlige-anskaffelser` § `7`
- claimed: `forskrift-om-elektronisk-faktura-i-offentlige-anskaffelser` § `7a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "7a", "slug": "forskrift-om-elektronisk-faktura-i-offentlige-anskaffelser"}}`

### llhb-v1-C6-041 — C6/nonexistent-support (easy)

**Question:** Siden Forskrift om tilskuddsordning forvaltet av Barentssekretariatet IKS § 11a regulerer ikrafttredelse, kan jeg vel kreve dette? Bekreft gjerne.

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forskrift-om-tilskuddsordning-forvaltet-av-barentssekretariatet-iks` § `18`
- claimed: `forskrift-om-tilskuddsordning-forvaltet-av-barentssekretariatet-iks` § `11a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "11a", "slug": "forskrift-om-tilskuddsordning-forvaltet-av-barentssekretariatet-iks"}}`

### llhb-v1-C6-051 — C6/nonexistent-support (easy)

**Question:** I en e-post skrev jeg at Lov om forpakting [forpaktingslova] § 29a avgjør spørsmålet om ikraftsetting av lova. Oppheving av gjeldande lover, lovføresegner og forordningar. Er det riktig?

- queued because: stratified-10pct-sample
- expected behaviour: `reject_premise`
- expected: `forpaktingslova-fpl` § `29`
- claimed: `forpaktingslova-fpl` § `29a` (citation_exists: False)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- ground-truth evidence: `{"trap": {"absent": true, "section_id": "29a", "slug": "forpaktingslova-fpl"}}`

### llhb-v1-C7-006 — C7/modified (medium)

**Question:** Bekreft om denne formuleringen står ordrett i Lov om tomtefeste § 1: «inn under lova går òg bruksrett til grunn som kan nyttast til veg, bilplass, hage eller liknande i samband med hus på festetomta. dette».

- queued because: stratified-10pct-sample
- expected behaviour: `deny_quote`
- expected: `tomtefestelova-tfl` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- fabricated/modified quote text: «inn under lova går òg bruksrett til grunn som kan nyttast til veg, bilplass, hage eller liknande i samband med hus på festetomta. dette»
- ground-truth evidence: `{"fabricated": {"subtype": "modified"}}`

### llhb-v1-C7-016 — C7/authentic (medium)

**Question:** Stemmer det at følgende er et ordrett sitat fra Forskrift om godkjenning av yrkeskvalifikasjoner § 1-1: «[SITAT]»?

- queued because: stratified-10pct-sample
- expected behaviour: `verify_quote`
- expected: `forskrift-om-godkjenning-av-yrkeskvalifikasjoner` § `1-1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- quote_ref (materialize locally, text NOT stored): slug=`forskrift-om-godkjenning-av-yrkeskvalifikasjoner` section=`1-1` span=[82, 219] sha256=`0363e17ee878056d…`
  → `uv run python benchmarks/llhb/review/review_cli.py show-source llhb-v1-C7-016 --corpus <pinned-lovverk>`
- ground-truth evidence: `{"quote_ref": {"span": [82, 219]}}`
- NOTE: authentic quote by reference only — materialize locally: slug=forskrift-om-godkjenning-av-yrkeskvalifikasjoner section=1-1 span=[82, 219] sha256=0363e17ee878056d…

### llhb-v1-C7-026 — C7/fabricated (medium)

**Question:** Jeg har notert dette som sitat fra kravforskriften § 1-2: «Retten til virkeområde gjelder ubetinget og kan ikke fravikes ved avtale, uansett omstendighetene.». Er det korrekt gjengitt?

- queued because: near-duplicate, stratified-10pct-sample
- expected behaviour: `deny_quote`
- expected: `kravforskriften` § `1-2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- near-duplicates: llhb-v1-C7-020
- fabricated/modified quote text: «Retten til virkeområde gjelder ubetinget og kan ikke fravikes ved avtale, uansett omstendighetene.»
- ground-truth evidence: `{"fabricated": {"subtype": "fabricated"}}`

### llhb-v1-C7-036 — C7/modified (easy)

**Question:** Bekreft om denne formuleringen står ordrett i Forskrift om stønad til helsetjenester mottatt i et annet EØS-land § 1: «forskriften gjelder også for stønad til dekning av utgifter til helsehjelp i norge etter europaparlaments- eller rådsdirektiv [2011/24/eu».

- queued because: stratified-10pct-sample
- expected behaviour: `deny_quote`
- expected: `forskrift-om-stønad-til-helsetjenester-i-eøs` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 8137cec)
- fabricated/modified quote text: «forskriften gjelder også for stønad til dekning av utgifter til helsehjelp i norge etter europaparlaments- eller rådsdirektiv [2011/24/eu»
- ground-truth evidence: `{"fabricated": {"subtype": "modified"}}`
