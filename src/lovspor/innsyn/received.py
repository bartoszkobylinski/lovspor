"""Documents an authority sent in answer to a request, held by content (#509 point 5).

They live in the innsyn workspace under ``received/<sha256[:2]>/<sha256>``,
never in a repository: a received PDF carries more than the regulation (cover
letters, minutes pages, names), so only text extracted from it, after review,
is ever published. The file name is not kept either: it can name a case
officer or a party (personopplysningsloven). The ledger line is what says
which request the bytes answered and when they arrived.
"""

from pathlib import Path

from lovspor.atomic_io import atomic_write_bytes
from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.ledger import DocumentReceived, ReceivedMediaType, precheck, record_events
from lovspor.innsyn.workspace import InnsynWorkspace

MEDIA_TYPES: dict[str, ReceivedMediaType] = {
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


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
