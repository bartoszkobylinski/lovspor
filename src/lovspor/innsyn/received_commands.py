"""The received-document commands: hold, classify, list (#509 point 5).

Two commands rather than one: receiving is a fact read off the mail, while
classifying is the operator's reading of the document, which can be revised.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from lovspor.innsyn.app import guarded, innsyn_app, instant, now, refuse, workspace
from lovspor.innsyn.ledger import (
    DOCUMENT_KINDS,
    DocumentReceived,
    ReceivedMediaType,
    RequestState,
    requests,
)
from lovspor.innsyn.ledger_commands import Request, record_event
from lovspor.innsyn.received import media_type, read_document, record_received


def _receipt(
    request: str, sha256: str, content_type: ReceivedMediaType, received_at: datetime
) -> DocumentReceived:
    fields = {"received_at": received_at, "sha256": sha256, "content_type": content_type}
    try:
        return DocumentReceived.model_validate(
            {"request_id": request, "recorded_at": now(), **fields}
        )
    except ValidationError as exc:
        raise refuse(exc) from exc


@innsyn_app.command("record-received")
def record_received_command(
    request: Request,
    file: Annotated[Path, typer.Option("--file", help="The document as the authority sent it.")],
    at: Annotated[str, typer.Option("--at", help="When the mail carrying it arrived (UTC).")],
) -> None:
    """Hold a document the authority sent, by content, and record when it arrived.

    The file name is not kept. Formats: PDF, HTML, DOCX.
    """
    target = workspace()
    content_type = guarded(lambda: media_type(file))
    payload = guarded(lambda: read_document(file))
    sha256 = hashlib.sha256(payload).hexdigest()
    event = _receipt(request, sha256, content_type, instant(at, "--at"))
    guarded(lambda: record_received(target, event, payload))
    typer.echo(f"{request}: document {sha256} received")


@innsyn_app.command("classify-received")
def classify_received(
    request: Request,
    document: Annotated[str, typer.Option("--document", help="The document's SHA-256.")],
    kind: Annotated[str, typer.Option("--kind", help=" | ".join(DOCUMENT_KINDS))],
    answers: Annotated[
        str | None,
        typer.Option("--answers", help="The requested LF id it is the text or vedtak of."),
    ] = None,
) -> None:
    """Record what a received document is; without --answers it was not requested."""
    record_event(
        request, "document_classified", {"sha256": document, "kind": kind, "answers": answers}
    )


def _rows(state: RequestState) -> list[str]:
    rows = []
    for document in state.documents:
        answers = document.answers or ("" if document.kind is None else "unrequested")
        fields = (
            state.sent.request_id,
            document.received_at.isoformat(),
            document.sha256,
            document.content_type,
            document.kind or "unclassified",
            answers,
        )
        rows.append("\t".join(fields))
    return rows


@innsyn_app.command("received")
def list_received() -> None:
    """List every received document, tab-separated.

    Columns: request, received_at, sha256, media type, kind (or unclassified),
    the requested LF id it answers (or unrequested). On stderr, per request
    with documents: the requested ids no received regulation text answers.
    """
    held = [s for s in guarded(lambda: requests(workspace())).values() if s.documents]
    for state in held:
        for row in _rows(state):
            typer.echo(row)
    documents = [d for state in held for d in state.documents]
    unclassified = sum(1 for d in documents if d.kind is None)
    typer.echo(f"{len(documents)} documents, {unclassified} unclassified", err=True)
    for state in (s for s in held if s.sent.lf_ids):
        missing, asked = len(state.unanswered_ids), len(state.sent.lf_ids)
        line = (
            f"{state.sent.request_id}: {missing} of {asked} requested ids without a received text"
        )
        typer.echo(line, err=True)
