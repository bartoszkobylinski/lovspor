"""Tests for lovspor.rendering.markdown_renderer.

The tiny fixture lov-17410217-000.xml is the real Lovdata file for the
Vimpel law from 1741 (NLOD 2.0, attributed in the other rendering-test
module docstring). Most tests here use inline synthetic HTML to isolate
rendering rules for individual element types.
"""

from pathlib import Path

import pytest

from lovspor.errors import ParseError, RenderError
from lovspor.rendering.markdown_renderer import render_markdown

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _wrap(main_body: bytes) -> bytes:
    return b'<!DOCTYPE html><html lang="nb"><body><main>' + main_body + b"</main></body></html>"


def test_render_markdown_on_tiny_vimpel_fixture() -> None:
    xml = (_FIXTURES / "lov-17410217-000.xml").read_bytes()
    md = render_markdown(xml)
    dash = "\N{EN DASH}"
    assert md == (
        "# Forbud paa Vimpel-Føring\n\n"
        "Ingen Skipper, som fører noget i Kongens Riger og Lande hjemmehørende Skib, "
        "enten det maatte føre Canoner eller ikke, ei heller nogen Kjøbmand eller "
        "Rheder deri, maa understaa sig enten i Søen, paa nogen indenlandsk Rhed eller "
        "Havn, eller og paa fremmede Kyster og Havne at føre nogen Vimpel eller Kgl. "
        "Flag og Gjøs med Split i, det være sig i hvad Slags Leilighed det være maatte, "
        f"{dash} {dash} {dash}; [under samme Straf] skal det være forbudt alle Skippere "
        "og Rhedere af noget i Kongens Riger og Lande hjemmehørende Skib dertil at lade "
        "gjøre eller derved at føre nogen Vimpel eller Kgl. Flag og Gjøs, og om de dermed "
        "betræffes, skal de uden Forskaansel ansees, ligesom de dem virkelig havde "
        f"misbrugt. {dash} {dash} {dash}\n"
    )


def test_render_h1_produces_h1_markdown() -> None:
    md = render_markdown(_wrap(b"<h1>Title</h1>"))
    assert md == "# Title\n"


def test_render_h2_produces_h2_markdown() -> None:
    md = render_markdown(_wrap(b"<section><h2>Chapter</h2></section>"))
    assert md == "## Chapter\n"


def test_render_legal_article_header_combines_value_and_title() -> None:
    xml = _wrap(
        b'<article class="legalArticle">'
        b'<h3 class="legalArticleHeader">'
        b'<span class="legalArticleValue">\xc2\xa7 1-1</span>. '
        b'<span class="legalArticleTitle">Virkeomr\xc3\xa5de</span>'
        b"</h3>"
        b"</article>",
    )
    md = render_markdown(xml)
    assert md == "### § 1-1. Virkeområde\n"


def test_render_legal_article_header_value_only_when_title_absent() -> None:
    xml = _wrap(
        b'<article class="legalArticle">'
        b'<h3 class="legalArticleHeader">'
        b'<span class="legalArticleValue">\xc2\xa7 5</span>'
        b"</h3>"
        b"</article>",
    )
    md = render_markdown(xml)
    assert md == "### § 5\n"


def test_render_plain_h3_without_legal_class() -> None:
    md = render_markdown(_wrap(b"<h3>Some heading</h3>"))
    assert md == "### Some heading\n"


@pytest.mark.parametrize("tag", ["h4", "h5", "h6"])
def test_render_lower_heading_levels_as_h3_markdown(tag: str) -> None:
    md = render_markdown(_wrap(f"<{tag}>Sub heading</{tag}>".encode()))
    assert md == "### Sub heading\n"


def test_render_legal_header_class_on_h4_uses_article_header_renderer() -> None:
    xml = _wrap(
        b'<h4 class="legalArticleHeader">'
        b'<span class="legalArticleValue">\xc2\xa7 2</span>. '
        b'<span class="legalArticleTitle">Tittel</span>'
        b"</h4>",
    )
    assert render_markdown(xml) == "### § 2. Tittel\n"


def test_render_legal_p_produces_plain_paragraph() -> None:
    md = render_markdown(_wrap(b'<article class="legalP">Hello world.</article>'))
    assert md == "Hello world.\n"


def test_render_numbered_legal_p_produces_plain_paragraph() -> None:
    md = render_markdown(
        _wrap(b'<article class="numberedLegalP">(2) Second clause.</article>'),
    )
    assert md == "(2) Second clause.\n"


def test_render_list_article_produces_plain_paragraph() -> None:
    md = render_markdown(
        _wrap(b'<article class="listArticle">a) Item.</article>'),
    )
    assert md == "a) Item.\n"


def test_render_changes_to_parent_produces_blockquote() -> None:
    md = render_markdown(
        _wrap(
            b'<article class="changesToParent">Endret ved lov 2020-05-01.</article>',
        ),
    )
    assert md == "> Endret ved lov 2020-05-01.\n"


