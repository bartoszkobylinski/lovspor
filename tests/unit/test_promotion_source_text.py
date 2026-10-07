"""Text out of captured HTML, PDF and DOCX bytes (ADR-0016 S2, the extractor's readers)."""

from __future__ import annotations

import io
import zipfile

import pypdf
import pytest
from lxml import html

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
    FAQ_SIBLINGS,
    REGULATION_LINES,
    docx_with_document,
    faq_page,
    html_page,
    minimal_docx,
    minimal_pdf,
    pdf_pages,
)

DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_body(paragraphs: str) -> bytes:
    document = f'<w:document xmlns:w="{_W}"><w:body>{paragraphs}</w:body></w:document>'
    return docx_with_document(document.encode())


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
        (f"{DOCX_MEDIA}; charset=binary", SourceForm.DOCX),
        (f'{DOCX_MEDIA}; charset=binary; name="forskrift.docx"', SourceForm.DOCX),
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


_FAQ_TAIL_LINES = (
    *(line for item in FAQ_SIBLINGS for line in item),
    "Les mer om følgende emner:",
    "skole",
    "Velkommen til Eksempelskolen",
    "Elevene løp stafett i høstferien",
)


def test_html_reads_the_innermost_article_holding_the_regulation() -> None:
    lines = html_lines(faq_page(), "text/html")
    assert lines == ("Hvilke ordensregler gjelder?", *REGULATION_LINES)


def test_html_innermost_article_drops_sibling_items_and_teasers_after_the_last_section() -> None:
    lines = html_lines(faq_page(), "text/html")
    assert lines[-1] == REGULATION_LINES[-1]
    assert set(lines).isdisjoint(_FAQ_TAIL_LINES)


def test_html_innermost_article_is_the_same_whatever_the_page_tail() -> None:
    assert html_lines(faq_page(tail=""), "text/html") == html_lines(faq_page(), "text/html")


def test_html_with_two_articles_holding_the_regulation_reads_the_whole_region() -> None:
    item = ("Hvilke ordensregler gjelder?", *REGULATION_LINES)
    lines = html_lines(faq_page(regulation_answers=2), "text/html")
    assert lines == ("Skole", *item, *item, *_FAQ_TAIL_LINES)


def test_html_with_the_title_outside_every_article_reads_the_whole_region() -> None:
    page = (
        "<html><body><main><h1>Forskrift om gebyr, Eksempel kommune</h1>"
        "<article><p>§ 1 Gebyr</p></article><article><p>§ 2 Betaling</p></article>"
        "<p>Kontakt oss</p></main></body></html>"
    ).encode()
    assert html_lines(page, "text/html") == (
        "Forskrift om gebyr, Eksempel kommune",
        "§ 1 Gebyr",
        "§ 2 Betaling",
        "Kontakt oss",
    )


def test_html_an_article_with_another_regulation_is_not_the_one_read() -> None:
    page = (
        "<html><body><main><h1>Forskrift om gebyr, Eksempel kommune</h1><p>§ 1 Gebyr</p>"
        "<article><p>Forskrift om parkering, Eksempel kommune</p><p>§ 1 Parkering</p></article>"
        "<article><p>Nyheter</p></article></main></body></html>"
    ).encode()
    assert html_lines(page, "text/html") == (
        "Forskrift om gebyr, Eksempel kommune",
        "§ 1 Gebyr",
        "Forskrift om parkering, Eksempel kommune",
        "§ 1 Parkering",
        "Nyheter",
    )


def test_html_single_article_is_read_whole_even_past_the_last_section() -> None:
    page = (
        "<html><body><main><p>Velkommen</p><article><h1>Forskrift om gebyr, Eksempel kommune</h1>"
        "<p>§ 1 Gebyr</p><p>Kontakt servicetorget</p></article></main></body></html>"
    ).encode()
    assert html_lines(page, "text/html") == (
        "Forskrift om gebyr, Eksempel kommune",
        "§ 1 Gebyr",
        "Kontakt servicetorget",
    )


