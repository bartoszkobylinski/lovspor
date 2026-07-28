"""Tests for lovspor.rendering.markdown_renderer.

The tiny fixture lov-17410217-000.xml is the real Lovdata file for the
Vimpel law from 1741 (NLOD 2.0, attributed in the other rendering-test
module docstring). Most tests here use inline synthetic HTML to isolate
rendering rules for individual element types.
"""

from pathlib import Path

import pytest

from lovspor.errors import ParseError, RenderError
from lovspor.headings import parse_section_heading
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


def test_render_legal_article_header_captures_nested_span_text() -> None:
    # Regression: _span_text must collect the whole span subtree via
    # itertext(), not just span.text (which stops at the first child). Both
    # the value and the title carry text inside a nested inline element, so a
    # mutant reading only .text would drop the "1" and "reach" fragments and
    # produce "### § 1-. Scope and\n" instead.
    xml = _wrap(
        b'<article class="legalArticle">'
        b'<h3 class="legalArticleHeader">'
        b'<span class="legalArticleValue">\xc2\xa7 1-<em>1</em></span>. '
        b'<span class="legalArticleTitle">Scope and <em>reach</em></span>'
        b"</h3>"
        b"</article>",
    )
    md = render_markdown(xml)
    assert md == "### § 1-1. Scope and reach\n"


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


# --------------------------------------------------------------------------
# Fused block boundaries.
#
# A paragraph-class article carrying a block child was flattened by _inline,
# which concatenates descendant text with NO separator: the last word before
# the block ran into the block's first word and produced a word that exists
# in neither. Corpus-wide this fabricated 74,622 words across 2,690 of 5,916
# documents (analysis/llm-infra/10-byte-audit-results.md). No text was lost —
# the damage is that "navn" stops being a findable word, so the passage
# becomes unsearchable and unquotable.
#
# The shapes below are the real ones: 222,751 such articles live in 3,514
# documents, their block children being nested <article> (191,599), <ol>
# (31,044), <ul> (5,904) and <p> (1,801).
# --------------------------------------------------------------------------


def test_render_legal_p_with_nested_list_keeps_the_list_a_list() -> None:
    # kredittopplysningsforskriften § 2: the enumeration of personal-data
    # categories a credit agency may register, rendered as one run-on string.
    md = render_markdown(
        _wrap(
            b'<article class="legalP">Grunndata:'
            b'<ol class="defaultList"><li>navn</li><li>adresse</li></ol>'
            b"</article>",
        ),
    )
    assert md == "Grunndata:\n\n1. navn\n2. adresse\n"


def test_render_legal_p_with_nested_list_fabricates_no_word() -> None:
    md = render_markdown(
        _wrap(
            b'<article class="legalP">Grunndata:'
            b'<ol class="defaultList"><li>navn</li></ol>'
            b"</article>",
        ),
    )
    # The defect signature: text before the block fused to the block's first
    # word. "navn" must survive as a word in its own right.
    assert "Grunndata:navn" not in md


def test_render_numbered_legal_p_with_nested_articles_keeps_them_apart() -> None:
    md = render_markdown(
        _wrap(
            b'<article class="numberedLegalP">(1) Innledning'
            b'<article class="legalP">Andre ledd.</article>'
            b"</article>",
        ),
    )
    assert md == "(1) Innledning\n\nAndre ledd.\n"
    assert "InnledningAndre" not in md


def test_render_list_article_with_nested_paragraph_block_keeps_the_break() -> None:
    md = render_markdown(
        _wrap(
            b'<article class="listArticle">a) Innledning<p>Etterfolgende avsnitt.</p></article>',
        ),
    )
    assert md == "a) Innledning\n\nEtterfolgende avsnitt.\n"


def test_render_legal_p_keeps_text_that_follows_the_block_child() -> None:
    md = render_markdown(
        _wrap(
            b'<article class="legalP">For:<ul><li>ett</li></ul>etterpa</article>',
        ),
    )
    # The tail after a block child is sibling text, not part of the block.
    assert md == "For:\n\n- ett\n\netterpa\n"


def test_render_inline_only_paragraph_classes_are_unchanged() -> None:
    # The block-child route must not capture the ordinary case: an article
    # whose children are all inline still renders as one paragraph.
    md = render_markdown(
        _wrap(
            b'<article class="legalP">Se <a href="lov/1999-03-26-14">loven</a> her.</article>',
        ),
    )
    assert md == "Se [loven](lov/1999-03-26-14) her.\n"


@pytest.mark.parametrize(
    ("tag", "marker"),
    [("h1", "#"), ("h2", "##")],
)
def test_a_top_level_heading_inside_an_article_stays_a_heading(tag: str, marker: str) -> None:
    # h1/h2 are in _BLOCK_TAGS, so they leave the inline accumulator like any
    # other block child. Nothing exercised them nested until a mutation run
    # removed each from the set and no test noticed: the article would render
    # "Foer# Tittel" on one line, fusing the text into the heading mark.
    md = render_markdown(
        _wrap(
            f'<article class="legalP">Foer<{tag}>Tittel</{tag}>etter</article>'.encode(),
        ),
    )

    assert md == f"Foer\n\n{marker} Tittel\n\netter\n"


def test_render_footnotes_footer_keeps_the_notes_apart() -> None:
    # The corpus's most common wrapper: <footer class="footnotes"> closing a
    # legalArticle, 2,063 of them in 772 documents. As an inline child the whole
    # footer collapsed into the article's last paragraph, fusing each note into
    # the next ("For dieselmotorer.2 Unntak:").
    md = render_markdown(
        _wrap(
            b'<article class="legalArticle">'
            b'<article class="legalP">Brodtekst.</article>'
            b'<footer class="footnotes">'
            b'<article class="footnote"><span class="footnoteLabel">1</span>'
            b"For dieselmotorer.</article>"
            b'<article class="footnote"><span class="footnoteLabel">2</span>'
            b"Unntak: kjoretoy.</article>"
            b"</footer></article>",
        ),
    )
    assert md == "Brodtekst.\n\n1For dieselmotorer.\n\n2Unntak: kjoretoy.\n"
    assert "dieselmotorer.2" not in md


