"""Flat (chapterless) instruments round-trip: XML → render → parse.

The two fixtures are synthetic reconstructions of the shape the pre-8
renderer blacked out completely. Every legal section is a titleless H2
``legalArticleHeader`` with a sibling footnote marker, so every heading
rendered as ``## § 1.[^1]`` — a line the section grammar cannot read.
``list_sections`` returned [] for documents of this shape, no section
embedding existed, and ``get_section`` could not return a single
provision, while ``get_law`` showed the full text. These tests pin the
end-to-end repair on that shape:

* ``synthetic-flat-forskrift-4s-list.xml`` — §§ 1-4 plus a preamble
  ``ol.defaultList`` of ``listArticle`` items
* ``synthetic-flat-forskrift-2s.xml`` — §§ 1-2

Both fixtures are invented documents (see the comment inside each file);
only the markup shape matches the upstream pattern.
"""

from pathlib import Path

from lovspor.embeddings.sections import iter_sections
from lovspor.headings import is_block_id
from lovspor.mcp import _parse_sections
from lovspor.rendering.markdown_renderer import render_markdown

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_FOUR_SECTIONS = (_FIXTURES / "synthetic-flat-forskrift-4s-list.xml").read_bytes()
_TWO_SECTIONS = (_FIXTURES / "synthetic-flat-forskrift-2s.xml").read_bytes()


def _section_ids(xml: bytes) -> list[str]:
    return [
        section["section_id"]
        for section in _parse_sections(render_markdown(xml))
        if not is_block_id(section["section_id"])
    ]


def test_four_section_flat_act_every_legal_section_is_discoverable() -> None:
    assert _section_ids(_FOUR_SECTIONS) == ["1", "2", "3", "4"]


def test_two_section_flat_act_every_legal_section_is_discoverable() -> None:
    assert _section_ids(_TWO_SECTIONS) == ["1", "2"]


def test_no_section_carries_marker_or_heading_garbage() -> None:
    # Before the repair the unparsed "## § 1.[^1]" line fell through to the
    # chapter branch of _parse_sections, so following content inherited a
    # parent_chapter that was really a broken section heading.
    for xml in (_FOUR_SECTIONS, _TWO_SECTIONS):
        sections = _parse_sections(render_markdown(xml))
        assert sections
        for section in sections:
            assert "[^" not in section["heading"]
            assert "[^" not in section["parent_chapter"]
            assert "§" not in section["parent_chapter"]


def test_every_legal_section_reaches_the_embedding_walk() -> None:
    for xml, expected in (
        (_FOUR_SECTIONS, {"1", "2", "3", "4"}),
        (_TWO_SECTIONS, {"1", "2"}),
    ):
        embedded = {section.section_id for section in iter_sections(render_markdown(xml))}
        assert expected <= embedded