@pytest.mark.parametrize(
    ("title", "section"),
    [
        ("Forskrift om gebyr", "§ 1 Betaling"),
        ("Forskrift om parkering", "§ 1 Gebyr"),
    ],
)
def test_html_article_must_match_both_region_anchors(title: str, section: str) -> None:
    """The PR requires the same title AND first section, not just either anchor."""
    page = _main(
        "<h1>Forskrift om gebyr</h1><h2>§ 1 Gebyr</h2>"
        f"<article><h1>{title}</h1><h2>{section}</h2></article>"
        "<article><p>Nyheter</p></article><p>Etter regionen</p>"
    )
    assert html_lines(page, "text/html") == (
        "Forskrift om gebyr",
        "§ 1 Gebyr",
        title,
        section,
        "Nyheter",
        "Etter regionen",
    )


def test_html_title_after_first_section_does_not_select_an_article() -> None:
    page = _main(
        "<article><h2>§ 1 Gebyr</h2><h1>Forskrift om gebyr</h1></article>"
        "<article><p>Nyheter</p></article><p>Etter regionen</p>"
    )
    assert html_lines(page, "text/html") == (
        "§ 1 Gebyr",
        "Forskrift om gebyr",
        "Nyheter",
        "Etter regionen",
    )


@pytest.mark.parametrize("section", ["§ 1 Gebyr", "Kapittel 1 Gebyr", "Kap. I Gebyr"])
def test_html_nested_article_anchor_reads_preserve_br_wraps(section: str) -> None:
    """Reading ancestors and candidates must not rewrite the selected article's breaks."""
    page = _main(
        "<article><p>Velkommen</p><article><article>"
        "<h1>Forskrift om gebyr i<br>kommunen</h1>"
        f"<h2>{section}</h2><p>Gebyret gjelder for<br><strong>hele</strong> kommunen.</p>"
        "</article><p>Utenfor forskriften</p></article>"
        "<article><p>Nyheter</p></article></article>"
    )
    expected = (
        "Forskrift om gebyr i kommunen",
        section,
        "Gebyret gjelder for hele kommunen.",
    )
    assert html_lines(page, "text/html") == html_lines(page, "text/html") == expected


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


@pytest.mark.parametrize(
    "chrome",
    [
        "<nav><a href='/'>Hjem</a></nav>",
        "<header><p>Eksempel kommune</p></header>",
        "<div id='sidebar'>Relaterte saker</div>",
        "<script>var x = 'Forskrift om noe annet';</script>",
    ],
)
def test_chrome_by_tag_or_id_alone_is_dropped(chrome: str) -> None:
    page = f"<html><body><main>{chrome}<p>§ 1 Formål</p></main></body></html>".encode()
    assert html_lines(page, "text/html") == ("§ 1 Formål",)


def test_html_with_no_document_region_says_so() -> None:
    with pytest.raises(UnreadableSourceError) as caught:
        html_lines(b"<frameset></frameset>", "text/html")
    assert str(caught.value) == "HTML has no document region (<main> or <body>)"


def test_html_the_parser_rejects_is_unreadable_with_the_parser_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lxml's ValueError (an encoding declaration in text) is unreachable through
    ``_decoded``'s guard, so the parser is made to raise it here."""

    def rejecting(*_args: object, **_kwargs: object) -> html.HtmlElement:
        raise ValueError("Unicode strings with encoding declaration are not supported")

    monkeypatch.setattr(html, "document_fromstring", rejecting)
    with pytest.raises(UnreadableSourceError) as caught:
        html_lines(b"<html><body><main><p>x</p></main></body></html>", "text/html")
    assert str(caught.value) == (
        "HTML did not parse: Unicode strings with encoding declaration are not supported"
    )