def test_render_empty_changes_to_parent_and_paragraph_produce_no_output() -> None:
    md = render_markdown(
        _wrap(
            b'<article class="changesToParent"></article><article class="legalP"></article>',
        ),
    )
    assert md == "\n"


def test_render_ol_produces_numbered_markdown_list() -> None:
    md = render_markdown(
        _wrap(b'<ol class="defaultList"><li>first</li><li>second</li></ol>'),
    )
    assert md == "1. first\n2. second\n"


def test_render_ul_produces_bullet_markdown_list() -> None:
    md = render_markdown(_wrap(b"<ul><li>a</li><li>b</li></ul>"))
    assert md == "- a\n- b\n"


def test_render_anchor_with_href_produces_md_link() -> None:
    md = render_markdown(
        _wrap(
            b'<article class="legalP">See <a href="https://x.no/y">\xc2\xa7 2</a>.</article>',
        ),
    )
    assert md == "See [§ 2](https://x.no/y).\n"


def test_render_anchor_without_href_produces_plain_text() -> None:
    md = render_markdown(
        _wrap(b'<article class="legalP">Mention <a>anchor</a>.</article>'),
    )
    assert md == "Mention anchor.\n"


def test_render_span_without_class_does_not_inject_placeholder_text() -> None:
    md = render_markdown(_wrap(b'<article class="legalP"><span>inner</span></article>'))

    assert md == "inner\n"


def test_render_strong_produces_bold_markdown() -> None:
    md = render_markdown(
        _wrap(b'<article class="legalP">Very <strong>important</strong>.</article>'),
    )
    assert md == "Very **important**.\n"


def test_render_preserves_space_between_adjacent_inline_elements() -> None:
    """Regression: the renderer parsed with remove_blank_text=True, which
    stripped the whitespace-only tail between two inline elements — fusing
    the words and producing a broken ``**fet***kursiv*`` emphasis run. The
    space must be preserved."""
    md = render_markdown(
        _wrap(b'<article class="legalP"><strong>fet</strong> <em>kursiv</em> etter</article>'),
    )
    assert md == "**fet** *kursiv* etter\n"


def test_render_em_and_i_produce_italic_markdown() -> None:
    md_em = render_markdown(
        _wrap(b'<article class="legalP">Word <em>italics</em>.</article>'),
    )
    md_i = render_markdown(
        _wrap(b'<article class="legalP">Word <i>italics</i>.</article>'),
    )
    assert md_em == "Word *italics*.\n"
    assert md_i == "Word *italics*.\n"


def test_render_br_produces_newline() -> None:
    md = render_markdown(_wrap(b'<article class="legalP">Line1<br/>Line2</article>'))
    assert "Line1\nLine2" in md


def test_render_unknown_tag_traverses_children() -> None:
    """Unknown wrapper tags must not drop content."""
    md = render_markdown(
        _wrap(
            b'<div><article class="legalP">Still visible.</article></div>',
        ),
    )
    assert md == "Still visible.\n"


def test_render_raises_when_block_level_text_would_be_dropped() -> None:
    """A <p> carrying direct text is an unhandled text-bearing wrapper:
    the block walk emits only child *elements*, so the text would vanish.
    The lost-content guard must raise rather than silently commit an
    incomplete legal document."""
    with pytest.raises(RenderError, match="drop block-level text"):
        render_markdown(_wrap(b"<p>Direct paragraph text in a plain p tag</p>"))


def test_render_raises_when_table_content_would_be_dropped() -> None:
    """Whole tables (fee/rate schedules in forskrifter) currently vanish
    because tr/td are unknown text-bearing wrappers; the guard catches it."""
    with pytest.raises(RenderError):
        render_markdown(
            _wrap(b"<table><tr><td>sats 25 kr</td><td>2020</td></tr></table>"),
        )


def test_render_raises_when_block_level_tail_text_would_be_dropped() -> None:
    """Text after a child element's close tag (its tail) at block level is
    dropped by the walk; only whitespace tails are safe."""
    with pytest.raises(RenderError):
        render_markdown(
            _wrap(b"<section>intro tekst<h2>Overskrift</h2>hale tekst</section>"),
        )


def test_render_guard_tolerates_whitespace_between_block_elements() -> None:
    """Whitespace-only text and tails around block elements (the normal
    Lovdata document shape) carry no words and must NOT trip the guard."""
    md = render_markdown(_wrap(b'\n  <article class="legalP">Tekst</article>\n  '))
    assert md == "Tekst\n"


def test_render_nested_legal_article_walks_children() -> None:
    xml = _wrap(
        b'<article class="legalArticle">'
        b'<h3 class="legalArticleHeader">'
        b'<span class="legalArticleValue">\xc2\xa7 1</span>. '
        b'<span class="legalArticleTitle">Om</span>'
        b"</h3>"
        b'<article class="legalP">First ledd.</article>'
        b'<article class="legalP">Second ledd.</article>'
        b"</article>",
    )
    md = render_markdown(xml)
    assert "### § 1. Om" in md
    assert "First ledd." in md
    assert "Second ledd." in md


def test_render_raises_parse_error_on_malformed_xml() -> None:
    with pytest.raises(ParseError) as exc_info:
        render_markdown(b"<not><closed")
    assert str(exc_info.value).startswith("malformed XML: ")


