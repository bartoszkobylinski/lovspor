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
    LegalDocumentFrontMatter,
    build_frontmatter,
    extract_xml_metadata,
    normalize_language,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_TINY_XML = (_FIXTURES / "lov-17410217-000.xml").read_bytes()


def _default_context(**overrides: object) -> FrontmatterContext:
    base = {
        "doc_id": "lov-17410217-000",
        "slug": "forbud-paa-vimpel-foering",
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
        slug="x-slug",
        doc_type="lov",
        xml_hash="y",
        source_dataset="z",
        retrieved_at=datetime(2026, 4, 22, tzinfo=UTC),
    )
    assert ctx.status == "current"
    assert ctx.source_provider == "Lovdata"
    assert ctx.source_license == "NLOD 2.0"


def test_frontmatter_context_is_frozen() -> None:
    ctx = _default_context()

    with pytest.raises(ValidationError):
        ctx.slug = "mutated"  # type: ignore[misc]


def test_legal_document_frontmatter_default_eu_basis_is_empty_list_direct() -> None:
    fm = LegalDocumentFrontMatter(
        id="id",
        slug="slug",
        type="lov",
        ref_id=None,
        title="Title",
        short_title=None,
        language="nb",
        ministry=[],
        date_in_force=None,
        last_change_in_force=None,
        last_updated=None,
        xml_hash="a" * 64,
        source_provider="Lovdata",
        source_dataset="gjeldende-lover",
        source_license="NLOD 2.0",
        retrieved_at=datetime(2026, 4, 22, tzinfo=UTC),
        status="current",
    )

    assert fm.eu_basis == []


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


def test_build_frontmatter_defaults_missing_language_to_empty_string() -> None:
    xml = b"""<!DOCTYPE html><html><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.language == ""


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
    with pytest.raises(ParseError) as exc_info:
        build_frontmatter(b"not xml at all", _default_context())
    assert str(exc_info.value).startswith("malformed XML: ")


def test_build_frontmatter_raises_parse_error_when_title_missing() -> None:
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="ministry">D</dt><dd class="ministry">X</dd>
    </dl></header><main><h1>Empty</h1></main></body></html>"""
    with pytest.raises(ParseError) as exc_info:
        build_frontmatter(xml, _default_context())
    assert str(exc_info.value) == "no <dd class='title'> in document header"


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


def test_build_frontmatter_uses_title_short_when_title_missing() -> None:
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="titleShort">T</dt><dd class="titleShort">Kortform</dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    metadata = extract_xml_metadata(xml)
    assert metadata["title"] == "Kortform"
    assert metadata["short_title"] == "Kortform"


def test_build_frontmatter_extracts_last_change_in_force_date_only() -> None:
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    <dt class="lastChangeInForce">X</dt>
    <dd class="lastChangeInForce">2024-02-03 (endringslov)</dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.last_change_in_force == "2024-02-03"


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


def test_build_frontmatter_ministry_list_items_are_preserved() -> None:
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    <dt class="ministry">D</dt>
    <dd class="ministry"><ul><li>Finansdepartementet</li><li>Justisdepartementet</li></ul></dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.ministry == ["Finansdepartementet", "Justisdepartementet"]


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
    with pytest.raises(ParseError) as exc_info:
        build_frontmatter(xml, _default_context())
    assert str(exc_info.value) == "no <header><dl> found in document"


# ---------------------------------------------------------------------------
# Sprint 8 PR-D: EU / EEA cross-reference extraction
# ---------------------------------------------------------------------------


def test_eu_basis_empty_when_no_eea_references_block() -> None:
    """An act without a <dd class='eeaReferences'> block has eu_basis=[]."""
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.eu_basis == []


def test_eu_basis_extracts_celex_from_eea_references_anchors() -> None:
    """Lovdata renders implemented EU docs as <a href='eu/<celex>'>label</a>
    inside the eeaReferences <dd>. Extract every such href and normalize
    the CELEX to uppercase (Lovdata stores them lowercase)."""
    xml = """<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    <dt class="eeaReferences">EØS</dt>
    <dd class="eeaReferences">
      <a href="avtale/avt-1992-05-02-1-v19">EØS-avtalen vedlegg XIX</a>
      <br />
      nr. 7a (direktiv <a href="eu/31993l0013">93/13/EØF</a>).
    </dd>
    </dl></header><main><h1>T</h1></main></body></html>""".encode()
    fm = build_frontmatter(xml, _default_context())
    assert fm.eu_basis == ["31993L0013"]


