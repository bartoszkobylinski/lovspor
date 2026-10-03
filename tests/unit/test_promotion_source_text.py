"""Text out of captured HTML, PDF and DOCX bytes (ADR-0016 S2, the extractor's readers)."""

from __future__ import annotations

import pypdf
import pytest

from lovspor.errors import LovsporError, UnreadableSourceError
from lovspor.promotion import SourceForm
from lovspor.promotion.source_text import (
    PDF_LIBRARY_VERSION,
    docx_lines,
    html_lines,
    pdf_lines,
    source_form,
)
from tests.unit.promotion_fixtures import (
    REGULATION_LINES,
    docx_with_document,
    html_page,
    minimal_docx,
    minimal_pdf,
)


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("text/html; charset=utf-8", SourceForm.HTML),
        ("application/xhtml+xml", SourceForm.HTML),
        ("APPLICATION/PDF", SourceForm.PDF),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            SourceForm.DOCX,
        ),
        ("application/msword", None),
        ("image/png", None),
        ("", None),
    ],
)
def test_source_form_by_recorded_media_type(content_type: str, expected: SourceForm | None) -> None:
    assert source_form(content_type) == expected


def test_html_keeps_the_regulation_and_drops_page_chrome() -> None:
    lines = html_lines(html_page(), "text/html; charset=utf-8")
    assert lines == (*REGULATION_LINES,)


def test_html_without_main_reads_body_but_still_drops_header_nav_and_footer() -> None:
    page = html_page().replace(b"<main>", b"<div>").replace(b"</main>", b"</div>")
    lines = html_lines(page, "text/html")
    assert lines == (*REGULATION_LINES,)


def test_html_header_carrying_the_title_is_kept() -> None:
    page = (
        "<html><body><main><article><header><h1>Forskrift om gebyr, Eksempel kommune</h1>"
        "</header><p>§ 1 Gebyr</p></article></main></body></html>"
    ).encode()
    assert html_lines(page, "text/html") == (
        "Forskrift om gebyr, Eksempel kommune",
        "§ 1 Gebyr",
    )


def test_html_line_breaks_and_inline_markup() -> None:
    page = (
        "<html><body><main><p>§ 1 <strong>Formål</strong><br>Forskriften   skal\n sikre"
        "</p><ul><li>a) avfall</li><li>b) slam</li></ul></main></body></html>"
    ).encode()
    assert html_lines(page, "text/html") == (
        "§ 1 Formål",
        "Forskriften skal sikre",
        "a) avfall",
        "b) slam",
    )


def test_html_drops_sidebars_named_by_role_or_class() -> None:
    page = (
        "<html><body><main><div role='complementary'>Les også dette</div>"
        "<div class='page-sidebar'>Relaterte saker</div><div class='share-buttons'>Del</div>"
        "<p>§ 1 Formål</p></main></body></html>"
    ).encode()
    assert html_lines(page, "text/html") == ("§ 1 Formål",)


def test_html_reads_the_one_article_when_main_holds_more() -> None:
    page = (
        "<html><body><main><p>Velkommen til våre sider</p>"
        "<article><p>§ 1 Formål</p></article></main></body></html>"
    ).encode()
    assert html_lines(page, "text/html") == ("§ 1 Formål",)


def test_html_with_two_articles_reads_the_whole_region() -> None:
    page = (
        "<html><body><main><article><p>§ 1 Formål</p></article>"
        "<article><p>§ 2 Virkeområde</p></article></main></body></html>"
    ).encode()
    assert html_lines(page, "text/html") == ("§ 1 Formål", "§ 2 Virkeområde")


def test_html_drops_update_stamps_and_page_furniture_lines() -> None:
    page = (
        "<html><body><main><p>§ 1 Formål</p><p>Sist oppdatert: 3. oktober 2026</p>"
        "<p>Publisert 12.08.2024</p><p>Skriv ut</p><p>Del denne siden</p><p>Til toppen</p>"
        "</main></body></html>"
    ).encode()
    assert html_lines(page, "text/html") == ("§ 1 Formål",)


def test_html_processing_instruction_is_neither_text_nor_chrome() -> None:
    page = b"<html><body><main><?cms block?><p>\xc2\xa7 1 Form\xc3\xa5l</p></main></body></html>"
    assert html_lines(page, "text/html; charset=utf-8") == ("§ 1 Formål",)


def test_html_decodes_by_declared_charset() -> None:
    page = "<html><body><main><p>Formål og virkeområde</p></main></body></html>"
    assert html_lines(page.encode("latin-1"), "text/html; charset=iso-8859-1") == (
        "Formål og virkeområde",
    )


def test_html_external_entity_is_never_resolved() -> None:
    page = (
        b'<?xml version="1.0"?><!DOCTYPE html [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<html><body><main><p>&xxe;</p></main></body></html>"
    )
    assert all("root:" not in line for line in html_lines(page, "text/html"))


def test_html_with_no_document_region_is_unreadable() -> None:
    with pytest.raises(UnreadableSourceError, match="no document region"):
        html_lines(b"<frameset></frameset>", "text/html")


def test_html_that_does_not_parse_is_unreadable() -> None:
    with pytest.raises(UnreadableSourceError):
        html_lines(b"", "text/html")


def test_pdf_reads_page_text_line_by_line() -> None:
    assert pdf_lines(minimal_pdf()) == (*REGULATION_LINES,)


def test_pdf_metadata_is_never_read() -> None:
    assert all("Testperson" not in line for line in pdf_lines(minimal_pdf()))


def test_pdf_drops_page_numbers() -> None:
    lines = pdf_lines(minimal_pdf(("§ 1 Formål", "Side 1 av 3", "7", "§ 2 Gebyr")))
    assert lines == ("§ 1 Formål", "§ 2 Gebyr")


def test_pdf_that_does_not_parse_is_unreadable() -> None:
    with pytest.raises(UnreadableSourceError):
        pdf_lines(b"%PDF-1.4 this is not a pdf")


def test_unreadable_source_is_a_lovspor_error() -> None:
    assert issubclass(UnreadableSourceError, LovsporError)


def test_pdf_library_is_the_pinned_one() -> None:
    """A pypdf bump can change extracted text and so every PDF's content_hash.

    Bump PDF_LIBRARY_VERSION and EXTRACTOR_VERSION together, deliberately.
    """
    assert pypdf.__version__ == PDF_LIBRARY_VERSION


def test_docx_reads_one_line_per_paragraph_joining_runs() -> None:
    assert docx_lines(minimal_docx()) == (*REGULATION_LINES,)


def test_docx_without_document_part_is_unreadable() -> None:
    with pytest.raises(UnreadableSourceError, match="document.xml"):
        docx_lines(docx_with_document(b"<w:document/>", part="word/other.xml"))


def test_docx_that_is_not_a_zip_is_unreadable() -> None:
    with pytest.raises(UnreadableSourceError):
        docx_lines(b"not a zip")


def test_docx_external_entity_is_never_resolved() -> None:
    document = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body></w:document>"
    )
    try:
        lines = docx_lines(docx_with_document(document))
    except UnreadableSourceError:
        return
    assert all("root:" not in line for line in lines)


def test_docx_oversized_document_part_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lovspor.promotion.source_text.MAX_DOCX_PART_BYTES", 100)
    with pytest.raises(UnreadableSourceError, match="larger than"):
        docx_lines(minimal_docx())