def test_render_raises_parse_error_when_main_missing() -> None:
    with pytest.raises(ParseError) as exc_info:
        render_markdown(b"<html><body><h1>no main</h1></body></html>")
    assert str(exc_info.value) == "no <main> in document"


def test_render_is_deterministic_across_calls() -> None:
    xml = (_FIXTURES / "lov-17410217-000.xml").read_bytes()
    assert render_markdown(xml) == render_markdown(xml)


def test_render_output_has_exactly_one_trailing_newline() -> None:
    md = render_markdown(_wrap(b"<h1>X</h1>"))
    assert md.endswith("\n")
    assert not md.endswith("\n\n")


def test_render_preserves_norwegian_utf8_characters() -> None:
    md = render_markdown(
        _wrap("<h1>Æ Ø Å æ ø å</h1>".encode()),
    )
    assert md == "# Æ Ø Å æ ø å\n"


def test_render_legal_article_header_fallback_when_no_spans() -> None:
    """Missing both legalArticleValue and legalArticleTitle spans falls back
    to inline text rendering."""
    xml = _wrap(b'<h3 class="legalArticleHeader">Raw heading text</h3>')
    md = render_markdown(xml)
    assert md == "### Raw heading text\n"


def test_render_legal_article_header_ignores_span_tail_in_value() -> None:
    xml = _wrap(
        b'<h3 class="legalArticleHeader">'
        b'<span class="legalArticleValue">\xc2\xa7 1</span> tail '
        b'<span class="legalArticleTitle">Title</span>'
        b"</h3>",
    )
    assert render_markdown(xml) == "### § 1. Title\n"


def test_render_empty_list_yields_nothing() -> None:
    """An <ol> or <ul> without <li> children produces no Markdown output."""
    xml = _wrap(b'<h1>Before</h1><ol class="defaultList"></ol><h1>After</h1>')
    md = render_markdown(xml)
    assert md == "# Before\n\n# After\n"


def test_render_unknown_inline_element_uses_text_content() -> None:
    """An unknown inline tag (e.g. <span>) inside a paragraph renders its
    text content without the wrapping element."""
    md = render_markdown(
        _wrap(
            b'<article class="legalP">Prefix <span class="x">inner</span> suffix.</article>',
        ),
    )
    assert md == "Prefix inner suffix.\n"


def test_render_nested_unordered_list_indents_with_two_spaces() -> None:
    """MEDIUM regression guard: nested <ul> inside <li> must render as an
    indented child, not flatten into the parent item. Codex PR #9
    reproducer."""
    md = render_markdown(
        _wrap(b"<ul><li>outer<ul><li>inner</li></ul></li></ul>"),
    )
    assert md == "- outer\n  - inner\n"


def test_render_mixed_nested_ordered_in_unordered() -> None:
    md = render_markdown(
        _wrap(b"<ul><li>first<ol><li>sub-a</li><li>sub-b</li></ol></li></ul>"),
    )
    assert md == "- first\n  1. sub-a\n  2. sub-b\n"


def test_render_list_item_with_nested_list_and_trailing_text() -> None:
    """Text after a nested </ul> stays on the parent's inline line."""
    md = render_markdown(
        _wrap(b"<ul><li>before<ul><li>x</li></ul> after</li></ul>"),
    )
    assert md == "- before after\n  - x\n"


def test_render_list_item_continues_after_nested_list_child() -> None:
    md = render_markdown(
        _wrap(
            b"<ul><li>before<ul><li>x</li></ul><strong>after</strong></li></ul>",
        ),
    )
    assert md == "- before**after**\n  - x\n"


def test_render_list_item_without_leading_text_uses_child_text_only() -> None:
    md = render_markdown(_wrap(b"<ul><li><strong>bold</strong> tail</li></ul>"))
    assert md == "- **bold** tail\n"


def test_render_three_levels_of_nesting() -> None:
    md = render_markdown(
        _wrap(b"<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>"),
    )
    assert md == "- a\n  - b\n    - c\n"


def test_render_list_item_with_inline_emphasis_and_link() -> None:
    """A <li> may contain inline children (strong, a, br, etc) — the
    inline collector applies the same Markdown mapping as in paragraphs."""
    md = render_markdown(
        _wrap(
            b"<ul>"
            b"<li>plain</li>"
            b"<li>has <strong>bold</strong> text</li>"
            b'<li>see <a href="x">ref</a></li>'
            b"</ul>",
        ),
    )
    assert md == "- plain\n- has **bold** text\n- see [ref](x)\n"


def test_render_inline_without_leading_text_preserves_child_tail() -> None:
    md = render_markdown(_wrap(b'<article class="legalP"><strong>Bold</strong> tail</article>'))
    assert md == "**Bold** tail\n"


def test_render_inline_without_tail_does_not_inject_placeholder_text() -> None:
    md = render_markdown(_wrap(b'<article class="legalP">Prefix <strong>Bold</strong></article>'))

    assert md == "Prefix **Bold**\n"
