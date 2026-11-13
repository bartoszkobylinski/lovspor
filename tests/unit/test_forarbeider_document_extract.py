"""FC3: the whole chapter as ordered spans and explicit gaps, never withheld whole (#738)."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.document_extract import SourcePage, extract_document
from lovspor.forarbeider.document_models import (
    EXTRACTOR_VERSION,
    DocumentExtraction,
    Gap,
    GapCode,
    Part,
    Span,
    extraction_text,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason

FIXTURE = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "chapter-l.html"
URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=2"
WHEN = datetime(2100, 4, 1, 12, tzinfo=UTC)
HEAD = '<div id="kap2" data-longdoc-section><h1 class="K-DEL-KAPITTEL">2 Bakgrunn</h1></div>'
_BODY = re.compile(r"<!--BODY-->.*<!--/BODY-->", re.DOTALL)


def page(body: str | None = None) -> bytes:
    text = FIXTURE.read_text(encoding="utf-8")
    return (text if body is None else _BODY.sub(lambda _: body, text)).encode()


def section(inner: str, ident: str = "kap2-1") -> str:
    return f'<div id="{ident}" data-longdoc-section>{inner}</div>'


def extract(body: str | None = None) -> DocumentExtraction:
    return extract_document([SourcePage(payload=page(body), url=URL, retrieved_at=WHEN)])


def part(body: str | None = None) -> Part:
    (only,) = extract(body).parts
    return only


def spans(body: str | None = None) -> list[Span]:
    return [entry for entry in part(body).entries if isinstance(entry, Span)]


def gaps(body: str | None = None) -> list[Gap]:
    return [entry for entry in part(body).entries if isinstance(entry, Gap)]


def texts(body: str | None = None) -> list[str]:
    return [span.text for span in spans(body)]


QUOTED = HEAD + section(
    "<h2>2.1 Høringen</h2>"
    "<p>Departementet sendte forslaget på høring.</p>"
    "<blockquote><p>Første sitat fra en høringsinstans.</p></blockquote>"
    "<blockquote><p>Andre sitat, en annen instans.</p></blockquote>"
    "<p>Departementet er enig.</p>"
)


# --- ordinary chapters -------------------------------------------------------------


def test_ordinary_chapter_keeps_every_block_in_source_order() -> None:
    assert [(span.kind, span.level, span.text) for span in spans()] == [
        ("heading", 1, "2 Bakgrunn"),
        ("heading", 2, "2.1 Innledning"),
        ("paragraph", 0, "Departementet viser til lov om prøving § 14 første ledd, jf. § 24."),
        ("paragraph", 0, "Første linje andre linje, med æøå og «sitattegn» \u2013 og §\u00a03."),
        ("list_item", 1, "første punkt"),
        ("list_item", 1, "andre punkt"),
        ("list_item", 2, "a) underpunkt"),
        ("heading", 3, "2.2 Vurdering"),
        ("paragraph", 0, "Avsluttende vurdering."),
    ]
    assert gaps() == []
    assert part().coverage.exact


def test_same_text_as_the_existing_extractor_where_it_writes_a_body() -> None:
    blocks = build_chapter_result(page(), URL, WHEN).blocks
    assert blocks is not None
    assert texts() == [block.text for block in blocks]


def test_document_identity_source_digest_and_extractor_version() -> None:
    document = extract()
    assert document.document_id == "regjeringen-9000001"
    assert (document.document_type, document.session, document.proposition_number) == (
        "prop-l",
        "2099-2100",
        901,
    )
    assert document.extractor_version == EXTRACTOR_VERSION
    (only,) = document.parts
    assert only.id == "regjeringen-9000001-ch2"
    assert only.source_page_url == URL
    assert only.source_sha256 == hashlib.sha256(page()).hexdigest()
    assert (only.chapter_number, only.title) == (2, "2 Bakgrunn")


def test_every_span_carries_its_digest_section_and_source_location() -> None:
    for span in spans():
        assert span.text_sha256 == hashlib.sha256(span.text.encode()).hexdigest()
        assert span.section_id in {"kap2", "kap2-1", "kap2-2"}
        assert span.locator.startswith(f'div[@id="{span.section_id}"]/')
        assert span.source_line is not None
    assert spans()[2].locator == 'div[@id="kap2-1"]/p[1]'


# --- quotations ------------------------------------------------------------------


def test_quotations_never_suppress_the_official_text_around_them() -> None:
    assert texts(QUOTED) == [
        "2 Bakgrunn",
        "2.1 Høringen",
        "Departementet sendte forslaget på høring.",
        "Første sitat fra en høringsinstans.",
        "Andre sitat, en annen instans.",
        "Departementet er enig.",
    ]
    assert gaps(QUOTED) == []


def test_the_existing_extractor_withholds_that_chapter_whole() -> None:
    """The baseline FC3 lifts: A2b keeps reading this result unchanged."""
    assert build_chapter_result(page(QUOTED), URL, WHEN).blocks is None


def test_adjacent_quotations_are_separate_groups() -> None:
    groups = [(span.text[:5], span.quote_group) for span in spans(QUOTED)]
    assert groups[2:] == [("Depar", None), ("Først", 1), ("Andre", 2), ("Depar", None)]


def test_nested_quotation_stays_in_its_outer_group_one_level_deeper() -> None:
    body = HEAD + section(
        "<blockquote><p>Ytre.</p><blockquote><p>Indre.</p></blockquote><p>Ytre igjen.</p>"
        "</blockquote><blockquote>Neste gruppe, uten avsnitt.</blockquote>"
    )
    assert [(span.text, span.quote_group, span.quote_depth) for span in spans(body)[1:]] == [
        ("Ytre.", 1, 1),
        ("Indre.", 1, 2),
        ("Ytre igjen.", 1, 1),
        ("Neste gruppe, uten avsnitt.", 2, 1),
    ]


def test_consultation_excerpt_with_list_inside_a_quotation() -> None:
    body = HEAD + section(
        "<blockquote><p>Instansen skriver:</p><ul><li>punkt en</li><li>punkt to</li></ul>"
        "</blockquote>"
    )
    shaped = [(span.kind, span.level, span.quote_group) for span in spans(body)[1:]]
    assert shaped == [("paragraph", 0, 1), ("list_item", 1, 1), ("list_item", 1, 1)]


# --- lists, headings, repeats, ids ---------------------------------------------------


def test_adjacent_lists_are_separate_groups_and_items_keep_depth() -> None:
    body = HEAD + section(
        "<ul><li>a</li><li><p>b</p><ul><li>b1</li></ul></li></ul><ul><li>c</li></ul>"
    )
    shaped = [(span.text, span.level, span.list_group, span.list_item) for span in spans(body)[1:]]
    assert shaped == [("a", 1, 1, 1), ("b", 1, 1, 2), ("b1", 2, 1, 3), ("c", 1, 2, 1)]


def test_paragraphs_of_one_item_are_told_from_two_items() -> None:
    body = HEAD + section("<ul><li><p>a1</p><p>a2</p></li><li><p>b</p></li></ul>")
    shaped = [(span.text, span.list_group, span.list_item) for span in spans(body)[1:]]
    assert shaped == [("a1", 1, 1), ("a2", 1, 1), ("b", 1, 2)]


def test_list_inside_a_quotation_inside_a_list_is_its_own_group() -> None:
    body = HEAD + section("<ul><li><blockquote><ul><li>x</li></ul></blockquote></li></ul>")
    (span,) = spans(body)[1:]
    assert (span.list_group, span.list_item, span.level, span.quote_group) == (2, 1, 1, 1)


def test_repeated_paragraphs_are_kept_each_with_its_own_id() -> None:
    body = HEAD + section("<p>Samme.</p><p>Samme.</p>") + section("<p>Samme.</p>", "kap2-2")
    same = [span for span in spans(body) if span.text == "Samme."]
    assert len(same) == 3
    assert [span.id for span in same] == [
        "regjeringen-9000001-ch2/kap2-1/1",
        "regjeringen-9000001-ch2/kap2-1/2",
        "regjeringen-9000001-ch2/kap2-2/1",
    ]
    assert len({span.text_sha256 for span in same}) == 1


def test_an_inserted_section_leaves_ids_in_other_sections_unchanged() -> None:
    rest = section("<p>A.</p>") + section("<p>B.</p>", "kap2-2")
    before = {span.text: span.id for span in spans(HEAD + rest)}
    after = {span.text: span.id for span in spans(HEAD + section("<p>Ny.</p>", "kap2-0") + rest)}
    assert after["A."] == before["A."]
    assert after["B."] == before["B."]


def test_duplicate_section_ids_are_a_gap_and_ids_stay_unique() -> None:
    body = HEAD + section("<p>en</p>") + section("<p>to</p>")
    assert [gap.code for gap in gaps(body)] == [GapCode.DUPLICATE_SECTION_ID]
    ids = [span.id for span in spans(body)]
    assert len(ids) == len(set(ids))
    assert texts(body)[1:] == ["en", "to"]


# --- unsupported structures become gaps with their text kept ----------------------


@pytest.mark.parametrize(
    ("markup", "code"),
    [
        ("<table><tr><td>celle</td></tr></table>", GapCode.TABLE),
        ('<div class="K-TBL"><p>tabelltekst</p></div>', GapCode.TABLE),
        ('<div class="footnotecontainer"><p>1 fotnote</p></div>', GapCode.FOOTNOTE),
        ('<div class="K-NOTE-FOTNOTE"><p>fotnote</p></div>', GapCode.FOOTNOTE),
        ("<figure><figcaption>Figur 1</figcaption></figure>", GapCode.FIGURE),
        ('<div class="K-DEL-VEDLEGG"><p>vedleggstekst</p></div>', GapCode.ATTACHMENT),
        ("<ol><li>nummerert</li></ol>", GapCode.UNSUPPORTED_STRUCTURE),
        ('<div class="ukjent"><p>innpakket</p></div>', GapCode.UNSUPPORTED_STRUCTURE),
        ('<p hidden="">skjult</p>', GapCode.HIDDEN_CONTENT),
    ],
)
def test_unsupported_structure_is_one_gap_and_the_rest_of_the_part_is_kept(
    markup: str, code: GapCode
) -> None:
    body = HEAD + section(f"<p>Før.</p>{markup}<p>Etter.</p>")
    (gap,) = gaps(body)
    assert gap.code is code
    assert gap.scope == "span"
    assert gap.locator is not None
    assert texts(body) == ["2 Bakgrunn", "Før.", "Etter."]
    assert part(body).coverage.exact


def test_gap_keeps_the_unstructured_text_it_could_not_shape() -> None:
    """As served, whitespace collapsed: no cell separator is invented (shaping tables is FC4)."""
    body = HEAD + section("<table><tr><td>celle én</td>\n<td>celle to</td></tr></table>")
    (gap,) = gaps(body)
    assert gap.text == "celle én celle to"
    tight = HEAD + section("<table><tr><td>en</td><td>to</td></tr></table>")
    assert gaps(tight)[0].text == "ento"


def test_image_is_a_gap_without_invented_text() -> None:
    body = HEAD + section('<p>Se figuren.</p><img src="/x.png" alt="Diagram">')
    (gap,) = gaps(body)
    assert (gap.code, gap.text, gap.detail) == (GapCode.IMAGE, None, "img")


def test_inline_element_outside_the_allowlist_keeps_its_text_in_the_span() -> None:
    body = HEAD + section("<p>Før <code>kode</code> etter.</p>")
    assert texts(body)[1] == "Før kode etter."
    (gap,) = gaps(body)
    assert (gap.code, gap.text, gap.detail) == (GapCode.UNSUPPORTED_INLINE, None, "code")
    assert part(body).coverage.exact


def test_text_outside_any_known_element_is_a_gap_not_dropped() -> None:
    body = HEAD + section("løs tekst<p>Avsnitt.</p>")
    (gap,) = gaps(body)
    assert (gap.code, gap.text) == (GapCode.BARE_TEXT, "løs tekst")
    assert part(body).coverage.exact


# --- malformed and missing regions ---------------------------------------------------


def test_repaired_markup_keeps_spans_and_records_a_part_gap() -> None:
    body = HEAD + section("<p>en<p>to")
    assert texts(body) == ["2 Bakgrunn", "en", "to"]
    assert GapCode.MALFORMED_MARKUP in {gap.code for gap in gaps(body)}
    assert next(gap for gap in gaps(body) if gap.code is GapCode.MALFORMED_MARKUP).scope == "part"


def test_missing_region_is_an_empty_part_with_a_gap() -> None:
    raw = page().replace(b"data-longdoc-content data-longdoc", b"data-longdoc")
    (only,) = extract_document([SourcePage(payload=raw, url=URL, retrieved_at=WHEN)]).parts
    assert [(entry.code, entry.scope) for entry in only.entries] == [
        (GapCode.MISSING_REGION, "part")
    ]
    assert not only.coverage.exact


def test_title_heading_that_does_not_match_navigation_is_a_boundary_gap() -> None:
    body = '<div id="kap2" data-longdoc-section><h1>2 Noe annet</h1></div>' + section("<p>x</p>")
    assert GapCode.BOUNDARY_UNCONFIRMED in {gap.code for gap in gaps(body)}
    assert texts(body) == ["2 Noe annet", "x"]


# --- document level --------------------------------------------------------------------


def test_chapters_in_the_navigation_without_a_page_are_not_fetched_gaps() -> None:
    document = extract()
    assert [chapter.chapter_number for chapter in document.chapters_listed] == [1, 2, 3, 4]
    not_fetched = [gap.detail for gap in document.gaps if gap.code is GapCode.NOT_FETCHED]
    assert not_fetched == ["ch1", "ch3", "ch4"]
    assert all(gap.scope == "document" for gap in document.gaps)


def test_a_refused_page_is_a_document_gap_not_a_silent_skip() -> None:
    good = SourcePage(payload=page(), url=URL, retrieved_at=WHEN)
    bad = SourcePage(payload=b"<html></html>", url=URL.replace("ch=2", "ch=3"), retrieved_at=WHEN)
    document = extract_document([good, bad])
    refused = [gap for gap in document.gaps if gap.code is GapCode.PAGE_REFUSED]
    assert len(refused) == 1
    assert refused[0].locator == bad.url


def test_the_same_page_twice_is_one_part() -> None:
    one = SourcePage(payload=page(), url=URL, retrieved_at=WHEN)
    assert extract_document([one, one]).parts == extract_document([one]).parts


def test_two_different_pages_for_one_chapter_are_a_conflict_gap() -> None:
    first = SourcePage(payload=page(), url=URL, retrieved_at=WHEN)
    second = SourcePage(payload=page(QUOTED), url=URL, retrieved_at=WHEN)
    document = extract_document([first, second])
    assert document.parts == ()
    assert [gap.code for gap in document.gaps if gap.detail == "ch2"] == [GapCode.CONFLICTING_PAGES]
    assert extract_document([second, first]) == document


def test_no_page_proving_the_document_is_refused() -> None:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        extract_document([SourcePage(payload=b"<html></html>", url=URL, retrieved_at=WHEN)])
    assert raised.value.reason is RefusalReason.SOURCE_IDENTITY_MISSING


_PAGING = re.compile(r'href="([^"]*)\?ch=\d+"(\s*)data-longdoc-(prev|next)')


def chapter_page(number: int, title: str, last: int = 4) -> SourcePage:
    """The fixture as chapter ``number``: paging links either side, none past ``last``."""

    def paging(match: re.Match[str]) -> str:
        target = number - 1 if match[3] == "prev" else number + 1
        attribute = f"data-longdoc-{match[3]}" if target <= last else "data-x"
        return f'href="{match[1]}?ch={target}"{match[2]}{attribute}'

    body = (
        f'<div id="kap{number}" data-longdoc-section><h1>{title}</h1></div>'
        f'<div id="kap{number}-1" data-longdoc-section><p>Tekst i kapittel {number}.</p></div>'
    )
    raw = _PAGING.sub(paging, page(body).decode()).encode()
    return SourcePage(payload=raw, url=URL.replace("ch=2", f"ch={number}"), retrieved_at=WHEN)


def test_parts_follow_chapter_order_whatever_the_input_order() -> None:
    pages = [chapter_page(4, "1 Oppdiktet vedlegg"), chapter_page(3, "Tilråding")]
    document = extract_document([*pages, SourcePage(payload=page(), url=URL, retrieved_at=WHEN)])
    assert [item.chapter_number for item in document.parts] == [2, 3, 4]
    assert [gap.detail for gap in document.gaps] == ["ch1"]
    assert extract_document(list(reversed(pages))).parts == extract_document(pages).parts


def test_annex_chapter_is_marked_from_the_navigation_and_still_extracted() -> None:
    document = extract_document([chapter_page(4, "1 Oppdiktet vedlegg")])
    (annex,) = document.parts
    assert annex.annex is True
    assert [span.text for span in annex.entries if isinstance(span, Span)] == [
        "1 Oppdiktet vedlegg",
        "Tekst i kapittel 4.",
    ]
    assert part().annex is False


# --- determinism and no silent loss -----------------------------------------------


def test_replay_is_byte_identical() -> None:
    assert extraction_text(extract(QUOTED)) == extraction_text(extract(QUOTED))


def test_written_extraction_round_trips() -> None:
    document = extract(QUOTED)
    assert DocumentExtraction.model_validate_json(extraction_text(document)) == document
    assert json.loads(extraction_text(document))["record_type"] == "forarbeid_document_extraction"


def test_coverage_counts_every_non_space_character_once() -> None:
    body = HEAD + section("<p>abc</p><table><tr><td>de</td></tr></table><p>f g</p>")
    coverage = part(body).coverage
    assert (coverage.source_characters, coverage.span_characters, coverage.gap_characters) == (
        len("2Bakgrunnabcdefg"),
        len("2Bakgrunnabcfg"),
        len("de"),
    )
    assert coverage.exact


def test_raw_source_bytes_are_not_changed() -> None:
    raw = page(QUOTED)
    copy = bytes(raw)
    extract_document([SourcePage(payload=raw, url=URL, retrieved_at=WHEN)])
    assert raw == copy


def test_span_text_digest_must_match_its_text() -> None:
    span = spans()[0]
    with pytest.raises(ValueError, match="text_sha256"):
        Span.model_validate(span.model_dump() | {"text": "endret"})


def test_span_ids_must_be_unique_in_a_document() -> None:
    document = extract()
    entries = document.parts[0].entries
    doubled = document.parts[0].model_copy(update={"entries": (*entries, entries[-1])})
    with pytest.raises(ValueError, match="unique"):
        DocumentExtraction.model_validate(document.model_dump() | {"parts": (doubled,)})


def test_pages_of_two_documents_are_refused() -> None:
    other = page().replace(b"9000001", b"9000002")
    pages = [
        SourcePage(payload=page(), url=URL, retrieved_at=WHEN),
        SourcePage(payload=other, url=URL.replace("9000001", "9000002"), retrieved_at=WHEN),
    ]
    with pytest.raises(ForarbeiderRefusedError) as raised:
        extract_document(pages)
    assert raised.value.reason is RefusalReason.CONFLICTING_IDENTITY


def test_hidden_inline_text_stays_in_the_span_and_is_noted() -> None:
    body = HEAD + section('<p>Synlig <span aria-hidden="true">skjult</span> slutt.</p>')
    assert texts(body)[1] == "Synlig skjult slutt."
    assert [(gap.code, gap.text) for gap in gaps(body)] == [(GapCode.HIDDEN_CONTENT, None)]


def test_span_ids_and_text_never_depend_on_retrieval_time() -> None:
    later = SourcePage(payload=page(QUOTED), url=URL, retrieved_at=WHEN.replace(year=2101))
    (moved,) = extract_document([later]).parts
    (base,) = extract(QUOTED).parts
    assert moved.entries == base.entries
    assert moved.retrieved_at != base.retrieved_at