def test_eu_basis_deduplicates_repeated_celex_in_source_order() -> None:
    """Same CELEX may appear multiple times in the eeaReferences block
    (e.g. once in the annex link, once in the inline directive
    paragraph). Keep first occurrence; drop duplicates."""
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    <dd class="eeaReferences">
      <a href="eu/32016r0679">GDPR</a>
      <a href="eu/32014l0090">Dir 2014/90</a>
      <a href="eu/32016r0679">GDPR again</a>
    </dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.eu_basis == ["32016R0679", "32014L0090"]


def test_eu_basis_skips_non_eu_anchors_in_eea_block() -> None:
    """The eeaReferences block also contains the <a href='avtale/...'>
    EØS-avtalen annex link, which is not itself an EU document — only
    extract anchors whose href starts with 'eu/'."""
    xml = """<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    <dd class="eeaReferences">
      <a href="avtale/avt-1992-05-02-1-v19">EØS-avtalen vedlegg XIX</a>
    </dd>
    </dl></header><main><h1>T</h1></main></body></html>""".encode()
    fm = build_frontmatter(xml, _default_context())
    assert fm.eu_basis == []


def test_eu_basis_skips_empty_eu_href() -> None:
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    <dd class="eeaReferences"><a href="eu/">empty</a><a href="eu/32016r0679">GDPR</a></dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.eu_basis == ["32016R0679"]


def test_eu_basis_handles_multiple_celex_in_one_block() -> None:
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    <dd class="eeaReferences">
      <a href="eu/32016r0679">GDPR</a>
      <a href="eu/32014l0090">Dir 2014/90</a>
      <a href="eu/32018l1972">EECC</a>
    </dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.eu_basis == ["32016R0679", "32014L0090", "32018L1972"]


def test_legal_document_frontmatter_default_eu_basis_is_empty_list() -> None:
    """Backward-compat: callers that don't supply eu_basis (e.g. older
    rendering paths) get an empty list — matching the shape that the
    Sprint 8 manifest field guarantees post-migration."""
    xml = b"""<!DOCTYPE html><html lang="nb"><body>
    <header><dl>
    <dt class="title">T</dt><dd class="title">T</dd>
    </dl></header><main><h1>T</h1></main></body></html>"""
    fm = build_frontmatter(xml, _default_context())
    assert fm.eu_basis == []


# --- language normalization (the bredbåndsutbyggingsloven upstream corruption) ---

_LOVDATA_CORRUPT_LANG = "nb<br/>nr. 5czn (direktiv <a href="


@pytest.mark.parametrize("tag", ["nb", "nn", "no", "se", "nb-NO", "sma", "und-Latn-150"])
def test_normalize_language_passes_valid_tags_unchanged(tag: str) -> None:
    assert normalize_language(tag) == tag


@pytest.mark.parametrize("absent", [None, "", " ", "   ", "\t", " \n "])
def test_normalize_language_keeps_missing_and_blank_empty(
    absent: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Blank includes whitespace-only: absent-shaped upstream metadata is not
    malformed, so it must stay silent — no warning, no recovery attempt."""
    with caplog.at_level("WARNING"):
        assert normalize_language(absent) == ""
    assert caplog.text == ""


def test_normalize_language_recovers_leading_tag_before_markup_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one sanctioned recovery: a complete valid tag immediately followed
    by markup — the exact Lovdata shape on lov/2020-05-07-40."""
    with caplog.at_level("WARNING"):
        assert normalize_language(_LOVDATA_CORRUPT_LANG) == "nb"
    assert "malformed upstream lang attribute" in caplog.text


@pytest.mark.parametrize(
    "garbage",
    [
        '<a href="x">nb</a>',
        "norsk bokmål",
        "nb nn",
        "NB",
        "n",
        "nb-",
        "12<br/>",
    ],
)
def test_normalize_language_drops_unrecoverable_garbage(
    garbage: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        assert normalize_language(garbage) == ""
    assert "malformed upstream lang attribute" in caplog.text


def test_build_frontmatter_never_lets_markup_reach_language() -> None:
    """Full-pipeline guard: the escaped corrupt attribute from upstream yields
    the recovered tag, and no markup character survives into the model."""
    xml = (
        b'<!DOCTYPE html><html lang="nb&lt;br/&gt;nr. 5czn (direktiv &lt;a href="><body>'
        b'<header><dl><dt class="title">T</dt><dd class="title">T</dd></dl></header>'
        b"<main><h1>T</h1></main></body></html>"
    )
    fm = build_frontmatter(xml, _default_context())
    assert fm.language == "nb"
    assert not any(ch in fm.language for ch in '<>"')
