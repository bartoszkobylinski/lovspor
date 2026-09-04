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
    provision_page_html,
    section_slices,
)

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

    def test_no_script_and_no_inline_handlers(self) -> None:
        html = _document()
        assert "<script" not in html
        assert "onclick" not in html


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
