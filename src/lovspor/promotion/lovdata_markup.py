"""Lovdata's markup in a received text, found and removed (ADR-0016 A1-3, #632).

An authority answering an innsynskrav often sends a print out of Lovdata, or
a Norsk Lovtidend avd. II PDF, rather than its archive original. The
regulation text is free under åndsverkloven § 14 whoever printed it; Lovdata's
editorial markup around it is not the regulation, so on the received channel
it is removed before anything is rendered. The observed channel never calls
this module: a Lovdata copy found on a website is still held.

``received_form`` names the form from the text alone:

* ``lovtidend`` — the ``NORSK LOVTIDEND`` banner line;
* ``lovdata_print`` — the "Utskrift fra Lovdata" stamp, a browser print's
  chrome, or Lovdata's metadata block (``lovdata_header.lovdata_dato``);
* ``authority_original`` — none of these. Nothing is removed from it.

``strip_markup`` removes, deterministically and line by line:

* from a Lovdata print: its print chrome; everything up to the
  ``Innholdsfortegnelse`` line that closes the metadata block (the page title,
  the ``OPPHEVET`` stamp, the field lines); the second ``Innholdsfortegnelse``
  and the contents entries after it; amendment annotations (``Endret ved
  forskrift 12 nov 2008 nr. …``, ``0 Opphevet …``, ``Tilføyd hjemmel: …``,
  ``… (Lovdatas anm.)``), also when one ends a preamble line; and the footnote
  "Ikke kunngjort i Norsk Lovtidend". An annotation is told from regulation
  text by Lovdata's own date form, ``12 nov 2008`` with no period after the
  day, which a regulation's text does not use;
* from a Lovtidend PDF: the banner (``NORSK LOVTIDEND``, ``Avd. II …``,
  ``Utgitt i henhold til …``, ``Kunngjort … PDF-versjon …``) and the
  "For ev. rettelser …" link to lovdata.no, keeping what a page break joined
  to it. The ``Hjemmel: Fastsatt av …`` line is the kunngjøring's and stays.

The id is a fact about the regulation, not markup, and is minted from the text
before removal (ADR-0016 §1a): the lines that state it — Lovdata's ``Dato``
line (with its value line in a DOCX export), or Lovtidend's ``Kunngjort``
line — are returned beside the published lines for identity to read. What a
print's block states about a repealed regulation — the ``OPPHEVET`` stamp and
the end of the ``Ikrafttredelse`` range — is read from the removed block the
same way and returned as its ``validity`` (#639); the published lines do not
change. Every other form states none.

A Lovdata print whose metadata block cannot be bounded is ``None``: it is held,
never published with part of the block left in. ``residual_markup`` is the
extractor's last check that nothing of Lovdata's is left.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import NamedTuple

from lovspor.promotion.lovdata_header import (
    LOVDATA_PRINT,
    LOVDATA_PRINT_CHROME,
    header_validity,
    lovdata_dato,
)
from lovspor.promotion.models import HeaderValidity, ReceivedForm

_UTSKRIFT = re.compile(
    r"Utskrift\s+fra\s+Lovdata\s+-\s+\d{1,2}\.\d{1,2}\.\d{4}(?:\s+\d{1,2}:\d{2})?"
)
_CONTENTS = re.compile(r"Innholdsfortegnelse\d*")
_CONTENTS_ENTRY = re.compile(
    r"(?:(?:Kapittel|Kap\.)\s*(?:\d+|[IVXLC]+)\b|Vedlegg\s+\d+\b).{0,90}"
    r"|[IVXLC]+\.?(?:\s+[A-ZÆØÅ].{0,90})?"
)
_LOVDATA_DATE = r"\d{1,2}\s+(?:jan|feb|mars|april|mai|juni|juli|aug|sep|okt|nov|des)\s+\d{4}\b"
_AMENDED = rf"(?:Endret|Tilføyd|Opphevet)\b[^.]{{0,40}}?{_LOVDATA_DATE}"
_ANNOTATION = re.compile(
    rf"(?:Endringer\s*:\s*)?{_AMENDED}.*"
    r"|0\s+(?:Endret|Tilføyd|Opphevet|Endring)\b.*"
    r"|(?:Tilføyd|Endret)\s+hjemmel\s*:.*"
    r"|.*\(Lovdatas\s+anm\.\).*"
    r"|\d{1,2}\s+Ikke\s+kunngjort\s+i\s+Norsk\s+Lovtidend\.?"
)
_ANNOTATION_TAIL = re.compile(rf"(?:(?<=\.)\s+|\s+0\s+){_AMENDED}.*$")
_LOVDATA_NOTE = re.compile(r"\(Lovdatas\s+anm\.\)")
_LOVTIDEND_MARK = "NORSK LOVTIDEND"
_KUNNGJORT = re.compile(r"Kunngjort\s+.*\bPDF-versjon\b.*")
_BANNER = re.compile(
    rf"{_LOVTIDEND_MARK}"
    r"|Avd\.\s+II\s+Regionale\s+og\s+lokale\s+forskrifter\s+mv\."
    r"|Utgitt\s+i\s+henhold\s+til\s+lov\s+19\.\s+juni\s+1969\s+nr\.\s+53\.?"
    rf"|{_KUNNGJORT.pattern}"
)
_CORRECTIONS = re.compile(
    r"For\s+ev\.\s+rettelser\s+se\s+nederst\s+i\s+den\s+elektroniske\s+versjonen:\s*"
    r"(?:https?://)?(?:www\.)?lovdata\.no/\S*\s*"
)
_LOVDATA_DOMAIN = re.compile(r"lovdata\.no", re.IGNORECASE)

Lines = tuple[str, ...]


class Stripped(NamedTuple):
    """The lines to publish, the removed lines that state the id, and the block's validity."""

    lines: Lines
    identification: Lines
    validity: HeaderValidity = HeaderValidity()


