"""Append-only JSONL with the observation log's integrity rules (ADR-0010 §7).

Opened only for append, under an exclusive lock, flushed and fsynced, one
record per line. Read strictly: a line that does not parse is refused, never
skipped, and a last line without its newline is named as the torn write it
is. The same discipline as ``promotions.jsonl``
(:class:`lovspor.promotion.decisions.DecisionLog`).
"""

import fcntl
import os
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError

from lovspor.errors import InnsynRecordError


class Journal[RecordT: BaseModel]:
    """One append-only file of ``RecordT`` records."""

    def __init__(self, path: Path, adapter: TypeAdapter[RecordT]) -> None:
        self._path = path
        self._adapter = adapter

    @property
    def path(self) -> Path:
        return self._path

    def append(self, records: list[RecordT]) -> None:
        """Append ``records`` in one locked, fsynced write; the parent must exist."""
        payload = "".join(record.model_dump_json() + "\n" for record in records)
        with self._path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def records(self) -> list[RecordT]:
        """Every record in append order; an absent file is an empty journal.

        Raises:
            InnsynRecordError: a torn last line, or a line that does not parse.
        """
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").split("\n")
        if lines[-1]:
            raise InnsynRecordError(f"{self._path}: the last record is incomplete (no newline)")
        return [self._parse(line, number) for number, line in enumerate(lines[:-1], start=1)]

    def _parse(self, line: str, number: int) -> RecordT:
        try:
            return self._adapter.validate_json(line)
        except ValidationError as exc:
            raise InnsynRecordError(
                f"{self._path}:{number}: does not parse ({exc.error_count()} error(s)); "
                "the file is append-only evidence, so it is refused rather than read past",
            ) from exc
