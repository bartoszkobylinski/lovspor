"""Citation extractor: documented syntax in, structured citations out.

Every test uses synthetic act names via ``ActNameIndex.from_pairs`` —
no Lovdata text, no real corpus.
"""

from lovspor.llhb.citations import ExtractionResult, extract_citations
from lovspor.llhb.names import ActNameIndex
from lovspor.llhb.stances import Stance

_INDEX = ActNameIndex.from_pairs(
    [
        ("arbeidsmiljøloven", "arbeidsmiljøloven"),
        ("skatteloven", "skatteloven-sktl"),
        ("forvaltningsloven", "forvaltningsloven"),
    ],
)


def _extract(answer: str) -> ExtractionResult:
    return extract_citations(answer, _INDEX)


def test_act_before_section_binds_adjacent() -> None:
    result = _extract("Etter arbeidsmiljøloven § 15-7 kreves saklig grunn.")
    (citation,) = result.citations
    assert citation.section_id == "15-7"
    assert citation.act_key == "arbeidsmiljøloven"
    assert citation.act_binding == "before"
    assert citation.stance is Stance.ASSERTED
    assert result.unresolved == []


def test_no_space_variant_binds_adjacent() -> None:
    (citation,) = _extract("arbeidsmiljøloven §15-7 gjelder.").citations
    assert citation.section_id == "15-7"
    assert citation.act_binding == "before"


def test_section_before_act_binds_after() -> None:
    (citation,) = _extract("Se § 15-7 arbeidsmiljøloven.").citations
    assert citation.act_key == "arbeidsmiljøloven"
    assert citation.act_binding == "after"


def test_section_i_act_preserves_longest_read_and_binds_after() -> None:
    (citation,) = _extract("Dette følger av § 12 i skatteloven.").citations
    # The " i" stays in the raw id (production longest-read); the
    # resolver applies the production tail-strip, not the extractor.
    assert citation.section_id_raw == "12 i"
    assert citation.act_key == "skatteloven"
    assert citation.act_binding == "after"


def test_known_abbreviation_binds() -> None:
    (citation,) = _extract("Oppsigelse krever saklig grunn, jf. aml. § 15-7.").citations
    assert citation.act_key == "arbeidsmiljøloven"
    assert citation.act_binding == "abbreviation"
    assert citation.abbreviation == "aml."


def test_unknown_abbreviation_does_not_bind() -> None:
    (citation,) = _extract("Se xyz. § 15-7 om dette.").citations
    assert citation.act_key is None
    assert citation.act_binding is None


def test_bare_section_binds_to_sentence_antecedent() -> None:
    text = "Skatteloven regulerer dette. Skatteloven har regler, og § 5-1 sier mer."
    citations = _extract(text).citations
    (citation,) = citations
    assert citation.act_key == "skatteloven"
    assert citation.act_binding == "sentence"


def test_bare_section_falls_back_to_paragraph() -> None:
    text = "Vurderingen gjelder skatteloven.\nDet følger av § 5-1 at dette gjelder."
    (citation,) = _extract(text).citations
    assert citation.act_key == "skatteloven"
    assert citation.act_binding == "paragraph"


def test_bare_section_with_no_act_reference_is_missing_act() -> None:
    (citation,) = _extract("Det følger av § 36 at avtalen kan settes til side.").citations
    assert citation.section_id == "36"
    assert citation.act_key is None


def test_paragraph_break_stops_binding() -> None:
    text = "Skatteloven regulerer dette.\n\nDet følger av § 5-1 at dette gjelder."
    (citation,) = _extract(text).citations
    assert citation.act_key is None


def test_multiple_citations_in_one_answer() -> None:
    text = "Etter arbeidsmiljøloven § 15-7 og forvaltningsloven § 11 gjelder krav."
    citations = _extract(text).citations
    assert [(c.act_key, c.section_id) for c in citations] == [
        ("arbeidsmiljøloven", "15-7"),
        ("forvaltningsloven", "11"),
    ]


def test_double_section_conjunction_splits() -> None:
    citations = _extract("Se arbeidsmiljøloven §§ 14-9 og 14-10.").citations
    assert [c.section_id for c in citations] == ["14-9", "14-10"]
    assert all(not c.from_range for c in citations)
    assert all(c.act_key == "arbeidsmiljøloven" for c in citations)


def test_double_section_range_yields_endpoints_only() -> None:
    result = _extract("Reglene i forvaltningsloven §§ 4 til 8 gjelder her.")
    assert [(c.section_id, c.from_range) for c in result.citations] == [
        ("4", True),
        ("8", True),
    ]
    assert all(c.act_key == "forvaltningsloven" for c in result.citations)


