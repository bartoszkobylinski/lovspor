"""Candidate validator: schema layer + category checks C1-C8, fail closed."""

from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.corpus_pin import CorpusPin
from lovspor.llhb.quotes import normalize_quote_text, quote_sha256
from lovspor.llhb.schema import load_schema
from lovspor.llhb.validation import CandidateValidator, CaseIssue, IssueSeverity
from lovspor.mcp import CorpusReader
from tests.unit.llhb_fixtures import EKKOLOVEN_BODY, GENERATED_AT, build_corpus, standard_corpus
from tests.unit.test_llhb_schema import SCHEMA_PATH, make_case


@pytest.fixture
def reader(tmp_path: Path) -> CorpusReader:
    return standard_corpus(tmp_path)


@pytest.fixture
def validator(reader: CorpusReader) -> CandidateValidator:
    return CandidateValidator(reader, load_schema(SCHEMA_PATH))


def _codes(issues: list[CaseIssue]) -> list[str]:
    return [issue.code for issue in issues]


def test_issue_severity_vocabulary_is_frozen() -> None:
    """Severity values are artifact vocabulary: validation.jsonl rows and the
    review-queue split (pool matches serialized strings against the enum)."""
    assert [severity.value for severity in IssueSeverity] == ["error", "warning"]


def test_schema_issues_carry_the_case_id(validator: CandidateValidator) -> None:
    """A schema issue must be attributable to the case that raised it."""
    case = make_case(category="C9")
    issues = validator.validate_case(case)
    assert issues
    assert all(issue.case_id == case["case_id"] for issue in issues)


def test_valid_c1_passes(validator: CandidateValidator) -> None:
    assert validator.validate_case(make_case()) == []


def test_c1_missing_expected_provision_fails(validator: CandidateValidator) -> None:
    case = make_case(expected_section_id="99")
    assert _codes(validator.validate_case(case)) == ["provision-missing"]


def test_schema_errors_short_circuit_category_checks(validator: CandidateValidator) -> None:
    issues = validator.validate_case(make_case(category="C9"))
    assert issues and all(issue.code == "schema" for issue in issues)


def test_valid_c2_passes_and_leaky_question_fails(validator: CandidateValidator) -> None:
    c2 = make_case(
        case_id="llhb-v1-C2-001",
        category="C2",
        expected_behaviour="identify_provision",
        question="Hvilken bestemmelse regulerer fradrag for kostnader?",
    )
    assert validator.validate_case(c2) == []
    leaky = {**c2, "question": "Hvilken § i testloven regulerer fradrag?"}
    assert "c2-question-leaks-target" in _codes(validator.validate_case(leaky))


def _c3(**overrides: Any) -> dict[str, Any]:
    case = make_case(
        case_id="llhb-v1-C3-001",
        category="C3",
        expected_behaviour="reject_citation",
        expected_act_slug=None,
        expected_section_id=None,
        claimed_act_slug="testloven",
        claimed_section_id="15-99",
        citation_exists=False,
    )
    case.update(overrides)
    return case


def test_valid_c3_trap_passes(validator: CandidateValidator) -> None:
    assert validator.validate_case(_c3()) == []


def test_c3_existing_section_is_not_a_trap(validator: CandidateValidator) -> None:
    case = _c3(claimed_section_id="1")
    assert _codes(validator.validate_case(case)) == ["trap-section-exists"]


def test_c3_ambiguous_id_is_not_nonexistent(validator: CandidateValidator) -> None:
    case = _c3(claimed_act_slug="dobbeltloven", claimed_section_id="6-2")
    assert _codes(validator.validate_case(case)) == ["trap-section-ambiguous-not-nonexistent"]


def test_c3_unknown_act_fails(validator: CandidateValidator) -> None:
    case = _c3(claimed_act_slug="finnesikkeloven")
    assert _codes(validator.validate_case(case)) == ["act-unknown"]


def _c4(**overrides: Any) -> dict[str, Any]:
    case = make_case(
        case_id="llhb-v1-C4-001",
        category="C4",
        expected_behaviour="reject_premise",
        claimed_act_slug="dobbeltloven",
        claimed_section_id="15-99",
        citation_exists=False,
    )
    case.update(overrides)
    return case


