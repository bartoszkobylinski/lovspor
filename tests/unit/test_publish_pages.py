"""Document and provision pages (ADR-0013 Decisions 4-5).

A page is a pure function of its plan, its section slice and the build's
provenance — no wall-clock values, no global corpus commit. The contract
under test: core content present in the initial HTML (title, text, lang,
canonical), the provenance block with the NLOD transformation statement,
and the duplicate-pid rule (no provision links, anchors only for unique
pids).
"""

from lovspor.publish.inventory import DocumentPlan, ProvisionRef
from lovspor.publish.pages import (
    PageProvenance,
    document_page_html,
    layout,
    provision_page_html,
    section_slices,
)
from lovspor.site.chrome import chrome_html
from lovspor.site.style import stylesheet

PROVENANCE = PageProvenance(
    source_revision="ab388cbdeadbeef",
    xml_hash="c" * 64,
    renderer_version=8,
)


def _plan(**overrides: object) -> DocumentPlan:
    base: dict[str, object] = {
        "doc_id": "nl-20241220-096",
        "slug": "abortloven",
        "route": "lov",
        "title": "Lov om abort (abortloven)",
        "markdown_path": "lover/abortloven.md",
        "source_dataset": "gjeldende-lover",
        "xml_hash": "c" * 64,
        "renderer_version": 8,
        "language": "nb",
        "ref_id": "lov/2024-12-20-96",
        "retrieved_at": "2026-07-30T18:17:57+00:00",
        "date_in_force": "2025-06-01",
        "last_change_in_force": None,
        "provisions": (
            ProvisionRef(pid="1", heading_id="1", title="Formål"),
            ProvisionRef(pid="2", heading_id="2", title="Virkeområde"),
        ),
        "duplicate_pids": {},
    }
    base.update(overrides)
    return DocumentPlan.model_validate(base)


BODY_LINES = [
    "# Lov om abort (abortloven)",
    "",
    "## Kapittel 1. Alminnelige bestemmelser",
    "",
    "### § 1. Formål",
    "",
    "Loven skal sikre gravide rett til selvbestemmelse.",
    "",
    "### § 2. Virkeområde",
    "",
    "Loven gjelder aborter i riket.",
]


def _document(plan: DocumentPlan | None = None) -> str:
    return document_page_html(plan or _plan(), BODY_LINES, PROVENANCE, lambda t: None)


class TestDocumentPage:
    def test_core_content_is_in_the_initial_html(self) -> None:
        html = _document()
        assert '<html lang="nb">' in html
        assert "<title>Lov om abort (abortloven)</title>" in html
        assert "Loven skal sikre gravide rett til selvbestemmelse." in html
        assert '<link rel="canonical" href="https://lovspor.no/lov/abortloven/">' in html

    def test_one_h1_only(self) -> None:
        assert _document().count("<h1") == 1

    def test_language_comes_from_the_plan(self) -> None:
        html = document_page_html(
            _plan(language="nn"),
            BODY_LINES,
            PROVENANCE,
            lambda t: None,
        )
        assert '<html lang="nn">' in html

    def test_provision_links_emitted_for_unique_pids(self) -> None:
        html = _document()
        assert '<a href="/lov/abortloven/paragraf/1/">' in html
        assert '<a href="/lov/abortloven/paragraf/2/">' in html

    def test_duplicate_pid_document_gets_no_provision_links(self) -> None:
        plan = _plan(duplicate_pids={"1": 2})
        html = document_page_html(plan, BODY_LINES, PROVENANCE, lambda t: None)
        assert "/paragraf/" not in html
        assert "XXXX" not in html

    def test_empty_provision_list_gets_no_toc(self) -> None:
        html = _document(_plan(provisions=()))
        assert '<nav class="toc"' not in html
        assert "XXXX" not in html

    def test_toc_has_exact_separator_and_omits_missing_title(self) -> None:
        plan = _plan(
            provisions=(
                ProvisionRef(pid="1", heading_id="1", title=None),
                ProvisionRef(pid="2", heading_id="2", title="Virkeområde"),
            )
        )
        html = _document(plan)
        assert '<span class="pid">§ 1</span></a></li>\n<li' in html
        assert '<span class="pid">§ 2</span> Virkeområde</a>' in html
        assert "§ 1XXXX" not in html

    def test_duplicate_pid_document_suppresses_only_ambiguous_anchors(self) -> None:
        plan = _plan(duplicate_pids={"1": 2})
        html = document_page_html(plan, BODY_LINES, PROVENANCE, lambda t: None)
        assert 'id="paragraf-1"' not in html
        assert html.count('id="paragraf-2"') == 1

    def test_provenance_block_present(self) -> None:
        html = _document()
        assert "Norsk lisens for offentlige data (NLOD 2.0)" in html
        assert "transformert og strukturert av Lovspor" in html
        assert "https://data.norge.no/nlod/no/2.0" in html
        assert "ab388cbdead" in html
        assert "cccccccccc" in html
        assert "ikke offisiell kunngj" in html

    def test_last_change_provenance_label_is_exact(self) -> None:
        html = _document(_plan(last_change_in_force="2026-08-15"))
        assert "<dt>Siste endring i kraft</dt><dd>2026-08-15</dd>" in html

    def test_no_script_and_no_inline_handlers(self) -> None:
        html = _document()
        assert "<script" not in html
        assert "onclick" not in html


