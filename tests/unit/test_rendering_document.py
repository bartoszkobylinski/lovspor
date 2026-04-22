"""Tests for lovspor.rendering.document.

The lov-17410217-000.xml fixture is the real Lovdata-published file for
Norway's oldest still-in-force law (Forbud paa Vimpel-Føring, 1741).
Captured from the gjeldende-lover public-data tarball on 2026-04-22,
NLOD 2.0, attributed to Lovdata.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.errors import ParseError
from lovspor.rendering.document import (
    FrontmatterContext,
    build_frontmatter,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_TINY_XML = (_FIXTURES / "lov-17410217-000.xml").read_bytes()


def _default_context(**overrides: object) -> FrontmatterContext:
    base = {
        "doc_id": "lov-17410217-000",
        "doc_type": "lov",
        "xml_hash": "a" * 64,
        "source_dataset": "gjeldende-lover",
        "retrieved_at": datetime(2026, 4, 22, 1, 31, tzinfo=UTC),
    }
    base.update(overrides)
    return FrontmatterContext(**base)  # type: ignore[arg-type]


def test_legal_document_frontmatter_is_frozen() -> None:
    fm = build_frontmatter(_TINY_XML, _default_context())
    with pytest.raises(ValidationError):
        fm.title = "mutated"  # type: ignore[misc]


def test_frontmatter_context_has_expected_defaults() -> None:
    ctx = FrontmatterContext(
        doc_id="x",
        doc_type="lov",
        xml_hash="y",
        source_dataset="z",
        retrieved_at=datetime(2026, 4, 22, tzinfo=UTC),
    )
    assert ctx.status == "current"
    assert ctx.source_provider == "Lovdata"
    assert ctx.source_license == "NLOD 2.0"


def test_build_frontmatter_extracts_title_from_fixture() -> None:
    fm = build_frontmatter(_TINY_XML, _default_context())
    assert fm.title == "Forbud paa Vimpel-Føring"


def test_build_frontmatter_extracts_short_title_from_fixture() -> None:
    fm = build_frontmatter(_TINY_XML, _default_context())
    assert fm.short_title == "Forbud paa Vimpel-føring"


def test_build_frontmatter_extracts_ref_id_from_fixture() -> None:
    fm = build_frontmatter(_TINY_XML, _default_context())
    assert fm.ref_id == "lov/1741-02-17"


def test_build_frontmatter_extracts_language_from_html_lang() -> None:
    fm = build_frontmatter(_TINY_XML, _default_context())
    assert fm.language == "nb"


def test_build_frontmatter_extracts_ministry_as_list() -> None:
    fm = build_frontmatter(_TINY_XML, _default_context())
    assert fm.ministry == ["Utenriksdepartementet"]


def test_build_frontmatter_handles_missing_date_in_force() -> None:
    """The 1741 law has no <dd class='dateInForce'>."""
    fm = build_frontmatter(_TINY_XML, _default_context())
    assert fm.date_in_force is None


def test_build_frontmatter_extracts_last_updated_date_only() -> None:
    """The raw field is '2021-06-21 (struktur)'; we keep only the ISO date."""
    fm = build_frontmatter(_TINY_XML, _default_context())
    assert fm.last_updated == "2021-06-21"


def test_build_frontmatter_carries_context_fields() -> None:
    ctx = _default_context(
        doc_id="my-id",
        doc_type="lov",
        xml_hash="deadbeef" * 8,
        source_dataset="gjeldende-lover",
    )
    fm = build_frontmatter(_TINY_XML, ctx)
    assert fm.id == "my-id"
    assert fm.type == "lov"
    assert fm.xml_hash == "deadbeef" * 8
    assert fm.source_dataset == "gjeldende-lover"
    assert fm.source_provider == "Lovdata"
    assert fm.source_license == "NLOD 2.0"
    assert fm.status == "current"


def test_build_frontmatter_preserves_retrieved_at() -> None:
    when = datetime(2026, 4, 22, 6, 0, tzinfo=UTC)
    fm = build_frontmatter(_TINY_XML, _default_context(retrieved_at=when))
    assert fm.retrieved_at == when


def test_build_frontmatter_raises_parse_error_on_malformed_xml() -> None:
    with pytest.raises(ParseError, match="malformed XML"):
        build_frontmatter(b"not xml at all", _default_context())


def test_build_frontmatter_raises_parse_error_when_title_missing() -> None:
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="ministry">D</dt><dd class="ministry">X</dd>
    </dl></header><main><h1>Empty</h1></main></body></html>"""
    with pytest.raises(ParseError, match="no <dd class='title'>"):
        build_frontmatter(xml, _default_context())


def test_build_frontmatter_is_deterministic_on_same_input() -> None:
    ctx = _default_context()
    fm_a = build_frontmatter(_TINY_XML, ctx)
    fm_b = build_frontmatter(_TINY_XML, ctx)
    assert fm_a.model_dump() == fm_b.model_dump()


def test_build_frontmatter_extracts_date_only_from_date_plus_note() -> None:
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    <dt class="dateInForce">X</dt><dd class="dateInForce">1999-03-26 (noen merknad)</dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.date_in_force == "1999-03-26"


def test_build_frontmatter_ministry_empty_when_absent() -> None:
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.ministry == []


def test_build_frontmatter_ministry_as_plain_text_falls_back_to_single_item() -> None:
    """Some documents may inline ministry as plain text rather than
    <ul><li>. Fall back to [text] single-element list."""
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    <dt class="ministry">D</dt><dd class="ministry">Finansdepartementet</dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.ministry == ["Finansdepartementet"]


def test_build_frontmatter_ignores_body_level_dd_elements() -> None:
    """HIGH regression guard: metadata extraction must be scoped to the
    <header><dl>, not any <dd class='title'> in the body. Codex PR #9
    reproducer: body-level <dd class='title'>Body title</dd> previously
    leaked into frontmatter.title."""
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">Real header title</dd>
    </dl></header>
    <main>
      <h1>Real header title</h1>
      <article class="legalP">
        Some quote: <dd class="title">Body-level injected title</dd>
      </article>
    </main>
    </body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.title == "Real header title"


def test_build_frontmatter_raises_when_header_dl_missing() -> None:
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <main><h1>No header dl at all</h1></main>
    </body></html>"""
    with pytest.raises(ParseError, match="no <header><dl>"):
        build_frontmatter(xml, _default_context())
