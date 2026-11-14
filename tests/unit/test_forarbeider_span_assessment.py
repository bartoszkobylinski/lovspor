"""FC5: span rights classes and privacy states from verified raw evidence (#738).

Synthetic pages only (the invented Prop. 901 L). Every name, address and
number is invented; the national id number below has an impossible date
(month 13), so it cannot belong to anyone.
"""

import json
from typing import Any

import pytest

from lovspor.forarbeider.document_classify import RightsCandidate
from lovspor.forarbeider.document_extract import SourcePage, extract_document
from lovspor.forarbeider.document_models import Span
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.span_assessment import (
    assess_document,
    assessment_text,
    parse_assessment,
    verify_assessment,
)
from lovspor.forarbeider.span_assessment_models import (
    EntryAssessment,
    Evidence,
    PrivacyState,
    RightsClass,
    SpanAssessment,
)
from lovspor.forarbeider.version_manifest import Evidence as SourceEvidence
from lovspor.forarbeider.version_manifest import build_version_manifest
from tests.unit.forarbeider_version_support import (
    evidence,
    first_or_any,
    pages,
    sha,
    with_body,
)


def fake_national_id() -> str:
    """Eleven digits with valid check digits and month 13: never a real person."""
    base = [int(char) for char in "311399123"]
    for weights in ((3, 7, 6, 1, 8, 9, 4, 5, 2), (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)):
        remainder = sum(w * d for w, d in zip(weights, base, strict=False)) % 11
        check = 0 if remainder == 0 else 11 - remainder
        assert check != 10
        base.append(check)
    return "".join(map(str, base))


def assessed(body: str | None = None, given: list[SourcePage] | None = None) -> SpanAssessment:
    chapter_two = [first_or_any(2) if body is None else with_body(2, body)]
    chosen = given if given is not None else [*pages(1, 3, 4), *chapter_two]
    return assess_document(evidence(chosen))


def by_text(assessment: SpanAssessment, given: list[SourcePage] | None = None) -> dict[str, Any]:
    """Assessments of chapter 2's spans, keyed by their text (the test's own invented text)."""
    texts = {
        entry.id: entry.text
        for part in extract_document(given or evidence().pages).parts
        for entry in part.entries
        if isinstance(entry, Span)
    }
    return {texts[item.entry_id]: item for item in assessment.entries if item.entry_id in texts}


def chapter_two(body: str) -> dict[str, EntryAssessment]:
    given = [*pages(1, 3, 4), with_body(2, body)]
    return by_text(assess_document(evidence(given)), given)


def verdict(item: EntryAssessment) -> tuple[RightsClass, Evidence]:
    return item.rights, item.evidence


CONSULTATION = (
    "<h2>2.2 Høringsinstansenes syn</h2>"
    "<p>Advokatforeningen mener forslaget går for langt.</p>"
    "<p>Flere høringsinstanser støtter forslaget.</p>"
    "<p>Oslo kommune skriver:</p>"
    "<blockquote><p>Kommunen støtter forslaget fullt ut.</p></blockquote>"
    "<blockquote><p>Et sitat uten innledning.</p></blockquote>"
    "<h2>2.3 Departementets vurdering</h2>"
    "<p>Departementet er enig.</p>"
)


# --- rights ------------------------------------------------------------------------


def test_official_prose_and_headings_are_official_text_by_rule_not_clearance() -> None:
    spans = chapter_two("<p>Departementet viser til gjeldende rett.</p>")
    assert verdict(spans["Departementet viser til gjeldende rett."]) == (
        RightsClass.OFFICIAL_TEXT,
        Evidence.DEFAULT_RULE,
    )
    assert verdict(spans["2 Bakgrunn"]) == (RightsClass.OFFICIAL_TEXT, Evidence.STRUCTURE)


