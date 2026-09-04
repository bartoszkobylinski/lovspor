"""The body renderer: corpus Markdown to inert, escaped HTML (ADR-0013).

The renderer handles the renderer-v8 body grammar — headings, paragraphs,
ordered/unordered lists, pipe tables, blockquotes, emphasis, links — and
nothing else. Two contracts dominate: every character of corpus text is
escaped at this boundary (raw HTML never passes through as markup), and a
link becomes an ``<a>`` only when the resolver maps its ref target to an
emitted canonical URL — everything else renders as visible text.
"""

import pytest

from lovspor.publish.html import render_body_html


def _resolve(target: str) -> str | None:
    return {
        "lov/2024-12-20-96": "/lov/abortloven/",
        "lov/2024-12-20-96/§3": "/lov/abortloven/paragraf/3/",
    }.get(target)


def render(text: str) -> str:
    return render_body_html(text.split("\n"), _resolve)


class TestBlocks:
    def test_rendering_is_byte_deterministic(self) -> None:
        source = (
            "### § 35 a. Unntak\n\n"
            "Se [abortloven § 3](lov/2024-12-20-96/§3).\n\n"
            "| Vilkår | Verdi |\n| --- | --- |\n| **type** | <ingen> |"
        )

        assert render(source).encode("utf-8") == render(source).encode("utf-8")

    def test_heading_levels_map_to_html(self) -> None:
        html = render("## Kapittel 1. Alminnelige bestemmelser")
        assert html == "<h2>Kapittel 1. Alminnelige bestemmelser</h2>"

    def test_section_heading_gets_anchor_id(self) -> None:
        assert render("### § 1. Formål") == '<h3 id="paragraf-1">§ 1. Formål</h3>'

    def test_section_anchor_uses_normalised_pid(self) -> None:
        assert render("### § 35 a. Unntak") == ('<h3 id="paragraf-35a">§ 35 a. Unntak</h3>')

    def test_paragraph_joins_continuation_lines(self) -> None:
        assert render("Første linje\nandre linje") == ("<p>Første linje\nandre linje</p>")

    def test_blank_line_separates_paragraphs(self) -> None:
        assert render("En.\n\nTo.") == "<p>En.</p>\n<p>To.</p>"

    def test_ordered_list(self) -> None:
        html = render("1. første\n2. andre")
        assert html == "<ol>\n<li>første</li>\n<li>andre</li>\n</ol>"

    def test_unordered_list(self) -> None:
        html = render("- en\n- to")
        assert html == "<ul>\n<li>en</li>\n<li>to</li>\n</ul>"

    def test_blockquote(self) -> None:
        assert render("> sitat") == "<blockquote><p>sitat</p></blockquote>"

    def test_pipe_table(self) -> None:
        html = render("| A | B |\n| --- | --- |\n| en | to |")
        assert html == (
            "<table>\n<thead><tr><th>A</th><th>B</th></tr></thead>\n"
            "<tbody>\n<tr><td>en</td><td>to</td></tr>\n</tbody>\n</table>"
        )


class TestAnchorSuppression:
    def test_duplicate_pids_render_without_any_anchor(self) -> None:
        lines = [
            "### § 1. En",
            "",
            "A.",
            "",
            "### § 1. To",
            "",
            "B.",
            "",
            "### § 2. Tre",
        ]
        html = render_body_html(lines, lambda _t: None, frozenset({"1"}))
        assert 'id="paragraf-1"' not in html
        assert html.count('id="paragraf-2"') == 1
        assert "§ 1. En" in html
        assert "§ 1. To" in html

    def test_default_is_unsuppressed(self) -> None:
        html = render_body_html(["### § 1. En"], lambda _t: None)
        assert 'id="paragraf-1"' in html


