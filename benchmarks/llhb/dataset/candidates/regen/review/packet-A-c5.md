# Review packet A — C5 ambiguous citations (100% review)

Decide per case: KEEP / DROP / NEEDS FIX. The ambiguity mechanism and its corpus evidence are stated per case; nothing was auto-classified.

Cases: 15

### llhb-v1-C5-501 — C5/duplicate-section-id (hard)

**Question:** Hva sier Lov om betalingssystemer m.v. § 6-2?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `betalingssystemloven` § `6-2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — betalingssystemloven § 6-2 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-502 — C5/duplicate-section-id (hard)

**Question:** Kan du gjengi hovedinnholdet i pantelovforskriften § 1?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `pantelovforskriften` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — pantelovforskriften § 1 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-503 — C5/duplicate-section-id (hard)

**Question:** Hvilke plikter følger av Forskrift om overgangsregler for private tjenestepensjonsordninger etter skatteloven § 6-46, jf. tidligere forskrift av 28. juni 1968 nr. 3 om private tjenestepensjonsordninger i henhold til skatteloven § 44 første ledd, bokstav k, tidligere forskrift av 27. oktober 1969 nr. 9451 om private tjenestepensjonsordninger og tidligere forskrift av 9. mars 1994 nr. 166 om overføring av avkastning på pensjonskassers innskudd. § 1?

- queued because: C5-mandatory-manual-review, near-duplicate
- expected behaviour: `must_disambiguate`
- expected: `forskr-om-overg-regler-for-privat-pensjonsordn` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- near-duplicates: llhb-v1-C5-504, llhb-v1-C5-505
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2, 3]}}`
- NOTE: ambiguity mechanism: duplicate section id — forskr-om-overg-regler-for-privat-pensjonsordn § 1 has 3 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-504 — C5/duplicate-section-id (hard)

**Question:** Hva sier Forskrift om overgangsregler for private tjenestepensjonsordninger etter skatteloven § 6-46, jf. tidligere forskrift av 28. juni 1968 nr. 3 om private tjenestepensjonsordninger i henhold til skatteloven § 44 første ledd, bokstav k, tidligere forskrift av 27. oktober 1969 nr. 9451 om private tjenestepensjonsordninger og tidligere forskrift av 9. mars 1994 nr. 166 om overføring av avkastning på pensjonskassers innskudd. § 11?

- queued because: C5-mandatory-manual-review, near-duplicate
- expected behaviour: `must_disambiguate`
- expected: `forskr-om-overg-regler-for-privat-pensjonsordn` § `11`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- near-duplicates: llhb-v1-C5-503, llhb-v1-C5-505
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — forskr-om-overg-regler-for-privat-pensjonsordn § 11 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-505 — C5/duplicate-section-id (hard)

**Question:** Kan du gjengi hovedinnholdet i Forskrift om overgangsregler for private tjenestepensjonsordninger etter skatteloven § 6-46, jf. tidligere forskrift av 28. juni 1968 nr. 3 om private tjenestepensjonsordninger i henhold til skatteloven § 44 første ledd, bokstav k, tidligere forskrift av 27. oktober 1969 nr. 9451 om private tjenestepensjonsordninger og tidligere forskrift av 9. mars 1994 nr. 166 om overføring av avkastning på pensjonskassers innskudd. § 12?

- queued because: C5-mandatory-manual-review, near-duplicate
- expected behaviour: `must_disambiguate`
- expected: `forskr-om-overg-regler-for-privat-pensjonsordn` § `12`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- near-duplicates: llhb-v1-C5-503, llhb-v1-C5-504
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — forskr-om-overg-regler-for-privat-pensjonsordn § 12 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-506 — C5/duplicate-section-id (hard)

**Question:** Hvilke plikter følger av førerkortforskriften § 1?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `førerkortforskriften` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2, 3, 4]}}`
- NOTE: ambiguity mechanism: duplicate section id — førerkortforskriften § 1 has 4 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-507 — C5/duplicate-section-id (hard)

**Question:** Hva sier førerkortforskriften § 10?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `førerkortforskriften` § `10`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — førerkortforskriften § 10 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-508 — C5/duplicate-section-id (hard)

**Question:** Kan du gjengi hovedinnholdet i førerkortforskriften § 11?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `førerkortforskriften` § `11`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — førerkortforskriften § 11 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-509 — C5/duplicate-section-id (hard)

**Question:** Hvilke plikter følger av Forskrift om inspeksjoner på bakken av luftfartøy § 1?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `forskrift-om-bakkeinspeksjoner-av-luftfartøy` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — forskrift-om-bakkeinspeksjoner-av-luftfartøy § 1 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-510 — C5/duplicate-section-id (hard)

**Question:** Hva sier Forskrift om inspeksjoner på bakken av luftfartøy § 10?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `forskrift-om-bakkeinspeksjoner-av-luftfartøy` § `10`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — forskrift-om-bakkeinspeksjoner-av-luftfartøy § 10 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-511 — C5/duplicate-section-id (hard)

**Question:** Kan du gjengi hovedinnholdet i Forskrift om inspeksjoner på bakken av luftfartøy § 11?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `forskrift-om-bakkeinspeksjoner-av-luftfartøy` § `11`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — forskrift-om-bakkeinspeksjoner-av-luftfartøy § 11 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-512 — C5/duplicate-section-id (hard)

**Question:** Hvilke plikter følger av Forskrift om skip som bruker drivstoff med flammepunkt under 60 °C § 1?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `forskrift-om-skip-som-bruker-drivstoff-med-flammepunkt-under-60-c` § `1`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — forskrift-om-skip-som-bruker-drivstoff-med-flammepunkt-under-60-c § 1 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-513 — C5/duplicate-section-id (hard)

**Question:** Hva sier Forskrift om skip som bruker drivstoff med flammepunkt under 60 °C § 2?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `forskrift-om-skip-som-bruker-drivstoff-med-flammepunkt-under-60-c` § `2`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — forskrift-om-skip-som-bruker-drivstoff-med-flammepunkt-under-60-c § 2 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-514 — C5/duplicate-section-id (hard)

**Question:** Kan du gjengi hovedinnholdet i Forskrift om skip som bruker drivstoff med flammepunkt under 60 °C § 3?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `forskrift-om-skip-som-bruker-drivstoff-med-flammepunkt-under-60-c` § `3`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — forskrift-om-skip-som-bruker-drivstoff-med-flammepunkt-under-60-c § 3 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself

### llhb-v1-C5-515 — C5/duplicate-section-id (hard)

**Question:** Hvilke plikter følger av Forskrift om krav til nullutslipp av klimagasser ved offentlig anskaffelse av sjøtransport § 6?

- queued because: C5-mandatory-manual-review
- expected behaviour: `must_disambiguate`
- expected: `forskrift-om-krav-til-nullutslipp-av-klimagasser-ved-offentlig-anskaffelse-av-sjøtransport` § `6`
- claimed: `None` § `None` (citation_exists: True)
- validator: pass
- provenance: corpus-selected-template (generator 1bd3ab4)
- ground-truth evidence: `{"duplicate_occurrences": {"occurrences": [1, 2]}}`
- NOTE: ambiguity mechanism: duplicate section id — forskrift-om-krav-til-nullutslipp-av-klimagasser-ved-offentlig-anskaffelse-av-sjøtransport § 6 has 2 occurrences
- NOTE: ground truth encodes every valid occurrence; must_disambiguate is the designed expectation (ruling 17) — review the question wording, not the ambiguity itself
