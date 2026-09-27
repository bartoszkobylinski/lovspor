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
retracting it, with the same ``correction_id``, reason, author and time
(:func:`pairing`). The writer appends the
re-filed half first, so at every crash point a reader sees either the original
or the corrected record — never both, never neither.

This module owns the rule so no reader has to learn it: every reader of
attribution goes through :meth:`ObservationLog.scan_corrected_into`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from lovspor.observatory.model import (
    ArtifactObservation,
    Correction,
    FetchFailure,
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


def pairing(half: Correction | RecordTombstone) -> tuple[str, str, str, datetime]:
    """What both halves of one correction must share (ADR-0015 §4).

    Not the ``correction_id`` alone: the re-filed half carries "reason,
    corrected_by, corrected_at: identical to the record tombstone", so two
    halves that disagree on who, why or when are not one correction. Neither
    stands, and `verify` reports both as incomplete.
    """
    return (half.correction_id, half.reason, half.corrected_by, half.corrected_at)


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
                if pairing(entry.record.correction) == pairing(tombstone):
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


class CorrectionAudit(BaseModel):
    """What ``verify`` finds wrong with the corrections in a log (ADR-0015 §5).

    Each list holds the keys of the records the defective corrections name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A correction half whose key matches no line in the log.
    corrections_without_record: tuple[str, ...] = ()
    #: A re-filed record that changes a field it does not declare, or whose
    #: ``previous_values`` are not what the original says.
    refiled_mismatches: tuple[str, ...] = ()
    #: An original named by more than one completed correction.
    multiply_corrected: tuple[str, ...] = ()
    #: One half of a correction present without the other.
    incomplete_corrections: tuple[str, ...] = ()


def collect_named(
    corrections: CorrectionSet, into: dict[str, ObservationRecord]
) -> Callable[[ObservationRecord, bytes], None]:
    """A line collector keeping every record a correction names, by its key."""
    named = set(corrections.refiled) | set(corrections.tombstones)

    def collect(record: ObservationRecord, line: bytes) -> None:
        key = record_key(line)
        if key in named:
            into.setdefault(key, record)

    return collect


def audit_corrections(
    corrections: CorrectionSet, named: dict[str, ObservationRecord]
) -> CorrectionAudit:
    """Audit every correction against the records it names, read raw."""
    keys = set(corrections.refiled) | set(corrections.tombstones)
    return CorrectionAudit(
        corrections_without_record=tuple(sorted(keys - named.keys())),
        refiled_mismatches=tuple(
            sorted(
                key
                for key, entries in corrections.refiled.items()
                if key in named and not all(_faithful(e.record, named[key]) for e in entries)
            )
        ),
        multiply_corrected=tuple(sorted(k for k in keys if len(corrections.completed(k)) > 1)),
        incomplete_corrections=tuple(sorted(k for k in keys if _unpaired(corrections, k))),
    )


def _faithful(refiled: RefiledObservation, original: ObservationRecord) -> bool:
    """True when ``refiled`` restates ``original`` except in its declared fields."""
    if isinstance(original, RefiledObservation):
        original = original.observation
    if not isinstance(original, ArtifactObservation | FetchFailure):
        return False
    correction = refiled.correction
    fields: set[str] = set(correction.corrected_fields)
    if any(
        getattr(original, name) != correction.previous_values[name]
        for name in correction.corrected_fields
    ):
        return False
    return refiled.observation.model_dump(exclude=fields) == original.model_dump(exclude=fields)


def _unpaired(corrections: CorrectionSet, key: str) -> bool:
    retracted = {pairing(tombstone) for tombstone in corrections.tombstones.get(key, [])}
    refiled = {pairing(entry.record.correction) for entry in corrections.refiled.get(key, [])}
    return bool(retracted ^ refiled)