def test_mixed_range_conjunction_is_unresolved() -> None:
    result = _extract("Se §§ 4 til 8 og 12 om dette.")
    assert result.citations == []
    (claim,) = result.unresolved
    assert claim.reason == "unsupported §§ range/conjunction mix"


def test_letter_and_hyphen_section_ids() -> None:
    text = "Se arbeidsmiljøloven § 14-9a og skatteloven § 10-4-1 om detaljer."
    citations = _extract(text).citations
    assert [c.section_id for c in citations] == ["14-9a", "10-4-1"]


def test_spaced_letter_suffix_is_canonicalized() -> None:
    (citation,) = _extract("Etter arbeidsmiljøloven § 8-7 a. gjelder dette").citations
    assert citation.section_id_raw == "8-7 a"
    assert citation.section_id == "8-7a"


def test_orphan_section_marker_is_unresolved() -> None:
    result = _extract("Loven har en § om dette.")
    assert result.citations == []
    (claim,) = result.unresolved
    assert claim.reason == "§ marker with no parseable section id"


def test_no_citation_yields_empty_result() -> None:
    result = _extract("Arbeidsgiver må ha saklig grunn for oppsigelse.")
    assert result.citations == []
    assert result.unresolved == []


def test_rejection_language_marks_denied_not_asserted() -> None:
    (citation,) = _extract("arbeidsmiljøloven § 15-99 finnes ikke.").citations
    assert citation.stance is Stance.DENIED


def test_correction_language_marks_corrected() -> None:
    text = "Nei, § 15-99 finnes ikke. Riktig bestemmelse er arbeidsmiljøloven § 15-7."
    citations = _extract(text).citations
    assert [c.stance for c in citations] == [Stance.DENIED, Stance.CORRECTED]


def test_denied_then_contrast_citation_is_asserted() -> None:
    text = "arbeidsmiljøloven § 15-99 finnes ikke, men § 15-7 regulerer oppsigelse."
    citations = _extract(text).citations
    assert [c.stance for c in citations] == [Stance.DENIED, Stance.ASSERTED]


def test_unattachable_denial_is_unresolved_stance() -> None:
    (citation,) = _extract("Det stemmer ikke at skatteloven § 99 gir fradrag her.").citations
    assert citation.stance is Stance.UNRESOLVED


def test_repeated_citation_extracted_each_time() -> None:
    text = "arbeidsmiljøloven § 15-7 gjelder. Som nevnt gir arbeidsmiljøloven § 15-7 vern."
    citations = _extract(text).citations
    assert [c.section_id for c in citations] == ["15-7", "15-7"]
    assert all(c.stance is Stance.ASSERTED for c in citations)


def test_result_carries_frozen_rule_versions() -> None:
    result = _extract("ingen paragrafer her")
    assert result.abbreviations_version == "llhb-abbrev-v1"
    assert result.stance_rules_version == "llhb-stance-v1"


def test_norwegian_word_after_number_is_not_a_letter_suffix() -> None:
    """Issue #85: «første»/«følger»/«hører» tokenize as a standalone letter
    because their second character (æ/ø/å) ends the [A-Za-z] word — the
    extractor must not swallow that letter into the section id."""
    (citation,) = _extract("Etter arbeidsmiljøloven § 8 første ledd gjelder dette.").citations
    assert citation.section_id == "8"
    assert citation.section_id_raw == "8"

    (citation,) = _extract("Kravet i skatteloven § 9-3 hører under kapittel 9.").citations
    assert citation.section_id == "9-3"

    (citation,) = _extract("Av forvaltningsloven § 2 følger det at reglene gjelder.").citations
    assert citation.section_id == "2"


def test_deliberate_spaced_i_longest_read_survives_the_suffix_guard() -> None:
    """The documented «§ 12 i skatteloven» longest-read (resolver tail-strip
    parity) must be unaffected: after the swallowed «i» comes a space, not
    an æ/ø/å letter."""
    (citation,) = _extract("Dette følger av § 12 i skatteloven.").citations
    assert citation.section_id_raw == "12 i"


def test_norwegian_word_after_multi_section_is_not_a_letter_suffix() -> None:
    """The suffix guard also applies to the second id parsed by _MULTI_JOIN."""
    citations = _extract("Se arbeidsmiljøloven §§ 8 og 9 første ledd.").citations

    assert [citation.section_id for citation in citations] == ["8", "9"]
    assert [citation.section_id_raw for citation in citations] == ["8", "9"]
