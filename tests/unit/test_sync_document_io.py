"""Tests for lovspor.sync.document_io."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.rendering.document import FrontmatterContext
from lovspor.storage.manifest import Manifest, ManifestRecord
from lovspor.sync.document_io import (
    dataset_dir,
    delete_document,
    doc_type_for_dataset,
    document_path,
    generate_index,
    render_full_document,
    write_document,
)

_TINY_FIXTURE = (Path(__file__).parent.parent / "fixtures" / "synthetic-flat-law.xml").read_bytes()


def _context(**overrides: object) -> FrontmatterContext:
    base = {
        "doc_id": "lov-17990401-000",
        "slug": "forbud-mot-drage-flyging",
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
    with pytest.raises(
        ValueError,
        match=r"^unknown source_dataset: 'something-else'$",
    ):
        doc_type_for_dataset("something-else")


def test_dataset_dir_for_known_datasets(tmp_path: Path) -> None:
    """Sprint 5 helper: dataset_dir returns the on-disk subdir for
    sibling outputs (history/, INDEX.md)."""
    assert dataset_dir(tmp_path, "gjeldende-lover") == tmp_path / "lover"
    assert dataset_dir(tmp_path, "gjeldende-sentrale-forskrifter") == tmp_path / "forskrifter"


def test_dataset_dir_for_unknown_dataset_raises(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r"^unknown source_dataset: 'something-else'$",
    ):
        dataset_dir(tmp_path, "something-else")


def test_document_path_for_law(tmp_path: Path) -> None:
    path = document_path(tmp_path, "gjeldende-lover", "skatteloven")
    assert path == tmp_path / "lover" / "skatteloven.md"


def test_document_path_for_regulation(tmp_path: Path) -> None:
    path = document_path(
        tmp_path,
        "gjeldende-sentrale-forskrifter",
        "skogbruksforskriften",
    )
    assert path == tmp_path / "forskrifter" / "skogbruksforskriften.md"


def test_document_path_for_unknown_dataset_raises(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r"^unknown source_dataset: 'unknown-dataset'$",
    ):
        document_path(tmp_path, "unknown-dataset", "x")


def test_render_full_document_contains_frontmatter_and_body() -> None:
    out = render_full_document(_TINY_FIXTURE, _context())
    assert out.startswith("---\n")
    assert 'title: "Forbud mot Drage-Flyging"' in out
    assert "# Forbud mot Drage-Flyging" in out


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


def _record(
    *,
    slug: str,
    title: str,
    source_dataset: str = "gjeldende-lover",
    status: str = "current",
) -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov" if source_dataset == "gjeldende-lover" else "forskrift",
        xml_hash="a" * 64,
        markdown_path=(
            "lover/" + slug + ".md"
            if source_dataset == "gjeldende-lover"
            else "forskrifter/" + slug + ".md"
        ),
        source_dataset=source_dataset,
        last_seen=datetime(2026, 4, 26, tzinfo=UTC),
        status=status,  # type: ignore[arg-type]
        slug=slug,
        title=title,
    )


def _manifest(documents: dict[str, ManifestRecord]) -> Manifest:
    return Manifest(
        generated_at=datetime(2026, 4, 26, tzinfo=UTC),
        documents=documents,
    )


def test_generate_index_writes_to_dataset_subdir(tmp_path: Path) -> None:
    manifest = _manifest({"id-1": _record(slug="skatteloven", title="Skatteloven")})
    path = generate_index(tmp_path, "gjeldende-lover", manifest)
    assert path == tmp_path / "lover" / "INDEX.md"
    assert path.exists()


def test_generate_index_lists_only_matching_dataset(tmp_path: Path) -> None:
    """Records from other datasets must not appear in this INDEX."""
    manifest = _manifest(
        {
            "lov-1": _record(slug="skatteloven", title="Skatteloven"),
            "fk-1": _record(
                slug="trafikkforskriften",
                title="Forskrift om trafikk",
                source_dataset="gjeldende-sentrale-forskrifter",
            ),
        },
    )
    path = generate_index(tmp_path, "gjeldende-lover", manifest)
    text = path.read_text(encoding="utf-8")
    assert "skatteloven" in text
    assert "trafikkforskriften" not in text


def test_generate_index_excludes_tombstoned_records(tmp_path: Path) -> None:
    manifest = _manifest(
        {
            "lov-here": _record(slug="present", title="Present Law"),
            "lov-gone": _record(slug="absent", title="Removed Law", status="removed"),
        },
    )
    path = generate_index(tmp_path, "gjeldende-lover", manifest)
    text = path.read_text(encoding="utf-8")
    assert "present" in text
    assert "absent" not in text


def test_generate_index_sorts_alphabetically_by_slug(tmp_path: Path) -> None:
    """Output must sort deterministically regardless of dict insertion order."""
    manifest = _manifest(
        {
            "z-id": _record(slug="zebraloven", title="Zebra"),
            "a-id": _record(slug="alfaloven", title="Alfa"),
            "m-id": _record(slug="midtloven", title="Midt"),
        },
    )
    path = generate_index(tmp_path, "gjeldende-lover", manifest)
    text = path.read_text(encoding="utf-8")
    assert text.index("alfaloven") < text.index("midtloven") < text.index("zebraloven")


def test_generate_index_includes_doc_count_in_header(tmp_path: Path) -> None:
    manifest = _manifest(
        {
            "lov-1": _record(slug="a", title="A"),
            "lov-2": _record(slug="b", title="B"),
        },
    )
    path = generate_index(tmp_path, "gjeldende-lover", manifest)
    text = path.read_text(encoding="utf-8")
    assert "_2 current documents_" in text


def test_generate_index_uses_dataset_label_in_header(tmp_path: Path) -> None:
    lover_path = generate_index(tmp_path, "gjeldende-lover", _manifest({}))
    forskrift_path = generate_index(
        tmp_path,
        "gjeldende-sentrale-forskrifter",
        _manifest({}),
    )
    assert "# Lover" in lover_path.read_text(encoding="utf-8")
    assert "# Sentrale forskrifter" in forskrift_path.read_text(encoding="utf-8")


def test_generate_index_is_byte_deterministic(tmp_path: Path) -> None:
    """Same manifest -> byte-identical INDEX. Same dict insertion order
    is not guaranteed by the caller."""
    manifest = _manifest(
        {
            "lov-2": _record(slug="b", title="B"),
            "lov-1": _record(slug="a", title="A"),
        },
    )
    path_a = generate_index(tmp_path / "a", "gjeldende-lover", manifest)
    path_b = generate_index(tmp_path / "b", "gjeldende-lover", manifest)
    assert path_a.read_bytes() == path_b.read_bytes()


def test_generate_index_raises_on_unknown_dataset(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r"^unknown source_dataset: 'something-else'$",
    ):
        generate_index(tmp_path, "something-else", _manifest({}))


def test_generate_index_format_links_to_md_file(tmp_path: Path) -> None:
    """Each entry must be a Markdown link of form `- [slug](slug.md) — title`."""
    manifest = _manifest({"id-1": _record(slug="skatteloven", title="Skatteloven")})
    path = generate_index(tmp_path, "gjeldende-lover", manifest)
    text = path.read_text(encoding="utf-8")
    assert "- [skatteloven](skatteloven.md) — Skatteloven" in text


_LOVER_INDEX_FM = (
    '---\ntype: "index"\ndataset: "gjeldende-lover"\n'
    'source_provider: "Lovdata"\nsource_license: "NLOD 2.0"\n---\n\n'
)


def test_generate_index_empty_file_has_exact_layout(tmp_path: Path) -> None:
    path = generate_index(tmp_path, "gjeldende-lover", _manifest({}))

    assert path.read_text(encoding="utf-8") == (
        _LOVER_INDEX_FM + "# Lover\n\n_0 current documents_\n\n"
    )


def test_generate_index_entry_file_has_exact_layout(tmp_path: Path) -> None:
    manifest = _manifest({"id-1": _record(slug="skatteloven", title="Skatteloven")})
    path = generate_index(tmp_path, "gjeldende-lover", manifest)

    assert path.read_text(encoding="utf-8") == (
        _LOVER_INDEX_FM
        + "# Lover\n\n_1 current documents_\n\n- [skatteloven](skatteloven.md) — Skatteloven\n"
    )


def test_generate_index_carries_nlod_frontmatter(tmp_path: Path) -> None:
    """Corpus invariant: every output Markdown file, INDEX.md included,
    carries NLOD 2.0 attribution in YAML front matter."""
    content = generate_index(tmp_path, "gjeldende-lover", _manifest({})).read_text(
        encoding="utf-8",
    )

    assert content.startswith("---\n")
    assert 'source_license: "NLOD 2.0"' in content
    assert 'source_provider: "Lovdata"' in content


def test_generate_index_falls_back_to_markdown_stem_and_no_title(tmp_path: Path) -> None:
    record = _record(slug="ignored", title="ignored")
    record = ManifestRecord(
        doc_type=record.doc_type,
        xml_hash=record.xml_hash,
        markdown_path="lover/fallback-slug.md",
        source_dataset=record.source_dataset,
        last_seen=record.last_seen,
        status=record.status,
        slug=None,
        title=None,
    )
    path = generate_index(tmp_path, "gjeldende-lover", _manifest({"id": record}))

    assert "- [fallback-slug](fallback-slug.md) — (no title)" in path.read_text(
        encoding="utf-8",
    )


def test_generate_index_sorts_missing_slug_by_markdown_path(tmp_path: Path) -> None:
    z_record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/zeta.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 26, tzinfo=UTC),
        status="current",
        slug=None,
        title="Zeta",
    )
    a_record = ManifestRecord(
        doc_type="lov",
        xml_hash="b" * 64,
        markdown_path="lover/alfa.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 26, tzinfo=UTC),
        status="current",
        slug=None,
        title="Alfa",
    )
    manifest = _manifest({"z": z_record, "a": a_record})

    text = generate_index(tmp_path, "gjeldende-lover", manifest).read_text(
        encoding="utf-8",
    )

    assert text.index("alfa") < text.index("zeta")
