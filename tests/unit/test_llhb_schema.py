"""Schema loading, validation messages, canonical JSONL and checksum."""

from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.schema import (
    DatasetFormatError,
    canonical_case_line,
    canonical_jsonl,
    dataset_checksum,
    load_cases_jsonl,
    load_schema,
    validate_case,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "llhb" / "schema" / "case.schema.json"
)


def make_case(**overrides: Any) -> dict[str, Any]:
    """A schema-valid synthetic C1 case; overrides patch fields."""
    case: dict[str, Any] = {
        "llhb_version": "1.0",
        "case_id": "llhb-v1-C1-001",
        "category": "C1",
        "difficulty": "easy",
        "language": "nb",
        "question": "Hva er formålet med testloven?",
        "expected_behaviour": "answer_with_citation",
        "expected_act_slug": "testloven",
        "expected_section_id": "1",
        "expected_occurrence": None,
        "claimed_act_slug": None,
        "claimed_section_id": None,
        "citation_exists": True,
        "quote_ref": None,
        "fabricated_quote_text": None,
        "corpus_pin": {
            "lovverk_commit": "a" * 40,
            "manifest_generated_at": "2026-08-05T06:00:00Z",
        },
        "ground_truth_evidence": {"validate_citation": {"valid": True}},
        "deterministic_criteria": ["expected-provision-cited"],
        "provenance": {
            "method": "corpus-selected-template",
            "phrasing_model": None,
            "generator_commit": "a" * 40,
            "created": "2026-08-05",
        },
        "validation": {
            "status": "pass",
            "validated_at": "2026-08-05T06:00:00Z",
            "validator_commit": "a" * 40,
        },
    }
    case.update(overrides)
    return case


def test_committed_schema_loads_and_accepts_valid_case() -> None:
    schema = load_schema(SCHEMA_PATH)
    assert validate_case(make_case(), schema) == []


def test_schema_violations_are_deterministic_and_pathed() -> None:
    schema = load_schema(SCHEMA_PATH)
    broken = make_case(case_id="wrong-format", language="en")
    errors = validate_case(broken, schema)
    assert errors == validate_case(broken, schema)
    assert any(error.startswith("$.case_id:") for error in errors)
    assert any(error.startswith("$.language:") for error in errors)


def test_schema_enforces_category_conditionals() -> None:
    schema = load_schema(SCHEMA_PATH)
    c3 = make_case(
        case_id="llhb-v1-C3-001",
        category="C3",
        expected_behaviour="reject_citation",
        expected_act_slug=None,
        expected_section_id=None,
        claimed_act_slug="testloven",
        claimed_section_id="15-99",
        citation_exists=False,
    )
    assert validate_case(c3, schema) == []
    assert validate_case({**c3, "citation_exists": True}, schema) != []


def test_canonical_line_sorts_keys_and_keeps_norwegian_letters() -> None:
    line = canonical_case_line({"b": "å", "a": 1})
    assert line == '{"a":1,"b":"å"}'


def test_canonical_jsonl_is_order_independent_with_trailing_lf() -> None:
    first = make_case()
    second = make_case(case_id="llhb-v1-C1-002")
    payload = canonical_jsonl([second, first])
    assert payload == canonical_jsonl([first, second])
    assert payload.endswith(b"\n") and not payload.endswith(b"\n\n")
    assert payload.decode("utf-8").splitlines()[0].startswith('{"case_id":"llhb-v1-C1-001"')


def test_canonical_jsonl_refuses_duplicate_ids() -> None:
    with pytest.raises(DatasetFormatError, match="duplicate case_id"):
        canonical_jsonl([make_case(), make_case()])


def test_checksum_golden_value_is_stable() -> None:
    """Byte-level golden: the exact canonical bytes AND their SHA-256, both
    derived independently of the implementation (hashlib over the literal),
    so a canonicalization drift can never silently re-bless itself."""
    payload = canonical_jsonl([{"case_id": "x", "verdi": "blåbær"}])
    assert payload == b'{"case_id":"x","verdi":"bl\xc3\xa5b\xc3\xa6r"}\n'
    assert (
        dataset_checksum(payload)
        == "7878dc996ce949b0b55a0da20f5f74d36f636ffb6dc0a18b4491369644abb740"
    )


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    cases = [make_case(), make_case(case_id="llhb-v1-C1-002")]
    path = tmp_path / "cases.jsonl"
    path.write_bytes(canonical_jsonl(cases))
    loaded = load_cases_jsonl(path)
    assert canonical_jsonl(loaded) == canonical_jsonl(cases)


def test_jsonl_loader_rejects_non_object_lines(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"case_id": "x"}\n[1, 2]\n', encoding="utf-8")
    with pytest.raises(DatasetFormatError, match="not a JSON object"):
        load_cases_jsonl(path)


def test_jsonl_loader_rejects_invalid_json_with_line_number(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text('{"case_id": "x"}\n{not json}\n', encoding="utf-8")
    with pytest.raises(DatasetFormatError, match=r"broken\.jsonl:2: invalid JSON"):
        load_cases_jsonl(path)


def test_jsonl_loader_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "gaps.jsonl"
    path.write_text('{"case_id": "x"}\n\n  \n{"case_id": "y"}\n', encoding="utf-8")
    assert [case["case_id"] for case in load_cases_jsonl(path)] == ["x", "y"]


def test_schema_loader_rejects_non_object_schema(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(DatasetFormatError, match="not a JSON object"):
        load_schema(path)


def test_canonical_jsonl_refuses_missing_case_id() -> None:
    with pytest.raises(DatasetFormatError, match="needs a case_id"):
        canonical_jsonl([{"category": "C1"}])
