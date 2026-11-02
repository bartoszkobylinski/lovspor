"""Deterministic Markdown of released forarbeider chapters (#738, ADR-0018 PR3B-2).

Rendered from the pinned PR2 text blocks alone: nothing else of the page, no
annex, image, table or footnote (PR2 never produced a body with one), and no
evidence, reviewer or private note. No clock: the same metadata, chapters,
times and renderer version give the same bytes.

The text is the source's, so it is escaped rather than trusted: every
character that could open inline markup is backslash-escaped, and a line start
that could open a block is too, so a paragraph stays a paragraph. Front matter
values are JSON strings, which YAML reads as double-quoted scalars, and any
text holding a control, a line separator or a bidi override is refused instead of reflowed. A URL is
written only if it has the source grammar, inside angle brackets.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import NamedTuple

from lovspor.forarbeider.chapter_models import ChapterResult, TextBlock, text_hash
from lovspor.forarbeider.corpus_models import (
    RENDERER_VERSION,
    ChapterReceipt,
    DocumentTimes,
    ForarbeiderManifest,
    ManifestDocument,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import ForarbeidRecord
from lovspor.forarbeider.release_models import destination_of, digest, document_of

_URL = re.compile(
    r"https://www\.regjeringen\.no/no/dokumenter/[A-Za-z0-9._~-]+/id[1-9]\d{0,11}/(\?ch=[1-9]\d{0,4})?"
)
_INLINE = re.compile(r"([\\`*_\[\]<>&#|~!$])")
_ORDERED = re.compile(r"^(\d{1,9})([.)])")
_BLOCK_START = re.compile(r"^([>+\-=:])")
#: Controls, line and paragraph separators, a byte-order mark, bidi overrides and surrogates.
_UNSAFE_CHARACTER = re.compile(
    "[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff\u202a-\u202e\u2066-\u2069\ud800-\udfff]"
)
_DEEPEST = 6
_TYPE_LABELS = {"prop-l": "L", "prop-ls": "LS"}
_EN_DASH = "\u2013"
NOT_LAW = (
    "> **Forarbeid: preparatory material, NOT enacted law.** It does not state whether"
    " the proposal was adopted, and it is not a source of law in force."
)
_RIGHTS = (
    "> Published on the basis of åndsverkloven § 14; no licence is asserted."
    " Annexes, figures, images, tables and footnotes are never included."
)


class ReleasedChapter(NamedTuple):
    """A chapter cleared for this rendering and the moment its review approved it."""

    result: ChapterResult
    review_decided_at: datetime


class Rendering(NamedTuple):
    """Everything a rendering is made of: no clock, no path, no private record."""

    metadata: ForarbeidRecord
    chapters: tuple[ReleasedChapter, ...]
    times: DocumentTimes


def _unsafe(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RENDER_UNSAFE, detail)


def _line(text: str, detail: str) -> str:
    if _UNSAFE_CHARACTER.search(text):
        raise _unsafe(detail)
    return text


def _url(url: str) -> str:
    if _URL.fullmatch(url) is None:
        raise _unsafe("source_url")
    return f"<{url}>"


def _inline(text: str) -> str:
    """Source text as Markdown text: no markup it holds can open."""
    escaped = _ORDERED.sub(r"\1\\\2", _INLINE.sub(r"\\\1", text))
    return _BLOCK_START.sub(r"\\\1", escaped)


def _scalar(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _checked(rendering: Rendering) -> tuple[ChapterResult, ...]:
    """The released results, refused unless they are this document's listed chapters, in order.

    Raises:
        ForarbeiderRefusedError: ``render_unsafe``.
    """
    results = tuple(item.result for item in rendering.chapters)
    numbers = [result.chapter_number for result in results]
    listed = {chapter.chapter_number for chapter in rendering.metadata.chapters}
    if not numbers or numbers != sorted(set(numbers)) or not set(numbers) <= listed:
        raise _unsafe("chapters")
    document = document_of(rendering.metadata)
    for result in results:
        if document_of(result) != document:
            raise _unsafe("chapters")
        if result.blocks is None:
            raise _unsafe("body")
        if text_hash(result.blocks) != result.text_sha256:
            raise _unsafe("text")
    return results


def _coverage(rendering: Rendering) -> str:
    listed = [chapter.chapter_number for chapter in rendering.metadata.chapters]
    released = [item.result.chapter_number for item in rendering.chapters]
    return "every_listed_chapter" if released == listed else "partial_curated_excerpt"


def reference(metadata: ForarbeidRecord) -> str:
    session = metadata.session.replace("-", _EN_DASH)
    return f"Prop. {metadata.proposition_number} {_TYPE_LABELS[metadata.document_type]} ({session})"


def _identity_fields(metadata: ForarbeidRecord) -> list[tuple[str, object]]:
    ministry = metadata.ministry
    return [
        ("dataset", "forarbeider"),
        ("id", metadata.id),
        ("source_document_type", "forarbeid"),
        ("normative", False),
        ("document_type", metadata.document_type),
        ("session", metadata.session),
        ("proposition_number", metadata.proposition_number),
        ("title", _line(metadata.title, "title")),
        ("ministry", None if ministry is None else _line(ministry, "ministry")),
        ("source_provider", "regjeringen.no"),
        ("source_url", _url(metadata.source_url)[1:-1]),
        ("source_license", None),
        ("rights_basis", "åndsverkloven § 14"),
    ]


def _front_matter(rendering: Rendering, results: Sequence[ChapterResult]) -> list[str]:
    metadata = rendering.metadata
    published = metadata.published_at
    fields = [
        *_identity_fields(metadata),
        ("release_status", "released"),
        ("coverage", _coverage(rendering)),
        ("asserts_complete_proposition", False),
        ("listed_chapters", [chapter.chapter_number for chapter in metadata.chapters]),
        ("released_chapters", [result.chapter_number for result in results]),
        ("chapter_text_sha256", [result.text_sha256 for result in results]),
        ("published_at", None if published is None else published.isoformat()),
        ("renderer_version", RENDERER_VERSION),
    ]
    return ["---", *(f"{key}: {_yaml(value)}" for key, value in fields), "---"]


def _yaml(value: object) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(item) for item in value) + "]"
    return _scalar(value)


def _scope_note(rendering: Rendering) -> str:
    listed = len(rendering.metadata.chapters)
    released = ", ".join(str(item.result.chapter_number) for item in rendering.chapters)
    if _coverage(rendering) == "every_listed_chapter":
        return f"> Every chapter the source lists (1{_EN_DASH}{listed}) is included."
    return (
        f"> **Partial curated excerpt:** chapters {released} of the {listed} the source lists."
        " This is not the complete proposition."
    )


def _block(block: TextBlock) -> str:
    text = _inline(_line(block.text, "text"))
    if block.kind == "heading":
        return f"{'#' * min(block.level + 1, _DEEPEST)} {text}"
    if block.kind == "list_item":
        return f"{'  ' * (block.level - 1)}- {text}"
    if block.kind == "quote":
        return f"> {text}"
    return text


def _chapter(result: ChapterResult) -> list[str]:
    blocks = result.blocks or ()
    lines = [f"Source: {_url(result.source_page_url)}", ""]
    opens = blocks[0].kind == "heading" and blocks[0].level == 1
    if not opens or blocks[0].text != result.chapter_title:
        lines += [f"## {_inline(_line(result.chapter_title, 'chapter_title'))}", ""]
    for previous, block in zip((None, *blocks), blocks, strict=False):
        tight = previous is not None and previous.kind == block.kind == "list_item"
        if previous is not None and not tight:
            lines.append("")
        lines.append(_block(block))
    return lines


def render_document(rendering: Rendering) -> str:
    """The Markdown of the released chapters, in source order.

    Raises:
        ForarbeiderRefusedError: ``render_unsafe``: a chapter that is not this
            document's, listed and in order, has no body or another text, or a
            URL or line outside the grammar.
    """
    results = _checked(rendering)
    metadata = rendering.metadata
    title = _inline(_line(metadata.title, "title"))
    lines = [*_front_matter(rendering, results), "", f"# {reference(metadata)}: {title}", ""]
    lines += [NOT_LAW, ">", _scope_note(rendering), ">", _RIGHTS, ""]
    lines += [f"Source: {_url(metadata.source_url)}", ""]
    for result in results:
        lines += [*_chapter(result), ""]
    return "\n".join(lines).rstrip("\n") + "\n"


_FIXED_MEMBER: dict[str, object] = {
    "source_document_type": "forarbeid",
    "normative": False,
    "source_provider": "regjeringen.no",
    "rights_basis": "åndsverkloven § 14",
    "source_license": None,
    "asserts_complete_proposition": False,
    "release_status": "released",
}


def member_of(rendering: Rendering, markdown: str) -> ManifestDocument:
    """The manifest member that lists ``markdown`` as this document's rendering."""
    metadata = rendering.metadata
    document = document_of(metadata)
    return ManifestDocument.model_validate(
        {
            **_FIXED_MEMBER,
            "document": document,
            "title": metadata.title,
            "ministry": metadata.ministry,
            "path": destination_of(document),
            "markdown_sha256": digest(markdown.encode()),
            "listed_chapters": tuple(chapter.chapter_number for chapter in metadata.chapters),
            "chapters": tuple(_receipt(item) for item in rendering.chapters),
            "coverage": _coverage(rendering),
            "times": rendering.times,
        }
    )