def received_form(lines: Lines) -> ReceivedForm:
    """The form ``lines`` arrived in, read from the text alone."""
    if _LOVTIDEND_MARK in lines:
        return ReceivedForm.LOVTIDEND
    if lovdata_dato(lines) is not None or any(_is_print_chrome(line) for line in lines):
        return ReceivedForm.LOVDATA_PRINT
    return ReceivedForm.AUTHORITY_ORIGINAL


def strip_markup(lines: Lines, form: ReceivedForm) -> Stripped | None:
    """``lines`` without Lovdata's markup; ``None`` when its block cannot be bounded."""
    if form is ReceivedForm.LOVDATA_PRINT:
        return _lovdata_print(lines)
    if form is ReceivedForm.LOVTIDEND:
        return _lovtidend(lines)
    return Stripped(lines, ())


def residual_markup(lines: Lines) -> bool:
    """Whether anything of Lovdata's is still in ``lines``."""
    text = "\n".join(lines)
    markers = (LOVDATA_PRINT, LOVDATA_PRINT_CHROME, _LOVDATA_DOMAIN, _LOVDATA_NOTE)
    return lovdata_dato(lines) is not None or any(m.search(text) for m in markers)


def _is_print_chrome(line: str) -> bool:
    return LOVDATA_PRINT.search(line) is not None or LOVDATA_PRINT_CHROME.search(line) is not None


def _lovdata_print(lines: Lines) -> Stripped | None:
    kept = tuple(line for line in lines if not _is_chrome_line(line))
    dato = lovdata_dato(kept)
    if dato is None:
        return None
    bound = next((i for i in range(dato[1], len(kept)) if _CONTENTS.fullmatch(kept[i])), None)
    if bound is None:
        return None
    published = tuple(_annotation_free(_without_contents(kept[bound + 1 :])))
    return Stripped(published, kept[dato[0] : dato[1] + 1], header_validity(kept[:bound]))


def _is_chrome_line(line: str) -> bool:
    return _UTSKRIFT.fullmatch(line) is not None or LOVDATA_PRINT_CHROME.search(line) is not None


def _without_contents(lines: Lines) -> Lines:
    start = next((i for i, line in enumerate(lines) if _CONTENTS.fullmatch(line)), None)
    if start is None:
        return lines
    end, seen = start + 1, set()
    while end < len(lines) and _CONTENTS_ENTRY.fullmatch(lines[end]) and lines[end] not in seen:
        seen.add(lines[end])
        end += 1
    return lines[:start] + lines[end:]


def _annotation_free(lines: Lines) -> Iterator[str]:
    for line in lines:
        if _ANNOTATION.fullmatch(line) is None:
            yield _ANNOTATION_TAIL.sub("", line)


def _lovtidend(lines: Lines) -> Stripped:
    identification = tuple(line for line in lines if _KUNNGJORT.fullmatch(line))
    unlinked = (
        _CORRECTIONS.sub("", line, count=1) for line in lines if not _BANNER.fullmatch(line)
    )
    return Stripped(tuple(line for line in unlinked if line), identification)
