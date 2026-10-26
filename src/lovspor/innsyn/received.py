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
"""

from pathlib import Path
from urllib.parse import urlsplit

import httpx

from lovspor.atomic_io import atomic_write_bytes
from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.ledger import DocumentReceived, ReceivedMediaType, precheck, record_events
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.sources.lovdata import DEFAULT_USER_AGENT

MEDIA_TYPES: dict[str, ReceivedMediaType] = {
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_BY_HEADER: dict[str, ReceivedMediaType] = {value: value for value in MEDIA_TYPES.values()}
LINK_TIMEOUT_SECONDS = 60.0
_HTTP_OK = 200


def media_type(path: Path) -> ReceivedMediaType:
    """Raises: InnsynRefusedError: a suffix the promotion extractor cannot read."""
    try:
        return MEDIA_TYPES[path.suffix.lower()]
    except KeyError as exc:
        raise InnsynRefusedError(
            f"{path.name}: not a format the extractor reads (PDF, HTML, DOCX)"
        ) from exc


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
    """The header's type when the extractor reads it, else the URL's suffix.

    Raises: InnsynRefusedError: neither names PDF, HTML or DOCX.
    """
    declared = header.partition(";")[0].strip().lower()
    if declared in _BY_HEADER:
        return _BY_HEADER[declared]
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in MEDIA_TYPES:
        return MEDIA_TYPES[suffix]
    raise InnsynRefusedError(
        f"{url}: {declared or 'no content type'} is not a format the extractor reads "
        "(PDF, HTML, DOCX)"
    )


def fetch_document(client: httpx.Client, url: str) -> tuple[bytes, ReceivedMediaType]:
    """Read a linked document once, as the authority delivered the link.

    Raises:
        InnsynRefusedError: the read failed, was not answered 200, came back
            empty, or in a format the extractor cannot read.
    """
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise InnsynRefusedError(f"{url}: could not fetch: {exc}") from exc
    if response.status_code != _HTTP_OK:
        raise InnsynRefusedError(f"{url}: HTTP {response.status_code}")
    if not response.content:
        raise InnsynRefusedError(f"{url}: the document is empty")
    return response.content, _link_media_type(url, response.headers.get("content-type", ""))


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
