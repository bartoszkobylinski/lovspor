"""The first-seen ledger of Lovdata local-regulation ids (issue #509).

The owner's rule: when a kommune's pages link an LF id the archive has not
seen that kommune link before, the regulation is requested from the kommune
under offentleglova the next day. This module keeps the fact that rule runs
on — for each (authority, kind, id), the earliest ``ObservedAt`` of a captured
page that linked it, with that page's URL and blob.

It lives beside the observation log, under the same checked root, never in a
repository (ADR-0010 §5): ``lf-ledger.jsonl`` is append-only, one entry per
key, and ``lf-ledger-cursor.json`` says how far into the log it has read, so a
nightly update reads only the records appended since — the new blobs, not
the archive. The cursor is trusted only when it proves itself the way the
freshness index does (issue #201): the log must still begin with the exact
bytes it was built from, and no correction may have landed after it. Any
doubt costs one full read, never a missed id; the full read appends nothing
the ledger already holds, so an update is idempotent however it starts.

Every entry is a function of the log alone — the minimum ``ObservedAt`` over
the pages that linked the key — so a damaged ledger is discarded and rebuilt
(``lovspor observatory lf-ledger --rebuild``), never repaired by hand. Only
the order of entries depends on when updates ran. An entry is never retracted:
a later correction that re-attributes a page adds the corrected authority's
entry and leaves the first one standing, because a request already sent on it
is history too.
"""

import fcntl
import hashlib
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import IO, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lovspor.atomic_io import atomic_write_bytes
from lovspor.errors import LogIntegrityError
from lovspor.observatory.lf_refs import LF_ID_PATTERN, LovdataKind, LovdataRef, extract_lovdata_refs
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, ObservationRecord, require_utc

LF_LEDGER_FILENAME = "lf-ledger.jsonl"
LF_CURSOR_FILENAME = "lf-ledger-cursor.json"

#: The calendar a "day" is counted on. The requests go to Norwegian bodies and
#: the nightly fires on Oslo time, so a page seen at 23:30 UTC on 1 October was
#: seen on 2 October.
LEDGER_DAY_ZONE = ZoneInfo("Europe/Oslo")

_DIGEST_CHUNK = 1 << 20

#: A page served with any other status is an error page or a redirect body: its
#: links are the site template's, not the page the archive asked for.
_SUCCESS = range(200, 300)

LedgerKey = tuple[str, str, str]