def test_pdf_pages_join_line_by_line_in_page_order() -> None:
    pdf = pdf_pages(("§ 1 Formål", "Forskriften gjelder"), ("§ 2 Gebyr", "Gebyret er kr 100"))
    assert pdf_lines(pdf) == ("§ 1 Formål", "Forskriften gjelder", "§ 2 Gebyr", "Gebyret er kr 100")


def test_pdf_whose_startxref_misses_the_xref_table_is_still_read() -> None:
    """A wrong ``startxref`` is common in served PDFs; the reader repairs it, as viewers do."""
    broken = minimal_pdf().replace(b"startxref\n", b"startxref\n1")
    assert broken != minimal_pdf()
    assert pdf_lines(broken) == (*REGULATION_LINES,)


def test_pdf_that_does_not_parse_says_why() -> None:
    with pytest.raises(UnreadableSourceError) as caught:
        pdf_lines(b"%PDF-1.4 this is not a pdf")
    assert str(caught.value).startswith("PDF did not parse: ")


def test_docx_internal_entity_is_never_expanded() -> None:
    document = (
        f'<!DOCTYPE w:document [<!ENTITY k "Eksempel kommune">]><w:document xmlns:w="{_W}">'
        "<w:body><w:p><w:r><w:t>Forskrift om gebyr, &k;</w:t></w:r></w:p></w:body></w:document>"
    )
    lines = docx_lines(docx_with_document(document.encode()))
    assert all("Eksempel kommune" not in line for line in lines)


def test_docx_keeps_a_blank_run_beside_a_comment_as_the_word_space() -> None:
    runs = "<w:r><w:t>om</w:t><w:t> <!--x--></w:t><w:t>gebyr</w:t></w:r>"
    assert docx_lines(
        _docx_body(f"<w:p><w:r><w:t>Forskrift</w:t></w:r></w:p><w:p>{runs}</w:p>")
    ) == (
        "Forskrift",
        "om gebyr",
    )


def test_docx_empty_run_adds_no_text() -> None:
    paragraph = "<w:p><w:r><w:t>Forskrift om</w:t><w:t/><w:t> gebyr</w:t></w:r></w:p>"
    assert docx_lines(_docx_body(paragraph)) == ("Forskrift om gebyr",)


def test_docx_tracked_deletion_and_field_codes_are_not_text() -> None:
    paragraph = (
        "<w:p><w:r><w:t>Gebyret er kr </w:t></w:r>"
        "<w:del><w:r><w:delText>100</w:delText></w:r></w:del>"
        "<w:r><w:instrText>PAGE</w:instrText></w:r><w:r><w:t>200</w:t></w:r></w:p>"
    )
    assert docx_lines(_docx_body(paragraph)) == ("Gebyret er kr 200",)


def test_docx_that_does_not_parse_says_why() -> None:
    with pytest.raises(UnreadableSourceError) as caught:
        docx_lines(docx_with_document(b"<w:document><w:body>"))
    assert str(caught.value).startswith("DOCX word/document.xml did not parse: ")


def test_docx_that_is_not_a_zip_says_why() -> None:
    with pytest.raises(UnreadableSourceError) as caught:
        docx_lines(b"not a zip")
    assert str(caught.value).startswith("DOCX is not a readable zip: ")


def test_docx_document_part_of_exactly_the_cap_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = minimal_docx()
    size = len(_document_xml(payload))
    monkeypatch.setattr("lovspor.promotion.source_text.MAX_DOCX_PART_BYTES", size)
    assert docx_lines(payload) == (*REGULATION_LINES,)


