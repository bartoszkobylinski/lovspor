"""Tests for lovspor.parsing.placeholder.

The real-world case is sf-20200309-0720, whose upstream ``<main>`` is
exactly::

    <main class="documentBody"><span class="errorMessage"><strong>Vi klarer
    dessverre ikke vise hele dokumentet.</strong></span></main>

Lovdata serves the document as current but substitutes an editorial error
notice for the legal text. Across both public-data archives (5,913 documents)
``errorMessage`` occurs in that one document and nowhere else.
"""

from lovspor.parsing.placeholder import is_content_placeholder


def _wrap(main_body: bytes) -> bytes:
    return (
        b'<!DOCTYPE html><html lang="nb"><body><main class="documentBody">'
        + main_body
        + b"</main></body></html>"
    )


_ERROR_NOTICE = (
    b'<span class="errorMessage"><strong>Vi klarer dessverre ikke vise hele '
    b"dokumentet.</strong></span>"
)


def test_upstream_error_notice_is_a_placeholder() -> None:
    assert is_content_placeholder(_wrap(_ERROR_NOTICE)) is True


def test_ordinary_document_is_not_a_placeholder() -> None:
    xml = _wrap(b"<h1>Lov om noe</h1><article class='legalP'><p>Teksten.</p></article>")
    assert is_content_placeholder(xml) is False


def test_error_notice_alongside_real_legal_content_is_not_a_placeholder() -> None:
    """Conservative: a document that still carries law stays in the corpus.

    Tombstoning is reserved for the case where the notice *replaces* the
    document. A partial document keeps its current behaviour (render, and
    fail loudly if the renderer cannot handle it) rather than silently
    dropping law out of the corpus.
    """
    xml = _wrap(_ERROR_NOTICE + b"<article class='legalArticle'><p>Ekte lovtekst.</p></article>")
    assert is_content_placeholder(xml) is False


def test_document_without_main_is_not_a_placeholder() -> None:
    xml = b'<!DOCTYPE html><html lang="nb"><body><p>Ingen main.</p></body></html>'
    assert is_content_placeholder(xml) is False


def test_malformed_xml_is_not_a_placeholder() -> None:
    """Malformed XML is not this predicate's problem.

    Returning ``False`` leaves the existing render path to raise ``ParseError``
    and skip the document, exactly as before. The predicate must never turn a
    parse failure into a silent corpus removal.
    """
    assert is_content_placeholder(b"<html><main>unclosed") is False


def test_empty_main_is_not_a_placeholder() -> None:
    """An empty body is not an upstream error notice.

    Only Lovdata's explicit ``errorMessage`` marker tombstones a document; a
    body that merely renders to nothing is a different (renderer-side)
    condition and must not be swept into the same bucket.
    """
    assert is_content_placeholder(_wrap(b"")) is False