@pytest.mark.parametrize(
    ("intro", "expected", "reason"),
    [
        ("Advokatforeningen skriver:", RightsClass.THIRD_PARTY_QUOTE, "attributed_to_named_party"),
        ("Oslo kommune uttaler:", RightsClass.PUBLIC_STATEMENT, "attributed_to_public_body"),
        ("Lovens § 3 første ledd lyder:", RightsClass.STATUTE_TEXT, "quoted_legal_source"),
        (
            "Departementet foreslo i høringsnotatet:",
            RightsClass.OFFICIAL_TEXT,
            "quoted_own_government_text",
        ),
    ],
)
def test_an_introduced_quotation_takes_its_class_from_the_stated_source(
    intro: str, expected: RightsClass, reason: str
) -> None:
    spans = chapter_two(f"<p>{intro}</p><blockquote><p>Sitert tekst her.</p></blockquote>")
    quote = spans["Sitert tekst her."]
    assert verdict(quote) == (expected, Evidence.STRUCTURE_AND_ATTRIBUTION)
    assert quote.rights_reasons == ("quotation", reason)
    assert quote.evidence_entries == (spans[intro].entry_id,)
    assert quote.candidate_rights is RightsCandidate.QUOTATION_REQUIRES_ASSESSMENT


def test_a_named_party_quoting_a_legal_source_is_conflicting_and_unknown() -> None:
    body = (
        "<p>Advokatforeningen foreslår at § 3 skal lyde:</p>"
        "<blockquote><p>Foreslått ordlyd.</p></blockquote>"
    )
    quote = chapter_two(body)["Foreslått ordlyd."]
    assert verdict(quote) == (RightsClass.UNKNOWN, Evidence.CONFLICTING)


def test_a_quotation_without_attribution_or_context_stays_unknown() -> None:
    spans = chapter_two("<p>Det vises til følgende.</p><blockquote><p>Løst sitat.</p></blockquote>")
    assert verdict(spans["Løst sitat."]) == (RightsClass.UNKNOWN, Evidence.INSUFFICIENT)
    assert spans["Løst sitat."].rights_reasons == ("quotation_without_attribution",)


def test_consultation_views_refine_quotes_and_reported_statements_only() -> None:
    spans = chapter_two(CONSULTATION)
    heading = spans["2.2 Høringsinstansenes syn"].entry_id
    assert verdict(spans["Advokatforeningen mener forslaget går for langt."]) == (
        RightsClass.ATTRIBUTED_STATEMENT,
        Evidence.STATED_ATTRIBUTION,
    )
    assert spans["Advokatforeningen mener forslaget går for langt."].rights_reasons == (
        "reported_statement",
        "speaker_named_party",
        "consultation_section",
    )
    assert verdict(spans["Flere høringsinstanser støtter forslaget."]) == (
        RightsClass.OFFICIAL_TEXT,
        Evidence.DEFAULT_RULE,
    )
    assert verdict(spans["Kommunen støtter forslaget fullt ut."]) == (
        RightsClass.PUBLIC_STATEMENT,
        Evidence.STRUCTURE_AND_ATTRIBUTION,
    )
    unintroduced = spans["Et sitat uten innledning."]
    assert verdict(unintroduced) == (RightsClass.THIRD_PARTY_QUOTE, Evidence.STRUCTURE_AND_CONTEXT)
    assert unintroduced.evidence_entries == (heading,)
    assert verdict(spans["Departementet er enig."]) == (
        RightsClass.OFFICIAL_TEXT,
        Evidence.DEFAULT_RULE,
    )


def test_a_generic_mention_of_horing_changes_nothing() -> None:
    body = (
        "<h2>2.2 Høringen</h2>"
        "<p>Departementet sendte høringsnotatet på høring til høringsinstansene.</p>"
        "<blockquote><p>Et sitat under høringen.</p></blockquote>"
    )
    spans = chapter_two(body)
    assert verdict(
        spans["Departementet sendte høringsnotatet på høring til høringsinstansene."]
    ) == (
        RightsClass.OFFICIAL_TEXT,
        Evidence.DEFAULT_RULE,
    )
    assert verdict(spans["Et sitat under høringen."]) == (
        RightsClass.UNKNOWN,
        Evidence.INSUFFICIENT,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Figur © Oppdiktet Forlag.",
        "Gjengitt med tillatelse fra forfatteren.",
        "Foto: Ola Nordmann.",
    ],
)
def test_a_third_party_marker_makes_a_third_party_work(text: str) -> None:
    item = chapter_two(f"<p>{text}</p>")[text]
    assert verdict(item) == (RightsClass.THIRD_PARTY_WORK, Evidence.TEXTUAL_MARKER)


def test_mixed_rights_within_one_chapter_are_told_apart() -> None:
    spans = chapter_two(CONSULTATION + "<p>Gjengitt med tillatelse.</p>")
    classes = {item.rights for item in spans.values()}
    assert {
        RightsClass.OFFICIAL_TEXT,
        RightsClass.ATTRIBUTED_STATEMENT,
        RightsClass.PUBLIC_STATEMENT,
        RightsClass.THIRD_PARTY_QUOTE,
        RightsClass.THIRD_PARTY_WORK,
    } <= classes