def test_render_wrapper_div_keeps_the_quoted_convention_structured() -> None:
    # <div class="indent"> around a quoted convention text: 144 sites in ~50
    # documents. Flattened, an entire annex — headings, articles, list items —
    # came out as one paragraph ("...gjelder, ellerdersom eieren...").
    md = render_markdown(
        _wrap(
            b'<article class="legalArticle">'
            b'<article class="legalP">Folgende tekst legges til:</article>'
            b'<div class="indent">'
            b'<article class="defaultP"><strong>Art 4bis.</strong>'
            b'<ol class="defaultList" type="a">'
            b'<li><article class="legalP">forsikringens art og gyldighetstid</article></li>'
            b'<li><article class="legalP">navn og hovedforretningssted</article></li>'
            b"</ol></article></div></article>",
        ),
    )
    assert md == (
        "Folgende tekst legges til:\n\n**Art 4bis.**\n\n"
        "1. forsikringens art og gyldighetstid\n"
        "2. navn og hovedforretningssted\n"
    )
    assert "gyldighetstidnavn" not in md


def test_render_stray_list_item_outside_a_list_stays_a_block() -> None:
    # 171 sites in 27 documents: an <li> parented straight by an article,
    # with no <ol>/<ul> between. It is not reached by the list walk, so it used
    # to flatten into the article's inline run.
    md = render_markdown(
        _wrap(
            b'<article class="change">Endringer:'
            b"<li>forste punkt</li><li>andre punkt</li></article>",
        ),
    )
    assert md == "Endringer:\n\nforste punkt\n\nandre punkt\n"
    assert "punktandre" not in md


def test_render_figure_caption_is_not_fused_into_the_running_text() -> None:
    md = render_markdown(
        _wrap(
            b'<article class="legalP">Rundt 9'
            b"<figure><figcaption>Figur 3: Stalror</figcaption></figure></article>",
        ),
    )
    assert md == "Rundt 9\n\nFigur 3: Stalror\n"
    assert "9Figur" not in md


def test_render_blockquote_wrapper_keeps_its_articles_apart() -> None:
    md = render_markdown(
        _wrap(
            b'<article class="legalArticle"><blockquote>'
            b'<article class="legalP">Forste avsnitt.</article>'
            b'<article class="legalP">Andre avsnitt.</article>'
            b"</blockquote></article>",
        ),
    )
    assert md == "Forste avsnitt.\n\nAndre avsnitt.\n"
    assert "avsnitt.Andre" not in md


def test_render_wrapper_carrying_its_own_text_keeps_that_text() -> None:
    # Wrappers route through _render_mixed_article, not the child-only walk, so
    # text sitting directly in the wrapper survives instead of tripping
    # _assert_no_dropped_text.
    md = render_markdown(
        _wrap(b'<div class="box">Innledning<article class="legalP">Avsnitt.</article>slutt</div>'),
    )
    assert md == "Innledning\n\nAvsnitt.\n\nslutt\n"


def test_a_div_is_a_block_child_whatever_its_role() -> None:
    # _is_block_element decides which children leave the inline accumulator, so
    # since renderer 4 it governs both _render_article and _list_item_lines. A
    # role="heading" div becomes an ATX heading; a plain wrapper div is still a
    # block, and carries its text directly, so it renders through
    # _render_mixed_article rather than the child-only walk (which would trip
    # _assert_no_dropped_text on that text).
    heading = render_markdown(
        _wrap(
            b'<article class="legalP">Foer'
            b'<div role="heading" aria-level="4">Tittel</div></article>',
        ),
    )
    assert heading == "Foer\n\n#### Tittel\n"

    plain = render_markdown(
        _wrap(b'<article class="legalP">Foer<div>inni</div>etter</article>'),
    )
    assert plain == "Foer\n\ninni\n\netter\n"


def test_render_empty_list_item_emits_a_bare_marker() -> None:
    md = render_markdown(_wrap(b"<ul><li></li><li>x</li></ul>"))
    # The marker is emitted without its trailing space: a line of "- " alone
    # is trailing whitespace that the corpus would carry forever.
    assert md == "-\n- x\n"


def test_render_change_note_whose_only_block_renders_empty_produces_nothing() -> None:
    # An empty <ol> renders to "", so the note has no content to quote. It must
    # emit nothing rather than a stray blockquote marker.
    md = render_markdown(_wrap(b'<article class="changesToParent"><ol></ol></article>'))
    assert md == "\n"


def test_render_change_note_with_a_block_child_stays_a_blockquote() -> None:
    # skatteloven nl-19990326-014 is the corpus's only change note carrying a
    # block child, and what it carries is the full text of a repealed § quoted
    # back under "Paragrafen lod for oppheving:". It must keep BOTH its
    # change-note marking and the structure of the quoted provision.
    md = render_markdown(
        _wrap(
            b'<article class="changesToParent">Opphevet. Paragrafen lod for oppheving:'
            b'<ol class="defaultList"><li>forste ledd</li><li>andre ledd</li></ol>'
            b"</article>",
        ),
    )
    assert md == (
        "> Opphevet. Paragrafen lod for oppheving:\n>\n> 1. forste ledd\n> 2. andre ledd\n"
    )
    assert "opphevingforste" not in md