class TestContentsAreNavigation:
    """The contents of a law are read on a phone, and ~87k provision pages
    hang off them, so they are an index rather than a wall of links."""

    def test_the_law_is_named_before_its_provisions_are_listed(self) -> None:
        """A reader landing from a search engine met 24 links before the
        law's own title; the title comes first now."""
        html = _document()

        assert html.index("<h1>") < html.index('<nav class="toc"')

    def test_the_paragraph_number_is_its_own_element(self) -> None:
        html = _document()

        assert '<span class="pid">§ 1</span> Formål' in html
        assert '<span class="pid">§ 2</span> Virkeområde' in html

    def test_the_body_text_still_follows_the_contents_once_and_whole(self) -> None:
        """Splitting the title off the body must not drop or duplicate it."""
        html = _document()

        assert html.count("<h1>Lov om abort (abortloven)</h1>") == 1
        assert html.count("Loven skal sikre gravide rett til selvbestemmelse.") == 1
        assert html.count("Loven gjelder aborter i riket.") == 1
        assert html.index('<nav class="toc"') < html.index('id="paragraf-1"')

    def test_a_body_with_no_title_heading_keeps_its_order(self) -> None:
        """No guessing where a title was meant to be."""
        html = document_page_html(
            _plan(), ["### § 1. Formål", "", "Tekst."], PROVENANCE, lambda t: None
        )

        assert '<nav class="toc"' in html
        assert html.index('<nav class="toc"') < html.index('id="paragraf-1"')
        assert "<h1>" not in html

    def test_a_chapter_heading_before_the_first_provision_survives(self) -> None:
        html = _document()

        assert "<h2>Kapittel 1. Alminnelige bestemmelser</h2>" in html
        assert html.index('<nav class="toc"') < html.index("<h2>")


class TestSharedChrome:
    """ADR-0014 Decision 5, issue #335: corpus pages carry the site's chrome.

    Before this, a reader landing on a law page from a search engine had no
    link back into the site in either language — 757 laws, 5,107
    regulations and 87,046 provision pages, every one of them a dead end.
    """

    def test_the_document_page_carries_the_header_and_footer_verbatim(self) -> None:
        html = _document()
        chrome = chrome_html("nb")

        assert chrome.header in html
        assert chrome.footer in html

    def test_the_provision_page_carries_them_too(self) -> None:
        plan = _plan()
        html = provision_page_html(
            plan, plan.provisions[0], PROVENANCE, ["### § 1. Formål"], lambda t: None
        )
        chrome = chrome_html("nb")

        assert chrome.header in html
        assert chrome.footer in html

    def test_every_page_offers_the_way_back_to_the_site_and_both_indexes(self) -> None:
        html = _document()

        assert '<a class="brand" href="/">' in html
        assert '<a href="/lov/">Lover</a>' in html
        assert '<a href="/forskrift/">Forskrifter</a>' in html

    def test_the_corpus_chrome_carries_no_language_switch_and_no_badge(self) -> None:
        """Norwegian, no switch, no status badge (ADR-0014 Decision 5): the
        corpus has no English twin for a switch to point at."""
        html = _document()

        assert ">EN</a>" not in html
        assert "<strong>NO</strong>" not in html
        assert 'class="tag"' not in html

    def test_the_content_sits_in_a_main_landmark(self) -> None:
        html = _document()

        assert html.count("<main>") == 1
        assert html.count("</main>") == 1
        assert "Loven skal sikre gravide rett til selvbestemmelse." in html.split("<main>")[1]

    def test_the_chrome_is_outside_the_main_landmark(self) -> None:
        """A header repeated on 93k pages must not sit inside the document's
        own content, or every page's main landmark starts with navigation."""
        html = _document()

        assert html.index('<a class="brand"') < html.index("<main>")
        assert html.index("</main>") < html.index("<footer>")


class TestSharedStylesheet:
    def test_the_page_inlines_the_one_shared_stylesheet(self) -> None:
        assert f"<style>\n{stylesheet()}</style>" in _document()

    def test_there_is_exactly_one_style_block_and_no_linked_asset(self) -> None:
        html = _document()

        assert html.count("<style>") == 1
        assert '<link rel="stylesheet"' not in html
        assert "@import" not in html

    def test_the_old_private_stylesheet_is_gone(self) -> None:
        """The corpus surface had its own 1990s design — Georgia and 1px
        table grids — which is how it drifted from the site (#335)."""
        html = _document()

        assert "Georgia" not in html
        assert "1px solid #999" not in html


