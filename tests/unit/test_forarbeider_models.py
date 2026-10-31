"""The forarbeider metadata schema, separate from every Lovdata contract (#738)."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import (
    Chapter,
    ForarbeidRecord,
    metadata_text,
    read_metadata,
    write_metadata,
)

PAGE = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"


def _fields() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "regjeringen-9000001",
        "source_document_type": "forarbeid",
        "normative": False,
        "document_type": "prop-l",
        "session": "2099-2100",
        "proposition_number": 901,
        "title": "Oppdiktet tittel",
        "ministry": None,
        "published_at": None,
        "source_document_id": 9000001,
        "source_url": PAGE,
        "source_page_url": f"{PAGE}?ch=1",
        "retrieved_at": "2100-01-02T03:04:05Z",
        "source_sha256": "a" * 64,
        "parser_version": 1,
        "source_provider": "regjeringen.no",
        "rights_basis": "åndsverkloven § 14",
        "source_license": None,
        "chapters": [
            {"chapter_number": 1, "title": "1 Innledning", "url": f"{PAGE}?ch=1"},
            {"chapter_number": 2, "title": "2 Bakgrunn", "url": f"{PAGE}?ch=2"},
        ],
    }


def test_valid_record_round_trips_the_contract_fields() -> None:
    record = ForarbeidRecord.model_validate(_fields())
    assert record.retrieved_at == datetime(2100, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert record.chapters[1] == Chapter(chapter_number=2, title="2 Bakgrunn", url=f"{PAGE}?ch=2")
    assert json.loads(metadata_text(record)) == _fields()


def test_retrieval_time_is_stored_in_utc() -> None:
    fields = _fields() | {"retrieved_at": "2100-01-02T04:04:05+01:00"}
    record = ForarbeidRecord.model_validate(fields)
    assert json.loads(metadata_text(record))["retrieved_at"] == "2100-01-02T03:04:05Z"


def test_published_at_keeps_a_stated_date() -> None:
    record = ForarbeidRecord.model_validate(_fields() | {"published_at": "2100-03-01"})
    assert record.published_at == date(2100, 3, 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_license": "NLOD 2.0"},
        {"source_license": "åndsverkloven § 14"},
        {"source_document_type": "lov"},
        {"normative": True},
        {"rights_basis": "NLOD 2.0"},
        {"source_provider": "lovdata.no"},
        {"schema_version": 2},
        {"parser_version": 2},
        {"document_type": "nou"},
        {"session": "2099-2101"},
        {"session": "2099"},
        {"title": ""},
        {"ministry": ""},
        {"id": "regjeringen-1"},
        {"source_document_id": 1},
        {"proposition_number": 902},
        {"document_type": "prop-ls"},
        {"source_url": "https://evil.test/no/dokumenter/prop.-901-l-20992100/id9000001/"},
        {"source_page_url": PAGE},
        {"source_sha256": "A" * 64},
        {"source_sha256": "a" * 63},
        {"retrieved_at": "2100-01-02T03:04:05"},
        {"chapters": []},
        {"unexpected": "field"},
    ],
    ids=str,
)
def test_invalid_field_is_a_validation_failure(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ForarbeidRecord.model_validate(_fields() | changes)


@pytest.mark.parametrize("field", ["source_license", "ministry", "published_at", "normative"])
def test_omitting_a_field_is_a_validation_failure(field: str) -> None:
    fields = _fields()
    del fields[field]
    with pytest.raises(ValidationError):
        ForarbeidRecord.model_validate(fields)


@pytest.mark.parametrize(
    "chapters",
    [
        [{"chapter_number": 2, "title": "t", "url": f"{PAGE}?ch=2"}],
        [{"chapter_number": 1, "title": "t", "url": f"{PAGE}?ch=2"}],
        [{"chapter_number": 1, "title": "", "url": f"{PAGE}?ch=1"}],
        [{"chapter_number": 0, "title": "t", "url": f"{PAGE}?ch=0"}],
    ],
    ids=str,
)
def test_chapter_inventory_is_ordered_and_of_this_document(chapters: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        ForarbeidRecord.model_validate(_fields() | {"chapters": chapters})


def test_text_is_deterministic_sorted_utf8_with_one_newline() -> None:
    fields = _fields()
    shuffled = dict(reversed(list(fields.items())))
    text = metadata_text(ForarbeidRecord.model_validate(fields))
    assert text == metadata_text(ForarbeidRecord.model_validate(shuffled))
    assert text.endswith("}\n")
    assert not text.endswith("\n\n")
    assert "åndsverkloven § 14" in text
    assert text.index('"chapters"') < text.index('"id"') < text.index('"title": "Oppdiktet')


def test_write_and_read_round_trip(tmp_path: Path) -> None:
    record = ForarbeidRecord.model_validate(_fields())
    target = tmp_path / "new" / "record.json"
    write_metadata(record, target)
    assert target.read_text(encoding="utf-8") == metadata_text(record)
    assert read_metadata(target) == record
    assert [path.name for path in target.parent.iterdir()] == ["record.json"]


@pytest.mark.parametrize(
    "payload", [b"\xff", b"{", b'{"schema_version": 1}'], ids=["bytes", "json", "schema"]
)
def test_read_refuses_what_is_not_the_schema(tmp_path: Path, payload: bytes) -> None:
    target = tmp_path / "record.json"
    target.write_bytes(payload)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_metadata(target)
    assert caught.value.reason is RefusalReason.SCHEMA_INVALID
    assert caught.value.detail == str(target)
