"""A linked HTML page with no document in it is refused before it is held (#721).

Drammen answered with Digdem meeting links; each returned the same JavaScript
app shell, whose only text is the app's title and a ``noscript`` notice. The
first was held as a received document. The operator's question: does
``record-received-link`` refuse a page a reader would see nothing in, before
the blob or the append-only ledger line is written, while still holding a
real HTML page, however short, and every other format? The server is mocked
at the transport; the pages are synthetic.
"""

import hashlib
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.innsyn.ledger import ledger_journal
from lovspor.innsyn.received import blob_path, html_text
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.innsyn_support import FIXTURES, innsyn

FJALER = "4646-20261005T074425Z"
ARRIVED = "2026-10-21T16:01:12Z"
URL = "https://moter.example.no/motekalender/abc123?proceedingIdQ=def456"
SHELL = (
    b'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Moteapp</title>'
    b'<link rel="stylesheet" href="/static/main.css"><script src="/static/main.js"></script>'
    b"</head><body><noscript>You need to enable JavaScript to run this app.</noscript>"
    b'<div id="root"></div></body></html>'
)
PAGE = (
    b'<!doctype html><html><head><meta charset="utf-8"><title>Forskrift</title></head>'
    b"<body><noscript>Aktiver JavaScript.</noscript><h1>Forskrift om renovasjon</h1>"
    b"<p>\xc2\xa7 1. Form\xc3\xa5l</p></body></html>"
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "innsyn"
    result = innsyn(target, "import-pilot", "--register", str(FIXTURES / "pilot" / "sent.csv"))
    assert result.exit_code == 0, result.output
    return target


def _lines(workspace: Path) -> int:
    return len(ledger_journal(InnsynWorkspace(workspace, [])).records())


def _link(workspace: Path, httpx_mock: HTTPXMock, content: bytes, header: str) -> tuple[int, str]:
    httpx_mock.add_response(url=URL, content=content, headers={"content-type": header})
    args = ("--request", FJALER, "--url", URL, "--at", ARRIVED)
    result = innsyn(workspace, "record-received-link", *args)
    return result.exit_code, result.stdout + result.stderr


@pytest.mark.parametrize(
    "content",
    [
        SHELL,
        b"<html><head><title>Only a title</title></head><body>  \n&nbsp; </body></html>",
        b"<html><body><script>render()</script><style>p{}</style>"
        b"<template><p>later</p></template></body></html>",
        b"<html><body><!-- nothing here --></body></html>",
        b" \n\t ",
    ],
    ids=["app-shell", "blank-body", "code-only", "comment-only", "whitespace"],
)
def test_a_page_with_no_readable_text_is_refused_and_nothing_is_held(
    workspace: Path, httpx_mock: HTTPXMock, content: bytes
) -> None:
    before = _lines(workspace)
    code, output = _link(workspace, httpx_mock, content, "text/html; charset=utf-8")
    assert code == 1, output
    assert "Refused" in output
    assert (
        f"{URL}: the HTML page has no readable text (a page that needs JavaScript, such as an "
        "app shell, holds no document); fetch the document itself another way and hold it "
        "with record-received"
    ) in " ".join(output.split())
    assert _lines(workspace) == before
    assert not (workspace / "received").exists()


def test_an_html_suffix_with_a_generic_type_is_read_as_a_page_too(
    workspace: Path, httpx_mock: HTTPXMock
) -> None:
    url = "https://moter.example.no/dokument/sak.html"
    httpx_mock.add_response(url=url, content=SHELL, headers={"content-type": ""})
    args = ("--request", FJALER, "--url", url, "--at", ARRIVED)
    result = innsyn(workspace, "record-received-link", *args)
    assert result.exit_code == 1, result.output
    assert f"{url}: the HTML page has no readable text" in result.stdout + result.stderr
    assert not (workspace / "received").exists()


@pytest.mark.parametrize(
    "content",
    [PAGE, b"<p>\xc2\xa7 1.</p>", b"<html><body><div id='root'>Vedtak</div></body></html>"],
    ids=["page-with-a-noscript-notice", "short-fragment", "rendered-app"],
)
def test_a_page_with_text_is_held(workspace: Path, httpx_mock: HTTPXMock, content: bytes) -> None:
    code, output = _link(workspace, httpx_mock, content, "text/html")
    assert code == 0, output
    sha = hashlib.sha256(content).hexdigest()
    assert blob_path(InnsynWorkspace(workspace, []), sha).read_bytes() == content


@pytest.mark.parametrize(
    "header",
    [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/png",
    ],
)
def test_other_formats_are_not_read_as_pages(
    workspace: Path, httpx_mock: HTTPXMock, header: str
) -> None:
    """Bytes that would read as an empty page are held when they are not HTML."""
    code, output = _link(workspace, httpx_mock, SHELL, header)
    assert code == 0, output


@pytest.mark.parametrize(
    ("content", "text"),
    [
        (SHELL, ""),
        (PAGE, "Forskrift om renovasjon § 1. Formål"),
        (b"<p>a</p>\n<p>b</p>", "a b"),
        (b"<html><body>x<script>y</script>z</body></html>", "xz"),
        (b"   ", ""),
        (b"<template>Not rendered</template>", ""),
        (b"<noscript>Enable JavaScript</noscript>", ""),
        (b"<script>render()</script>", ""),
        (b"<style>p{}</style>X", "X"),
    ],
)
def test_html_text_is_what_a_reader_sees(content: bytes, text: str) -> None:
    assert html_text(content) == text


def test_html_comments_do_not_insert_spaces_inside_visible_words() -> None:
    assert html_text(b"<p>Ved<!-- internal note -->tak</p>") == "Vedtak"


@pytest.mark.parametrize("tag", ["script", "style", "noscript", "template"])
def test_unseen_elements_preserve_adjacent_visible_text(tag: str) -> None:
    page = f"<html><body>A<{tag}>hidden</{tag}>Vedtak</body></html>".encode()
    assert html_text(page) == "AVedtak"


@pytest.mark.parametrize("tag", ["script", "style", "noscript", "template"])
def test_one_character_after_an_unseen_element_is_still_a_document(
    workspace: Path, httpx_mock: HTTPXMock, tag: str
) -> None:
    """Removing page code must preserve its tail: any text is held (#721)."""
    content = f"<html><body><{tag}>ignored</{tag}>X</body></html>".encode()
    assert html_text(content) == "X"
    code, output = _link(workspace, httpx_mock, content, "text/html")
    assert code == 0, output
    sha = hashlib.sha256(content).hexdigest()
    assert blob_path(InnsynWorkspace(workspace, []), sha).read_bytes() == content


@pytest.mark.parametrize(
    ("url", "header"),
    [
        ("https://example.no/document.pdf", "text/html; charset=utf-8"),
        ("https://example.no/document.HTM?download=1", "application/octet-stream"),
    ],
)
def test_shell_refusal_uses_the_resolved_media_type(
    workspace: Path, httpx_mock: HTTPXMock, url: str, header: str
) -> None:
    httpx_mock.add_response(url=url, content=SHELL, headers={"content-type": header})
    before = _lines(workspace)
    result = innsyn(
        workspace, "record-received-link", "--request", FJALER, "--url", url, "--at", ARRIVED
    )
    assert result.exit_code == 1, result.output
    assert f"{url}: the HTML page has no readable text" in result.stdout + result.stderr
    assert _lines(workspace) == before
    assert not (workspace / "received").exists()


def test_shell_refusal_preserves_an_existing_receipt_and_workspace(
    workspace: Path, httpx_mock: HTTPXMock
) -> None:
    code, output = _link(workspace, httpx_mock, PAGE, "text/html")
    assert code == 0, output
    before = {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    code, output = _link(workspace, httpx_mock, SHELL, "text/html")
    assert code == 1, output
    assert f"{URL}: the HTML page has no readable text" in output
    after = {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize("space", [b"&#32;", b"&#9;", b"&#10;", b"&#160;", b"&nbsp;"])
def test_entity_encoded_whitespace_is_not_a_document(
    workspace: Path, httpx_mock: HTTPXMock, space: bytes
) -> None:
    """The documented blank-body refusal also applies after HTML entity decoding."""
    before = _lines(workspace)
    content = b"<html><body><p>" + space + b"</p></body></html>"
    code, output = _link(workspace, httpx_mock, content, "text/html")
    assert code == 1, output
    assert f"{URL}: the HTML page has no readable text" in output
    assert _lines(workspace) == before
    assert not (workspace / "received").exists()


def test_html_text_does_not_expand_external_file_entities(tmp_path: Path) -> None:
    """The new HTML read must preserve the project's entity-expansion safety rule."""
    secret = tmp_path / "private.txt"
    secret.write_text("PRIVATE_ENTITY_CONTENT", encoding="utf-8")
    content = (
        f'<!DOCTYPE html [<!ENTITY xxe SYSTEM "{secret.as_uri()}">]>'
        "<html><body><p>Vedtak &xxe;</p></body></html>"
    ).encode()
    text = html_text(content)
    assert "PRIVATE_ENTITY_CONTENT" not in text
    assert "Vedtak" in text
    assert "&xxe;" in text


def test_accepted_html_records_the_original_bytes_and_resolved_type(
    workspace: Path, httpx_mock: HTTPXMock
) -> None:
    """Text inspection must not replace the delivered page with extracted text."""
    from lovspor.innsyn.ledger import DocumentReceived  # noqa: PLC0415

    before = _lines(workspace)
    code, output = _link(workspace, httpx_mock, PAGE, "Text/HTML; charset=utf-8")
    assert code == 0, output
    journal = ledger_journal(InnsynWorkspace(workspace, [])).records()
    assert len(journal) == before + 1
    event = journal[-1]
    assert isinstance(event, DocumentReceived)
    assert event.sha256 == hashlib.sha256(PAGE).hexdigest()
    assert event.content_type == "text/html"
    assert event.source_url == URL
    assert blob_path(InnsynWorkspace(workspace, []), event.sha256).read_bytes() == PAGE