class TestDeterminism:
    def test_two_renders_of_one_page_are_the_same_bytes(self) -> None:
        """A page is a pure function of its inputs; the chrome and the
        stylesheet must not have made it a function of anything else."""
        first = _document().encode("utf-8")
        second = _document().encode("utf-8")

        assert first == second

    def test_the_shell_is_the_same_whatever_the_page(self) -> None:
        """The chrome takes no fact argument, so two different documents
        render byte-identical chrome (ADR-0014 Decision 5)."""
        one = layout("nb", "A", "/lov/a/", "<p>a</p>")
        two = layout("nb", "B", "/lov/b/", "<p>b</p>")
        chrome = chrome_html("nb")

        assert chrome.header in one and chrome.header in two
        assert chrome.footer in one and chrome.footer in two


class TestSectionSlices:
    def test_slices_cover_each_section_to_the_next_boundary(self) -> None:
        slices = section_slices(BODY_LINES)
        assert list(slices) == ["1", "2"]
        assert slices["1"][0] == "### § 1. Formål"
        assert "Loven skal sikre gravide rett til selvbestemmelse." in slices["1"]
        assert "### § 2. Virkeområde" not in slices["1"]

    def test_chapter_heading_ends_a_slice(self) -> None:
        lines = [
            "### § 1. En",
            "",
            "Tekst en.",
            "",
            "## Kapittel 2. Neste",
            "",
            "### § 2. To",
            "",
            "Tekst to.",
        ]
        slices = section_slices(lines)
        assert "## Kapittel 2. Neste" not in slices["1"]
        assert "Tekst to." in slices["2"]

    def test_duplicate_pid_keeps_first_slice_only_by_contract(self) -> None:
        # Slices for a duplicate pid are never published (the inventory
        # withholds those pages); the mapping still must not explode.
        lines = ["### § 1. En", "A.", "### § 1. To", "B."]
        slices = section_slices(lines)
        assert "A." in slices["1"]

    def test_final_section_extends_to_end_of_document(self) -> None:
        lines = ["### § 9. Siste", "", "Siste linje."]
        assert section_slices(lines)["9"] == lines


class TestProvisionPage:
    def test_core_content_and_identity(self) -> None:
        plan = _plan()
        html = provision_page_html(
            plan, plan.provisions[0], PROVENANCE, ["### § 1. Formål", "", "Tekst."], lambda t: None
        )
        assert '<html lang="nb">' in html
        assert "<title>§ 1. Formål — Lov om abort (abortloven)</title>" in html
        assert "Tekst." in html
        assert '<link rel="canonical" href="https://lovspor.no/lov/abortloven/paragraf/1/">' in html

    def test_parent_and_neighbour_links(self) -> None:
        plan = _plan()
        html = provision_page_html(
            plan, plan.provisions[0], PROVENANCE, ["### § 1. Formål", "Tekst."], lambda t: None
        )
        assert '<a href="/lov/abortloven/">' in html
        assert '<a href="/lov/abortloven/paragraf/2/"' in html

    def test_first_provision_has_no_previous_link(self) -> None:
        plan = _plan()
        html = provision_page_html(
            plan, plan.provisions[0], PROVENANCE, ["### § 1. Formål", "Tekst."], lambda t: None
        )
        assert "forrige" not in html.lower() or "paragraf/0" not in html

    def test_untitled_provision_title_has_no_trailing_punctuation(self) -> None:
        plan = _plan(provisions=(ProvisionRef(pid="1", heading_id="1", title=None),))
        html = provision_page_html(
            plan, plan.provisions[0], PROVENANCE, ["### § 1"], lambda t: None
        )
        assert "<title>§ 1 — Lov om abort (abortloven)</title>" in html

    def test_provision_title_preserves_trailing_x(self) -> None:
        plan = _plan(provisions=(ProvisionRef(pid="1", heading_id="1", title="Vedlegg X"),))
        html = provision_page_html(
            plan, plan.provisions[0], PROVENANCE, ["### § 1. Vedlegg X"], lambda t: None
        )
        assert "<title>§ 1. Vedlegg X — Lov om abort (abortloven)</title>" in html

    def test_middle_provision_links_to_immediate_neighbours(self) -> None:
        provisions = tuple(
            ProvisionRef(pid=str(i), heading_id=str(i), title=f"Del {i}") for i in range(1, 4)
        )
        plan = _plan(provisions=provisions)
        html = provision_page_html(plan, provisions[1], PROVENANCE, ["### § 2"], lambda t: None)
        assert (
            '<a href="/lov/abortloven/paragraf/1/" rel="prev">Forrige paragraf</a>'
            " · "
            '<a href="/lov/abortloven/paragraf/3/" rel="next">Neste paragraf</a>'
        ) in html

    def test_head_values_escape_quotes(self) -> None:
        plan = _plan(language='nb" onload="bad', title='A "quoted" law')
        html = document_page_html(plan, BODY_LINES, PROVENANCE, lambda t: None)
        assert '<html lang="nb&quot; onload=&quot;bad">' in html
        assert "<title>A &quot;quoted&quot; law</title>" in html
        assert 'onload="bad"' not in html

    def test_canonical_attribute_escapes_quotes(self) -> None:
        html = layout("nb", "Tittel", '/lov/a" onclick="bad/', "Tekst")
        assert 'href="https://lovspor.no/lov/a&quot; onclick=&quot;bad/"' in html
        assert 'onclick="bad"' not in html
