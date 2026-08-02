"""Flat (chapterless) instruments round-trip: real XML → render → parse.

The two fixtures are the two instruments the pre-8 renderer blacked out
completely. Every one of their legal sections is a titleless H2
``legalArticleHeader`` with a sibling footnote marker, so every heading
rendered as ``## § 1.[^1]`` — a line the section grammar cannot read.
``list_sections`` returned [] for both, no section embedding existed, and
``get_section`` could not return a single provision, while ``get_law``
showed the full text. These tests pin the end-to-end repair on the real
documents (captured from the 2026-07-28 Lovdata tarballs):

* ``sf-19900629-0486.xml`` — instruks-om-delegering-fra-namsretten, §§ 1-4
* ``sf-19750627-4943.xml`` — forskrift-om-utlegg-ved-lønnstrekk, §§ 1-2
"""

from pathlib import Path

from lovspor.embeddings.sections import iter_sections
from lovspor.headings import is_block_id
from lovspor.mcp import _parse_sections
from lovspor.rendering.markdown_renderer import render_markdown

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_INSTRUKS = (_FIXTURES / "sf-19900629-0486.xml").read_bytes()
_UTLEGG = (_FIXTURES / "sf-19750627-4943.xml").read_bytes()


def _section_ids(xml: bytes) -> list[str]:
    return [
        section["section_id"]
        for section in _parse_sections(render_markdown(xml))
        if not is_block_id(section["section_id"])
    ]


def test_instruks_om_delegering_every_legal_section_is_discoverable() -> None:
    assert _section_ids(_INSTRUKS) == ["1", "2", "3", "4"]


def test_utlegg_ved_lonnstrekk_every_legal_section_is_discoverable() -> None:
    assert _section_ids(_UTLEGG) == ["1", "2"]


def test_no_section_carries_marker_or_heading_garbage() -> None:
    # Before the repair the unparsed "## § 1.[^1]" line fell through to the
    # chapter branch of _parse_sections, so following content inherited a
    # parent_chapter that was really a broken section heading.
    for xml in (_INSTRUKS, _UTLEGG):
        sections = _parse_sections(render_markdown(xml))
        assert sections
        for section in sections:
            assert "[^" not in section["heading"]
            assert "[^" not in section["parent_chapter"]
            assert "§" not in section["parent_chapter"]


def test_every_legal_section_reaches_the_embedding_walk() -> None:
    for xml, expected in ((_INSTRUKS, {"1", "2", "3", "4"}), (_UTLEGG, {"1", "2"})):
        embedded = {section.section_id for section in iter_sections(render_markdown(xml))}
        assert expected <= embedded
