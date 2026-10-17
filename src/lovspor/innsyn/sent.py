"""A letter of a wave recorded as sent: the one path ``record-sent`` and ``send`` share.

The event takes the ids, the general clause and the recipient from the
manifest and the SHA-256 of the letter as it went out, so a letter recorded
by hand and one delivered by ``send`` leave the same line in the ledger.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.ledger import Sent, request_id
from lovspor.innsyn.waves import ManifestEntry, read_manifest


def manifest_entry(manifest_path: Path, authority_id: str) -> ManifestEntry:
    """Raises: InnsynRefusedError: no manifest there, or no letter in it for the authority."""
    manifest = read_manifest(manifest_path)
    for entry in manifest.letters:
        if entry.authority_id == authority_id:
            return entry
    raise InnsynRefusedError(f"{manifest_path} has no letter for {authority_id}")


def letter_digest(manifest_path: Path, entry: ManifestEntry) -> str:
    """The SHA-256 of the letter as it stands, after its ``Til:`` is checked."""
    letter = manifest_path.parent / entry.letter_file
    payload = letter.read_bytes()
    first = payload.decode().partition("\n")[0]
    if first != f"Til: {entry.recipient}":
        raise InnsynRefusedError(f"{letter}: {first!r} is not the manifest's recipient")
    return hashlib.sha256(payload).hexdigest()


def sent_event(
    entry: ManifestEntry, digest: str, sent_at: datetime, message_id: str | None
) -> Sent:
    """The ``sent`` event for ``entry``, recorded now.

    Raises:
        ValidationError: ``sent_at`` is not UTC, or ``message_id`` is not one.
    """
    data = {
        "event": "sent",
        "request_id": request_id(entry.authority_id, sent_at),
        "recorded_at": datetime.now(UTC),
        "authority_id": entry.authority_id,
        "recipient": entry.recipient,
        "sent_at": sent_at,
        "lf_ids": entry.lf_ids,
        "general_clause": entry.general_clause,
        "letter_sha256": digest,
        "message_id": message_id,
    }
    return Sent.model_validate(data)
