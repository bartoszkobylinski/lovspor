"""Tests for lovspor.storage.manifest.

Determinism is the core invariant: same in-memory Manifest produces
byte-identical JSON on disk. The sync pipeline relies on this to
recognize "no changes" runs.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.errors import ParseError
from lovspor.storage.manifest import (
    MANIFEST_VERSION,
    Manifest,
    ManifestRecord,
    read_manifest,
    write_manifest,
)


def _record(
    *,
    xml_hash: str = "a" * 64,
    doc_type: str = "lov",
    markdown_path: str = "lover/x.md",
    status: str = "current",
) -> ManifestRecord:
    return ManifestRecord(
        doc_type=doc_type,
        xml_hash=xml_hash,
        markdown_path=markdown_path,
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 22, 1, 31, tzinfo=UTC),
        status=status,
    )


def _manifest(documents: dict[str, ManifestRecord] | None = None) -> Manifest:
    return Manifest(
        generated_at=datetime(2026, 4, 22, 6, 0, tzinfo=UTC),
        documents=documents or {},
    )


def test_manifest_record_is_frozen() -> None:
    rec = _record()
    with pytest.raises(ValidationError):
        rec.xml_hash = "changed"  # type: ignore[misc]


def test_manifest_record_history_fields_default_to_none() -> None:
    """Sprint 5 history metadata fields are optional and default to
    None so manifests written by Sprint 4 engines still load without
    a migration."""
    rec = _record()
    assert rec.total_changes is None
    assert rec.last_changed is None


def test_manifest_record_title_defaults_to_none() -> None:
    rec = _record()
    assert rec.title is None


def test_manifest_record_accepts_history_fields_when_provided() -> None:
    rec = ManifestRecord(
        doc_type="lov",
        xml_hash="a",
        markdown_path="lover/skatteloven.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 27, 5, 0, tzinfo=UTC),
        status="current",
        slug="skatteloven",
        title="Skatteloven",
        total_changes=12,
        last_changed="2026-04-27",
    )
    assert rec.total_changes == 12
    assert rec.last_changed == "2026-04-27"


def test_manifest_record_eu_basis_defaults_to_none() -> None:
    """Sprint 8 PR-D added eu_basis as an optional field defaulting to
    None so pre-Sprint-8 manifests still load. The None value is what
    the orchestrator's Sprint 8 backfill migration trigger looks for."""
    rec = _record()
    assert rec.eu_basis is None


def test_manifest_record_accepts_eu_basis_list() -> None:
    rec = ManifestRecord(
        doc_type="lov",
        xml_hash="a",
        markdown_path="lover/personopplysningsloven.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 27, 5, 0, tzinfo=UTC),
        status="current",
        slug="personopplysningsloven",
        title="Personopplysningsloven",
        eu_basis=["32016R0679"],
    )
    assert rec.eu_basis == ["32016R0679"]


def test_manifest_record_accepts_empty_eu_basis_list() -> None:
    """Empty list is the canonical 'no EEA references' value — distinct
    from None (pre-Sprint-8 unknown)."""
    rec = ManifestRecord(
        doc_type="lov",
        xml_hash="a",
        markdown_path="lover/straffeloven.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 27, 5, 0, tzinfo=UTC),
        status="current",
        slug="straffeloven",
        title="Straffeloven",
        eu_basis=[],
    )
    assert rec.eu_basis == []


def test_manifest_is_frozen() -> None:
    mf = _manifest()
    with pytest.raises(ValidationError):
        mf.version = 99  # type: ignore[misc]


def test_manifest_default_version_is_literal_one() -> None:
    """Pin the on-disk manifest version to literal 1 (not the
    MANIFEST_VERSION constant). A mutation that changes the constant
    would otherwise pass this test trivially."""
    assert _manifest().version == 1
    assert MANIFEST_VERSION == 1


def test_manifest_record_status_rejects_unknown_value() -> None:
    """MEDIUM regression guard: ManifestRecord.status is constrained to
    'current' | 'removed'. A typo like 'curent' previously slipped
    through and was silently treated as 'not current' by the change
    detector, producing false-positive 'new' classifications. Codex
    PR #11 reproducer."""
    with pytest.raises(ValidationError):
        ManifestRecord(
            doc_type="lov",
            xml_hash="a",
            markdown_path="lover/x.md",
            source_dataset="gjeldende-lover",
            last_seen=datetime(2026, 4, 22, 1, 31, tzinfo=UTC),
            status="curent",  # type: ignore[arg-type]
        )


def test_read_manifest_rejects_unknown_status_in_record(tmp_path: Path) -> None:
    """End-to-end: a manifest file with an invalid status value must
    fail to load, not silently slip through."""
    payload = {
        "version": 1,
        "generated_at": "2026-04-22T06:00:00+00:00",
        "documents": {
            "x": {
                "doc_type": "lov",
                "xml_hash": "a",
                "markdown_path": "lover/x.md",
                "source_dataset": "gjeldende-lover",
                "last_seen": "2026-04-22T01:31:00+00:00",
                "status": "stale",
            },
        },
    }
    path = tmp_path / "bad-status.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ParseError, match="invalid manifest schema"):
        read_manifest(path)


def test_write_read_round_trip_preserves_data(tmp_path: Path) -> None:
    original = _manifest(
        {
            "lov-19990326-014": _record(markdown_path="lover/skatteloven.md"),
            "lov-17410217-000": _record(markdown_path="lover/vimpel.md"),
        },
    )
    path = tmp_path / "manifest.json"
    write_manifest(original, path)
    loaded = read_manifest(path)
    assert loaded == original