def _receipt(item: ReleasedChapter) -> ChapterReceipt:
    result = item.result
    return ChapterReceipt.model_validate(
        {
            "chapter_number": result.chapter_number,
            "chapter_id": result.id,
            "chapter_title": result.chapter_title,
            "source_page_url": result.source_page_url,
            "source_sha256": result.source_sha256,
            "text_sha256": result.text_sha256,
            "extractor_version": result.extractor_version,
            "normalizer_version": result.normalizer_version,
            "retrieved_at": result.retrieved_at,
            "review_decided_at": item.review_decided_at,
        }
    )


def _index_line(member: ManifestDocument) -> str:
    numbers = ", ".join(str(item.chapter_number) for item in member.chapters)
    listed = len(member.listed_chapters)
    scope = (
        "every listed chapter"
        if member.coverage == "every_listed_chapter"
        else "partial curated excerpt"
    )
    label = (
        f"Prop. {member.document.proposition_number} {_TYPE_LABELS[member.document.document_type]}"
    )
    session = member.document.session.replace("-", _EN_DASH)
    target = member.path.removeprefix("forarbeider/")
    title = _inline(_line(member.title, "title"))
    return f"- [{label} ({session}): {title}]({target}): chapters {numbers} of {listed} ({scope})"


def render_index(manifest: ForarbeiderManifest) -> str:
    """``forarbeider/INDEX.md``: released members in manifest order; withdrawn ones are gone."""
    released = [member for member in manifest.documents if member.release_status == "released"]
    lines = ["# Forarbeider", "", NOT_LAW, "", "Membership is `forarbeider/manifest.json`.", ""]
    lines += [_index_line(member) for member in released] or ["No document is released."]
    return "\n".join(lines) + "\n"
