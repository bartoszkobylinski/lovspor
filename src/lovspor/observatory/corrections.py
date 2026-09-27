"""The corrected view of the observation log (ADR-0015 §5, §6).

A correction names an earlier record by its key, so a single forward pass
cannot apply it: by the time the correction is read, the record it corrects
has already gone past. The correction set is therefore folded first — a cheap
pass that parses only the lines that can be corrections — and the log is then
read with each corrected original replaced **at its original position** by its
re-filed observation. Keeping the position keeps every order-dependent fold
(failure holds, runs of unchanged content) identical to what it would have been
had the record been filed correctly.

An original is corrected only when the log holds both halves of one
correction: a re-filed observation superseding it and a record tombstone
retracting it, under the same ``correction_id``. The writer appends the
re-filed half first, so at every crash point a reader sees either the original
or the corrected record — never both, never neither.

This module owns the rule so no reader has to learn it: every reader of
attribution goes through :meth:`ObservationLog.scan_corrected_into`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from lovspor.observatory.model import (
    ObservationRecord,
    RecordTombstone,
    RefiledObservation,
    record_key,
)

# Every correction line carries its kind literally: the log's writer
# serialises with ``ensure_ascii=False``, so neither name is ever escaped. A
# line that merely mentions one (in a URL, say) parses as what it is and is
# ignored — the filter only decides which lines are worth parsing.
_MARKERS = (b"record_tombstone", b"refiled_observation")

_ADAPTER: TypeAdapter[ObservationRecord] = TypeAdapter(ObservationRecord)


@dataclass(frozen=True)
class Refiled:
    """A re-filed observation and its own key — the key a later correction names."""

    record: RefiledObservation
    key: str


@dataclass
class CorrectionSet:
    """Every correction record in the log, by the key of the record it names."""

    refiled: dict[str, list[Refiled]] = field(default_factory=dict)
    tombstones: dict[str, list[RecordTombstone]] = field(default_factory=dict)
    #: Byte offset just past the last correction line folded; 0 when none.
    last_offset: int = 0

    def __bool__(self) -> bool:
        return bool(self.refiled or self.tombstones)

    def add(self, record: ObservationRecord, line: bytes, end: int) -> None:
        """Fold one line; anything but a correction is ignored."""
        if isinstance(record, RecordTombstone):
            self.tombstones.setdefault(record.retracts, []).append(record)
        elif isinstance(record, RefiledObservation):
            entry = Refiled(record, record_key(line))
            self.refiled.setdefault(record.correction.supersedes, []).append(entry)
        else:
            return
        self.last_offset = end

    def completed(self, key: str) -> list[Refiled]:
        """The completed corrections of ``key``, in the order their tombstones landed."""
        halves = self.refiled.get(key, [])
        found: list[Refiled] = []
        for tombstone in self.tombstones.get(key, []):
            for entry in halves:
                if entry.record.correction.correction_id == tombstone.correction_id:
                    found.append(entry)
        return found

    def in_force(self, key: str) -> Refiled | None:
        """The correction that stands for ``key``, or None when the original does."""
        completed = self.completed(key)
        return completed[0] if completed else None

    def resolve(self, record: ObservationRecord, key: str) -> ObservationRecord:
        """What stands at ``record``'s position once every correction is applied.

        Follows a chain: a wrong correction is itself corrected by correcting
        its re-filed observation (ADR-0015 §6), never by re-correcting the
        original. The visited set only guards against a cycle no writer can
        produce — a key is a hash of the line that would have to contain it.
        """
        visited: set[str] = set()
        while key not in visited and (entry := self.in_force(key)) is not None:
            visited.add(key)
            record, key = entry.record.observation, entry.key
        return record


def fold_corrections(path: Path, into: CorrectionSet, start: int) -> int:
    """Fold the correction lines from byte ``start``; return where the fold stopped.

    Stops before a line with no terminator: that is a write still in progress
    or one that never finished, and folding past it would anchor a later
    resume inside it. An unreadable candidate is skipped here and reported by
    the scan every reader runs next, which refuses a damaged log.
    """
    offset = start
    with path.open("rb") as handle:
        handle.seek(start)
        for line in handle:
            if not line.endswith(b"\n"):
                break
            offset += len(line)
            if any(marker in line for marker in _MARKERS):
                _fold_line(into, line, offset)
    return offset


def _fold_line(into: CorrectionSet, line: bytes, end: int) -> None:
    try:
        record = _ADAPTER.validate_json(line)
    except ValidationError:
        return
    into.add(record, line, end)


def corrected_view(
    corrections: CorrectionSet, collect: Callable[[ObservationRecord], None]
) -> Callable[[ObservationRecord, bytes], None]:
    """Wrap ``collect`` so it sees the corrected log instead of the raw one.

    Correction records are never handed over: in force they are applied at
    the original's position, and not in force they change nothing. The blob
    :class:`~lovspor.observatory.model.Tombstone` is not a correction and
    passes through. With no correction in the log no line is hashed — the
    view costs nothing until the first correction is written.
    """

    def view(record: ObservationRecord, line: bytes) -> None:
        if isinstance(record, RecordTombstone | RefiledObservation):
            return
        collect(corrections.resolve(record, record_key(line)) if corrections else record)

    return view