def test_valid_c4_wrong_act_trap_passes(validator: CandidateValidator) -> None:
    assert validator.validate_case(_c4()) == []


def test_c4_trap_equal_to_expected_fails(validator: CandidateValidator) -> None:
    case = _c4(claimed_act_slug="testloven", claimed_section_id="1", citation_exists=True)
    assert _codes(validator.validate_case(case)) == ["trap-equals-expected"]


def test_valid_c5_duplicate_section_passes(validator: CandidateValidator) -> None:
    c5 = make_case(
        case_id="llhb-v1-C5-001",
        category="C5",
        expected_behaviour="answer_with_citation",
        expected_act_slug="dobbeltloven",
        expected_section_id="6-2",
    )
    # The duplicate id IS the trap — valid with or without a pinned occurrence.
    assert validator.validate_case(c5) == []
    assert validator.validate_case({**c5, "expected_occurrence": 1}) == []


def test_c5_veileder_only_duplicate_fails_closed(tmp_path: Path) -> None:
    """RC3: a must_disambiguate case pinned to an id whose duplication is
    only a veileder echo is rejected — the oracle sees one provision."""
    reader = build_corpus(
        tmp_path,
        {"ekkoloven": ("Lov om ekko-regler (ekkoloven)", EKKOLOVEN_BODY)},
    )
    echo_validator = CandidateValidator(reader, load_schema(SCHEMA_PATH))
    c5 = make_case(
        case_id="llhb-v1-C5-201",
        category="C5",
        expected_behaviour="must_disambiguate",
        expected_act_slug="ekkoloven",
        expected_section_id="2",
        valid_occurrences=[1, 2],
    )
    assert "not-genuinely-ambiguous" in _codes(echo_validator.validate_case(c5))


def test_c5_v2_valid_occurrences_must_match_oracle(validator: CandidateValidator) -> None:
    c5 = make_case(
        case_id="llhb-v1-C5-101",
        category="C5",
        expected_behaviour="must_disambiguate",
        expected_act_slug="dobbeltloven",
        expected_section_id="6-2",
        valid_occurrences=[1, 2],
    )
    assert validator.validate_case(c5) == []
    curated = {**c5, "valid_occurrences": [1, 2, 3]}
    assert _codes(validator.validate_case(curated)) == ["valid-occurrences-mismatch"]
    unique = {
        **c5,
        "expected_act_slug": "testloven",
        "expected_section_id": "1",
        "valid_occurrences": [1, 2],
    }
    assert "not-genuinely-ambiguous" in _codes(validator.validate_case(unique))


def test_c5_unique_section_is_not_ambiguous(validator: CandidateValidator) -> None:
    c5 = make_case(
        case_id="llhb-v1-C5-002",
        category="C5",
        expected_act_slug="testloven",
        expected_section_id="1",
    )
    assert "not-genuinely-ambiguous" in _codes(validator.validate_case(c5))


def test_valid_c6_false_premise_passes(validator: CandidateValidator) -> None:
    c6 = make_case(
        case_id="llhb-v1-C6-001",
        category="C6",
        expected_behaviour="reject_premise",
        claimed_act_slug="testloven",
        claimed_section_id="15-99",
        citation_exists=False,
    )
    assert validator.validate_case(c6) == []


def _quote_ref(reader: CorpusReader) -> dict[str, Any]:
    normalized = normalize_quote_text(str(reader.get_section("testloven", "5-12")["body"]))
    needle = "fradrag for kostnader"
    start = normalized.find(needle)
    return {
        "slug": "testloven",
        "section_id": "5-12",
        "occurrence": None,
        "char_span": [start, start + len(needle)],
        "sha256_normalized": quote_sha256(needle),
    }


def test_valid_c7_true_quote_passes(
    validator: CandidateValidator,
    reader: CorpusReader,
) -> None:
    c7 = make_case(
        case_id="llhb-v1-C7-001",
        category="C7",
        expected_behaviour="verify_quote",
        expected_act_slug="testloven",
        expected_section_id="5-12",
        quote_ref=_quote_ref(reader),
    )
    assert validator.validate_case(c7) == []