def test_an_annex_chapter_is_unknown_and_a_draft_law_chapter_is_statute_text() -> None:
    title = "Forslag til lov om prøving"

    def renamed(page: SourcePage) -> SourcePage:
        text = page.payload.decode().replace("Tilr&#xE5;ding", title).replace("Tilråding", title)
        return page._replace(payload=text.encode())

    given = [renamed(item) for item in pages()]
    result = assess_document(evidence(given))
    parts = {item.part_id: item for item in result.entries if item.entry == "span"}
    draft = [item for item in result.entries if item.part_id and item.part_id.endswith("-ch3")]
    annex = [item for item in result.entries if item.part_id and item.part_id.endswith("-ch4")]
    assert parts
    assert {verdict(item) for item in draft} == {
        (RightsClass.STATUTE_TEXT, Evidence.CHAPTER_CONTEXT)
    }
    assert {verdict(item) for item in annex} == {(RightsClass.UNKNOWN, Evidence.INSUFFICIENT)}
    assert {item.rights_reasons for item in annex} == {("annex_part",)}


# --- gaps and unproved parts ---------------------------------------------------------


def test_hidden_and_unsupported_inline_content_are_unknown_gaps_screened_for_privacy() -> None:
    body = (
        '<p>Synlig <span hidden="">skjult ola@example.invalid</span> tekst <code>kode</code>.</p>'
    )
    result = assessed(body)
    gaps = [item for item in result.entries if item.entry == "gap" and item.part_id]
    assert {verdict(item) for item in gaps} == {(RightsClass.UNKNOWN, Evidence.INSUFFICIENT)}
    privacy = {item.rights_reasons[0]: (item.privacy, item.privacy_reasons) for item in gaps}
    assert privacy["extraction_gap:hidden_content"] == (
        PrivacyState.REVIEW_REQUIRED,
        ("email_address",),
    )
    assert privacy["extraction_gap:unsupported_inline"] == (
        PrivacyState.UNRESOLVED,
        ("extraction_gap:unsupported_inline",),
    )


def test_spans_of_a_part_with_a_hidden_region_are_unknown_and_unscreened() -> None:
    hidden = with_body(2, "<p>Tekst.</p>")
    raw = hidden.payload.replace(b'<div class="contentArea', b'<div hidden="" class="contentArea')
    result = assessed(given=[*pages(1, 3, 4), hidden._replace(payload=raw)])
    spans = [i for i in result.entries if i.entry == "span" and i.part_id.endswith("-ch2")]  # type: ignore[union-attr]
    assert {item.rights_reasons for item in spans} == {("part_unproved",)}
    assert {item.privacy for item in spans} == {PrivacyState.UNRESOLVED}


def test_document_gaps_are_unknown_and_unresolved() -> None:
    result = assessed(given=pages(1, 2, 3))
    documents = [item for item in result.entries if item.part_id is None]
    assert documents
    assert {(item.rights, item.privacy) for item in documents} == {
        (RightsClass.UNKNOWN, PrivacyState.UNRESOLVED)
    }


# --- privacy -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Kontakt ola@example.invalid for mer.", "email_address"),
        ("Ring tlf. 12345678 for mer.", "phone_number"),
        ("Konto 1234.56.78903 oppgis.", "bank_account_number"),
        ("Hun bor i Oppdiktetveien 12 i dag.", "street_address"),
        (f"Nummeret {fake_national_id()} oppgis.", "national_id_number"),
        ("Nummeret 31139912345 oppgis.", "national_id_like_number"),
        ("Saken gjelder helseopplysninger om pasienten.", "special_category_term"),
    ],
)
def test_identifiers_and_sensitive_terms_require_review_without_storing_the_match(
    text: str, reason: str
) -> None:
    item = chapter_two(f"<p>{text}</p>")[text]
    assert (item.privacy, item.privacy_reasons) == (PrivacyState.REVIEW_REQUIRED, (reason,))
    assert item.rights is RightsClass.OFFICIAL_TEXT
    stored = assessment_text(assessed(f"<p>{text}</p>"))
    assert "example.invalid" not in stored and "12345678" not in stored