def test_write_manifest_is_byte_deterministic(tmp_path: Path) -> None:
    """Same in-memory manifest -> byte-identical file on repeated writes."""
    mf = _manifest(
        {
            "b": _record(xml_hash="b" * 64),
            "a": _record(xml_hash="a" * 64),
        },
    )
    p1 = tmp_path / "m1.json"
    p2 = tmp_path / "m2.json"
    write_manifest(mf, p1)
    write_manifest(mf, p2)
    assert p1.read_bytes() == p2.read_bytes()


def test_write_manifest_sorts_documents_by_id(tmp_path: Path) -> None:
    """Output must sort keys alphabetically so insertion order does not
    affect the serialized bytes."""
    mf = _manifest(
        {
            "zzz": _record(),
            "aaa": _record(),
            "mmm": _record(),
        },
    )
    path = tmp_path / "manifest.json"
    write_manifest(mf, path)
    text = path.read_text(encoding="utf-8")
    assert text.index("aaa") < text.index("mmm") < text.index("zzz")


def test_write_manifest_ends_with_single_trailing_newline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    write_manifest(_manifest(), path)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_write_manifest_uses_utf8_for_norwegian_characters(
    tmp_path: Path,
) -> None:
    mf = _manifest(
        {
            "lov-skatt": _record(markdown_path="lover/Skatteloven — æøå.md"),
        },
    )
    path = tmp_path / "manifest.json"
    write_manifest(mf, path)
    text = path.read_text(encoding="utf-8")
    assert "Skatteloven — æøå" in text


def test_write_manifest_indents_with_two_spaces(tmp_path: Path) -> None:
    mf = _manifest({"x": _record()})
    path = tmp_path / "manifest.json"
    write_manifest(mf, path)
    text = path.read_text(encoding="utf-8")
    assert '\n  "' in text


def test_write_manifest_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "manifest.json"
    write_manifest(_manifest(), path)
    assert path.exists()


def test_read_manifest_raises_file_not_found_when_missing(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        read_manifest(tmp_path / "does-not-exist.json")


def test_read_manifest_raises_parse_error_on_malformed_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ParseError) as exc_info:
        read_manifest(path)
    assert str(exc_info.value).startswith(f"{path}: malformed JSON manifest: ")


def test_read_manifest_raises_parse_error_on_invalid_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wrong-schema.json"
    path.write_text(
        json.dumps({"not": "a manifest"}),
        encoding="utf-8",
    )
    with pytest.raises(ParseError) as exc_info:
        read_manifest(path)
    assert str(exc_info.value).startswith(f"{path}: invalid manifest schema: ")


def test_read_manifest_rejects_extra_record_fields(
    tmp_path: Path,
) -> None:
    """Schema drift in records must surface, not silently pass. Forward
    compatibility comes via bumping MANIFEST_VERSION in a coordinated
    way; tolerating unknown fields makes silent semantic mismatch
    possible."""
    path = tmp_path / "manifest.json"
    payload = {
        "version": 1,
        "generated_at": "2026-04-22T06:00:00+00:00",
        "documents": {
            "x": {
                "doc_type": "lov",
                "xml_hash": "a",
                "markdown_path": "lover/x.md",
                "source_dataset": "gjeldende-lover",
                "last_seen": "2026-04-22T01:31:00+00:00",
                "status": "current",
                "future_field": "should be rejected",
            },
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ParseError, match="invalid manifest schema"):
        read_manifest(path)


def test_read_manifest_rejects_extra_top_level_fields(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    payload = {
        "version": 1,
        "generated_at": "2026-04-22T06:00:00+00:00",
        "documents": {},
        "future_top_level": "should be rejected",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ParseError, match="invalid manifest schema"):
        read_manifest(path)


def test_read_manifest_rejects_unsupported_version(tmp_path: Path) -> None:
    """MEDIUM regression guard: a future manifest version must be
    rejected, not silently accepted. Codex PR #10 reproducer."""
    path = tmp_path / "future.json"
    payload = {
        "version": 999,
        "generated_at": "2026-04-22T06:00:00+00:00",
        "documents": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ParseError) as exc_info:
        read_manifest(path)
    assert "unsupported manifest version 999; this engine reads version 1" in str(
        exc_info.value,
    )


def test_read_manifest_wraps_unicode_decode_error_as_parse_error(
    tmp_path: Path,
) -> None:
    """MEDIUM regression guard: malformed UTF-8 must raise ParseError
    via the module's own contract, not leak UnicodeDecodeError. Codex
    PR #10 reproducer."""
    path = tmp_path / "bad-utf8.json"
    path.write_bytes(b"\xff\xfe\xfd not valid utf-8")
    with pytest.raises(ParseError) as exc_info:
        read_manifest(path)
    assert str(exc_info.value).startswith(f"{path}: manifest is not valid UTF-8: ")


def test_empty_manifest_round_trips(tmp_path: Path) -> None:
    empty = _manifest()
    path = tmp_path / "empty.json"
    write_manifest(empty, path)
    assert read_manifest(path) == empty


def test_removed_status_is_preserved_through_round_trip(
    tmp_path: Path,
) -> None:
    mf = _manifest(
        {
            "gone": _record(status="removed"),
            "here": _record(status="current"),
        },
    )
    path = tmp_path / "manifest.json"
    write_manifest(mf, path)
    loaded = read_manifest(path)
    assert loaded.documents["gone"].status == "removed"
    assert loaded.documents["here"].status == "current"


def test_manifest_generated_at_round_trips_as_tz_aware_datetime(
    tmp_path: Path,
) -> None:
    original = _manifest()
    path = tmp_path / "manifest.json"
    write_manifest(original, path)
    loaded = read_manifest(path)
    assert loaded.generated_at == original.generated_at
    assert loaded.generated_at.tzinfo is not None
