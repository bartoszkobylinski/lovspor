"""Import the hand-kept pilot register (``sent.csv``, 2026-10-05) into the ledger.

The register says how many ids each letter asked for, not which; the letters
as sent (``innsyn-<authority>.txt`` beside it) list them, so each row is read
together with its letter and refused when the two disagree. The letter's
SHA-256 is the one recorded.

Columns used: ``authority_id``, ``sent_at``, ``recipient``, ``n_ids``,
``kommune_saksnummer``, ``acknowledged_at``, ``answered_at``, ``outcome``,
``refusal_basis``, ``klage_sent_at``. The outcome cell is the class before the
first ``;`` (``partial; 1 document pending`` -> ``partial``); the rest is a
note. ``notes`` and ``sent_from`` are not imported: the notes name case
officers (personopplysningsloven: keep the minimum). An outcome the register
gives no time for is recorded with none, rather than with an invented one.

The pilot letters had no general clause. Every event is marked
``source: pilot-import``. Rows already in the ledger are skipped, so a rerun
imports nothing twice; the rest go in one write or not at all.
"""

import csv
import hashlib
import io
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.errors import InnsynRecordError
from lovspor.innsyn.ledger import LedgerEvent, parse_event, request_id

_LETTER_ID = re.compile(r"^- FOR-(\d{4}-\d{2}-\d{2}-\d+)\b", re.MULTILINE)

_REQUIRED = ("authority_id", "sent_at", "recipient", "n_ids")


class PilotRow(BaseModel):
    """One register row with the ids its letter listed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    events: tuple[LedgerEvent, ...]

    @property
    def request_id(self) -> str:
        return self.events[0].request_id


def _instant(raw: str) -> datetime | None:
    text = raw.strip()
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC) if text else None


def _letter(letters: Path, authority_id: str, n_ids: int) -> tuple[list[str], str]:
    path = letters / f"innsyn-{authority_id}.txt"
    try:
        payload = path.read_bytes()
    except FileNotFoundError as exc:
        raise InnsynRecordError(f"{path}: the letter as sent is missing") from exc
    ids = sorted(set(_LETTER_ID.findall(payload.decode())))
    if len(ids) != n_ids:
        raise InnsynRecordError(f"{path}: lists {len(ids)} ids, the register says {n_ids}")
    return ids, hashlib.sha256(payload).hexdigest()


def _base(row: dict[str, str], recorded_at: datetime) -> dict[str, object]:
    sent_at = _instant(row["sent_at"])
    if sent_at is None:
        raise InnsynRecordError(f"{row['authority_id']}: no sent_at")
    return {
        "request_id": request_id(row["authority_id"], sent_at),
        "recorded_at": recorded_at,
        "source": "pilot-import",
    }


def _transitions(row: dict[str, str], base: dict[str, object]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if row.get("acknowledged_at", "").strip():
        found.append({"event": "acknowledged", "acknowledged_at": _instant(row["acknowledged_at"])})
    if row.get("kommune_saksnummer", "").strip():
        found.append({"event": "case_number", "saksnummer": row["kommune_saksnummer"]})
    outcome = row.get("outcome", "").partition(";")[0].strip()
    if outcome:
        basis = row.get("refusal_basis", "").strip() or None
        at = _instant(row.get("answered_at", ""))
        found.append({"event": "outcome", "outcome": outcome, "at": at, "refusal_basis": basis})
    if row.get("klage_sent_at", "").strip():
        found.append({"event": "klage", "klage_sent_at": _instant(row["klage_sent_at"])})
    return [{**base, **transition} for transition in found]


def _row(row: dict[str, str], letters: Path, recorded_at: datetime) -> PilotRow:
    base = _base(row, recorded_at)
    ids, digest = _letter(letters, row["authority_id"], int(row["n_ids"]))
    sent = {
        **base,
        "event": "sent",
        "authority_id": row["authority_id"],
        "recipient": row["recipient"].strip().lower(),
        "sent_at": _instant(row["sent_at"]),
        "lf_ids": ids,
        "general_clause": False,
        "letter_sha256": digest,
    }
    events = [parse_event(data) for data in [sent, *_transitions(row, base)]]
    return PilotRow(events=tuple(events))


def read_register(register: Path, letters: Path, recorded_at: datetime) -> list[PilotRow]:
    """Every row of the register with its letter's ids.

    Raises:
        InnsynRecordError: a missing column, a row that does not validate, or
            a letter that is missing or disagrees with its row.
    """
    try:
        rows = list(csv.DictReader(io.StringIO(register.read_bytes().decode())))
    except FileNotFoundError as exc:
        raise InnsynRecordError(f"{register}: no such register") from exc
    except UnicodeDecodeError as exc:
        raise InnsynRecordError(f"{register}: not UTF-8 at byte {exc.start}") from exc
    missing = [c for c in _REQUIRED if rows and c not in rows[0]]
    if missing:
        raise InnsynRecordError(f"{register}: missing columns {', '.join(missing)}")
    parsed = []
    for number, row in enumerate(rows, start=2):
        try:
            parsed.append(_row(row, letters, recorded_at))
        except ValueError as exc:  # ValidationError included
            raise InnsynRecordError(f"{register}:{number}: {exc}") from exc
    return parsed
