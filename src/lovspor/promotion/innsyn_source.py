"""The innsyn channel's reader: a received regulation text, read from the workspace.

The second read-only reader of ADR-0016 A2, beside the observatory archive.
It folds the request ledger, finds one document an authority sent in answer
to a request, and reads its held copy; it writes nothing to the workspace.
Only a document the operator classified ``regulation_text`` is read (A1):
every other kind is innsyn material and never promoted.

The innsyn side does not re-hash a blob on read, so this reader does: the
copy is named by its SHA-256, and a copy that no longer hashes to its name
is not the document the ledger says arrived.

What is carried is what provenance needs and nothing more: no recipient,
sender, letter or file name (personopplysningsloven).
"""

import hashlib
import re

from pydantic import AwareDatetime, BaseModel, ConfigDict

from lovspor.errors import InnsynError, PromotionRefusedError
from lovspor.innsyn.ledger import ReceivedDocument, ReceivedMediaType, RequestState, requests
from lovspor.innsyn.received import blob_path
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.promotion.decisions import ReceivedKey
from lovspor.promotion.models import SAKSNUMMER_PATTERN, ReceivedSource

_SAKSNUMMER = re.compile(SAKSNUMMER_PATTERN)


class ReceivedArtifact(BaseModel):
    """One received regulation text and the receipt it arrived with."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: ReceivedKey
    content_type: ReceivedMediaType
    payload: bytes
    received_at: AwareDatetime
    saksnummer: str | None
    answers: str | None


def _ledger(workspace: InnsynWorkspace) -> dict[str, RequestState]:
    try:
        return requests(workspace)
    except InnsynError as exc:
        raise PromotionRefusedError(f"the innsyn ledger cannot be read: {exc}") from exc


def _state(held: dict[str, RequestState], request_id: str) -> RequestState:
    state = held.get(request_id)
    if state is None:
        raise PromotionRefusedError(f"no request {request_id} in the innsyn ledger")
    return state


def _document(state: RequestState, sha256: str) -> ReceivedDocument:
    for document in state.documents:
        if document.sha256 == sha256:
            return document
    raise PromotionRefusedError(
        f"{state.sent.request_id}: document {sha256} was not received on this request"
    )


def _require_regulation_text(request_id: str, document: ReceivedDocument) -> None:
    where = f"{request_id}: document {document.sha256}"
    if document.kind is None:
        raise PromotionRefusedError(
            f"{where} is not classified yet; classify it with classify-received first"
        )
    if document.kind != "regulation_text":
        raise PromotionRefusedError(
            f"{where} is classified {document.kind}; only a regulation_text is promoted, "
            "every other kind is innsyn material and never published (ADR-0016 A1)"
        )


def _payload(workspace: InnsynWorkspace, sha256: str) -> bytes:
    path = blob_path(workspace, sha256)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise PromotionRefusedError(
            f"document {sha256}: its held copy is missing or unreadable ({exc.strerror}): {path}"
        ) from exc
    if hashlib.sha256(payload).hexdigest() != sha256:
        raise PromotionRefusedError(
            f"document {sha256}: the held copy does not hash to its name; "
            f"refused rather than promoted: {path}"
        )
    return payload


def read_received(workspace: InnsynWorkspace, request_id: str, sha256: str) -> ReceivedArtifact:
    """The received regulation text ``sha256`` on request ``request_id``.

    Raises:
        PromotionRefusedError: the ledger does not read, holds no such request
            or no such document on it, the document is unclassified or not a
            regulation text, or its held copy is missing or not its hash.
    """
    state = _state(_ledger(workspace), request_id)
    document = _document(state, sha256)
    _require_regulation_text(request_id, document)
    key = ReceivedKey(
        authority_id=state.sent.authority_id, innsyn_request=request_id, sha256=sha256
    )
    return ReceivedArtifact(
        key=key,
        content_type=document.content_type,
        payload=_payload(workspace, sha256),
        received_at=document.received_at,
        saksnummer=state.saksnummer,
        answers=document.answers,
    )


def received_source(artifact: ReceivedArtifact) -> ReceivedSource:
    """The provenance a version rendered from ``artifact`` carries (ADR-0016 A3, A5).

    Raises:
        PromotionRefusedError: the saksnummer is not digits only. It is held,
            never rewritten, and never echoed: what is not digits can be a
            case officer's initials.
    """
    saksnummer = artifact.saksnummer
    if saksnummer is not None and _SAKSNUMMER.fullmatch(saksnummer) is None:
        raise PromotionRefusedError(
            f"{artifact.key.innsyn_request}: the recorded saksnummer does not have the "
            "digits-only shape (such as 2026/1234); held rather than rewritten (ADR-0016 A5)"
        )
    return ReceivedSource(
        received_at=artifact.received_at,
        innsyn_request=artifact.key.innsyn_request,
        innsyn_saksnummer=saksnummer,
        source_sha256=artifact.key.sha256,
    )