def test_c7_wrong_hash_fails_closed(
    validator: CandidateValidator,
    reader: CorpusReader,
) -> None:
    ref = {**_quote_ref(reader), "sha256_normalized": "0" * 64}
    c7 = make_case(
        case_id="llhb-v1-C7-002",
        category="C7",
        expected_behaviour="verify_quote",
        expected_act_slug="testloven",
        expected_section_id="5-12",
        quote_ref=ref,
    )
    assert _codes(validator.validate_case(c7)) == ["quote-ref-hash-mismatch"]


def test_valid_c7_fabricated_quote_passes(validator: CandidateValidator) -> None:
    c7 = make_case(
        case_id="llhb-v1-C7-003",
        category="C7",
        expected_behaviour="deny_quote",
        expected_act_slug="testloven",
        expected_section_id="5-12",
        fabricated_quote_text="Det gis aldri fradrag for noe som helst.",
    )
    assert validator.validate_case(c7) == []


def test_c7_fabricated_quote_that_verifies_fails(validator: CandidateValidator) -> None:
    c7 = make_case(
        case_id="llhb-v1-C7-004",
        category="C7",
        expected_behaviour="deny_quote",
        expected_act_slug="testloven",
        expected_section_id="5-12",
        fabricated_quote_text="fradrag for kostnader til testing",
    )
    assert _codes(validator.validate_case(c7)) == ["fabricated-quote-actually-verifies"]


def _c8(**overrides: Any) -> dict[str, Any]:
    case = make_case(
        case_id="llhb-v1-C8-001",
        category="C8",
        expected_behaviour="abstain",
        expected_act_slug=None,
        expected_section_id=None,
        citation_exists=None,
        question="Hvilke rundskriv utdyper reglene om testing?",
    )
    case.update(overrides)
    return case


def test_c8_without_spot_check_warns(validator: CandidateValidator) -> None:
    issues = validator.validate_case(_c8())
    assert _codes(issues) == ["c8-requires-manual-review"]
    assert issues[0].severity is IssueSeverity.WARNING


def test_c8_spot_checked_passes(validator: CandidateValidator) -> None:
    case = _c8(
        validation={
            "status": "pass",
            "validated_at": "2026-08-05T06:00:00Z",
            "validator_commit": "a" * 40,
            "spot_checked": True,
        },
    )
    assert validator.validate_case(case) == []


def test_c8_with_citation_fields_fails(validator: CandidateValidator) -> None:
    case = _c8(expected_act_slug="testloven")
    assert "c8-unexpected-citation-field" in _codes(validator.validate_case(case))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_act_slug", "testloven"),
        ("expected_section_id", "1"),
        ("expected_occurrence", 1),
        ("claimed_act_slug", "testloven"),
        ("claimed_section_id", "1"),
        ("citation_exists", True),
        ("citation_exists", False),
        ("quote_ref", {"slug": "testloven", "section_id": "1", "sha256_normalized": "0" * 64}),
        ("fabricated_quote_text", "oppdiktet tekst"),
    ],
)
def test_c8_rejects_every_nonnull_citation_or_quote_field(
    validator: CandidateValidator,
    field: str,
    value: Any,
) -> None:
    """Codex PR #16 finding 1: a partial null-field list was fail-open —
    claimed_section_id + citation_exists slipped through as structurally sound.
    Every trap/citation/quote field must independently trip an ERROR."""
    issues = validator.validate_case(_c8(**{field: value}))
    errors = [i for i in issues if i.severity is IssueSeverity.ERROR]
    assert _codes(errors) == ["c8-unexpected-citation-field"], (field, value)


def test_pin_mismatch_is_reported(reader: CorpusReader) -> None:
    pin = CorpusPin(lovverk_commit="b" * 40, manifest_generated_at=GENERATED_AT)
    validator = CandidateValidator(reader, load_schema(SCHEMA_PATH), pin)
    assert _codes(validator.validate_case(make_case())) == ["corpus-pin-mismatch"]


def test_dataset_level_duplicates_and_provision_cap(validator: CandidateValidator) -> None:
    base = make_case()
    dupes = [base, make_case()]
    assert "duplicate-case-id" in _codes(validator.validate_dataset(dupes))
    crowd = [make_case(case_id=f"llhb-v1-C1-{n:03d}") for n in range(1, 4)]
    assert "provision-cap-exceeded" in _codes(validator.validate_dataset(crowd))
