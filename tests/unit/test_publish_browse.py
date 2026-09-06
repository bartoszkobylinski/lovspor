"""Browse indexes: one A-Å page per route (ADR-0013 Decisions 1, 7).

The full-page golden is byte-exact — the template is the contract, the
same discipline as the document pages. Collation is pinned separately:
Norwegian alphabetical order (æ ø å after z) must never depend on the
build machine's locale tables, so the key function itself is under test.
"""

from lovspor.publish import browse as publish_browse
from lovspor.publish.browse import (
    BROWSE_ROUTES,
    browse_index_html,
    browse_index_url,
    collation_key,
    display_name,
)
from lovspor.publish.inventory import DocumentPlan, PublishInventory
from tests.unit.test_publish_pages_golden import _shell


def _plan(slug: str, title: str | None, route: str = "lov") -> DocumentPlan:
    return DocumentPlan.model_validate(
        {
            "doc_id": f"doc-{slug}",
            "slug": slug,
            "route": route,
            "title": title,
            "markdown_path": f"lover/{slug}.md",
            "source_dataset": "gjeldende-lover",
            "xml_hash": "a" * 64,
            "renderer_version": 8,
            "language": "nb",
            "ref_id": "lov/2020-01-01-1" if route == "lov" else "forskrift/2020-01-01-1",
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "date_in_force": None,
            "last_change_in_force": None,
            "provisions": (),
            "duplicate_pids": {},
        },
    )


class TestCollation:
    def test_alphabet_letters_have_the_documented_key_tag(self) -> None:
        assert collation_key("a") == ((1, 0),)
        assert collation_key("å") == ((1, 28),)

    def test_norwegian_letters_collate_after_z(self) -> None:
        names = ["Åloven", "Ærloven", "Zloven", "Østloven", "Bloven"]
        assert sorted(names, key=collation_key) == [
            "Bloven",
            "Zloven",
            "Ærloven",
            "Østloven",
            "Åloven",
        ]

    def test_collation_casefolds(self) -> None:
        assert collation_key("ÅLOVEN") == collation_key("åloven")

    def test_characters_outside_the_alphabet_sort_first_by_code_point(self) -> None:
        assert collation_key("1x") < collation_key("ax")
        assert collation_key(" x") < collation_key("1x")

    def test_key_orders_within_a_word(self) -> None:
        assert collation_key("lov") < collation_key("lover")
        assert collation_key("lova") < collation_key("lovb")


class TestDisplayName:
    def test_title_wins(self) -> None:
        assert display_name(_plan("abortloven", "Abortloven")) == "Abortloven"

    def test_slug_is_the_fallback_for_a_titleless_document(self) -> None:
        assert display_name(_plan("abortloven", None)) == "abortloven"


class TestBrowseIndex:
    def test_group_template_escapes_quotes_and_newline_separates_entries(self) -> None:
        group = publish_browse._group_html('A"', [_plan("en", "En"), _plan("to", "To")])
        assert '<section aria-label="A&quot;">' in group
        assert "</li>\n<li>" in group
        assert "XX" not in group

    def test_routes_are_the_two_canonical_prefixes(self) -> None:
        assert BROWSE_ROUTES == ("lov", "forskrift")
        assert browse_index_url("lov") == "/lov/"
        assert browse_index_url("forskrift") == "/forskrift/"

    def test_lov_page_is_byte_exact(self) -> None:
        inventory = PublishInventory(
            documents=(
                _plan("åloven", "Åloven"),
                _plan("abortloven", "Abortloven"),
                _plan("testforskriften", "Testforskriften", route="forskrift"),
            ),
        )
        expected = _shell(
            "nb",
            "Lover A–Å",  # noqa: RUF001 — deliberate EN DASH
            "https://lovspor.no/lov/",
            "<h1>Lover A–Å</h1>\n"  # noqa: RUF001 — deliberate EN DASH
            '<section aria-label="A">\n<h2>A</h2>\n<ul>\n'
            '<li><a href="/lov/abortloven/">Abortloven</a></li>\n'
            "</ul>\n</section>\n"
            '<section aria-label="Å">\n<h2>Å</h2>\n<ul>\n'
            '<li><a href="/lov/åloven/">Åloven</a></li>\n'
            "</ul>\n</section>",
        )
        assert browse_index_html("lov", inventory) == expected

    def test_forskrift_page_lists_only_forskrifter(self) -> None:
        inventory = PublishInventory(
            documents=(
                _plan("testloven", "Testloven"),
                _plan("testforskriften", "Testforskriften", route="forskrift"),
            ),
        )
        page = browse_index_html("forskrift", inventory)
        assert "Forskrifter A–Å" in page  # noqa: RUF001 — deliberate EN DASH
        assert '<a href="/forskrift/testforskriften/">Testforskriften</a>' in page
        assert "testloven" not in page

    def test_entries_group_and_sort_by_display_name(self) -> None:
        inventory = PublishInventory(
            documents=(
                _plan("b-loven", "Bloven"),
                _plan("aa-loven", "Åloven"),
                _plan("a-loven", "Abortloven"),
            ),
        )
        page = browse_index_html("lov", inventory)
        assert (
            page.index('<a href="/lov/a-loven/">')
            < page.index('<a href="/lov/b-loven/">')
            < page.index('<a href="/lov/aa-loven/">')
        )

    def test_names_outside_the_alphabet_group_first_under_hash(self) -> None:
        inventory = PublishInventory(
            documents=(
                _plan("a-loven", "Abortloven"),
                _plan("tall-loven", "1881-loven"),
            ),
        )
        page = browse_index_html("lov", inventory)
        assert '<section aria-label="#">\n<h2>#</h2>' in page
        assert page.index('aria-label="#"') < page.index('aria-label="A"')

    def test_identical_names_tie_break_on_slug(self) -> None:
        inventory = PublishInventory(
            documents=(
                _plan("b-utgave", "Sameloven"),
                _plan("a-utgave", "Sameloven"),
            ),
        )
        page = browse_index_html("lov", inventory)
        assert page.index("/lov/a-utgave/") < page.index("/lov/b-utgave/")

    def test_empty_route_still_emits_the_page(self) -> None:
        inventory = PublishInventory(documents=())
        page = browse_index_html("lov", inventory)
        assert "<h1>Lover A–Å</h1>" in page  # noqa: RUF001 — deliberate EN DASH
        assert "<section" not in page

    def test_display_names_are_escaped(self) -> None:
        inventory = PublishInventory(documents=(_plan("x-loven", "Lov & <endring>"),))
        page = browse_index_html("lov", inventory)
        assert "Lov &amp; &lt;endring&gt;" in page
        assert "<endring>" not in page
