"""The deterministic extractor: captured bytes to a regulation or a hold (ADR-0016 S2).

``extract_regulation(payload, content_type)`` is a pure function of the bytes
and their recorded media type — no clock, no network, and **no URL**: a URL
that says ``forslag`` or ``høringsutkast`` over an adopted text (the
classification study's §6.3) cannot move what is extracted. The same bytes
give the same result under the same ``EXTRACTOR_VERSION``.

A text that could mislead is held, never published, and the reason is typed
(ADR-0016 4h), checked in this order:

1. ``unsupported_format`` — not HTML, PDF or DOCX;
2. ``unreadable`` — a reader refused the bytes;
3. ``empty_text`` — under ``MIN_TEXT_CHARS`` after normalisation (a JavaScript
   shell, a scanned PDF with no text layer);
4. ``garbled_text`` — text that is not Norwegian prose: almost no function
   words, or control characters — what a PDF font without a ToUnicode map
   yields (study §6.2);
5. ``lovdata_copy`` — a print out of Lovdata, or a copy pasted from it that
   keeps its metadata block (``lovdata_header.py``), whose editorial markup
   § 14 does not cover (ADR-0016 Risks);
6. ``placeholder_date`` — a draft's blank date such as ``X.X.2016`` (§6.4);
7. ``no_body`` / ``no_title`` — the text cannot be split safely;
   ``title_truncated`` — the title ends on a function word (``title.py``);
8. ``personal_data`` — the gate of ``personal_data.py`` found something.

``extract_received`` is the received channel's extractor (ADR-0016 A1-3,
#632). It reads the same bytes the same way, names the form the text arrived
in (``lovdata_markup.received_form``) and removes Lovdata's markup before the
holds above run, so a Lovdata print or a Lovtidend PDF an authority sent is
not held as ``lovdata_copy``. It is held as ``lovdata_copy`` instead when the
metadata block cannot be bounded, or when anything of Lovdata's is left after
removal. The lines that stated the id are kept for identity (§1a), and a text
whose block names no enacting organ is read for a state instrument
(``Fastsatt ved kgl.res.``, A1-4). What a Lovdata print's block states about a
repealed regulation fills ``status`` and ``ikraft_end`` (#639). It is read
beside the text, never from it, so the extracted text is what it was without
the stamp. The observed channel never calls it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from lovspor.errors import UnreadableSourceError
from lovspor.promotion.dates import has_placeholder_date
from lovspor.promotion.fields import instrument_issuer, read_regulation
from lovspor.promotion.identity import normalise_text
from lovspor.promotion.lovdata_header import (
    LOVDATA_PRINT,
    LOVDATA_PRINT_CHROME,
    has_lovdata_header,
)
from lovspor.promotion.lovdata_markup import (
    Stripped,
    received_form,
    residual_markup,
    strip_markup,
)
from lovspor.promotion.models import (
    ExtractedDocument,
    ExtractionHoldReason,
    ExtractionResult,
    HeldExtraction,
    PersonalDataHit,
    ReceivedForm,
    SourceForm,
)
from lovspor.promotion.personal_data import screen_personal_data
from lovspor.promotion.source_text import docx_lines, html_lines, pdf_lines, source_form

#: Bump on any change that can change extracted text for the same bytes,
#: including a pypdf bump (``source_text.PDF_LIBRARY_VERSION``). A bump is a
#: ``migration:`` commit across the dataset, never new versions (ADR-0016 4e).
EXTRACTOR_VERSION = 10
MIN_TEXT_CHARS = 200

# Measured 2026-10-03 over the archive's extracted texts longer than 200
# characters: the median share of these words is 0.29-0.31 in HTML, PDF and
# DOCX alike, and under 0.05 for 2 % of PDFs and 1 % of HTML pages.
_MIN_FUNCTION_WORD_SHARE = 0.05
_MAX_UNPRINTABLE_SHARE = 0.01
_FUNCTION_WORDS = frozenset(
    {
        "og",
        "i",
        "av",
        "til",
        "for",
        "som",
        "med",
        "er",
        "på",
        "å",
        "det",
        "en",
        "et",
        "at",
        "om",
        "skal",
        "kan",
        "ikke",
        "ikkje",
        "eller",
        "ved",
        "fra",
        "frå",
        "den",
        "de",
        "har",
        "jf",
        "etter",
        "under",
        "kommune",
        "kommunen",
        "forskrift",
    }
)
_WORD = re.compile(r"[^\W\d_]+")
_UNPRINTABLE = frozenset({"Cc", "Co", "Cn", "Cs"})
_READERS: dict[SourceForm, Callable[[bytes, str], tuple[str, ...]]] = {
    SourceForm.HTML: html_lines,
    SourceForm.PDF: lambda payload, _: pdf_lines(payload),
    SourceForm.DOCX: lambda payload, _: docx_lines(payload),
}
_SPLIT_DETAIL = {
    ExtractionHoldReason.NO_BODY: "no first section (§ 1, Kapittel 1) bounds the block",
    ExtractionHoldReason.NO_TITLE: "no title line before the first section",
    ExtractionHoldReason.TITLE_TRUNCATED: "the title ends on a function word: it is cut off",
}


_UNBOUNDED = "Lovdata's metadata block cannot be bounded, so its markup cannot be removed"
_RESIDUAL = "Lovdata's markup remains after removal"

_Read = tuple[SourceForm, tuple[str, ...]]


def extract_regulation(payload: bytes, content_type: str) -> ExtractionResult:
    """The regulation in ``payload``, or a typed hold saying why it may not be published."""
    read = _read(payload, content_type)
    if isinstance(read, HeldExtraction):
        return read
    form, lines = read
    refused = _text_hold("\n".join(lines))
    if refused is not None:
        return _held(form, *refused)
    return _document(form, lines)


def extract_received(payload: bytes, content_type: str) -> ExtractionResult:
    """A received text's regulation with Lovdata's markup removed, or a typed hold."""
    read = _read(payload, content_type)
    if isinstance(read, HeldExtraction):
        return read
    form, lines = read
    received = received_form(lines)
    stripped = strip_markup(lines, received)
    if stripped is None:
        return _held(form, ExtractionHoldReason.LOVDATA_COPY, _UNBOUNDED)
    if received is not ReceivedForm.AUTHORITY_ORIGINAL and residual_markup(stripped.lines):
        return _held(form, ExtractionHoldReason.LOVDATA_COPY, _RESIDUAL)
    refused = _text_hold("\n".join(stripped.lines))
    if refused is not None:
        return _held(form, *refused)
    return _as_received(_document(form, stripped.lines), received, stripped)


def _read(payload: bytes, content_type: str) -> _Read | HeldExtraction:
    form = source_form(content_type)
    if form is None:
        detail = f"media type {content_type!r} is not HTML, PDF or DOCX"
        return _held(None, ExtractionHoldReason.UNSUPPORTED_FORMAT, detail)
    try:
        return form, _READERS[form](payload, content_type)
    except UnreadableSourceError as error:
        return _held(form, ExtractionHoldReason.UNREADABLE, str(error))


def _as_received(
    result: ExtractionResult, received: ReceivedForm, stripped: Stripped
) -> ExtractionResult:
    if isinstance(result, HeldExtraction):
        return result
    removed = "\n".join(stripped.identification) if stripped.identification else None
    regulation = result.regulation.model_copy(update={"removed_identification": removed})
    issuer = result.fields.vedtatt_av or instrument_issuer(regulation.identification_block)
    stated = stripped.validity
    fields = result.fields.model_copy(
        update={"vedtatt_av": issuer, "status": stated.status, "ikraft_end": stated.ikraft_end}
    )
    update = {"regulation": regulation, "fields": fields, "received_form": received}
    return result.model_copy(update=update)


def _text_hold(text: str) -> tuple[ExtractionHoldReason, str] | None:
    if len(normalise_text(text)) < MIN_TEXT_CHARS:
        return ExtractionHoldReason.EMPTY_TEXT, f"under {MIN_TEXT_CHARS} characters of text"
    if _is_garbled(text):
        return ExtractionHoldReason.GARBLED_TEXT, "text does not read as Norwegian prose"
    if LOVDATA_PRINT.search(text) or LOVDATA_PRINT_CHROME.search(text):
        return ExtractionHoldReason.LOVDATA_COPY, "the text is a print out of Lovdata"
    if has_lovdata_header(text):
        return ExtractionHoldReason.LOVDATA_COPY, "the text carries Lovdata's metadata block"
    if has_placeholder_date(text):
        return ExtractionHoldReason.PLACEHOLDER_DATE, "a date is a draft's placeholder"
    return None


def _is_garbled(text: str) -> bool:
    words = _WORD.findall(text)
    function_words = sum(word.casefold() in _FUNCTION_WORDS for word in words)
    if not words or function_words / len(words) < _MIN_FUNCTION_WORD_SHARE:
        return True
    unprintable = sum(
        char == "�" or (char != "\n" and unicodedata.category(char) in _UNPRINTABLE)
        for char in text
    )
    return unprintable / len(text) > _MAX_UNPRINTABLE_SHARE


def _document(form: SourceForm, lines: tuple[str, ...]) -> ExtractionResult:
    read = read_regulation(lines)
    if isinstance(read, ExtractionHoldReason):
        return _held(form, read, _SPLIT_DETAIL[read])
    regulation, fields = read
    hits = screen_personal_data(regulation.full_text)
    if hits:
        detail = f"{len(hits)} personal-data hit(s); held for review, never redacted"
        return _held(form, ExtractionHoldReason.PERSONAL_DATA, detail, hits)
    return ExtractedDocument(
        extractor_version=EXTRACTOR_VERSION,
        source_form=form,
        regulation=regulation,
        fields=fields,
    )


def _held(
    form: SourceForm | None,
    reason: ExtractionHoldReason,
    detail: str,
    personal_data: tuple[PersonalDataHit, ...] = (),
) -> HeldExtraction:
    return HeldExtraction(
        extractor_version=EXTRACTOR_VERSION,
        source_form=form,
        reason=reason,
        detail=detail,
        personal_data=personal_data,
    )