def test_render_empty_changes_to_parent_and_paragraph_produce_no_output() -> None:
    md = render_markdown(
        _wrap(
            b'<article class="changesToParent"></article><article class="legalP"></article>',
        ),
    )
    assert md == "\n"


# --------------------------------------------------------------------------
# The same fusion one level down: a <li> whose content is an <article>.
#
# _inline_of_li excluded nested <ul>/<ol> from the inline run but nothing else,
# so an <article> inside a list item — 184,344 items across 3,478 documents,
# the whole residue left after the article-level fix — was flattened, taking
# any table inside it along.
# --------------------------------------------------------------------------


def test_render_list_item_wrapping_an_article_keeps_the_text_separate() -> None:
    md = render_markdown(
        _wrap(
            b'<ol class="defaultList"><li data-name="(1)" value="1">'
            b'<article class="listArticle"><article class="legalP">Skattyter som '
            b"har hatt kostnader.</article></article></li></ol>",
        ),
    )
    assert md == "1. Skattyter som har hatt kostnader.\n"


def test_render_list_item_with_leading_text_and_an_article_does_not_fuse() -> None:
    md = render_markdown(
        _wrap(
            b'<ol><li>Innledning:<article class="legalP">Andre avsnitt.</article></li></ol>',
        ),
    )
    # Continuation blocks sit at the item's content column, so the item still
    # parses as one list item rather than ending the list.
    assert md == "1. Innledning:\n\n   Andre avsnitt.\n"
    assert "Innledning:Andre" not in md


def test_render_bullet_item_indents_continuation_to_its_own_marker_width() -> None:
    md = render_markdown(
        _wrap(b'<ul><li>Forste<article class="legalP">Andre.</article></li></ul>'),
    )
    assert md == "- Forste\n\n  Andre.\n"


def test_render_list_item_wrapping_a_table_keeps_the_cells_apart() -> None:
    # sf-20110606-0668 (food additives): an E-number table inside a list item.
    # It fused into "E 432Polyoksyetylensorbitanmonolaurat".
    md = render_markdown(
        _wrap(
            b"<ol><li>Polysorbater:"
            b'<article class="legalP"><table><tbody>'
            b"<tr><td>E 432</td><td>Polyoksyetylensorbitanmonolaurat</td></tr>"
            b"</tbody></table></article></li></ol>",
        ),
    )
    assert "E 432Polyoksyetylensorbitanmonolaurat" not in md
    assert "| E 432 | Polyoksyetylensorbitanmonolaurat |" in md


def test_render_list_item_with_only_inline_content_is_unchanged() -> None:
    md = render_markdown(_wrap(b"<ol><li>bare tekst</li></ol>"))
    assert md == "1. bare tekst\n"


def test_render_nested_sublists_still_indent_under_their_parent_item() -> None:
    md = render_markdown(
        _wrap(b"<ul><li>ytre<ul><li>indre</li></ul></li></ul>"),
    )
    assert md == "- ytre\n  - indre\n"


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


def test_render_escapes_leading_bullet_in_paragraph() -> None:
    """A legalP whose text begins with '- ' must not be reparsed as a
    Markdown bullet — the dash is escaped."""
    md = render_markdown(_wrap(b'<article class="legalP">- 500 kr per dag</article>'))
    assert md == "\\- 500 kr per dag\n"


def test_render_escapes_leading_ordered_list_marker_in_paragraph() -> None:
    md = render_markdown(_wrap(b'<article class="legalP">1. punkt</article>'))
    assert md == "1\\. punkt\n"


def test_render_escapes_leading_heading_and_quote_and_table_tokens() -> None:
    assert (
        render_markdown(_wrap(b'<article class="legalP"># ikke overskrift</article>'))
        == "\\# ikke overskrift\n"
    )
    assert (
        render_markdown(_wrap(b'<article class="legalP">> ikke sitat</article>'))
        == "\\> ikke sitat\n"
    )


def test_render_escapes_leading_token_inside_blockquote() -> None:
    md = render_markdown(_wrap(b'<article class="changesToParent">- endret</article>'))
    assert md == "> \\- endret\n"


def test_render_escapes_inline_asterisks_in_text() -> None:
    md = render_markdown(_wrap(b'<article class="legalP">2*2 og 3*3</article>'))
    assert md == "2\\*2 og 3\\*3\n"


def test_render_does_not_escape_hyphen_without_following_space() -> None:
    """A leading hyphen not followed by whitespace (e.g. a negative amount)
    is not a Markdown bullet and must stay verbatim."""
    md = render_markdown(_wrap(b'<article class="legalP">-500 kr i fradrag</article>'))
    assert md == "-500 kr i fradrag\n"


def test_render_escapes_leading_token_on_second_line_after_br_in_paragraph() -> None:
    """Markdown block parsing is line-oriented: a <br/> splits the paragraph
    into lines, and a line starting '- ' would reparse as a bullet even
    though it is not the first character of the block."""
    md = render_markdown(_wrap(b'<article class="legalP">Line<br/>- 500 kr</article>'))
    assert md == "Line\n\\- 500 kr\n"


def test_render_escapes_leading_heading_on_second_line_after_br() -> None:
    md = render_markdown(_wrap(b'<article class="legalP">Line<br/># heading</article>'))
    assert md == "Line\n\\# heading\n"


def test_render_escapes_second_line_token_inside_blockquote_after_br() -> None:
    """A '- ' on line 2 of a changesToParent would otherwise leave the
    blockquote and become a list; escaped, it stays lazy-continuation text."""
    md = render_markdown(_wrap(b'<article class="changesToParent">Line<br/>- endret</article>'))
    assert md == "> Line\n\\- endret\n"


