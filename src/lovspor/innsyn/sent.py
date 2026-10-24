"""A letter recorded as sent: a wave's, which ``record-sent`` and ``send`` share, or a follow-up.

The event takes the ids, the general clause and the recipient from the
manifest and the SHA-256 of the letter as it went out, so a letter recorded
by hand and one delivered by ``send`` leave the same line in the ledger.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.ledger import Sent, request_id
from lovspor.innsyn.received import read_document
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


def _sent_data(entry: ManifestEntry, digest: str, sent_at: datetime) -> dict[str, object]:
    return {
        "event": "sent",
        "request_id": request_id(entry.authority_id, sent_at),
        "recorded_at": datetime.now(UTC),
        "authority_id": entry.authority_id,
        "recipient": entry.recipient,
        "channel": entry.channel,
        "sent_at": sent_at,
        "lf_ids": entry.lf_ids,
        "general_clause": entry.general_clause,
        "letter_sha256": digest,
    }


def sent_event(
    entry: ManifestEntry, digest: str, sent_at: datetime, message_id: str | None
) -> Sent:
    """The ``sent`` event for ``entry``, recorded now.

    Raises:
        ValidationError: ``sent_at`` is not UTC, ``message_id`` is not one, or
            the letter goes by a web form or eDialog.
    """
    return Sent.model_validate({**_sent_data(entry, digest, sent_at), "message_id": message_id})


def manual_sent_event(entry: ManifestEntry, digest: str, sent_at: datetime, receipt: str) -> Sent:
    """The ``sent`` event for a letter the owner sent by hand, with its receipt's SHA-256.

    Raises:
        InnsynRefusedError: the letter goes by e-mail; ValidationError: ``sent_at`` is not UTC.
    """
    if entry.channel == "email":
        raise InnsynRefusedError(
            f"{entry.authority_id} goes by e-mail: `send` it, or record it with `record-sent`"
        )
    return Sent.model_validate({**_sent_data(entry, digest, sent_at), "receipt_sha256": receipt})


def addressee(letter: Path, payload: bytes) -> str:
    """The mailbox of a letter's ``Til:`` line; Sent checks it is one.

    Raises:
        InnsynRefusedError: the first line is not ``Til: …``.
    """
    first = payload.decode().partition("\n")[0]
    if not first.startswith("Til: "):
        raise InnsynRefusedError(f"{letter}: the first line is not `Til: <mailbox>`")
    return first.removeprefix("Til: ").strip()


def follow_up_event(parent: Sent, letter: Path, sent_at: datetime, message_id: str | None) -> Sent:
    """A request following ``parent``, to the letter's addressee, asking what it asked.

    Raises:
        InnsynRefusedError: the letter is missing, empty or names no addressee;
        ValidationError: the addressee is not a mailbox, or ``message_id`` is not one.
    """
    payload = read_document(letter)
    asked = parent.model_dump(include={"authority_id", "lf_ids", "general_clause"})
    data = {
        **asked,
        "event": "sent",
        "request_id": request_id(parent.authority_id, sent_at),
        "recorded_at": datetime.now(UTC),
        "recipient": addressee(letter, payload),
        "sent_at": sent_at,
        "letter_sha256": hashlib.sha256(payload).hexdigest(),
        "message_id": message_id,
        "follows": parent.request_id,
    }
    return Sent.model_validate(data)
