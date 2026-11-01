"""The chapter result schema cannot state a clearance or a false body (#738)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import (
    ChapterResult,
    HoldReason,
    TextBlock,
    chapter_text,
    read_chapter,
    result_text,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason

FIXTURE = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "chapter-l.html"
URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=2"
WHEN = datetime(2100, 4, 1, 12, tzinfo=UTC)


def valid() -> dict[str, Any]:
    held = build_chapter_result(FIXTURE.read_bytes(), URL, WHEN)
    return dict(json.loads(result_text(held)))


def test_valid_payload_round_trips() -> None:
    assert ChapterResult.model_validate(valid()).chapter_number == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("release_state", "approved"),
        ("release_state", "cleared"),
        ("normative", True),
        ("source_license", "NLOD 2.0"),
        ("rights_basis", "NLOD 2.0"),
        ("source_document_type", "lov"),
        ("source_provider", "lovdata.no"),
        ("extractor_version", 0),
        ("normalizer_version", 0),
        ("hold_reasons", []),
        ("hold_reasons", ["annex"]),
        ("hold_reasons", ["review_required", "review_required"]),
        ("hold_reasons", ["annex", "review_required"]),
        ("hold_reasons", ["cleared"]),
        ("text_sha256", "0" * 64),
        ("text_sha256", None),
        ("source_sha256", "x"),
        ("chapter_number", 0),
        ("chapter_number", 3),
        ("chapter_title", " "),
        ("id", "regjeringen-1-ch2"),
        ("source_url", "https://www.regjeringen.no/no/dokumenter/prop.-1-l-20992100/id1/"),
        ("session", "2000-2001"),
        ("proposition_number", 1),
        ("document_type", "prop-ls"),
        ("source_document_id", 1),
        ("retrieved_at", "2100-04-01T12:00:00"),
    ],
)
def test_invalid_field_fails(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ChapterResult.model_validate({**valid(), field: value})


@pytest.mark.parametrize("field", sorted(ChapterResult.model_fields))
def test_every_field_is_required(field: str) -> None:
    payload = valid()
    del payload[field]
    with pytest.raises(ValidationError):
        ChapterResult.model_validate(payload)


def test_extra_field_fails() -> None:
    with pytest.raises(ValidationError):
        ChapterResult.model_validate({**valid(), "approved": True})


def test_a_landing_page_url_fails() -> None:
    with pytest.raises(ValidationError):
        ChapterResult.model_validate({**valid(), "source_page_url": URL.removesuffix("?ch=2")})


@pytest.mark.parametrize(
    "reason",
    [
        HoldReason.ANNEX,
        HoldReason.IMAGE,
        HoldReason.TABLE,
        HoldReason.FIGURE,
        HoldReason.FOOTNOTES,
        HoldReason.AMBIGUOUS_BOUNDARY,
        HoldReason.DAMAGED_DOM,
        HoldReason.INCOMPLETE_EXTRACTION,
    ],
)
def test_a_body_cannot_sit_beside_a_withholding_reason(reason: HoldReason) -> None:
    with pytest.raises(ValidationError):
        ChapterResult.model_validate({**valid(), "hold_reasons": ["review_required", reason.value]})


def test_a_missing_body_must_name_why() -> None:
    with pytest.raises(ValidationError):
        ChapterResult.model_validate({**valid(), "blocks": None, "text_sha256": None})


def test_no_body_with_a_reason_is_valid() -> None:
    held = ChapterResult.model_validate(
        {
            **valid(),
            "blocks": None,
            "text_sha256": None,
            "hold_reasons": ["review_required", "annex"],
        }
    )
    assert held.blocks is None


def test_a_hash_needs_a_body() -> None:
    with pytest.raises(ValidationError):
        ChapterResult.model_validate(
            {**valid(), "blocks": None, "hold_reasons": ["review_required", "annex"]}
        )


def test_retrieved_at_lands_in_utc() -> None:
    held = ChapterResult.model_validate({**valid(), "retrieved_at": "2100-04-01T14:00:00+02:00"})
    assert held.retrieved_at == WHEN
    assert held.retrieved_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "block",
    [
        {"kind": "paragraph", "text": " "},
        {"kind": "paragraph", "text": ""},
        {"kind": "paragraph", "text": " x"},
        {"kind": "paragraph", "text": "x "},
        {"kind": "paragraph", "text": "a\nb"},
        {"kind": "paragraph", "text": "a\tb"},
        {"kind": "paragraph", "text": "a  b"},
        {"kind": "paragraph", "level": 1, "text": "x"},
        {"kind": "quote", "level": 1, "text": "x"},
        {"kind": "heading", "level": 0, "text": "x"},
        {"kind": "heading", "level": 7, "text": "x"},
        {"kind": "list_item", "level": 0, "text": "x"},
        {"kind": "poem", "text": "x"},
    ],
)
def test_invalid_block_fails(block: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TextBlock.model_validate(block)


def test_chapter_text_is_one_line_per_block() -> None:
    blocks = (
        TextBlock(kind="heading", level=1, text="En"),
        TextBlock(kind="paragraph", text="To"),
        TextBlock(kind="list_item", level=2, text="Tre"),
        TextBlock(kind="quote", text="Fire"),
    )
    assert (
        chapter_text(blocks) == "heading 1\tEn\nparagraph 0\tTo\nlist_item 2\tTre\nquote 0\tFire\n"
    )


def test_result_text_layout() -> None:
    text = result_text(ChapterResult.model_validate(valid()))
    assert text.endswith("}\n")
    assert not text.endswith("\n\n")
    assert "æøå" in text
    assert json.loads(text) == valid()
    assert list(json.loads(text)) == sorted(json.loads(text))
    assert text.splitlines()[1].startswith('  "')


def test_read_chapter_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(result_text(ChapterResult.model_validate(valid())), encoding="utf-8")
    assert read_chapter(path).chapter_number == 2


def test_read_chapter_refuses_an_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ForarbeiderRefusedError) as refused:
        read_chapter(path)
    assert refused.value.reason is RefusalReason.SCHEMA_INVALID


def test_read_chapter_refuses_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ForarbeiderRefusedError) as refused:
        read_chapter(tmp_path / "x.json")
    assert refused.value.reason is RefusalReason.INPUT_UNREADABLE


def test_the_id_must_name_the_chapter() -> None:
    with pytest.raises(ValidationError):
        ChapterResult.model_validate({**valid(), "id": "regjeringen-9000001"})


def test_a_foreign_page_url_fails() -> None:
    with pytest.raises(ValidationError):
        ChapterResult.model_validate({**valid(), "source_page_url": "https://example.org/?ch=2"})
