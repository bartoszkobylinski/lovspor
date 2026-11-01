"""One chapter of a saved page: what is extracted, what is held, what is withheld (#738)."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lxml import html

from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_body import ChapterBody, accounted_for
from lovspor.forarbeider.chapter_models import (
    ChapterResult,
    HoldReason,
    TextBlock,
    chapter_text,
    result_text,
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


def section(inner: str) -> str:
    return f'{HEAD}<div id="kap2-1" data-longdoc-section>{inner}</div>'


def result(body: str | None = None, url: str = URL) -> ChapterResult:
    return build_chapter_result(page(body), url, WHEN)


def test_text_keeps_sequence_structure_and_characters() -> None:
    assert result().blocks == (
        TextBlock(kind="heading", level=1, text="2 Bakgrunn"),
        TextBlock(kind="heading", level=2, text="2.1 Innledning"),
        TextBlock(
            kind="paragraph",
            text="Departementet viser til lov om prøving § 14 første ledd, jf. § 24.",
        ),
        TextBlock(
            kind="paragraph",
            text="Første linje andre linje, med æøå og «sitattegn» \u2013 og §\u00a03.",
        ),
        TextBlock(kind="list_item", level=1, text="første punkt"),
        TextBlock(kind="list_item", level=1, text="andre punkt"),
        TextBlock(kind="list_item", level=2, text="a) underpunkt"),
        TextBlock(kind="heading", level=3, text="2.2 Vurdering"),
        TextBlock(kind="paragraph", text="Avsluttende vurdering."),
    )


def test_page_chrome_stays_out() -> None:
    written = result_text(result())
    for chrome in ("Forrige kapittel", "Neste kapittel", "Du er her", "Dokumenter"):
        assert chrome not in written
    assert "Prop. 901 L (2099" not in "".join(block.text for block in result().blocks or ())


def test_identity_and_rights_are_stated() -> None:
    held = result()
    assert held.id == "regjeringen-9000001-ch2"
    assert held.source_page_url == URL
    assert held.source_url == URL.removesuffix("?ch=2")
    assert (held.document_type, held.session, held.proposition_number) == (
        "prop-l",
        "2099-2100",
        901,
    )
    assert (held.chapter_number, held.chapter_title) == (2, "2 Bakgrunn")
    assert held.title == "Strukturell testproposisjon om et oppdiktet lovforslag"
    assert held.ministry == "Testdepartementet"
    assert held.retrieved_at == WHEN
    assert (held.normative, held.source_license) == (False, None)
    assert held.rights_basis == "åndsverkloven § 14"
    assert (held.extractor_version, held.normalizer_version) == (1, 1)
    assert held.source_sha256 == hashlib.sha256(page()).hexdigest()


def test_plain_chapter_is_still_held() -> None:
    held = result()
    assert held.release_state == "held"
    assert held.hold_reasons == (HoldReason.REVIEW_REQUIRED,)


def test_text_hash_covers_the_normalized_text() -> None:
    held = result()
    assert held.blocks is not None
    expected = hashlib.sha256(chapter_text(held.blocks).encode()).hexdigest()
    assert held.text_sha256 == expected


def test_same_input_gives_identical_bytes() -> None:
    assert result_text(result()) == result_text(result())


def test_wrapper_markup_does_not_move_the_text_hash() -> None:
    plain = section("<h2>2.1 Innledning</h2><p>Tekst her.</p>")
    wrapped = section('<h2  class="annet">2.1\n Innledning</h2>\n<p id="x">Tekst\n   her. </p>')
    first, second = result(plain), result(wrapped)
    assert first.text_sha256 == second.text_sha256
    assert first.source_sha256 != second.source_sha256


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("Tekst her.", "Tekst der."),
        ("Tekst\u00a0her.", "Tekst her."),
        ("Tekst her.", "<b>Tekst</b> her.</p><p>Mer."),
    ],
)
def test_a_change_of_text_moves_the_hash(before: str, after: str) -> None:
    first = result(section(f"<p>{before}</p>"))
    second = result(section(f"<p>{after}</p>"))
    assert first.text_sha256 != second.text_sha256


def test_kind_and_level_are_part_of_the_hash() -> None:
    assert chapter_text((TextBlock(kind="paragraph", text="x"),)) != chapter_text(
        (TextBlock(kind="heading", level=2, text="x"),)
    )
    assert chapter_text((TextBlock(kind="list_item", level=1, text="x"),)) != chapter_text(
        (TextBlock(kind="list_item", level=2, text="x"),)
    )


WITHHELD = [
    ("annex class", '<h2 class="K-DEL-VEDLEGG">Tillegg</h2><p>x</p>', HoldReason.ANNEX),
    ("annex heading", "<h2>Vedlegg 1 Noe</h2><p>x</p>", HoldReason.ANNEX),
    ("annex wrapper", '<div class="K-DEL-VEDLEGG"><p>x</p></div>', HoldReason.ANNEX),
    ("image", '<p>x <img src="a.png" alt="a"></p>', HoldReason.IMAGE),
    ("picture", "<picture></picture><p>x</p>", HoldReason.IMAGE),
    ("svg", "<svg></svg><p>x</p>", HoldReason.IMAGE),
    ("table", '<div class="K-TBL"><table><tr><td>1</td></tr></table></div>', HoldReason.TABLE),
    ("figure", "<figure><figcaption>x</figcaption></figure>", HoldReason.FIGURE),
    ("video", "<video></video><p>x</p>", HoldReason.FIGURE),
    ("iframe", "<iframe></iframe><p>x</p>", HoldReason.FIGURE),
    (
        "footnote",
        '<p>x<span class="K-NOTE-FOTNOTE krnl_footnote"><a href="#fn1">1</a></span></p>',
        HoldReason.FOOTNOTES,
    ),
    ("note list", '<p>x</p><div class="footnotecontainer"><p>n</p></div>', HoldReason.FOOTNOTES),
    ("unknown block", "<section><p>x</p></section>", HoldReason.DAMAGED_DOM),
    ("unknown inline", "<p>x <mark>y</mark></p>", HoldReason.DAMAGED_DOM),
    ("unknown wrapper class", '<div class="sidebar"><p>x</p></div>', HoldReason.DAMAGED_DOM),
    ("script", "<script>x()</script><p>x</p>", HoldReason.DAMAGED_DOM),
    ("ordered list", "<ol><li><p>x</p></li></ol>", HoldReason.DAMAGED_DOM),
    ("bare text", "bare<p>x</p>", HoldReason.DAMAGED_DOM),
    ("bare tail", "<p>x</p>bare", HoldReason.DAMAGED_DOM),
    ("bare text in list", "<ul>bare<li><p>x</p></li></ul>", HoldReason.DAMAGED_DOM),
    ("bare tail in list", "<ul><li><p>x</p></li>bare</ul>", HoldReason.DAMAGED_DOM),
    ("bare text in item", "<ul><li>bare<p>x</p></li></ul>", HoldReason.DAMAGED_DOM),
    ("block in quote", "<blockquote><table></table></blockquote>", HoldReason.TABLE),
]


@pytest.mark.parametrize(("name", "inner", "reason"), WITHHELD, ids=[w[0] for w in WITHHELD])
def test_what_cannot_be_reproduced_withholds_the_body(
    name: str, inner: str, reason: HoldReason
) -> None:
    held = result(section(inner))
    assert reason in held.hold_reasons, name
    assert HoldReason.REVIEW_REQUIRED in held.hold_reasons
    assert held.blocks is None
    assert held.text_sha256 is None
    assert "x" not in json.loads(result_text(held))["hold_reasons"]


BOUNDARY = [
    ("no region", "", '<div class="longdoc-content">x</div>', HoldReason.DAMAGED_DOM),
    (
        "two regions",
        "",
        f"<div data-longdoc-content>{HEAD}</div><div data-longdoc-content>{HEAD}</div>",
        HoldReason.AMBIGUOUS_BOUNDARY,
    ),
]


def _replacing_region(region: str) -> bytes:
    text = FIXTURE.read_text(encoding="utf-8")
    pattern = re.compile(
        r'<div class="longdoc-content" data-longdoc-content.*?<!--/BODY-->\s*</div>', re.DOTALL
    )
    return pattern.sub(lambda _: region, text).encode()


@pytest.mark.parametrize(("name", "_", "region", "reason"), BOUNDARY, ids=[b[0] for b in BOUNDARY])
def test_the_body_region_must_be_exactly_one(
    name: str, _: str, region: str, reason: HoldReason
) -> None:
    held = build_chapter_result(_replacing_region(region), URL, WHEN)
    assert reason in held.hold_reasons, name
    assert held.blocks is None


def test_a_region_outside_main_is_not_the_body() -> None:
    text = FIXTURE.read_text(encoding="utf-8").replace("<main", "<div").replace("</main>", "</div>")
    with pytest.raises(ForarbeiderRefusedError):
        build_chapter_result(text.encode(), URL, WHEN)


@pytest.mark.parametrize(
    ("name", "body", "reason"),
    [
        ("empty", "", HoldReason.INCOMPLETE_EXTRACTION),
        ("heading only", HEAD, HoldReason.INCOMPLETE_EXTRACTION),
        ("no heading", '<div id="a" data-longdoc-section><p>x</p></div>', HoldReason.DAMAGED_DOM),
        (
            "other title",
            '<div id="a" data-longdoc-section><h1>3 Noe annet</h1><p>x</p></div>',
            HoldReason.AMBIGUOUS_BOUNDARY,
        ),
        ("two headings", section("<h1>2 Bakgrunn</h1><p>x</p>"), HoldReason.AMBIGUOUS_BOUNDARY),
        (
            "duplicate section",
            f'{HEAD}<div id="kap2" data-longdoc-section><p>x</p></div>',
            HoldReason.AMBIGUOUS_BOUNDARY,
        ),
        (
            "section without id",
            HEAD + "<div data-longdoc-section><p>x</p></div>",
            HoldReason.AMBIGUOUS_BOUNDARY,
        ),
        ("loose child", HEAD + "<p>x</p>", HoldReason.AMBIGUOUS_BOUNDARY),
        ("loose text", HEAD + "bare", HoldReason.AMBIGUOUS_BOUNDARY),
        ("loose first text", "bare" + HEAD, HoldReason.AMBIGUOUS_BOUNDARY),
        (
            "nested region",
            section("<div data-longdoc-content><p>x</p></div>"),
            HoldReason.AMBIGUOUS_BOUNDARY,
        ),
    ],
)
def test_empty_or_ambiguous_body_is_withheld(name: str, body: str, reason: HoldReason) -> None:
    held = result(body)
    assert reason in held.hold_reasons, name
    assert held.blocks is None


def test_comments_are_not_text() -> None:
    held = result(section("<!-- skjult --><p>x<!-- skjult -->y</p><!-- skjult -->"))
    assert held.blocks is not None
    assert held.blocks[-1].text == "xy"
    assert held.hold_reasons == (HoldReason.REVIEW_REQUIRED,)


def test_a_comment_tail_is_not_lost() -> None:
    held = result(section("<p>x</p><!-- skjult -->bare"))
    assert HoldReason.DAMAGED_DOM in held.hold_reasons
    assert held.blocks is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Høringsinstansene mente noe.", HoldReason.HEARING_PARTICIPANTS),
        ("Oppsummering av HØRINGSSVAR.", HoldReason.HEARING_PARTICIPANTS),
        ("Skriv til ola@example.no snarest.", HoldReason.PERSONAL_DATA),
        ("Ring +47 22 24 90 90 i dag.", HoldReason.PERSONAL_DATA),
        ("Fødselsnummer 01019912345 oppgitt.", HoldReason.PERSONAL_DATA),
        ("Saken gjelder helseopplysninger.", HoldReason.SENSITIVE_DATA),
        ("Tiltalt for straffbare forhold.", HoldReason.SENSITIVE_DATA),
        ("© Oppdiktet Forlag 2099.", HoldReason.THIRD_PARTY_WORK),
        ("Gjengitt med tillatelse fra forfatteren.", HoldReason.THIRD_PARTY_WORK),
    ],
)
def test_candidates_are_named_and_the_body_is_kept_for_review(
    text: str, reason: HoldReason
) -> None:
    held = result(section(f"<p>{text}</p>"))
    assert reason in held.hold_reasons
    assert HoldReason.REVIEW_REQUIRED in held.hold_reasons
    assert held.blocks is not None


def test_a_quotation_is_held_and_kept() -> None:
    held = result(section("<p>Innledning.</p><blockquote>Sitert tekst.</blockquote>"))
    assert HoldReason.QUOTATION in held.hold_reasons
    assert held.blocks is not None
    assert held.blocks[-1] == TextBlock(kind="quote", text="Sitert tekst.")


def test_paragraphs_inside_a_quotation_stay_quoted() -> None:
    held = result(section("<blockquote><p>En.</p><p>To.</p></blockquote>"))
    assert held.blocks is not None
    assert held.blocks[-2:] == (
        TextBlock(kind="quote", text="En."),
        TextBlock(kind="quote", text="To."),
    )


def test_mixed_rights_chapter_keeps_every_reason_in_order() -> None:
    held = result(section('<p>Høringssvar.</p><blockquote>S</blockquote><img src="a.png">'))
    assert held.hold_reasons == (
        HoldReason.REVIEW_REQUIRED,
        HoldReason.IMAGE,
        HoldReason.QUOTATION,
        HoldReason.HEARING_PARTICIPANTS,
    )
    assert held.blocks is None


def test_a_withheld_body_says_nothing_of_the_chapter_text() -> None:
    held = result(section('<p>Ola Nordmann ola@example.no</p><img src="a.png">'))
    assert "Nordmann" not in result_text(held)
    assert "example.no" not in result_text(held)


def test_a_wrong_chapter_is_refused_before_any_extraction() -> None:
    with pytest.raises(ForarbeiderRefusedError) as refused:
        result(url=URL.replace("ch=2", "ch=3"))
    assert refused.value.reason is RefusalReason.PAGE_CHAPTER_UNPROVED


def test_a_cut_page_is_refused() -> None:
    cut = page()[: page().index(b"<!--/BODY-->")]
    with pytest.raises(ForarbeiderRefusedError) as refused:
        build_chapter_result(cut, URL, WHEN)
    assert refused.value.reason is RefusalReason.INCOMPLETE_PAGE


def test_a_landing_url_is_refused() -> None:
    with pytest.raises(ForarbeiderRefusedError) as refused:
        result(url=URL.removesuffix("?ch=2"))
    assert refused.value.reason is RefusalReason.CHAPTER_PAGE_REQUIRED


def test_text_before_the_chapter_heading_is_a_boundary_problem() -> None:
    body = '<div id="a" data-longdoc-section><p>x</p><h1>2 Bakgrunn</h1><p>y</p></div>'
    held = result(body)
    assert HoldReason.AMBIGUOUS_BOUNDARY in held.hold_reasons
    assert held.blocks is None


def test_empty_paragraphs_make_no_block() -> None:
    held = result(section("<p>x</p><p> </p><p>\u00a0</p>"))
    assert held.blocks is not None
    assert held.blocks[-1].text == "x"
    assert held.hold_reasons == (HoldReason.REVIEW_REQUIRED,)


def test_a_line_break_keeps_words_apart() -> None:
    held = result(section("<p>en<br>to</p>"))
    assert held.blocks is not None
    assert held.blocks[-1].text == "en to"


def test_inline_tails_are_kept() -> None:
    held = result(section("<p>a <b>b</b> c <a href='#x'>d</a> e<sup>2</sup></p>"))
    assert held.blocks is not None
    assert held.blocks[-1].text == "a b c d e2"


def test_list_without_paragraphs_keeps_its_depth() -> None:
    held = result(section("<ul><li>en<ul><li>to</li></ul></li></ul>"))
    assert HoldReason.DAMAGED_DOM in held.hold_reasons


def test_plain_list_items_are_read() -> None:
    held = result(section("<ul><li>en</li><li>to <i>tre</i></li></ul>"))
    assert held.blocks is not None
    assert [(b.kind, b.level, b.text) for b in held.blocks[-2:]] == [
        ("list_item", 1, "en"),
        ("list_item", 1, "to tre"),
    ]


def test_a_foreign_element_in_a_list_is_damage() -> None:
    held = result(section("<ul><p>x</p><li><p>y</p></li></ul>"))
    assert HoldReason.DAMAGED_DOM in held.hold_reasons


@pytest.mark.parametrize("inner", ["<ul><li>x</li></ul>", "<h3>x</h3>"])
def test_structure_a_quotation_cannot_keep_is_damage(inner: str) -> None:
    held = result(section(f"<blockquote><p>y</p>{inner}</blockquote>"))
    assert HoldReason.DAMAGED_DOM in held.hold_reasons
    assert held.blocks is None


def test_a_law_draft_wrapper_is_read_through() -> None:
    held = result(
        section(
            '<div class="K-DEL-LOVKAP"><h2>Kapittel 1</h2>'
            '<div class="K-DEL-PARAGRAF"><h2>§ 1</h2>'
            '<div class="K-DEL-LEDD"><p class="K-LEDD">Ledd.</p></div></div></div>'
        )
    )
    assert held.blocks is not None
    assert [b.text for b in held.blocks[-3:]] == ["Kapittel 1", "§ 1", "Ledd."]


def test_a_classless_inner_div_is_damage() -> None:
    assert HoldReason.DAMAGED_DOM in result(section("<div><p>x</p></div>")).hold_reasons


def test_a_section_with_only_a_table_names_the_table_not_damage() -> None:
    held = result(section('<div class="K-TBL"><table><tr><td>1</td></tr></table></div>'))
    assert HoldReason.TABLE in held.hold_reasons
    assert HoldReason.DAMAGED_DOM not in held.hold_reasons


def test_lost_text_is_incomplete_extraction() -> None:
    region = html.fromstring("<div><p>en to</p></div>")
    assert accounted_for(region, [TextBlock(kind="paragraph", text="en to")])
    assert accounted_for(region, [TextBlock(kind="paragraph", text="ento")])
    assert not accounted_for(region, [TextBlock(kind="paragraph", text="en")])
    assert not accounted_for(region, [TextBlock(kind="paragraph", text="to en")])


@pytest.mark.parametrize("marker", ["ved1", "vedlegg-2"])
def test_an_annex_section_id_is_an_annex(marker: str) -> None:
    body = f'{HEAD}<div id="{marker}" data-longdoc-section><p>x</p></div>'
    assert HoldReason.ANNEX in result(body).hold_reasons


def test_a_body_the_schema_refuses_is_a_typed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    false_body = ChapterBody(
        blocks=(TextBlock(kind="paragraph", text="x"),),
        reasons=frozenset({HoldReason.ANNEX}),
    )
    monkeypatch.setattr("lovspor.forarbeider.chapter.read_body", lambda *_: false_body)
    with pytest.raises(ForarbeiderRefusedError) as refused:
        result()
    assert refused.value.reason is RefusalReason.SCHEMA_INVALID
    assert refused.value.detail == URL
