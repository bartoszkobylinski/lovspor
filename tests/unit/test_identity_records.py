"""Durable manifest records: a record never disappears (#871, ADR-0023 point 1)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.errors import CorpusStateError, ManifestRecordLossError
from lovspor.identity.records import ensure_no_record_loss, replace_manifest
from lovspor.storage.manifest import Manifest, ManifestRecord, write_manifest

AT = datetime(2026, 11, 1, tzinfo=UTC)


def _record(slug: str, status: str = "current") -> ManifestRecord:
    return ManifestRecord(
        doc_type="forskrift",
        xml_hash="a" * 64,
        markdown_path=f"forskrifter/{slug}.md",
        source_dataset="gjeldende-sentrale-forskrifter",
        last_seen=AT,
        status=status,  # type: ignore[arg-type]
        slug=slug,
    )


def _manifest(**records: ManifestRecord) -> Manifest:
    return Manifest(generated_at=AT, documents=records)


def test_no_loss_when_every_prior_id_is_kept() -> None:
    ensure_no_record_loss({"a", "b"}, {"a": _record("a"), "b": _record("b", "removed")})


def test_status_change_is_not_a_loss() -> None:
    ensure_no_record_loss({"a"}, {"a": _record("a", "removed")})


def test_new_ids_are_not_a_loss() -> None:
    ensure_no_record_loss({"a"}, {"a": _record("a"), "z": _record("z")})


def test_a_missing_prior_id_is_refused_by_name() -> None:
    with pytest.raises(ManifestRecordLossError, match="sf-1, sf-3") as raised:
        ensure_no_record_loss({"sf-1", "sf-2", "sf-3"}, {"sf-2": _record("b")})

    assert isinstance(raised.value, CorpusStateError)


def test_replace_manifest_writes_a_superset(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    write_manifest(_manifest(a=_record("a")), path)

    replace_manifest(_manifest(a=_record("a", "removed"), b=_record("b")), path)

    assert set(json.loads(path.read_text(encoding="utf-8"))["documents"]) == {"a", "b"}


def test_replace_manifest_refuses_a_lossy_write_and_leaves_disk_untouched(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manifest.json"
    write_manifest(_manifest(a=_record("a"), b=_record("b", "removed")), path)
    before = path.read_bytes()

    with pytest.raises(ManifestRecordLossError, match="b"):
        replace_manifest(_manifest(a=_record("a")), path)

    assert path.read_bytes() == before


def test_replace_manifest_writes_a_first_manifest(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"

    replace_manifest(_manifest(a=_record("a")), path)

    assert path.exists()