def _document_xml(payload: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return archive.read("word/document.xml")


# --- page chrome lines (#522) -------------------------------------------------


@pytest.mark.parametrize(
    "chrome",
    [
        "Fant du det du trengte?",
        "Fann du det du trong?",
        "Fann du det du leita etter?",
        "Fant du det du lette etter?",
        "Var denne siden nyttig?",
        "Var denne sida nyttig?",
        "Var informasjonen nyttig?",
        "Sist endra 13.10.2017 08.44",
        "Sist endret: 01.02.2024",
        "Endra 13.10.2017",
        "Del på Facebook",
        "Del på",
        "Del sida",
        "Skriv ut sida",
        "Tilbake til toppen",
        "Gå til toppen",
    ],
)
def test_municipal_cms_chrome_line_is_dropped(chrome: str) -> None:
    page = f"<html><body><main><p>§ 1 Formål</p><p>{chrome}</p></main></body></html>".encode()
    assert html_lines(page, "text/html") == ("§ 1 Formål",)


@pytest.mark.parametrize(
    "line",
    [
        "Publisert av Ola Nordmann",
        "Sist endra av Kari Nordmann 13.10.2017",
        "Del på kostnadene mellom eierne.",
        "Fant du feil i vedtaket, skal du klage.",
    ],
)
def test_bylines_and_prose_that_resemble_chrome_are_kept(line: str) -> None:
    """A byline must still reach the personal-data gate; prose is never chrome."""
    page = f"<html><body><main><p>§ 1 Formål</p><p>{line}</p></main></body></html>".encode()
    assert html_lines(page, "text/html") == ("§ 1 Formål", line)


# --- PDF hard line wraps (#522) -----------------------------------------------


def test_pdf_rejoins_a_wrapped_title_and_wrapped_prose() -> None:
    pdf = minimal_pdf(
        (
            "Forskrift om tidsfrister i saker som krever",
            "oppmålingsforretning, Eksempel kommune,",
            "Innlandet",
            "Fastsatt av kommunestyret 17.06.2024 med hjemmel lov 9.",
            "juni 2023 nr. 30 om grunnskolen § 15-2.",
            "§ 1 Formål og virkeområde",
            "Forskriften gjelder for skolene i Eksempel",
            "kommune. Skolen skal bruke hovedmålet i den skriftlige",
            "opplæringen.",
            "Neste ledd gjelder i perioden 1.",
            "november til 15. mai.",
        )
    )
    assert pdf_lines(pdf) == (
        "Forskrift om tidsfrister i saker som krever oppmålingsforretning, Eksempel kommune, "
        "Innlandet",
        "Fastsatt av kommunestyret 17.06.2024 med hjemmel lov 9. juni 2023 nr. 30 om grunnskolen "
        "§ 15-2.",
        "§ 1 Formål og virkeområde",
        "Forskriften gjelder for skolene i Eksempel kommune. Skolen skal bruke hovedmålet i den "
        "skriftlige opplæringen.",
        "Neste ledd gjelder i perioden 1. november til 15. mai.",
    )


def test_pdf_rejoins_a_year_wrapped_after_a_month() -> None:
    pdf = minimal_pdf(("Fastsatt med hjemmel i lov 9. juni", "2023 nr. 30 § 2-2.", "§ 1 Formål"))
    assert pdf_lines(pdf) == ("Fastsatt med hjemmel i lov 9. juni 2023 nr. 30 § 2-2.", "§ 1 Formål")


def test_pdf_rejoins_a_date_wrapped_before_its_day() -> None:
    pdf = minimal_pdf(("Forskriften trer i kraft", "1. januar 2020.", "§ 2 Gebyr"))
    assert pdf_lines(pdf) == ("Forskriften trer i kraft 1. januar 2020.", "§ 2 Gebyr")


def test_pdf_keeps_headings_list_items_and_new_sentences_apart() -> None:
    lines = (
        "§ 1 Formål",
        "forskriften gjelder hele kommunen.",
        "Kommunen kan gi fritak for:",
        "a) bygning som",
        "brukes til lager,",
        "b) annen bruk,",
        "1. Søknad sendes kommunen,",
        "\u2013 kopi til fylket,",
        "• kopi til eier,",
        "§ 2 Gebyr",
        "Gebyret er kr 100.",
        "Kapittel 2 Klage",
        "Klage sendes kommunen.",
        "2024 er første år.",
    )
    assert pdf_lines(minimal_pdf(lines)) == (
        "§ 1 Formål",
        "forskriften gjelder hele kommunen.",
        "Kommunen kan gi fritak for:",
        "a) bygning som brukes til lager,",
        *lines[5:],
    )


def test_pdf_keeps_an_enactment_line_off_the_title() -> None:
    lines = ("Forskrift om gebyr, Eksempel kommune", "vedtatt av kommunestyret 1.2.2020.", "§ 1")
    assert pdf_lines(minimal_pdf(lines)) == lines


def test_pdf_rejoins_across_a_page_break_and_a_page_number() -> None:
    pdf = pdf_pages(("§ 1 Formål", "Forskriften gjelder for", "Side 1 av 2"), ("hele kommunen.",))
    assert pdf_lines(pdf) == ("§ 1 Formål", "Forskriften gjelder for hele kommunen.")


def test_html_blocks_and_docx_lines_are_never_rejoined() -> None:
    lines = ("§ 1 Formål", "Forskriften gjelder for", "hele kommunen.")
    page = "<html><body><main>" + "".join(f"<p>{line}</p>" for line in lines) + "</main></body>"
    assert html_lines(page.encode(), "text/html") == lines
    assert docx_lines(minimal_docx(lines)) == lines


def test_pdf_rejoining_is_deterministic() -> None:
    pdf = minimal_pdf(
        ("Forskrift om gebyr i Eksempel", "kommune", "§ 1 Formål", "Gebyret", "er 1.")
    )
    assert (
        pdf_lines(pdf)
        == pdf_lines(pdf)
        == (
            "Forskrift om gebyr i Eksempel kommune",
            "§ 1 Formål",
            "Gebyret er 1.",
        )
    )


def test_pdf_rejoins_a_parenthesis_wrapped_after_a_word_but_not_after_a_full_stop() -> None:
    lines = (
        "Fastsatt med hjemmel i lov om eigedomsregistrering",
        "(matrikkellova).",
        "(Endret 2020.)",
    )
    assert pdf_lines(minimal_pdf(lines)) == (
        "Fastsatt med hjemmel i lov om eigedomsregistrering (matrikkellova).",
        "(Endret 2020.)",
    )


@pytest.mark.parametrize(
    "section",
    [
        "§ 2 Kommunen kan kreve gebyr for behandlingen,",
        "§ 4 Vedtaket kan påklages etter forvaltningsloven kap. 6.",
    ],
)
def test_pdf_a_section_line_ending_in_punctuation_is_prose_and_takes_its_wrap(section: str) -> None:
    pdf = minimal_pdf(("§ 1 Formål", section, "jf. forurensningsloven § 34."))
    assert pdf_lines(pdf) == ("§ 1 Formål", f"{section} jf. forurensningsloven § 34.")


def test_pdf_a_section_heading_of_exactly_the_heading_limit_takes_no_wrap() -> None:
    heading = "§ 3 " + "Gebyr for tømming av slamavskillere og tette tanker i hele kommunen " * 2
    heading = heading[:100]
    assert len(heading) == 100 and heading[-1].isalpha()
    pdf = minimal_pdf((heading, "forskriften gjelder alle."))
    assert pdf_lines(pdf) == (heading, "forskriften gjelder alle.")


def test_pdf_a_section_line_over_the_heading_limit_takes_its_wrap() -> None:
    line = ("§ 3 " + "Gebyr for tømming av slamavskillere og tette tanker i hele kommunen " * 2)[
        :101
    ]
    assert line[-1].isalpha()
    pdf = minimal_pdf((line, "gjelder alle."))
    assert pdf_lines(pdf) == (f"{line} gjelder alle.",)


# --- HTML <br> hard line wraps (#523) -----------------------------------------


def _main(body: str) -> bytes:
    return f"<html><body><main>{body}</main></body></html>".encode()


def test_html_rejoins_br_wraps_within_one_paragraph() -> None:
    page = _main(
        "<p>Fastsett av kommunestyret med heimel i forskrift 26. juni 2009<br />"
        "nr. 864 om eigedomsregistrering.</p>"
        "<h2>§ 2. Utsett tidsfrist</h2>"
        "<p>Tidsfristen gjeld ikkje i perioden 1.<br />november til 15. mai. Der vegar er "
        "stengde gjeld<br>ikkje tidsfristen,<br>Kommunen avgjer.</p>"
    )
    assert html_lines(page, "text/html") == (
        "Fastsett av kommunestyret med heimel i forskrift 26. juni 2009 nr. 864 om "
        "eigedomsregistrering.",
        "§ 2. Utsett tidsfrist",
        "Tidsfristen gjeld ikkje i perioden 1. november til 15. mai. Der vegar er stengde "
        "gjeld ikkje tidsfristen, Kommunen avgjer.",
    )


def test_html_keeps_br_breaks_before_headings_list_items_and_new_sentences() -> None:
    lines = (
        "§ 1 Formål",
        "forskrifta gjeld heile kommunen.",
        "Kommunen kan gi fritak for:",
        "a) bygning,",
        "1. søknad,",
        "- kopi til eigar,",
        "§ 2 Gebyr",
        "Gebyret er kr 100.",
        "Klage sendast kommunen.",
        "vedteke av kommunestyret 1.2.2020.",
    )
    assert html_lines(_main("<p>" + "<br>".join(lines) + "</p>"), "text/html") == lines


def test_html_never_rejoins_across_block_elements() -> None:
    page = _main("<p>Forskrifta gjeld for<br>heile</p><p>kommunen.</p><div>og fylket</div>")
    assert html_lines(page, "text/html") == ("Forskrifta gjeld for heile", "kommunen.", "og fylket")


def test_html_br_rejoin_drops_furniture_first_and_keeps_inline_markup() -> None:
    page = _main("<p>Forskrifta gjeld for<br>Del på<br><strong>heile</strong> kommunen.</p>")
    assert html_lines(page, "text/html") == ("Forskrifta gjeld for heile kommunen.",)


def test_html_br_rejoining_is_deterministic() -> None:
    page = _main("<p>Forskrift om gebyr i<br>kommunen<br>§ 1 Formål<br>gebyret er 1.</p>")
    assert (
        html_lines(page, "text/html")
        == html_lines(page, "text/html")
        == ("Forskrift om gebyr i kommunen", "§ 1 Formål", "gebyret er 1.")
    )


def test_html_line_separator_in_source_text_is_a_space_not_a_br() -> None:
    page = _main("<p>Kommunen\u2028Fylket</p>")
    assert html_lines(page, "text/html") == ("Kommunen Fylket",)


def test_a_dotted_section_number_starts_a_line_of_its_own() -> None:
    lines = ("Tapet dekkes etter punkt 3.2", "8.4 Avkorting i ytelser dekkes fullt ut.")
    assert html_lines(_main("<p>" + "<br>".join(lines) + "</p>"), "text/html") == lines
    assert pdf_lines(minimal_pdf(lines)) == lines


def test_a_dotted_date_after_a_word_is_still_a_wrap() -> None:
    lines = ("Forskrifta trer i kraft", "1.1.2010.")
    page = _main("<p>" + "<br>".join(lines) + "</p>")
    assert html_lines(page, "text/html") == ("Forskrifta trer i kraft 1.1.2010.",)


def test_html_text_after_a_closing_block_is_kept_on_a_line_of_its_own() -> None:
    page = _main("<div><p>§ 1 Formål</p>Forskrifta gjeld heile kommunen.</div>")
    assert html_lines(page, "text/html") == ("§ 1 Formål", "Forskrifta gjeld heile kommunen.")