def test_render_escapes_leading_token_on_second_line_in_list_item() -> None:
    md = render_markdown(_wrap(b"<ul><li>item<br/>- sub</li></ul>"))
    # Two guards now, not one: the '- ' is still escaped so it cannot reparse
    # as a bullet, and since renderer 4 the continuation also sits at the
    # item's content column instead of column 0, so it belongs to the item
    # outright rather than by lazy continuation.
    assert md == "- item\n  \\- sub\n"


def test_render_encodes_parens_and_spaces_in_href() -> None:
    md = render_markdown(
        _wrap(b'<article class="legalP">See <a href="https://x.no/a(b) c">ref</a>.</article>'),
    )
    assert md == "See [ref](https://x.no/a%28b%29%20c).\n"


def test_render_unknown_tag_traverses_children() -> None:
    """Unknown wrapper tags must not drop content."""
    md = render_markdown(
        _wrap(
            b'<div><article class="legalP">Still visible.</article></div>',
        ),
    )
    assert md == "Still visible.\n"


def test_render_bare_p_renders_as_a_paragraph() -> None:
    """Consolidated EU regulations embed running text in bare <p> blocks with
    no <article> wrapper. They render as paragraphs; the block walk used to
    trip the lost-content guard and skip the whole document (cluster P)."""
    md = render_markdown(_wrap(b"<p>Direct paragraph text in a plain p tag</p>"))
    assert md == "Direct paragraph text in a plain p tag\n"


def test_render_leddfortsettelse_p_keeps_eu_consolidation_markers() -> None:
    """The EU consolidation arrows (start/end of an amended passage) and the
    M-labels are meaningful source markup — kept verbatim rather than
    reinterpreting Lovdata's editorial layer."""
    md = render_markdown(
        _wrap(
            '<p class="leddfortsettelse">►<strong>M7</strong> Endret ved forordning.◄</p>'.encode(),
        ),
    )
    assert md == "►**M7** Endret ved forordning.◄\n"


def test_render_bare_p_preserves_trailing_bracket() -> None:
    md = render_markdown(_wrap(b"<p>VEDTATT DENNE FORORDNING:]</p>"))
    assert md == "VEDTATT DENNE FORORDNING:]\n"


def test_render_table_renders_as_gfm_not_dropped() -> None:
    """Whole tables (fee/rate schedules in forskrifter) used to flatten to
    mush or trip the lost-content guard; they now render as a GFM table with
    a blank header row when the source has no <thead>."""
    md = render_markdown(
        _wrap(b"<table><tr><td>sats 25 kr</td><td>2020</td></tr></table>"),
    )
    assert md == "|  |  |\n| --- | --- |\n| sats 25 kr | 2020 |\n"


def test_render_table_with_thead_as_gfm() -> None:
    xml = _wrap(
        b'<article class="defaultP"><table>'
        b"<thead><tr><th>A</th><th>B</th></tr></thead>"
        b"<tbody><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></tbody>"
        b"</table></article>",
    )
    md = render_markdown(xml)
    assert md == "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"


