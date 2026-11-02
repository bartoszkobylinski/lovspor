"""Deterministic Markdown from pinned PR2 text blocks (#738, ADR-0018 PR3B-2, M2).

The rendering states what it is (preparatory material, not law), what it is
not (a complete proposition, when a chapter is absent), carries nothing but
the reviewed blocks and the source URLs, and cannot be steered by the text it
renders.
"""

import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from lovspor.forarbeider.chapter_models import ChapterResult, TextBlock, text_hash
from lovspor.forarbeider.corpus_models import DocumentTimes, ForarbeiderManifest
from lovspor.forarbeider.corpus_render import (
    ReleasedChapter,
    Rendering,
    member_of,
    render_document,
    render_index,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from tests.unit.forarbeider_corpus_support import manifest, metadata_of, times
from tests.unit.forarbeider_release_support import PRIVATE_MARKER, REVIEWED, Desk

_DECIDED = datetime.fromisoformat(REVIEWED)


def _rendering(
    desk: Desk, chapters: tuple[int, ...] = (2, 3), **results: ChapterResult
) -> Rendering:
    released = tuple(
        ReleasedChapter(results.get(f"ch{number}", desk.results[number]), _DECIDED)
        for number in chapters
    )
    return Rendering(metadata_of(desk), released, DocumentTimes.model_validate(times()))


def _with_blocks(result: ChapterResult, *blocks: TextBlock) -> ChapterResult:
    return result.model_copy(update={"blocks": blocks, "text_sha256": text_hash(blocks)})


def _front_matter(markdown: str) -> str:
    match = re.match(r"---\n(.*?)\n---\n", markdown, re.DOTALL)
    assert match is not None
    return match[1]


def test_the_same_inputs_render_the_same_bytes(tmp_path: Path) -> None:
    first = render_document(_rendering(Desk(tmp_path / "a")))
    second = render_document(_rendering(Desk(tmp_path / "b")))

    assert first == second
    assert first.endswith("\n")
    assert not first.endswith("\n\n")


def test_front_matter_carries_the_fixed_labels_and_no_licence(tmp_path: Path) -> None:
    markdown = render_document(_rendering(Desk(tmp_path)))
    front = _front_matter(markdown)

    for line in [
        'source_document_type: "forarbeid"',
        "normative: false",
        'source_provider: "regjeringen.no"',
        "source_license: null",
        'rights_basis: "åndsverkloven § 14"',
        'coverage: "partial_curated_excerpt"',
        "asserts_complete_proposition: false",
        "listed_chapters: [1, 2, 3, 4]",
        "released_chapters: [2, 3]",
        "published_at: null",
        "renderer_version: 1",
    ]:
        assert line in front.splitlines(), line
    for word in ("NLOD", "CC0", "CC-BY", "valid_at", "in_force", "lisens"):
        assert word not in markdown


def test_the_reader_is_told_this_is_not_law_and_only_an_excerpt(tmp_path: Path) -> None:
    markdown = render_document(_rendering(Desk(tmp_path)))

    assert "preparatory material, NOT enacted law" in markdown
    assert "Partial curated excerpt" in markdown
    assert "not the complete proposition" in markdown
    assert "does not state whether the proposal was adopted" in markdown


def test_a_chapter_the_source_does_not_list_is_refused(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    metadata = metadata_of(desk)
    only = metadata.model_copy(update={"chapters": metadata.chapters[:1]})
    first = desk.results[2]
    rendering = Rendering(only, (ReleasedChapter(first, _DECIDED),), _rendering(desk).times)

    with pytest.raises(ForarbeiderRefusedError) as caught:
        render_document(rendering)

    assert caught.value.reason is RefusalReason.RENDER_UNSAFE


def test_chapters_keep_source_order_and_their_page_urls(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    markdown = render_document(_rendering(desk))

    second, third = desk.results[2].source_page_url, desk.results[3].source_page_url
    assert f"<{second}>" in markdown and f"<{third}>" in markdown
    assert markdown.index(f"<{second}>") < markdown.index(f"<{third}>")
    assert f"<{desk.results[2].source_url}>" in markdown


def test_headings_and_nested_lists_are_preserved(tmp_path: Path) -> None:
    markdown = render_document(_rendering(Desk(tmp_path), (3,)))

    lines = markdown.splitlines()
    assert "## Tilråding" in lines
    assert "### 2\\.1 Innledning" in lines
    assert "#### 2\\.2 Vurdering" in lines
    assert "- første punkt" in lines
    assert "  - a) underpunkt" in lines


def test_a_chapter_not_released_is_not_rendered(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    markdown = render_document(_rendering(desk, (2,)))

    assert desk.results[3].source_page_url not in markdown
    assert "Tilråding" not in markdown
    assert "released_chapters: [2]" in markdown


@pytest.mark.parametrize(
    "text",
    [
        "[klikk](javascript:alert(1))",
        "![bilde](https://example.org/x.png)",
        "<script>alert(1)</script>",
        "<a href='https://example.org'>x</a>",
        "&lt;tag&gt; &amp;",
        "**fet** _kursiv_ `kode` ~~slett~~",
        "| a | b |",
        "[^1]: fotnote",
        "\\[escaped\\]",
        "$x$ og $$y$$",
    ],
)
def test_inline_markup_in_the_source_text_stays_text(tmp_path: Path, text: str) -> None:
    desk = Desk(tmp_path)
    hostile = _with_blocks(desk.results[2], TextBlock(kind="paragraph", text=text))

    line = _last_line(render_document(_rendering(desk, (2,), ch2=hostile)))

    unescaped = re.sub(r"\\.", "", line)
    for marker in ("[", "]", "<", ">", "&", "*", "_", "`", "~", "|", "$", "!", "\\"):
        assert marker not in unescaped, (marker, line)


def _last_line(markdown: str) -> str:
    """The hostile paragraph is the chapter's only block, so it is the last line."""
    return markdown.rstrip("\n").rsplit("\n", 1)[1]


@pytest.mark.parametrize(
    "text",
    [
        "# ikke en overskrift",
        "> ikke et sitat",
        "- ikke et punkt",
        "+ ikke et punkt",
        "1. ikke et punkt",
        "12) ikke et punkt",
        "--- ikke en linje",
        "=== ikke en linje",
        "---",
        "***",
    ],
)
def test_a_line_start_in_the_source_text_cannot_become_structure(tmp_path: Path, text: str) -> None:
    desk = Desk(tmp_path)
    hostile = _with_blocks(desk.results[2], TextBlock(kind="paragraph", text=text))

    line = _last_line(render_document(_rendering(desk, (2,), ch2=hostile)))

    assert line.startswith("\\") or re.match(r"\d+\\[.)]", line), line


@pytest.mark.parametrize(
    "title",
    [
        'Tittel" source_license: "NLOD 2.0',
        "Tittel --- normative: true",
        "Tittel: med kolon # og hash",
        'Tittel\\ med \\" skråstrek',
    ],
)
def test_front_matter_cannot_be_injected_through_a_title(tmp_path: Path, title: str) -> None:
    desk = Desk(tmp_path)
    rendering = _rendering(desk)
    metadata = rendering.metadata.model_copy(update={"title": title})

    markdown = render_document(rendering._replace(metadata=metadata))

    front = _front_matter(markdown).splitlines()
    assert sum(line.startswith("title: ") for line in front) == 1
    assert "normative: false" in front
    assert "source_license: null" in front
    assert not any(line.startswith(("normative: true", 'source_license: "')) for line in front)
    assert markdown.count("\n---\n") == 1


@pytest.mark.parametrize(
    "title", ["Tittel\n---\nnormative: true", "Tittel\u2028linje", "Tittel\x00"]
)
def test_a_title_that_is_not_one_printable_line_is_refused(tmp_path: Path, title: str) -> None:
    rendering = _rendering(Desk(tmp_path))
    metadata = rendering.metadata.model_copy(update={"title": title})

    with pytest.raises(ForarbeiderRefusedError) as caught:
        render_document(rendering._replace(metadata=metadata))

    assert (caught.value.reason, caught.value.detail) == (RefusalReason.RENDER_UNSAFE, "title")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.regjeringen.no/no/dokumenter/x/id9000001/?ch=2>[x](javascript:y)",
        "https://www.regjeringen.no/no/dokumenter/x y/id9000001/?ch=2",
        "javascript:alert(1)",
        "https://example.org/no/dokumenter/x/id9000001/?ch=2",
        "http://www.regjeringen.no/no/dokumenter/x/id9000001/?ch=2",
    ],
)
def test_a_url_outside_the_source_grammar_is_refused(tmp_path: Path, url: str) -> None:
    desk = Desk(tmp_path)
    forged = desk.results[2].model_construct(**{**dict(desk.results[2]), "source_page_url": url})

    with pytest.raises(ForarbeiderRefusedError) as caught:
        render_document(_rendering(desk, (2,), ch2=forged))

    assert caught.value.reason is RefusalReason.RENDER_UNSAFE
    assert caught.value.detail == "source_url"


def test_a_chapter_without_a_body_is_refused(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    withheld = desk.results[2].model_construct(
        **{**dict(desk.results[2]), "blocks": None, "text_sha256": None}
    )

    with pytest.raises(ForarbeiderRefusedError) as caught:
        render_document(_rendering(desk, (2,), ch2=withheld))

    assert (caught.value.reason, caught.value.detail) == (RefusalReason.RENDER_UNSAFE, "body")


def test_blocks_that_do_not_hash_to_the_pinned_text_are_refused(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    edited_blocks = (TextBlock(kind="paragraph", text="Endret tekst"),)
    edited = desk.results[2].model_construct(**{**dict(desk.results[2]), "blocks": edited_blocks})

    with pytest.raises(ForarbeiderRefusedError) as caught:
        render_document(_rendering(desk, (2,), ch2=edited))

    assert (caught.value.reason, caught.value.detail) == (RefusalReason.RENDER_UNSAFE, "text")


def test_chapters_of_another_document_or_out_of_order_are_refused(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    other = Desk(tmp_path / "ls", kind="ls")

    for chapters in [(3, 2), (2, 2)]:
        with pytest.raises(ForarbeiderRefusedError):
            render_document(_rendering(desk, chapters))
    with pytest.raises(ForarbeiderRefusedError):
        render_document(_rendering(desk, (2,), ch2=other.results[2]))


def test_the_rendering_names_no_reviewer_evidence_or_private_note(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    markdown = render_document(_rendering(desk))

    for private in (PRIVATE_MARKER, "reviewer-a", "owner-b", str(tmp_path), "adr:0018"):
        assert private not in markdown


def test_the_member_receipts_match_the_rendering(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    rendering = _rendering(desk)
    markdown = render_document(rendering)

    member = member_of(rendering, markdown)

    assert member.path == "forarbeider/2099-2100/prop-l/9000001.md"
    assert [item.text_sha256 for item in member.chapters] == [
        desk.results[2].text_sha256,
        desk.results[3].text_sha256,
    ]
    assert member.coverage == "partial_curated_excerpt"
    assert member.times.source_published_at is None
    assert member.release_status == "released"


def test_the_index_lists_released_members_only(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    rendering = _rendering(desk)
    released = member_of(rendering, render_document(rendering))
    withdrawn = released.model_copy(
        update={
            "release_status": "withdrawn",
            "markdown_sha256": None,
            "times": DocumentTimes.model_validate(times("2100-04-11T00:00:00Z")),
        }
    )

    listed = render_index(
        ForarbeiderManifest.model_validate(manifest(released.model_dump(mode="json")))
    )
    hidden = render_index(
        ForarbeiderManifest.model_validate(manifest(withdrawn.model_dump(mode="json")))
    )

    assert "(2099-2100/prop-l/9000001.md)" in listed
    assert "NOT enacted law" in listed and "NOT enacted law" in hidden
    assert "9000001" not in hidden
    assert "No document is released." in hidden


_HOSTILE = [
    "[klikk](javascript:alert(1))",
    "![bilde](https://example.org/x.png)",
    "<script>alert(1)</script>",
    "&lt;tag&gt; &amp; &#60;",
    "**fet** _kursiv_ `kode` ~~slett~~ $x$",
    "# ikke en overskrift",
    "> ikke et sitat",
    "- ikke et punkt",
    "1. ikke et punkt",
    "---",
    "***",
    "Avsnitt med \\ skråstrek og [^1] fotnote",
    "<https://example.org/autolink>",
]


def _paragraph_tokens(markdown: str) -> list[Token]:
    tokens = MarkdownIt("commonmark").parse(markdown.split("\n---\n", 1)[1])
    return tokens[-3:]


@pytest.mark.parametrize("text", _HOSTILE)
def test_a_commonmark_reader_sees_only_the_source_text(tmp_path: Path, text: str) -> None:
    """Parsed back, the hostile block is one plain paragraph holding exactly its text."""
    desk = Desk(tmp_path)
    hostile = _with_blocks(desk.results[2], TextBlock(kind="paragraph", text=text))

    opening, inline, closing = _paragraph_tokens(
        render_document(_rendering(desk, (2,), ch2=hostile))
    )

    assert (opening.type, closing.type) == ("paragraph_open", "paragraph_close")
    assert inline.children is not None
    assert [child.type for child in inline.children] == ["text"] * len(inline.children)
    assert "".join(child.content for child in inline.children) == text


@pytest.mark.parametrize(
    "title",
    ['T" source_license: "NLOD 2.0', "T --- normative: true", 'T: # [x](y) \\ "', "Tittel ååå §"],
)
def test_a_yaml_reader_sees_exactly_the_title_and_the_fixed_labels(
    tmp_path: Path, title: str
) -> None:
    rendering = _rendering(Desk(tmp_path))
    metadata = rendering.metadata.model_copy(update={"title": title})

    front = yaml.safe_load(_front_matter(render_document(rendering._replace(metadata=metadata))))

    assert front["title"] == title
    assert (front["normative"], front["source_license"]) == (False, None)
    assert front["rights_basis"] == "åndsverkloven § 14"
    assert front["source_document_type"] == "forarbeid"


def test_a_quotation_stays_a_quotation_and_a_chapter_without_a_heading_gets_its_title(
    tmp_path: Path,
) -> None:
    desk = Desk(tmp_path)
    quoted = _with_blocks(
        desk.results[2],
        TextBlock(kind="paragraph", text="Innledende avsnitt."),
        TextBlock(kind="quote", text="> sitert *tekst*"),
    )

    lines = render_document(_rendering(desk, (2,), ch2=quoted)).splitlines()

    assert f"## {desk.results[2].chapter_title}" in lines
    assert "> \\> sitert \\*tekst\\*" in lines