class LedgerEntry(BaseModel):
    """The first time the archive saw an authority's page link one regulation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str = Field(min_length=1)
    lf_id: str = Field(pattern=LF_ID_PATTERN)
    kind: LovdataKind
    first_seen: datetime
    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("first_seen")
    @classmethod
    def _utc_first_seen(cls, value: datetime) -> datetime:
        return require_utc(value)

    @property
    def key(self) -> LedgerKey:
        return (self.authority_id, self.kind, self.lf_id)


class LedgerCursor(BaseModel):
    """How far into the log the ledger has read, and the proof of those bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    log_offset: int = Field(ge=0)
    prefix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LedgerUpdate(BaseModel):
    """What one update read and what it added."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    started_at_offset: int = Field(ge=0)
    html_records: int = Field(ge=0)
    blobs_read: int = Field(ge=0)
    blobs_missing: int = Field(ge=0)
    appended: tuple[LedgerEntry, ...]


def ledger_path(log: ObservationLog) -> Path:
    return log.root / LF_LEDGER_FILENAME


def ledger_cursor_path(log: ObservationLog) -> Path:
    return log.root / LF_CURSOR_FILENAME


def _is_html_capture(record: ArtifactObservation) -> bool:
    media_type = record.content_type.split(";", 1)[0].lower()
    return record.http_status in _SUCCESS and "html" in media_type


@dataclass
class _Harvest:
    """The fold: earliest sighting per key, reading each distinct blob once."""

    log: ObservationLog
    earliest: dict[LedgerKey, LedgerEntry] = field(default_factory=dict)
    refs_by_blob: dict[str, tuple[LovdataRef, ...]] = field(default_factory=dict)
    html_records: int = 0
    blobs_read: int = 0
    blobs_missing: int = 0

    def __call__(self, record: ObservationRecord) -> None:
        if not isinstance(record, ArtifactObservation) or not _is_html_capture(record):
            return
        self.html_records += 1
        for ref in self._refs(record.sha256):
            self._keep(_entry(record, ref))

    def _refs(self, sha256: str) -> tuple[LovdataRef, ...]:
        cached = self.refs_by_blob.get(sha256)
        if cached is not None:
            return cached
        try:
            refs = extract_lovdata_refs(self.log.read_blob(sha256))
            self.blobs_read += 1
        except FileNotFoundError:
            # A tombstoned removal, or damage `verify` reports; not this fold's call.
            refs = ()
            self.blobs_missing += 1
        self.refs_by_blob[sha256] = refs
        return refs

    def _keep(self, entry: LedgerEntry) -> None:
        held = self.earliest.get(entry.key)
        if held is None or entry.first_seen < held.first_seen:
            self.earliest[entry.key] = entry


def _entry(record: ArtifactObservation, ref: LovdataRef) -> LedgerEntry:
    return LedgerEntry(
        authority_id=record.authority_id,
        lf_id=ref.lf_id,
        kind=ref.kind,
        first_seen=record.observed_at,
        url=record.url,
        sha256=record.sha256,
    )


def update_ledger(log: ObservationLog, *, rebuild: bool = False) -> LedgerUpdate:
    """Fold the log records the ledger has not read, and append the new keys.

    Held under an exclusive lock on the ledger for its whole length, so a
    manual run and the nightly cannot both append the same key.

    Raises:
        LogIntegrityError: the log does not read clean (nothing is appended
            and the cursor does not move), or the ledger itself does not parse.
    """
    path = ledger_path(log)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        known = {entry.key for entry in _parse_ledger(handle, path)}
        start = 0 if rebuild else _resume_offset(log)
        harvest, end = _harvest(log, start)
        fresh = _fresh(harvest, known)
        _append(handle, fresh)
        _write_cursor(log, end)
    return LedgerUpdate(
        started_at_offset=start,
        html_records=harvest.html_records,
        blobs_read=harvest.blobs_read,
        blobs_missing=harvest.blobs_missing,
        appended=fresh,
    )


def _harvest(log: ObservationLog, start: int) -> tuple[_Harvest, int]:
    harvest = _Harvest(log)
    scan = log.scan_corrected_into(harvest, start=start)
    if not scan.complete:
        raise LogIntegrityError(
            f"{log.log_path}: unreadable record after byte {scan.clean_through}; "
            "the LF ledger is not updated from a damaged log (run `observatory verify`)",
        )
    return harvest, scan.clean_through


def _fresh(harvest: _Harvest, known: set[LedgerKey]) -> tuple[LedgerEntry, ...]:
    """The keys the ledger lacks, in first-seen order — fixed for a given log."""
    new = [entry for key, entry in harvest.earliest.items() if key not in known]
    return tuple(sorted(new, key=lambda entry: (entry.first_seen, entry.key)))


def _append(handle: IO[str], entries: tuple[LedgerEntry, ...]) -> None:
    handle.write("".join(entry.model_dump_json() + "\n" for entry in entries))
    handle.flush()
    os.fsync(handle.fileno())


def read_ledger(log: ObservationLog) -> list[LedgerEntry]:
    """Every entry, in the order appended; an absent ledger is an empty one.

    Raises:
        LogIntegrityError: a line does not parse.
    """
    path = ledger_path(log)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return _parse_ledger(handle, path)


def _parse_ledger(handle: IO[str], path: Path) -> list[LedgerEntry]:
    handle.seek(0)
    entries = []
    for number, line in enumerate(handle, start=1):
        try:
            entries.append(LedgerEntry.model_validate_json(line))
        except ValidationError as exc:
            raise LogIntegrityError(
                f"{path}:{number}: unreadable ledger entry; the ledger is derived from the "
                "log, so remove it and run `lovspor observatory lf-ledger --rebuild`",
            ) from exc
    return entries


def _resume_offset(log: ObservationLog) -> int:
    """Where the cursor proves the unread part of the log begins, else 0."""
    cursor = _load_cursor(ledger_cursor_path(log))
    if cursor is None or log.corrections().last_offset > cursor.log_offset:
        return 0
    size = log.log_path.stat().st_size if log.log_path.exists() else 0
    if cursor.log_offset > size:
        return 0
    if _prefix_digest(log.log_path, cursor.log_offset) != cursor.prefix_sha256:
        return 0
    return cursor.log_offset


def _load_cursor(path: Path) -> LedgerCursor | None:
    try:
        return LedgerCursor.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


def _write_cursor(log: ObservationLog, offset: int) -> None:
    digest = _prefix_digest(log.log_path, offset)
    cursor = LedgerCursor(log_offset=offset, prefix_sha256=digest)
    # The cursor sits in the root, which a write never creates (#534).
    payload = (cursor.model_dump_json() + "\n").encode()
    atomic_write_bytes(ledger_cursor_path(log), payload, make_parents=False)


def _prefix_digest(log_path: Path, offset: int) -> str:
    """SHA-256 of the log's first ``offset`` bytes, read in bounded chunks."""
    digest = hashlib.sha256()
    remaining = offset
    if remaining:
        with log_path.open("rb") as handle:
            while remaining:
                chunk = handle.read(min(_DIGEST_CHUNK, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)
    return digest.hexdigest()


def first_seen_between(entries: list[LedgerEntry], first: date, last: date) -> list[LedgerEntry]:
    """Entries first seen on an Oslo calendar day from ``first`` to ``last``, both included."""
    return [
        entry
        for entry in entries
        if first <= entry.first_seen.astimezone(LEDGER_DAY_ZONE).date() <= last
    ]