def test_a_domain_phrase_is_exempt_and_silence_is_not_proof() -> None:
    item = chapter_two("<p>Loven regulerer biometrisk risiko i systemet.</p>")[
        "Loven regulerer biometrisk risiko i systemet."
    ]
    assert (item.privacy, item.privacy_reasons) == (PrivacyState.NO_DETECTOR_HIT, ())


def test_quoted_personal_data_is_flagged_as_quoted() -> None:
    body = (
        "<p>Advokatforeningen skriver:</p>"
        "<blockquote><p>Kontakt ola@example.invalid om saken.</p></blockquote>"
    )
    quote = chapter_two(body)["Kontakt ola@example.invalid om saken."]
    assert quote.privacy_reasons == ("email_address", "in_quotation")
    assert quote.rights is RightsClass.THIRD_PARTY_QUOTE


# --- evidence, replay, no clearance ----------------------------------------------------


def test_the_record_names_its_version_extraction_and_raw_pages() -> None:
    result = assessed()
    manifest = build_version_manifest(evidence())
    assert result.version_id == manifest.version_id
    assert result.extraction_sha256 == manifest.provenance.extraction_sha256
    assert result.classification_sha256 == manifest.provenance.classification_sha256
    assert result.source_sha256s == tuple(page.raw_sha256 for page in manifest.provenance.pages)
    assert result.source_state == manifest.source.state.value
    assert all(
        item.source_sha256 in result.source_sha256s for item in result.entries if item.part_id
    )


def test_source_completeness_does_not_depend_on_rights() -> None:
    plain, quoted = evidence(), evidence([*pages(1, 3, 4), with_body(2, CONSULTATION)])
    assert build_version_manifest(plain).source.state == build_version_manifest(quoted).source.state
    assert assessed().source_state == "source_complete"


def test_nothing_is_cleared_eligible_or_a_legal_opinion() -> None:
    result = assessed(CONSULTATION)
    assert (result.cleared, result.automatic_release_eligible, result.legal_opinion) == (
        False,
        False,
        False,
    )
    for field in ("cleared", "automatic_release_eligible", "legal_opinion"):
        payload = json.loads(assessment_text(result))
        payload[field] = True
        raw = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
        with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
            parse_assessment(raw)


def test_same_evidence_in_any_order_gives_the_same_bytes_and_ids() -> None:
    given = [*pages(1, 3, 4), with_body(2, CONSULTATION)]
    forward = assess_document(evidence(given))
    backward = assess_document(evidence(list(reversed(given))))
    assert assessment_text(forward) == assessment_text(backward)
    assert [item.entry_id for item in forward.entries] == [
        item.entry_id for item in backward.entries
    ]


def test_parse_reads_back_and_verify_reproduces_from_the_evidence() -> None:
    given = [*pages(1, 3, 4), with_body(2, CONSULTATION)]
    raw = assessment_text(assess_document(evidence(given))).encode()
    assert parse_assessment(raw).entries
    assert verify_assessment(raw, evidence(given)).version_id


def _relabel(payload: dict[str, Any]) -> None:
    for item in payload["entries"]:
        if item["rights"] == "third_party_quote":
            item["rights"] = "official_text"
            item["evidence"] = "default_rule"


def test_a_relabelled_or_reattributed_assessment_fails_verification() -> None:
    given = [*pages(1, 3, 4), with_body(2, CONSULTATION)]
    payload = json.loads(assessment_text(assess_document(evidence(given))))
    _relabel(payload)
    raw = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    assert parse_assessment(raw)
    with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
        verify_assessment(raw, evidence(given))


def test_altered_source_bytes_fail_verification() -> None:
    raw = assessment_text(assessed()).encode()
    altered = [*pages(1, 3, 4), with_body(2, "<p>Endret tekst.</p>")]
    with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
        verify_assessment(raw, evidence(altered))


def test_missing_source_evidence_is_refused() -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        assess_document(SourceEvidence((), None, ()))
    assert caught.value.reason is RefusalReason.SOURCE_IDENTITY_MISSING


def test_non_canonical_bytes_are_refused() -> None:
    raw = assessment_text(assessed()).encode()
    with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
        parse_assessment(raw.replace(b"\n", b"\r\n"))


def test_an_unknown_class_must_carry_unsettled_evidence() -> None:
    item = assessed().entries[0].model_dump()
    with pytest.raises(ValueError, match="unknown exactly when"):
        EntryAssessment.model_validate({**item, "rights": "unknown"})
    assert sha(b"") != item["entry_id"]