class TestInline:
    def test_resolvable_link_becomes_internal_anchor(self) -> None:
        html = render("Se [abortloven § 3](lov/2024-12-20-96/§3).")
        assert html == ('<p>Se <a href="/lov/abortloven/paragraf/3/">abortloven § 3</a>.</p>')

    def test_unresolvable_link_renders_as_text(self) -> None:
        assert render("Jf. [direktivet](eu/32006L0123).") == ("<p>Jf. direktivet.</p>")

    def test_external_http_link_renders_as_text(self) -> None:
        assert render("Se [siden](https://example.com/x).") == "<p>Se siden.</p>"

    def test_strong_and_emphasis(self) -> None:
        assert render("**sterk** og *svak*") == ("<p><strong>sterk</strong> og <em>svak</em></p>")


class TestRendererEscapes:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            (r"\# ordlyd", "<p># ordlyd</p>"),
            (r"\- ordlyd", "<p>- ordlyd</p>"),
            (r"\> ordlyd", "<p>&gt; ordlyd</p>"),
            (r"\| ordlyd", "<p>| ordlyd</p>"),
            (r"1\. ordlyd", "<p>1. ordlyd</p>"),
        ],
    )
    def test_escaped_leading_tokens_stay_literal_paragraphs(
        self,
        line: str,
        expected: str,
    ) -> None:
        assert render(line) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (r"Vanlig \*tekst\*.", "<p>Vanlig *tekst*.</p>"),
            (
                r"Kall \`verdien\` og \\stien.",
                "<p>Kall `verdien` og \\stien.</p>",
            ),
            (r"stoff]\(2,2,3)", "<p>stoff](2,2,3)</p>"),
            (r"\[ikke lenke](lov/2024-12-20-96)", "<p>[ikke lenke](lov/2024-12-20-96)</p>"),
        ],
    )
    def test_escapes_removed_without_creating_markup(
        self,
        text: str,
        expected: str,
    ) -> None:
        assert render(text) == expected

    def test_escaped_char_is_still_html_escaped(self) -> None:
        assert render(r"\>") == "<p>&gt;</p>"


class TestSafety:
    def test_raw_html_is_escaped_not_executed(self) -> None:
        html = render('<script>alert("x")</script>')
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_event_handler_text_stays_text(self) -> None:
        html = render("En <img src=x onerror=alert(1)> to")
        assert "<img" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html

    def test_unsafe_scheme_link_renders_as_text_even_if_resolver_lies(self) -> None:
        html = render_body_html(
            ["[klikk](lov/2024-12-20-96)"],
            lambda _t: "javascript:alert(1)",
        )
        assert "<a" not in html
        assert "klikk" in html

    def test_protocol_relative_href_renders_as_text_even_if_resolver_lies(
        self,
    ) -> None:
        html = render_body_html(
            ["[klikk](lov/2024-12-20-96)"],
            lambda _t: "//evil.example/x",
        )
        assert "<a" not in html
        assert "klikk" in html

    def test_backslash_relative_href_refused(self) -> None:
        html = render_body_html(
            ["[klikk](lov/2024-12-20-96)"],
            lambda _t: "/\\evil.example/x",
        )
        assert "<a" not in html

    def test_link_text_is_escaped(self) -> None:
        html = render("[<b>fet</b>](lov/2024-12-20-96)")
        assert "<b>" not in html
        assert "&lt;b&gt;fet&lt;/b&gt;" in html

    def test_resolved_href_is_attribute_escaped(self) -> None:
        html = render_body_html(
            ["[klikk](lov/2024-12-20-96)"],
            lambda _target: '/lov/x/" onmouseover="alert(1)',
        )

        assert html == ('<p><a href="/lov/x/&quot; onmouseover=&quot;alert(1)">klikk</a></p>')
        assert 'href="/lov/x/"' not in html

    def test_escaping_applies_inside_table_cells(self) -> None:
        html = render("| A |\n| --- |\n| <i>x</i> |")
        assert "<i>" not in html
        assert "&lt;i&gt;x&lt;/i&gt;" in html
