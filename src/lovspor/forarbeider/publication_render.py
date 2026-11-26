"""AR4a: deterministic publication bytes of a document version, the index and the catalogue (#738).

A document is rendered from the FC3 extraction its raw pages reproduce, and
only from the spans the bound FC7 decision allows: AR4a refuses before
rendering when any span is not ``allow`` (``publication_prepare``), so what
reaches this module is publishable by the policy's own decision. Annex parts
are never rendered (the rights note says so in every file), nor is anything
the extraction recorded as a gap. No clock: the same extraction, selection
and policy give the same bytes.

Text is the source's, so it is escaped exactly as the manual corpus renderer
escapes it (:mod:`lovspor.forarbeider.corpus_render`): every character that
could open inline markup is backslash-escaped, a line start that could open a
block is too, and any text holding a control, a line separator or a bidi
override is **refused**, never reflowed (``render_unsafe``). Front matter
values are JSON scalars, which YAML reads as double-quoted strings.
"""

import json
from collections.abc import Iterator, Sequence

from lovspor.forarbeider.corpus_render import NOT_LAW, _inline, _line, _url
from lovspor.forarbeider.document_models import DocumentExtraction, Part, Span
from lovspor.forarbeider.publication_models import Catalogue, CatalogueEntry
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.version_release_models import PolicyReference

RENDERER = "publication-render/1"
RIGHTS = (
    "> Published on the basis of åndsverkloven § 14; no licence is asserted."
    " Annexes, figures, images, tables and footnotes are never included."
)
_DEEPEST = 6
_TYPE_LABELS = {"prop-l": "L", "prop-ls": "LS"}
_EN_DASH = chr(0x2013)


def _scalar(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _front_matter(extraction: DocumentExtraction, version_id: str, policy: PolicyReference) -> str:
    fields: dict[str, object] = {
        "basis": "åndsverkloven § 14",
        "document_id": extraction.document_id,
        "document_type": extraction.document_type,
        "normative": False,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.policy_sha256,
        "policy_version": policy.policy_version,
        "proposition_number": extraction.proposition_number,
        "renderer": RENDERER,
        "session": extraction.session,
        "source_license": None,
        "source_url": _line(extraction.source_url, "source_url"),
        "title": _line(extraction.title, "title"),
        "version_id": version_id,
    }
    lines = [f"{key}: {_scalar(value)}" for key, value in sorted(fields.items())]
    return "\n".join(["---", *lines, "---"])


def _span(span: Span) -> str:
    text = _inline(_line(span.text, "text"))
    if span.kind == "heading":
        line = f"{'#' * min(span.level + 1, _DEEPEST)} {text}"
    elif span.kind == "list_item":
        line = f"{'  ' * max(span.level - 1, 0)}- {text}"
    else:
        line = text
    return "> " * span.quote_depth + line


def _tight(previous: Span | None, span: Span) -> bool:
    return (
        previous is not None
        and previous.kind == span.kind == "list_item"
        and previous.list_group == span.list_group
    )


def _part(part: Part) -> list[str]:
    spans = [entry for entry in part.entries if isinstance(entry, Span)]
    lines = [f"Source: {_url(part.source_page_url)}"]
    previous: Span | None = None
    for span in spans:
        if not _tight(previous, span):
            lines.append("")
        lines.append(_span(span))
        previous = span
    return lines


def render_version(
    extraction: DocumentExtraction, version_id: str, policy: PolicyReference
) -> bytes:
    """The Markdown of one document version: front matter, notices, title, every non-annex part.

    Raises:
        ForarbeiderRefusedError: ``render_unsafe`` for text or a URL it cannot render safely.
    """
    lines = [
        _front_matter(extraction, version_id, policy),
        "",
        NOT_LAW,
        ">",
        RIGHTS,
        "",
        f"# {_inline(_line(extraction.title, 'title'))}",
    ]
    for part in extraction.parts:
        if not part.annex:
            lines += ["", *_part(part)]
    return ("\n".join(lines) + "\n").encode()


def _reference(entry: CatalogueEntry) -> str:
    label = _TYPE_LABELS.get(entry.document_type, entry.document_type)
    first, second = entry.session.split("-")
    return f"Prop. {entry.proposition_number} {label} ({first}{_EN_DASH}{second})"


def _index_lines(entries: Sequence[CatalogueEntry]) -> Iterator[str]:
    for entry in entries:
        title = _inline(_line(entry.title, "title"))
        yield f"- [{_reference(entry)} {title}]({entry.path}), version `{entry.version_id}`"


def render_index(catalogue: Catalogue) -> bytes:
    """``INDEX.md``: every catalogued document, in id order, with its version.

    Raises:
        ForarbeiderRefusedError: ``render_unsafe`` for a title it cannot render safely.
    """
    lines = ["# Forarbeider", "", NOT_LAW, ">", RIGHTS, ""]
    lines += list(_index_lines(catalogue.documents)) or ["No document is published."]
    return ("\n".join(lines) + "\n").encode()


def catalogue_bytes(catalogue: Catalogue) -> bytes:
    return canonical_text(catalogue).encode()
