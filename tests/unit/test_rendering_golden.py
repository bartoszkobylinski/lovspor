"""Golden-render guard: corpus bytes cannot change without a version bump.

Sync never re-renders a document whose XML is unchanged UNLESS its manifest
``renderer_version`` stamp is stale, so a rendering change that ships without
bumping ``RENDERER_VERSION`` silently never reaches frozen documents — the
exact failure mode behind the 2026-07 render-drift backlog (1,991 docs, ~6.3M
missing chars, `analysis/corpus-render-drift-audit.md`).

Two digests, because the risk has two halves:

``_PINNED_DOCUMENT`` pins ``render_full_document`` over the committed Vimpel
fixture — frontmatter *and* body, since a frontmatter-only rewrite is also
drift (the Sprint 8 eu_basis backfill was exactly that).

``_PINNED_SURFACE`` pins ``render_markdown`` over a synthetic document that
exercises the block-dispatch surface the fixture does not reach: tables under
BOTH article wrappers, lists, inline marks, change notes, and not-in-force
elision. The 1741 Vimpel law is two paragraphs of prose — a table-rendering
change would sail straight past a fixture-only guard, and a table-rendering
bug is what froze the sign catalogues of `skiltforskriften` in the first place.

When this fails after a deliberate rendering change:
1. bump ``RENDERER_VERSION`` (+1) in ``rendering/markdown_renderer.py``,
2. re-pin the digest(s) from the assertion message.
Never re-pin a digest without bumping the version — that re-freezes the corpus
on the old output.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from lovspor.rendering.document import FrontmatterContext
from lovspor.rendering.markdown_renderer import RENDERER_VERSION, render_markdown
from lovspor.sync.document_io import render_full_document

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "lov-17410217-000.xml"

_CONTEXT = FrontmatterContext(
    doc_id="lov-17410217-000",
    slug="lov-17410217-000",
    doc_type="lov",
    xml_hash="0" * 64,
    source_dataset="gjeldende-lover",
    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
)

# Mirrors the real skiltforskriften shapes: a sign catalogue is a <table> inside
# <article class="defaultP"> (dropped entirely by the pre-2026-07 renderer), and
# a road-marking catalogue is a <table> inside <article class="legalP"> (flattened
# into "…ytterkant.10001000 Kjørefeltlinje"). Both must stay pinned.
_SURFACE = (
    '<!DOCTYPE html><html lang="nb"><body><main>'
    "<h1>Tittel</h1>"
    "<section><h2>Kapittel 1</h2>"
    '<h3 class="legalArticleHeader"><span>§ 1.</span><span>Overskrift</span></h3>'
    '<article class="legalP">Tekst med <strong>fet</strong>, <em>kursiv</em> og '
    '<a href="https://lovdata.no/lov/1965-06-18-4/x">lenke</a>.<br/>Ny linje.</article>'
    '<article class="numberedLegalP">1. Nummerert ledd.</article>'
    '<article class="defaultP"><div aria-level="6" role="heading">Vedlegg 1</div>'
    "<table><caption>Skiltkatalog</caption>"
    "<thead><tr><th>Nr</th><th>Betydning</th></tr></thead>"
    "<tbody><tr><td>306.8</td><td>Forbudt for gående</td></tr>"
    '<tr><td colspan="2">Spennende celle</td></tr></tbody></table></article>'
    '<article class="legalP">Langsgående oppmerking.'
    "<table><tbody><tr><td>1000</td><td>1000 Kjørefeltlinje</td></tr></tbody></table>"
    "</article>"
    # A paragraph-class article holding a NON-table block child. The three
    # shapes below are the corpus's most common article structure by a wide
    # margin (222,751 articles in 3,514 documents) and were flattened into a
    # single run-on line until renderer 4. A table under the same wrapper is
    # pinned above but never protected them: the pre-4 code branched on
    # <table> specifically, so the guard covered the one shape that was
    # already correct.
    '<article class="legalP">Grunndata:'
    '<ol class="defaultList"><li>navn</li><li>adresse</li></ol></article>'
    '<article class="numberedLegalP">(1) Innledning'
    '<article class="legalP">Andre ledd.</article></article>'
    '<article class="listArticle">a) Innledning<p>Etterfølgende avsnitt.</p></article>'
    "<ol><li>Første</li><li>Andre</li></ol>"
    "<ul><li>Kule</li></ul>"
    # A list item wrapping an <article> — 184,344 items in 3,478 documents,
    # the corpus's second most common structure and the whole residue left
    # after the article-level fix. A table inside such an item (the food-
    # additive E-number catalogues) was flattened along with it.
    '<ol class="defaultList"><li data-name="(1)" value="1">Polysorbater:'
    '<article class="legalP">Innledende avsnitt.'
    "<table><tbody><tr><td>E 432</td><td>Polysorbat 20</td></tr></tbody></table>"
    "</article></li></ol>"
    '<div aria-level="7" role="heading">Avsnitt 1<br/>Driftsansvarlige</div>'
    '<p class="leddfortsettelse">►<strong>M7</strong> Endret ved forordning.◄</p>'
    '<article class="futureLegalArticle">Ikke i kraft ennå.</article>'
    '<article class="changesToParent">Endret ved lov 1 jan 2026.</article>'
    # skatteloven's shape: a change note quoting a repealed § back as a list.
    # It must keep the blockquote AND the list — see _render_change_note.
    '<article class="changesToParent">Opphevet. Paragrafen lød før oppheving:'
    '<ol class="defaultList"><li>første ledd</li></ol></article>'
    "</section></main></body></html>"
).encode()

_PINNED_DOCUMENT = (4, "24a23f2cdf1b55196731bfac763af20eee963dc2945170753a4ac8c5946f6be7")
_PINNED_SURFACE = (4, "4cc5c3f36506f70183d5a903681c9162f1437a98f353d47e61cfbb9b698a04c3")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_full_document_render_is_pinned_to_renderer_version() -> None:
    digest = _digest(render_full_document(_FIXTURE.read_bytes(), _CONTEXT))
    assert (RENDERER_VERSION, digest) == _PINNED_DOCUMENT, (
        f"Rendered document bytes changed. Bump RENDERER_VERSION and re-pin "
        f"_PINNED_DOCUMENT to: ({RENDERER_VERSION}, {digest!r})"
    )


def test_block_dispatch_surface_is_pinned_to_renderer_version() -> None:
    digest = _digest(render_markdown(_SURFACE))
    assert (RENDERER_VERSION, digest) == _PINNED_SURFACE, (
        f"Rendered block-dispatch surface changed. Bump RENDERER_VERSION and re-pin "
        f"_PINNED_SURFACE to: ({RENDERER_VERSION}, {digest!r})"
    )


def test_surface_actually_exercises_the_table_paths() -> None:
    """Guard the guard: if the surface stops covering both table wrappers, the
    pinned digest silently stops protecting the code path that caused the drift."""
    md = render_markdown(_SURFACE)
    assert "Forbudt for gående" in md  # table under <article class="defaultP">
    assert "Kjørefeltlinje" in md  # table under <article class="legalP">
    assert md.count("| --- |") >= 1  # GFM separator: rendered as a table, not flattened
    assert "###### Avsnitt 1" in md  # <div role="heading"> ARIA heading block (top level)
    assert "###### Vedlegg 1" in md  # heading div nested inside a mixed <article> (PR #126 fix)
    assert "►**M7** Endret ved forordning.◄" in md  # bare <p> with EU markers verbatim
    assert "Ikke i kraft" not in md  # not-in-force elision still applied
    # Non-table block children under a paragraph-class article: the shape the
    # table-only branch left fused until renderer 4. Asserted as the ABSENCE of
    # the fusion, because that is what the corpus audit measures.
    assert "Grunndata:navn" not in md
    assert "InnledningAndre" not in md
    assert "Innledningtterfølgende" not in md
    assert "> 1. første ledd" in md  # change note keeps blockquote AND structure
    assert "oppheving:første" not in md
    # Block content inside a list item: not fused, and indented to the item's
    # content column so the item does not end at the block.
    assert "Polysorbater:Innledende" not in md
    assert "E 432Polysorbat" not in md
    assert "1. Polysorbater:" in md
    assert "\n   | E 432 | Polysorbat 20 |" in md