def test_render_headerless_table_gets_blank_header() -> None:
    xml = _wrap(
        b'<article class="defaultP"><table><tbody>'
        b"<tr><td>x</td><td>y</td></tr></tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == "|  |  |\n| --- | --- |\n| x | y |\n"


def test_render_table_caption_becomes_lead_in_paragraph() -> None:
    xml = _wrap(
        b'<article class="defaultP"><table>'
        b"<caption>Rates:</caption>"
        b"<tbody><tr><td>a</td></tr></tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == "Rates:\n\n|  |\n| --- |\n| a |\n"


def test_render_table_expands_colspan_into_pad_cells() -> None:
    """A full-width colspan banner row (the real Lovdata certificate-form
    pattern) keeps the grid rectangular: the cell content goes in the first
    column and the spanned columns become empty pads."""
    xml = _wrap(
        b'<article class="defaultP"><table><tbody>'
        b'<tr><td colspan="3">Banner</td></tr>'
        b"<tr><td>a</td><td>b</td><td>c</td></tr>"
        b"</tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == ("|  |  |  |\n| --- | --- | --- |\n| Banner |  |  |\n| a | b | c |\n")


def test_render_table_colspan_pads_mid_row_not_just_at_end() -> None:
    """A colspan cell followed by another cell in the same row: the pad must
    land BETWEEN them, so a later cell stays in its true column. The banner-row
    test above can't catch this — its pad comes from end padding, so ignoring
    colspan (or an off-by-one pad count) would render identically. Here it does
    not: dropping the colspan puts ``B`` in column 2 instead of column 3."""
    xml = _wrap(
        b'<article class="defaultP"><table><tbody>'
        b'<tr><td colspan="2">A</td><td>B</td></tr>'
        b"</tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == "|  |  |  |\n| --- | --- | --- |\n| A |  | B |\n"


def test_render_mixed_article_inline_child_with_no_tail_before_table() -> None:
    """An inline child immediately followed by the table (no tail text) must
    not inject anything for the absent tail — pins the ``child.tail or ""``."""
    xml = _wrap(
        b'<article class="legalP">See <a href="https://x.no">rule</a>'
        b"<table><tbody><tr><td>a</td></tr></tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == "See [rule](https://x.no)\n\n|  |\n| --- |\n| a |\n"


def test_render_table_empty_caption_emits_no_lead_in() -> None:
    """An empty ``<caption>`` produces no lead-in paragraph — pins the
    ``if text else ""`` empty branch in the caption renderer."""
    xml = _wrap(
        b'<article class="defaultP"><table><caption></caption>'
        b"<tbody><tr><td>a</td></tr></tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == "|  |\n| --- |\n| a |\n"


def test_render_table_ragged_row_gets_blank_trailing_cells() -> None:
    """A row shorter than the widest row is end-padded with EMPTY cells, not
    placeholder text — pins the ``[""] * (width - len(cells))`` padding."""
    xml = _wrap(
        b'<article class="defaultP"><table><tbody>'
        b"<tr><td>a</td><td>b</td></tr><tr><td>c</td></tr>"
        b"</tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == "|  |  |\n| --- | --- |\n| a | b |\n| c |  |\n"


def test_render_table_zero_colspan_normalizes_to_one() -> None:
    """``colspan="0"`` clamps to 1 (like the malformed fallback), so the row
    stays two columns — pins the ``max(1, int(raw))`` lower bound."""
    xml = _wrap(
        b'<article class="defaultP"><table><tbody>'
        b'<tr><td colspan="0">x</td><td>y</td></tr>'
        b"</tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == "|  |  |\n| --- | --- |\n| x | y |\n"


def test_render_table_cell_inline_content_br_link_italic_and_pipe() -> None:
    xml = _wrap(
        b'<article class="defaultP"><table><tbody><tr>'
        b"<td>L1<br/>L2</td>"
        b'<td>See <a href="https://x.no">link</a></td>'
        b"<td>a<i>b</i></td>"
        b"<td>x|y</td>"
        b"</tr></tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == (
        "|  |  |  |  |\n"
        "| --- | --- | --- | --- |\n"
        "| L1<br>L2 | See [link](https://x.no) | a*b* | x\\|y |\n"
    )


def test_render_article_mixing_lead_in_text_and_table() -> None:
    """A legalP article with intro text before a table keeps BOTH — the
    text as a paragraph and the table as a GFM block — instead of flattening
    every cell value into one run."""
    xml = _wrap(
        b'<article class="legalP">Satser:<table><tbody>'
        b"<tr><td>a</td><td>b</td></tr></tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == "Satser:\n\n|  |  |\n| --- | --- |\n| a | b |\n"


def test_render_table_directly_under_section_without_article() -> None:
    md = render_markdown(
        _wrap(b"<section><table><tbody><tr><td>a</td><td>b</td></tr></tbody></table></section>"),
    )
    assert md == "|  |  |\n| --- | --- |\n| a | b |\n"


def test_render_table_is_deterministic() -> None:
    xml = _wrap(
        b'<article class="defaultP"><table><thead><tr><th>A</th><th>B</th></tr></thead>'
        b"<tbody><tr><td>1</td><td>2</td></tr></tbody></table></article>",
    )
    assert render_markdown(xml) == render_markdown(xml)


def test_render_mixed_article_with_inline_child_before_table() -> None:
    xml = _wrap(
        b'<article class="legalP">See <a href="https://x.no">rule</a> below:'
        b"<table><tbody><tr><td>a</td></tr></tbody></table></article>",
    )
    md = render_markdown(xml)
    assert md == "See [rule](https://x.no) below:\n\n|  |\n| --- |\n| a |\n"


def test_render_article_with_legal_header_and_table_keeps_both() -> None:
    """Regression (Codex): an article holding BOTH a legalArticleHeader and a
    table must render the ### heading as a heading — the mixed-content path
    routes block children through _render_element, not the inline accumulator,
    so the header is not flattened into text."""
    xml = _wrap(
        b'<article class="legalArticle">'
        b'<h3 class="legalArticleHeader">'
        b'<span class="legalArticleValue">\xc2\xa7 3</span>. '
        b'<span class="legalArticleTitle">Satser</span>'
        b"</h3>"
        b"<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>"
        b"</article>",
    )
    md = render_markdown(xml)
    assert md == "### § 3. Satser\n\n|  |  |\n| --- | --- |\n| a | b |\n"


def test_render_empty_table_produces_no_table() -> None:
    md = render_markdown(_wrap(b'<article class="defaultP"><table></table></article>'))
    assert md == "\n"


def test_render_table_treats_malformed_colspan_as_one() -> None:
    xml = _wrap(
        b'<article class="defaultP"><table><tbody>'
        b'<tr><td colspan="abc">x</td><td>y</td></tr></tbody></table></article>',
    )
    md = render_markdown(xml)
    assert md == "|  |  |\n| --- | --- |\n| x | y |\n"


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


def test_render_footnote_article_renders_reference_as_paragraph() -> None:
    """A ``<article class="footnote">`` (label span + "Jf." reference text +
    links to EØS/EU) is the dominant Lovdata structure that used to trip the
    lost-content guard. It now renders as a paragraph so the reference text and
    links are preserved instead of aborting the whole sync."""
    xml = _wrap(
        b'<footer class="footnotes">'
        b'<article class="footnote" data-name="1">'
        b'<span class="footnoteLabel">1</span> Jf. '
        b'<a href="avtale/avt-1992-05-02-1-v19">E\xc3\x98S-avtalen vedlegg XIX</a>'
        b" nr. 7a."
        b"</article>"
        b"</footer>",
    )
    md = render_markdown(xml)
    assert md == "1 Jf. [EØS-avtalen vedlegg XIX](avtale/avt-1992-05-02-1-v19) nr. 7a.\n"


def test_render_non_paragraph_article_with_mixed_text_and_link() -> None:
    """A non-paragraph article (e.g. ``defaultP``) carrying inline text next to
    a link renders as a paragraph rather than dropping the text."""
    xml = _wrap(
        b'<article class="defaultP">Se '
        b'<a href="lov/2016-05-27-14">skatteforvaltningsloven</a> for reglene.</article>',
    )
    md = render_markdown(xml)
    assert md == "Se [skatteforvaltningsloven](lov/2016-05-27-14) for reglene.\n"


def test_render_elides_futuretitle_instead_of_skipping_the_document() -> None:
    """Lovdata's ``futuretitle`` is a chapter heading that is enacted but NOT yet
    in force. The corpus tracks law as *currently* in force, so the proposed title
    must never reach the body — but refusing the whole document also loses the
    surrounding operative text. Elide the future subtree, render the rest."""
    md = render_markdown(
        _wrap(
            b'<section class="section"><span class="futuretitle">Kapittel 6A</span>'
            b'<article class="legalP">I kraft i dag.</article></section>',
        ),
    )
    assert md == "I kraft i dag.\n"
    assert "Kapittel 6A" not in md


def test_render_elides_future_legal_article_subtree() -> None:
    """``article.futureLegalArticle`` carries a proposed § heading and body. It
    used to render straight through ``_render_mixed_article``, leaking not-in-force
    text into the corpus whenever the document happened to render at all."""
    body = (
        '<article class="legalP">[§ 4-27] skal lyde:</article>'
        '<article class="futureLegalArticle">'
        '<span class="futureLegalArticleHeader">'
        '<span class="legalArticleValue">§ 4-27</span>'
        '<span class="legalArticleTitle">Plasseringsalternativer</span>'
        "</span>"
        "</article>"
    ).encode()
    md = render_markdown(_wrap(body))
    assert md == "[§ 4-27] skal lyde:\n"
    assert "Plasseringsalternativer" not in md


def test_render_elides_future_legal_article_subtree_with_body_text() -> None:
    """Mutation guard: eliding only ``futureLegalArticleHeader`` is not enough.
    The enclosing ``article.futureLegalArticle`` may also carry direct body
    paragraphs, and those must never leak into current-law output."""
    body = (
        '<article class="legalP">[§ 4-27] skal lyde:</article>'
        '<article class="futureLegalArticle">'
        '<article class="legalP">Ny regel som ikke er i kraft ennå.</article>'
        "</article>"
        '<article class="legalP">Videre gjeldende tekst.</article>'
    ).encode()
    md = render_markdown(_wrap(body))
    assert md == "[§ 4-27] skal lyde:\n\nVidere gjeldende tekst.\n"
    assert "ikke er i kraft ennå" not in md


def test_render_elides_standalone_future_legal_article_header() -> None:
    """Mutation guard: ``futureLegalArticleHeader`` can appear on its own, so it
    must be elided even when no enclosing ``futureLegalArticle`` wrapper is
    present."""
    body = (
        '<span class="futureLegalArticleHeader">'
        '<span class="legalArticleValue">§ 8 a.</span>'
        '<span class="legalArticleTitle">Aldersgrenser</span>'
        "</span>"
        '<article class="legalP">Gjeldende tekst.</article>'
    ).encode()
    md = render_markdown(_wrap(body))
    assert md == "Gjeldende tekst.\n"
    assert "Aldersgrenser" not in md


def test_render_eliding_future_block_preserves_its_tail_text() -> None:
    """Removing an element must not take its ``.tail`` with it — that text
    belongs to the parent and would otherwise be dropped silently."""
    md = render_markdown(
        _wrap(
            b'<article class="defaultP">Before'
            b'<span class="futuretitle">Kapittel 6A</span> after</article>',
        ),
    )
    assert md == "Before after\n"
    assert "Kapittel 6A" not in md


def test_render_block_level_span_without_future_class_still_raises() -> None:
    """The lost-content guard must stay armed for every wrapper the renderer does
    not understand. Only the three known ``future*`` classes are elided; anything
    else carrying block-level text still fails loudly rather than vanishing."""
    with pytest.raises(RenderError, match="drop block-level text"):
        render_markdown(
            _wrap(
                b'<section class="section"><span class="mysteryClass">Kapittel 6A</span></section>',
            ),
        )


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


# ---------------------------------------------------------------------------
# <div role="heading"> ARIA heading blocks. EU-regulation annexes (animal
# health, food safety, sanctions) lay out section titles as generic heading
# divs instead of the legalArticle schema; the block walk had no branch for
# them, so the lost-content guard froze ~29 forskrifter out of the corpus
# (analysis/deep-35-forskrifter.md, cluster H).
# ---------------------------------------------------------------------------


def test_render_heading_div_renders_as_atx_heading() -> None:
    md = render_markdown(_wrap(b'<div role="heading" aria-level="7">Avsnitt 1</div>'))
    assert md == "###### Avsnitt 1\n"


def test_render_heading_div_clamps_aria_level_to_six_markdown_levels() -> None:
    """aria-level in these annexes runs 5-7; Markdown tops out at ######."""
    md = render_markdown(_wrap(b'<div role="heading" aria-level="5">Underavsnitt 1</div>'))
    assert md == "##### Underavsnitt 1\n"


def test_render_heading_div_defaults_to_deepest_level_without_aria_level() -> None:
    md = render_markdown(_wrap(b'<div role="heading">KAPITTEL 1</div>'))
    assert md == "###### KAPITTEL 1\n"


def test_render_heading_div_tolerates_non_numeric_aria_level() -> None:
    md = render_markdown(_wrap(b'<div role="heading" aria-level="x">Tittel</div>'))
    assert md == "###### Tittel\n"


def test_render_heading_div_splits_br_subtitle_into_following_paragraph() -> None:
    """A heading cannot span lines: the <br/> continuation (a descriptive
    subtitle after the numbered label) renders as a following paragraph."""
    md = render_markdown(
        _wrap(
            b'<div role="heading" aria-level="7">'
            b"Avsnitt 1<br/>Driftsansvarlige og fagpersoner</div>",
        ),
    )
    assert md == "###### Avsnitt 1\n\nDriftsansvarlige og fagpersoner\n"


def test_render_heading_div_renders_inline_children() -> None:
    md = render_markdown(
        _wrap(
            b'<div role="heading" aria-level="6">'
            b'Se <strong>vedlegg</strong> <a href="x">her</a></div>',
        ),
    )
    assert md == "###### Se **vedlegg** [her](x)\n"


def test_render_plain_div_without_heading_role_still_traverses_children() -> None:
    """Only role="heading" divs become headings; a plain wrapper div keeps
    its walk-children behaviour and must not be promoted to a heading."""
    md = render_markdown(
        _wrap(b'<div><article class="legalP">Br\xc3\xb8dtekst.</article></div>'),
    )
    assert md == "Brødtekst.\n"


def test_render_heading_div_inside_mixed_article_preserves_heading_block() -> None:
    """Regression (Codex, PR #126): a heading div nested inside an <article>
    wrapper must still render as a heading, not be flattened through the inline
    path. _render_mixed_article routes it through _render_element via
    _is_block_element, not _inline_for_child."""
    md = render_markdown(
        _wrap(
            b'<article class="defaultP">'
            b'<div role="heading" aria-level="7">Avsnitt 1<br/>Driftsansvarlige</div>'
            b"</article>",
        ),
    )
    assert md == "###### Avsnitt 1\n\nDriftsansvarlige\n"


def test_render_heading_div_before_table_in_mixed_article() -> None:
    """A genuinely-mixed article (heading div + table): the heading renders as
    a heading block ahead of the GFM table, neither flattened nor dropped."""
    md = render_markdown(
        _wrap(
            b'<article class="defaultP">'
            b'<div role="heading" aria-level="6">Tabell 1</div>'
            b"<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>"
            b"</article>",
        ),
    )
    assert md == "###### Tabell 1\n\n|  |  |\n| --- | --- |\n| a | b |\n"


def test_render_bare_p_inside_mixed_article_renders_as_block() -> None:
    """A bare <p> nested in a mixed article renders as its own paragraph block,
    not inline-merged into surrounding text."""
    md = render_markdown(
        _wrap(
            b'<article class="defaultP">'
            b"<p>Plain lead paragraph.</p>"
            b"<table><tbody><tr><td>x</td></tr></tbody></table>"
            b"</article>",
        ),
    )
    assert md == "Plain lead paragraph.\n\n|  |\n| --- |\n| x |\n"


def test_a_footnote_marker_does_not_fuse_into_the_word_it_annotates() -> None:
    # The marker sits flush against its word, so rendering its bare text made
    # "Amtskasserere" into "Amtskasserere1" — 7,940 markers in 664 documents.
    # Measured consequence on the live corpus before renderer 5: verify_quote
    # rejected the faithful quote of § 8 of the 1894 Embedsværk act and
    # accepted only the variant carrying the digit.
    md = render_markdown(
        _wrap(
            b'<article class="legalP">Amtskasserere'
            b'<sup class="footnotereference" data-footnotereferencevalue="1">1</sup>'
            b" og Politimestre.</article>",
        ),
    )

    assert md == "Amtskasserere[^1] og Politimestre.\n"
    assert "Amtskasserere1" not in md


def test_a_plain_sup_still_runs_into_its_word() -> None:
    # The mirror image, and the reason the footnote case is decided by class
    # rather than by tag: "km2" is ONE word, and delimiting it would be the
    # same defect pointing the other way.
    md = render_markdown(
        _wrap(b'<article class="legalP">avgift per km<sup>2</sup> for CO<sub>2</sub>.</article>'),
    )

    assert md == "avgift per km2 for CO2.\n"


def test_a_footnote_marker_keeps_its_label_verbatim() -> None:
    # Labels are not always digits, and are not always one character.
    md = render_markdown(
        _wrap(
            b'<article class="legalP">tekst'
            b'<sup class="footnotereference">12</sup> mer'
            b'<sup class="footnotereference">a</sup></article>',
        ),
    )

    assert md == "tekst[^12] mer[^a]\n"


def test_a_footnote_marker_inside_a_heading_is_delimited_too() -> None:
    # h1/h2 go through the full inline walk rather than
    # _render_legal_article_header, so their markers reach the corpus — 33 of
    # them — and fused there exactly as body text did.
    md = render_markdown(
        _wrap(
            b'<h2 class="legalArticleHeader">Amtskasserere'
            b'<sup class="footnotereference">1</sup></h2>',
        ),
    )

    assert md == "## Amtskasserere[^1]\n"


def test_paragraph_class_articles_render_as_plain_paragraphs() -> None:
    # Renderer 5 dropped the branch that special-cased these classes: with no
    # block child among them, _render_mixed_article IS the inline paragraph
    # render. Removing it changed 0 of 5,917 rendered documents, and this pins
    # that the ordinary shape still comes out as one paragraph.
    for cls in (b"legalP", b"numberedLegalP", b"listArticle"):
        md = render_markdown(
            _wrap(
                b'<article class="'
                + cls
                + b'">Se <a href="lov/1999-03-26-14">loven</a> her.</article>'
            ),
        )

        assert md == "Se [loven](lov/1999-03-26-14) her.\n", cls


def test_a_footnote_marker_inside_a_heading_title_span_is_delimited() -> None:
    # trygdeavtalelova § 19 carries its marker INSIDE the title span, where
    # _span_text reads the subtree directly. Left raw it fused there while the
    # same marker in body text rendered as "[^1]" — one source, two renderings,
    # in a single document.
    md = render_markdown(
        _wrap(
            b'<h3 class="legalArticleHeader">'
            b'<span class="legalArticleValue">\xc2\xa7 19</span>. '
            b'<span class="legalArticleTitle">samh\xc3\xb8ve med trygdeavtalelova'
            b'<sup class="footnotereference">1</sup></span></h3>',
        ),
    )

    assert md == "### § 19. samhøve med trygdeavtalelova[^1]\n"
    assert "trygdeavtalelova1" not in md


def test_a_heading_span_does_not_gain_emphasis_markup() -> None:
    # _span_text delimits the footnote marker and nothing else. Routing it
    # through _inline would also emphasise, and a value span of "§ 1-<em>1</em>"
    # would render "### § 1-*1*", which parse_section_heading cannot read —
    # trading a fused word for a § no one can address.
    md = render_markdown(
        _wrap(
            b'<h3 class="legalArticleHeader">'
            b'<span class="legalArticleValue">\xc2\xa7 1-<em>1</em></span>. '
            b'<span class="legalArticleTitle">Scope and <em>reach</em></span></h3>',
        ),
    )

    assert md == "### § 1-1. Scope and reach\n"


def test_a_heading_carrying_a_marker_still_parses_as_a_section() -> None:
    # The delimiter lands in the title, so the section id in front of it must
    # still be readable — otherwise the § becomes unaddressable by every MCP
    # tool that keys on it.
    parsed = parse_section_heading("### § 19. samhøve med trygdeavtalelova[^1]")

    assert parsed == ("19", "samhøve med trygdeavtalelova[^1]")


def test_a_plain_sup_in_a_heading_span_is_not_treated_as_a_footnote() -> None:
    # _span_text decides by CLASS, not by tag. Flipping its "and" to "or" makes
    # every <sup> a marker, so "km2" in a § title would render "km[^2]" — this
    # fix pointing the other way. Body text pins the same invariant; a heading
    # span reaches it by a different path and needs its own.
    md = render_markdown(
        _wrap(
            b'<h3 class="legalArticleHeader">'
            b'<span class="legalArticleValue">\xc2\xa7 5</span>. '
            b'<span class="legalArticleTitle">Avgift per km<sup>2</sup></span></h3>',
        ),
    )

    assert md == "### § 5. Avgift per km2\n"


def test_a_heading_span_that_opens_with_a_child_keeps_the_child_text() -> None:
    # _span_text seeds its parts with span.text, which is None when the span
    # opens with an element. Nothing pinned that the rest still arrives.
    md = render_markdown(
        _wrap(
            b'<h3 class="legalArticleHeader">'
            b'<span class="legalArticleValue">\xc2\xa7 6</span>. '
            b'<span class="legalArticleTitle"><em>Kursiv</em> start</span></h3>',
        ),
    )

    assert md == "### § 6. Kursiv start\n"


def test_a_footnote_marker_label_split_across_nodes_stays_one_label() -> None:
    # The label is joined from the marker's whole subtree. Joining on anything
    # but "" would splice text into the middle of a label.
    md = render_markdown(
        _wrap(
            b'<h3 class="legalArticleHeader">'
            b'<span class="legalArticleValue">\xc2\xa7 7</span>. '
            b'<span class="legalArticleTitle">Tittel'
            b'<sup class="footnotereference">1<em>2</em></sup></span></h3>',
        ),
    )

    assert md == "### § 7. Tittel[^12]\n"


def test_a_heading_span_child_without_text_contributes_nothing() -> None:
    md = render_markdown(
        _wrap(
            b'<h3 class="legalArticleHeader">'
            b'<span class="legalArticleValue">\xc2\xa7 8</span>. '
            b'<span class="legalArticleTitle">Foer<em></em>etter</span></h3>',
        ),
    )

    assert md == "### § 8. Foeretter\n"


@pytest.mark.parametrize("tag", ["h3", "h4", "h5", "h6"])
def test_every_heading_level_is_a_block_child_of_a_mixed_article(tag: str) -> None:
    # h1/h2 were pinned after a mutation run removed them from _BLOCK_TAGS; the
    # same mutants for h4-h6 survived because nothing nested those either. A
    # heading that falls out of the set fuses into the surrounding paragraph.
    md = render_markdown(
        _wrap(f'<article class="legalP">Foer<{tag}>Tittel</{tag}>etter</article>'.encode()),
    )

    assert md == "Foer\n\n### Tittel\n\netter\n"


@pytest.mark.parametrize(
    "tag", ["div", "footer", "figure", "figcaption", "blockquote", "li", "dl", "dt", "dd"]
)
def test_every_wrapper_tag_keeps_block_content_apart(tag: str) -> None:
    # One tag silently dropping out of _WRAPPER_TAGS is the flattening defect
    # this set exists to prevent, and it fails quietly: the wrapper's subtree
    # collapses into one paragraph. dl/dt/dd have 0 occurrences in <main> today
    # and are kept against future Lovdata markup — that choice is only honest
    # while something proves they work.
    md = render_markdown(
        _wrap(
            f'<article class="legalArticle"><{tag}>'
            f'<article class="legalP">Forste.</article>'
            f'<article class="legalP">Andre.</article>'
            f"</{tag}></article>".encode(),
        ),
    )

    assert md == "Forste.\n\nAndre.\n", tag
    assert "Forste.Andre" not in md


def test_eliding_a_future_subtree_keeps_its_tail_in_document_order() -> None:
    # _remove_preserving_tail reads getprevious() BEFORE remove(), because a
    # detached element has no previous sibling. Taking it after sends every
    # tail to parent.text — ahead of the siblings that preceded it — and the
    # law reads out of order. Exactly the bug found in the audit script's copy
    # of this logic on 2026-07-27; the renderer's copy is correct and now
    # pinned.
    md = render_markdown(
        _wrap(
            b'<article class="legalP">Aaa <em>kursiv</em>'
            b'<span class="futuretitle">BORTE</span> Bbb</article>',
        ),
    )

    assert md == "Aaa *kursiv* Bbb\n"
    assert md.index("kursiv") < md.index("Bbb")
