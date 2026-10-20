"""Local regulation to Markdown (ADR-0016 Decision 3, slice S2).

``render_local_regulation(document)`` is byte-deterministic: the same
``LocalDocument`` gives the same bytes on every run and machine. It reads no
clock; every time in the output is an input.

**Front matter** has ADR-0016's keys in its fixed order, each scalar written
as a JSON literal — a valid YAML scalar that the ``lovverk`` integrity check
reads with ``json.loads`` — ``authority`` as a flow mapping and ``hjemmel``
as a flow list. The provenance is fixed: ``source_license: "åndsverkloven §
14"``, ``asserted: false``, and ``basis`` names the channel: ``"observed"``
with ``observed_at_first`` and ``source_url``, or ``"received"`` (ADR-0016 A5)
with ``received_at``, ``innsyn_request``, ``innsyn_saksnummer`` and
``received_form`` (``lovdata_print``, ``lovtidend`` or ``authority_original``,
A1-3) in their place — a received version names no URL. A repealed
regulation adds ``status: "repealed"`` and ``ikraft_end`` right after
``ikraft_text``, each only when its source states it (Lovdata's ``OPPHEVET``
stamp and ``Ikrafttredelse`` range, #639): a version that states neither
renders exactly as before, and none is ever said to be in force. There is no
``retrieved_at`` (Lovdata download time) and no ``observed_at_last`` (it would
churn the file daily).

**No NLOD.** These texts are not Lovdata data. Output that mentions NLOD at all
— a municipal text quoting the licence — is refused rather than written: the
``lovverk`` integrity check rejects any such file in the dataset, and a file
it rejects must never reach a commit.

**Body.** Only the regulation: the title as ``#``, the rest of the
identification block and the body one paragraph per extracted line; a short
``Kapittel``/``§`` line is a heading (``§`` one level below a chapter when the
text has chapters). A line that would read as Markdown syntax is escaped, so
the text renders as written.
"""

from __future__ import annotations

import json
import re

from lovspor.errors import PromotionRenderError
from lovspor.promotion.identity import content_hash
from lovspor.promotion.models import (
    Authority,
    LocalDocument,
    ObservedSource,
    ReceivedSource,
    RegulationFields,
)

LOCAL_RENDERER_VERSION = 1
DOC_TYPE = "lokal-forskrift"
SOURCE_LICENSE = "åndsverkloven § 14"

_MAX_HEADING_CHARS = 100
_CHAPTER = re.compile(r"kap(?:ittel|\.)\s*(?:\d+|[IVXLC]+)\b", re.IGNORECASE)
_SECTION = re.compile(r"§\s*\d")
_MARKDOWN_LEAD = frozenset("#>*+-=|`~")
_NLOD = re.compile(r"nlod", re.IGNORECASE)

_Fields = tuple[tuple[str, str], ...]


def render_local_regulation(document: LocalDocument) -> str:
    """The Published Rendering of one version of a local regulation."""
    regulation = document.extracted.regulation
    if content_hash(regulation.full_text) != document.identity.content_hash:
        msg = "identity content_hash is not the hash of the extracted text it names"
        raise PromotionRenderError(msg)
    markdown = _front_matter(document) + "\n" + _body(document)
    if _NLOD.search(markdown):
        msg = "a local regulation's rendering may not mention NLOD (ADR-0016 Decision 3)"
        raise PromotionRenderError(msg)
    return markdown


def _front_matter(document: LocalDocument) -> str:
    lines = ["---", *(f"{key}: {value}" for key, value in _fields(document)), "---"]
    return "\n".join(lines) + "\n"


