"""Documents an authority sent in answer to a request, held by content (#509 point 5).

They live in the innsyn workspace under ``received/<sha256[:2]>/<sha256>``,
never in a repository: a received PDF carries more than the regulation (cover
letters, minutes pages, names), so only text extracted from it, after review,
is ever published. The file name is not kept either: it can name a case
officer or a party (personopplysningsloven). The ledger line is what says
which request the bytes answered and when they arrived.

An authority that delivers documents as links (#688: Nordre Follo's 47
opengov links, Akershus's eInnsyn links) delivers them all the same: the
engine reads each link once, holds the bytes as it would an attachment, and
the ledger line carries the URL as delivered and when it was read.

Scanned archive documents come as images (#691: Høyanger's ``Forskrift.TIF``,
the text as sent to Norsk Lovtidend; Kragerø's boundary maps as JPEG). They
are held by content like any other document, with no text read from them:
the promotion extractor holds an image as an unsupported format.

A linked HTML page a reader would see nothing in is refused before it is held
(#721: Drammen's Digdem links all returned one JavaScript app shell, whose only
text is the app's title and a ``noscript`` notice). The ledger is append-only,
so a page with no document in it, once held, stays held.
"""

from pathlib import Path
from urllib.parse import urlsplit

import httpx
from lxml import etree, html

from lovspor.atomic_io import atomic_write_bytes
from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.ledger import DocumentReceived, ReceivedMediaType, precheck, record_events
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.listing import safe_html_parser
from lovspor.sources.lovdata import DEFAULT_USER_AGENT

MEDIA_TYPES: dict[str, ReceivedMediaType] = {
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
FORMATS = "PDF, HTML, DOCX, TIFF, JPEG, PNG"
_BY_HEADER: dict[str, ReceivedMediaType] = {value: value for value in MEDIA_TYPES.values()}
#: A Content-Type that says nothing about the document: the link's suffix is read instead.
_GENERIC = frozenset({"", "application/octet-stream"})
#: What a reader of a page never sees as its text: the head (the title names the
#: site or the app), page code and styles, the notice for a browser without
#: JavaScript, and templates not yet rendered.
_UNSEEN = ("head", "script", "style", "noscript", "template")
_HTML: ReceivedMediaType = "text/html"
LINK_TIMEOUT_SECONDS = 60.0
_HTTP_OK = 200


def media_type(path: Path) -> ReceivedMediaType:
    """Raises: InnsynRefusedError: a suffix of no format the ledger holds."""
    try:
        return MEDIA_TYPES[path.suffix.lower()]
    except KeyError as exc:
        raise InnsynRefusedError(f"{path.name}: not a format the ledger holds ({FORMATS})") from exc


def read_document(path: Path) -> bytes:
    """Raises: InnsynRefusedError: missing, unreadable or empty."""
    try:
        payload = path.read_bytes()
    except FileNotFoundError as exc:
        raise InnsynRefusedError(f"no such file: {path}") from exc
    except OSError as exc:
        raise InnsynRefusedError(f"cannot read {path}: {exc.strerror}") from exc
    if not payload:
        raise InnsynRefusedError(f"{path} is empty")
    return payload


def link_client() -> httpx.Client:
    return httpx.Client(
        timeout=LINK_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": DEFAULT_USER_AGENT},
    )


def _link_media_type(url: str, header: str) -> ReceivedMediaType:
    """The header's type when the ledger holds it; the URL's suffix when it says nothing.

    A server that names a type the ledger does not hold is believed: a GIF
    served from a ``.pdf`` link is not a PDF.

    Raises: InnsynRefusedError: neither names a format the ledger holds.
    """
    declared = header.partition(";")[0].strip().lower()
    if declared in _BY_HEADER:
        return _BY_HEADER[declared]
    suffix = Path(urlsplit(url).path).suffix.lower()
    if declared in _GENERIC and suffix in MEDIA_TYPES:
        return MEDIA_TYPES[suffix]
    raise InnsynRefusedError(
        f"{url}: {declared or 'no content type'} is not a format the ledger holds ({FORMATS})"
    )


def fetch_document(client: httpx.Client, url: str) -> tuple[bytes, ReceivedMediaType]:
    """Read a linked document once, as the authority delivered the link.

    Raises:
        InnsynRefusedError: the read failed, was not answered 200, came back
            empty, or in a format the ledger does not hold.
    """
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise InnsynRefusedError(f"{url}: could not fetch: {exc}") from exc
    if response.status_code != _HTTP_OK:
        raise InnsynRefusedError(f"{url}: HTTP {response.status_code}")
    if not response.content:
        raise InnsynRefusedError(f"{url}: the document is empty")
    content_type = _link_media_type(url, response.headers.get("content-type", ""))
    if content_type == _HTML and not html_text(response.content):
        raise InnsynRefusedError(
            f"{url}: the HTML page has no readable text (a page that needs JavaScript, such as "
            "an app shell, holds no document); fetch the document itself another way and hold it "
            "with record-received"
        )
    return response.content, content_type


def html_text(payload: bytes) -> str:
    """The text a reader of the page sees, whitespace collapsed; empty when there is none."""
    try:
        root = html.document_fromstring(payload, parser=safe_html_parser())
    except etree.ParserError:
        return ""
    etree.strip_elements(root, *_UNSEEN, with_tail=False)
    texts = (text for text in root.itertext() if isinstance(text, str))
    return " ".join(" ".join(texts).split())


def blob_path(workspace: InnsynWorkspace, sha256: str) -> Path:
    return workspace.received_dir / sha256[:2] / sha256


def record_received(workspace: InnsynWorkspace, event: DocumentReceived, payload: bytes) -> None:
    """Check, then store, then record: a refused receipt stores nothing.

    A blob without its ledger line (a crash between the two) is harmless and
    is reused by the rerun; a ledger line without its blob could not be.

    Raises:
        InnsynRefusedError: the ledger refuses the receipt.
    """
    precheck(workspace, [event])
    target = blob_path(workspace, event.sha256)
    if not target.exists():
        workspace.ensure(workspace.received_dir)
        atomic_write_bytes(target, payload)
    record_events(workspace, [event])
