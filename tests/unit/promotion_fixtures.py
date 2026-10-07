"""Synthetic sources for the promotion extractor and renderer (ADR-0016 S2).

Every page, PDF and DOCX here is built in code from invented text — an
invented "Eksempel kommune", invented people, invented numbers. Nothing is
copied from the observatory archive: ``tests/fixtures/`` holds captured
Lovdata samples, and municipal text has no place in this repository
(ADR-0010 §5). Each builder models a failure the 2026-10-03 classification
study met in the archive (its §6), not the bytes it met it in.
"""

from __future__ import annotations

import io
import zipfile
from html import escape

REGULATION_LINES = (
    "Forskrift om renovasjon og slam, Eksempel kommune",
    "Vedtatt av kommunestyret i møte 12.12.2019 med hjemmel i lov 13. mars 1981 nr. 6 om "
    "vern mot forurensninger og om avfall (forurensningsloven) § 30. Kunngjort på "
    "kommunens nettsted.",
    "§ 1 Formål",
    "Forskriften skal sikre en miljømessig forsvarlig innsamling av avfall i kommunen.",
    "§ 2 Virkeområde",
    "Forskriften gjelder for alle eiendommer i kommunen som er registrert med bolig "
    "eller fritidsbolig, og for den som eier eller fester slik eiendom.",
    "§ 3 Ikrafttredelse",
    "Forskriften trer i kraft 1. januar 2020.",
)

#: A CMS sidebar that embeds another regulation's teaser into every page (study §6.1).
SIDEBAR = (
    '<aside class="related"><h2>Andre forskrifter</h2>'
    "<p>Forskrift om skoleregler, Eksempel kommune § 1 Formål ...</p></aside>"
)


def html_page(
    lines: tuple[str, ...] = REGULATION_LINES,
    *,
    updated: str = "Sist oppdatert 01.09.2026",
    sidebar: str = SIDEBAR,
) -> bytes:
    """A municipal CMS page: site chrome around a ``<main>`` holding the regulation."""
    paragraphs = "".join(_html_block(line) for line in lines)
    return (
        "<!doctype html><html lang='no'><head><title>Renovasjon - Eksempel kommune</title>"
        "<script>var tracking = 'Forskrift om noe annet';</script></head><body>"
        "<header><nav><a href='/'>Hjem</a> <a href='/tjenester'>Tjenester</a></nav></header>"
        f"<main><nav class='breadcrumb'>Hjem &gt; Lokale forskrifter</nav>{sidebar}"
        f"<article>{paragraphs}<p>{escape(updated)}</p></article></main>"
        "<footer><p>Eksempel kommune, Postboks 1, 9999 Eksempelby</p></footer>"
        "</body></html>"
    ).encode()


#: Invented FAQ items around the regulation on a CMS "FAQ link collection"
#: page (issue #576): each a sibling ``<article class="faq">`` in the main column.
FAQ_SIBLINGS = (
    ("Hvilken skole hører jeg til?", "Skolekretsene i Eksempel kommune står i kartet."),
    ("Hvem kan få fri skoleskyss?", "Elever som bor mer enn fire kilometer fra skolen."),
)

#: A landing page's tag block and news-teaser list, each teaser its own ``<article>``.
TEASER_TAIL = (
    "<div class='tags d-print-none'><p>Les mer om følgende emner:</p><p>skole</p></div>"
    "<div class='articlelist'><article><h3>Velkommen til Eksempelskolen</h3></article>"
    "<article><h3>Elevene løp stafett i høstferien</h3></article></div>"
)


def faq_page(
    regulation_answers: int = 1, *, tail: str = TEASER_TAIL, sidebar: str = SIDEBAR
) -> bytes:
    """A page whose regulation is the answer of one FAQ item among sibling items.

    Modelled on the structure of the issue #576 pages (Kongsvinger 3401), with
    invented text: ``main > article.full-view > div.linkcollection >
    article.faq*``, then a tag block and a teaser list. ``regulation_answers``
    repeats the regulation's item, so no single article holds it.
    """
    paragraphs = "".join(_html_block(line) for line in REGULATION_LINES)
    item = _faq_item("Hvilke ordensregler gjelder?", paragraphs)
    siblings = "".join(_faq_item(question, f"<p>{answer}</p>") for question, answer in FAQ_SIBLINGS)
    return (
        "<!doctype html><html lang='no'><head><title>Skole - Eksempel kommune</title></head>"
        f"<body><main>{sidebar}<article class='default full-view'><h1>Skole</h1>"
        f"<div class='articleelement linkcollection faqarticle'>{item * regulation_answers}"
        f"{siblings}</div>{tail}</article></main></body></html>"
    ).encode()


def _faq_item(question: str, answer: str) -> str:
    heading = f"<h3>{escape(question)}</h3>"
    return f"<article class='faq'>{heading}<div class='answer'>{answer}</div></article>"


def _html_block(line: str) -> str:
    tag = "h2" if line.startswith("§") else "p"
    if line.startswith("Forskrift om"):
        tag = "h1"
    return f"<{tag}>{escape(line)}</{tag}>"


def minimal_pdf(lines: tuple[str, ...] = REGULATION_LINES, *, glyph_shift: int = 0) -> bytes:
    """A one-page PDF with one text line per entry, in Helvetica/WinAnsi.

    ``glyph_shift`` moves every character code by a constant while the font
    keeps its encoding — what a subset font without a ToUnicode map looks like
    to a text extractor (study §6.2, the Herøy case): the page renders, the
    extracted text is a cipher.
    """
    return _pdf_document(_pdf_stream(lines, glyph_shift))


def pdf_pages(*pages: tuple[str, ...]) -> bytes:
    """A PDF with one page per entry, each holding its lines as ``minimal_pdf`` does."""
    return _pdf_document(*(_pdf_stream(lines, 0) for lines in pages))


def _pdf_stream(lines: tuple[str, ...], glyph_shift: int) -> bytes:
    ops = [b"BT /F1 11 Tf 14 TL 72 760 Td"]
    for line in lines:
        raw = bytes((code + glyph_shift) % 256 for code in line.encode("cp1252"))
        ops.append(b"(" + _pdf_escape(raw) + b") Tj T*")
    ops.append(b"ET")
    return b"\n".join(ops)


def _pdf_escape(raw: bytes) -> bytes:
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _pdf_document(*streams: bytes) -> bytes:
    font = 3 + 2 * len(streams)
    kids = b" ".join(b"%d 0 R" % (3 + 2 * i) for i in range(len(streams)))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(streams)),
    ]
    for i, stream in enumerate(streams):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (4 + 2 * i, font)
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    objects.append(b"<< /Author (Kari Testperson) /Title (Utkast) >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\n" % (len(objects) + 1, len(objects))
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def minimal_docx(lines: tuple[str, ...] = REGULATION_LINES) -> bytes:
    """A DOCX holding one paragraph per entry, the second split across two runs."""
    paragraphs = "".join(_docx_paragraph(line) for line in lines)
    document = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_W}"><w:body>'
    document += paragraphs + "</w:body></w:document>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _docx_paragraph(line: str) -> str:
    half = len(line) // 2
    runs = (line[:half], line[half:])
    return "<w:p>" + "".join(f"<w:r><w:t>{escape(run)}</w:t></w:r>" for run in runs) + "</w:p>"


def docx_with_document(document: bytes, part: str = "word/document.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(part, document)
    return buffer.getvalue()