def _fields(document: LocalDocument) -> tuple[tuple[str, str], ...]:
    identity, fields = document.identity, document.extracted.fields
    return (
        ("id", _scalar(identity.doc_id)),
        ("slug", _scalar(document.slug)),
        ("type", _scalar(DOC_TYPE)),
        ("ref_id", _scalar(identity.ref_id)),
        ("title", _scalar(fields.title)),
        ("authority", _authority(identity.authority)),
        ("hjemmel", "[" + ", ".join(_scalar(h) for h in fields.hjemmel) + "]"),
        ("vedtatt", _scalar(fields.vedtatt.isoformat() if fields.vedtatt else None)),
        ("vedtatt_av", _scalar(fields.vedtatt_av)),
        ("ikraft", _scalar(fields.ikraft.isoformat() if fields.ikraft else None)),
        ("ikraft_text", _scalar(fields.ikraft_text)),
        *_validity(fields),
        *_provenance(document),
    )


def _validity(fields: RegulationFields) -> _Fields:
    """``status`` and ``ikraft_end``, each only when the source states it (#639)."""
    end = fields.ikraft_end.isoformat() if fields.ikraft_end else None
    status = fields.status.value if fields.status else None
    stated = (("status", status), ("ikraft_end", end))
    return tuple((key, _scalar(value)) for key, value in stated if value is not None)


def _provenance(document: LocalDocument) -> tuple[tuple[str, str], ...]:
    source, authority = document.source, document.identity.authority
    axis, channel, basis = _channel(source)
    return (
        ("version", _scalar(document.version)),
        ("content_hash", _scalar(document.identity.content_hash)),
        *axis,
        ("source_sha256", _scalar(source.source_sha256)),
        ("source_provider", _scalar(f"{authority.name} {authority.type.value} ({channel})")),
        ("source_license", _scalar(SOURCE_LICENSE)),
        ("basis", _scalar(basis)),
        ("asserted", _scalar(False)),
        ("language", _scalar("no")),
    )


def _channel(source: ObservedSource | ReceivedSource) -> tuple[_Fields, str, str]:
    if isinstance(source, ReceivedSource):
        return _received_axis(source), "received under offentleglova", "received"
    return _observed_axis(source), "observed", "observed"


def _observed_axis(source: ObservedSource) -> tuple[tuple[str, str], ...]:
    return (
        ("observed_at_first", _scalar(source.observed_at_first_utc)),
        ("source_url", _scalar(source.source_url)),
    )


def _received_axis(source: ReceivedSource) -> tuple[tuple[str, str], ...]:
    return (
        ("received_at", _scalar(source.received_at_utc)),
        ("innsyn_request", _scalar(source.innsyn_request)),
        ("innsyn_saksnummer", _scalar(source.innsyn_saksnummer)),
        ("received_form", _scalar(source.received_form.value)),
    )


def _scalar(value: str | int | bool | None) -> str:
    return json.dumps(value, ensure_ascii=False)


def _authority(authority: Authority) -> str:
    parts = (
        f"id: {_scalar(authority.id)}",
        f"type: {_scalar(authority.type.value)}",
        f"name: {_scalar(authority.name)}",
        f"klass_version: {_scalar(authority.klass_version)}",
    )
    return "{" + ", ".join(parts) + "}"


def _body(document: LocalDocument) -> str:
    regulation = document.extracted.regulation
    block = regulation.identification_block.split("\n")
    body = regulation.body.split("\n")
    has_chapters = any(_is_heading(line) and _CHAPTER.match(line) for line in body)
    paragraphs = [f"# {document.extracted.fields.title}"]
    paragraphs += [_paragraph(line) for line in block[1:]]
    paragraphs += [_body_line(line, has_chapters=has_chapters) for line in body]
    return "\n\n".join(paragraphs) + "\n"


def _body_line(line: str, *, has_chapters: bool) -> str:
    level = _heading_level(line, has_chapters=has_chapters)
    return f"{'#' * level} {line}" if level else _paragraph(line)


def _is_heading(line: str) -> bool:
    return len(line) <= _MAX_HEADING_CHARS and not line.endswith(".")


def _heading_level(line: str, *, has_chapters: bool) -> int:
    if not _is_heading(line):
        return 0
    if _CHAPTER.match(line):
        return 2
    if _SECTION.match(line):
        return 3 if has_chapters else 2
    return 0


def _paragraph(line: str) -> str:
    return "\\" + line if line[:1] in _MARKDOWN_LEAD else line
