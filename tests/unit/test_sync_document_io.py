"""Tests for lovspor.sync.document_io."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.rendering.document import FrontmatterContext
from lovspor.sync.document_io import (
    delete_document,
    doc_type_for_dataset,
    document_path,
    render_full_document,
    write_document,
)

_TINY_FIXTURE = (Path(__file__).parent.parent / "fixtures" / "lov-17410217-000.xml").read_bytes()


def _context(**overrides: object) -> FrontmatterContext:
    base = {
        "doc_id": "lov-17410217-000",
        "doc_type": "lov",
        "xml_hash": "a" * 64,
        "source_dataset": "gjeldende-lover",
        "retrieved_at": datetime(2026, 4, 22, 1, 31, tzinfo=UTC),
    }
    base.update(overrides)
    return FrontmatterContext(**base)  # type: ignore[arg-type]


def test_doc_type_for_known_datasets() -> None:
    assert doc_type_for_dataset("gjeldende-lover") == "lov"
    assert doc_type_for_dataset("gjeldende-sentrale-forskrifter") == "forskrift"


def test_doc_type_for_unknown_dataset_raises() -> None:
    with pytest.raises(ValueError, match="unknown source_dataset"):
        doc_type_for_dataset("something-else")


def test_document_path_for_law(tmp_path: Path) -> None:
    path = document_path(tmp_path, "gjeldende-lover", "lov-19990326-014")
    assert path == tmp_path / "lover" / "lov-19990326-014.md"


def test_document_path_for_regulation(tmp_path: Path) -> None:
    path = document_path(
        tmp_path,
        "gjeldende-sentrale-forskrifter",
        "sf-20240628-1392",
    )
    assert path == tmp_path / "forskrifter" / "sf-20240628-1392.md"


def test_document_path_for_unknown_dataset_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown source_dataset"):
        document_path(tmp_path, "unknown-dataset", "x")


def test_render_full_document_contains_frontmatter_and_body() -> None:
    out = render_full_document(_TINY_FIXTURE, _context())
    assert out.startswith("---\n")
    assert 'title: "Forbud paa Vimpel-Føring"' in out
    assert "# Forbud paa Vimpel-Føring" in out


def test_render_full_document_is_deterministic() -> None:
    assert render_full_document(_TINY_FIXTURE, _context()) == render_full_document(
        _TINY_FIXTURE,
        _context(),
    )


def test_render_full_document_separates_frontmatter_from_body_with_blank_line() -> None:
    out = render_full_document(_TINY_FIXTURE, _context())
    delimiter_index = out.index("---\n", 4)
    after_fm = out[delimiter_index + 4 :]
    assert after_fm.startswith("\n")


def test_write_document_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "lover" / "nested" / "x.md"
    write_document(path, "hello")
    assert path.read_text(encoding="utf-8") == "hello"


def test_write_document_overwrites_existing(tmp_path: Path) -> None:
    path = tmp_path / "x.md"
    path.write_text("old")
    write_document(path, "new")
    assert path.read_text(encoding="utf-8") == "new"


def test_write_document_utf8_preserves_norwegian_characters(tmp_path: Path) -> None:
    path = tmp_path / "x.md"
    write_document(path, "Æ Ø Å æ ø å")
    assert path.read_text(encoding="utf-8") == "Æ Ø Å æ ø å"


def test_delete_document_removes_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "x.md"
    path.write_text("gone")
    delete_document(path)
    assert not path.exists()


def test_delete_document_noop_on_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "never-existed.md"
    delete_document(path)  # should not raise
    assert not path.exists()
