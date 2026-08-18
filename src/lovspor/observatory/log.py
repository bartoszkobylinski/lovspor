"""Append-only observation log and hash-addressed blob store (ADR-0010 §7).

Fetching the live web is not reproducible — a municipal page as it stood last
Tuesday cannot be re-fetched, by any design. So the determinism boundary sits
one step downstream, at the **observation snapshot**: the append-only log plus
the hash-addressed blobs it references. Everything derived from a snapshot
must be a pure function of it (``derived = f(observation_snapshot,
derivation_version, config)``); this module owns the snapshot half of that
contract and deliberately owns nothing else.

Three consequences shape the API. The log is only ever opened for append, so
there is no code path here that can rewrite history. A blob is addressed by
the hash of its own bytes, so observing unchanged content twice appends a
second record without duplicating the payload — a re-crawl is always an
addition, never an edit. And a tombstoned hash is refused at the door:
capture must not quietly undo a sanctioned removal just because the source
still serves the same bytes.
"""

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from lovspor.atomic_io import atomic_write_bytes
from lovspor.errors import LogIntegrityError, StorageBoundaryError, TombstonedArtifactError
from lovspor.observatory.model import (
    ArtifactObservation,
    ObservationRecord,
    Tombstone,
    record_to_json_line,
)
from lovspor.observatory.storage import ObservatoryRoot

LOG_FILENAME = "observations.jsonl"
BLOBS_DIRNAME = "blobs"

_RECORD_ADAPTER: TypeAdapter[ObservationRecord] = TypeAdapter(ObservationRecord)


class LogScan(BaseModel):
    """As much of the log as parses, plus exactly where it stopped parsing.

    :meth:`ObservationLog.records` is strict on purpose — an append-only log
    that silently skips a line it cannot read has already lost the property it
    exists for. But strictness leaves an operator whose disk vanished mid-write
    unable to even *audit* the archive, because one torn tail raises before
    anything can be reported. This is the reading that answers "how bad is it?"
    without pretending the damage is not there.

    ``incomplete_final_record`` is the crash signature: a last line that does
    not parse and does not end in a newline is a write that never finished.
    ``malformed_lines`` is the other thing entirely — a line that fails
    anywhere else, which no interrupted append can produce.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    records: tuple[ObservationRecord, ...] = ()
    incomplete_final_record: bool = False
    malformed_lines: tuple[int, ...] = ()

    @property
    def complete(self) -> bool:
        """True when every line in the log was read."""
        return not (self.incomplete_final_record or self.malformed_lines)


class SnapshotVerification(BaseModel):
    """Result of auditing a snapshot's internal consistency.

    Returned rather than raised: validation is independent from
    transformation (Design Principle §17), so the audit reports everything it
    found instead of stopping at the first defect.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifacts_checked: int
    missing_blobs: tuple[str, ...] = ()
    hash_mismatches: tuple[str, ...] = ()
    tombstoned: tuple[str, ...] = ()
    unremoved_tombstones: tuple[str, ...] = ()
    orphan_blobs: tuple[str, ...] = ()
    tombstones_without_observation: tuple[str, ...] = ()
    observations_after_tombstone: tuple[str, ...] = ()
    incomplete_final_record: bool = False
    malformed_lines: tuple[int, ...] = ()

    @property
    def ok(self) -> bool:
        """True when the snapshot's bytes and its log account for each other.

        ``tombstoned`` does not count against a snapshot: a recorded,
        explained removal is the sanctioned way for bytes to disappear. Every
        other list does — a blob gone with no tombstone, a blob that no longer
        hashes to its record, a blob on disk that no record mentions, a
        tombstone for something never observed, a removal that never happened,
        an observation appended after the tombstone that retired it, or a log
        that could not be read to the end.
        """
        return not (
            self.missing_blobs
            or self.hash_mismatches
            or self.unremoved_tombstones
            or self.orphan_blobs
            or self.tombstones_without_observation
            or self.observations_after_tombstone
            or self.incomplete_final_record
            or self.malformed_lines
        )


class ObservationLog:
    """The append-only log and its blob store, under one validated root.

    The constructor takes an :class:`~lovspor.observatory.storage.ObservatoryRoot`
    rather than a ``Path`` on purpose: an instance of that type cannot exist
    without having passed the ADR-0010 §5 boundary check, so there is no way
    to point a log inside the engine repository or the published corpus.
    """

    def __init__(self, root: ObservatoryRoot) -> None:
        # Checked at runtime, not only by mypy: an unchecked path reaching this
        # constructor is the storage boundary being bypassed, and it should say
        # so rather than fail later with an incidental AttributeError.
        if not isinstance(root, ObservatoryRoot):
            raise StorageBoundaryError(
                f"ObservationLog requires an ObservatoryRoot, got {type(root).__name__}; "
                "resolve the root through observatory_root() so ADR-0010 §5 is checked",
            )
        self._root = root.path

    @property
    def root(self) -> Path:
        return self._root

    @property
    def log_path(self) -> Path:
        return self._root / LOG_FILENAME

    @property
    def blobs_dir(self) -> Path:
        return self._root / BLOBS_DIRNAME

    def blob_path(self, sha256: str) -> Path:
        """Locate a blob by its content hash, sharded on the first two digits."""
        return self.blobs_dir / sha256[:2] / sha256

    def append(self, record: ObservationRecord) -> None:
        """Append one record durably. The only write path; there is no update path.

        The write is flushed and fsynced before returning. The archive lives
        on external storage by design (ADR-0010 §5), where losing power or the
        disk mid-run is an expected event rather than an exotic one, and a
        record left half-written is the one failure this log cannot absorb:
        :meth:`records` refuses to skip a malformed line, so a torn tail would
        make the whole snapshot unreadable.

        The cost is not a trade-off worth agonising over. Capture waits out a
        per-source rate limit measured in seconds between requests, so an
        fsync per record is unmeasurable against the waiting the crawler is
        already doing.
        """
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(record_to_json_line(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def append_artifact(self, record: ArtifactObservation, payload: bytes) -> None:
        """Store captured bytes, then append the record that describes them.

        The payload is hashed here and checked against the record: a record
        claiming a hash its bytes do not have would make the whole log
        unverifiable, so it is refused at the door rather than discovered
        later by an audit. An existing blob is left untouched — identical
        bytes are identical evidence.

        Raises:
            LogIntegrityError: ``payload`` does not hash to ``record.sha256``.
            TombstonedArtifactError: these bytes were removed under a
                tombstone. Re-storing them because the source still serves
                them would silently reverse a legal or privacy removal, which
                is precisely what the tombstone exists to prevent.
        """
        digest = hashlib.sha256(payload).hexdigest()
        if digest != record.sha256:
            raise LogIntegrityError(
                f"payload for {record.url} hashes to {digest}, record claims {record.sha256}",
            )
        if digest in self.tombstoned_hashes():
            raise TombstonedArtifactError(
                f"{digest} was removed under a tombstone; re-storing it would reverse a "
                "sanctioned removal (ADR-0010 §7)",
            )
        blob = self.blob_path(digest)
        if not blob.exists():
            atomic_write_bytes(blob, payload)
        self.append(record)

    def tombstoned_hashes(self) -> frozenset[str]:
        """Hashes retired by a tombstone, and therefore closed to re-capture."""
        return frozenset(
            record.sha256 for record in self.records() if isinstance(record, Tombstone)
        )

    def records(self) -> Iterator[ObservationRecord]:
        """Read the log in append order.

        Raises:
            LogIntegrityError: a line is malformed — a torn write, or a record
                shape this engine does not recognise. Skipping it would shrink
                the evidence silently, which is the one outcome an append-only
                log is supposed to prevent.
        """
        if not self.log_path.exists():
            return
        with self.log_path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                yield self._parse_line(line, number)

    def _parse_line(self, line: str, number: int) -> ObservationRecord:
        try:
            return _RECORD_ADAPTER.validate_json(line)
        except ValidationError as exc:
            raise LogIntegrityError(f"{self.log_path}:{number}: unreadable record: {exc}") from exc

    def scan(self) -> LogScan:
        """Read every line the log will give up, and report what it will not.

        Split on bytes rather than decoded text: an append cut mid-character
        leaves a line that is not valid UTF-8 at all, and reading the file as
        text would fail before a single intact record could be recovered.

        Lines are decoded strictly. A lenient decode would substitute
        replacement characters into what is meant to be evidence, which is a
        worse outcome than refusing the line — a silently altered record reads
        as genuine forever after.
        """
        if not self.log_path.exists():
            return LogScan()
        raw = self.log_path.read_bytes()
        lines = raw.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        records, malformed = self._scan_lines(lines)
        torn_tail = bool(malformed) and malformed[-1] == len(lines) and not raw.endswith(b"\n")
        return LogScan(
            records=tuple(records),
            incomplete_final_record=torn_tail,
            malformed_lines=tuple(malformed[:-1] if torn_tail else malformed),
        )

    def _scan_lines(self, lines: list[bytes]) -> tuple[list[ObservationRecord], list[int]]:
        records: list[ObservationRecord] = []
        malformed: list[int] = []
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                records.append(self._parse_line(line.decode("utf-8"), number))
            except (UnicodeDecodeError, LogIntegrityError):
                malformed.append(number)
        return records, malformed

    def stored_hashes(self) -> frozenset[str]:
        """Hashes actually present in the blob store, read from disk."""
        if not self.blobs_dir.exists():
            return frozenset()
        return frozenset(path.name for path in self.blobs_dir.rglob("*") if path.is_file())

    def read_blob(self, sha256: str) -> bytes:
        """Return stored bytes.

        Raises:
            FileNotFoundError: the blob is absent. Absence is not necessarily
                corruption — it may be a tombstoned removal — so the caller,
                not this method, decides what it means.
        """
        return self.blob_path(sha256).read_bytes()


class _Timeline(BaseModel):
    """What the log says, in order, about each hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed: tuple[str, ...]
    tombstoned: tuple[str, ...]
    observed_after_tombstone: tuple[str, ...]


def _walk(records: tuple[ObservationRecord, ...]) -> _Timeline:
    """Replay the log in append order, noting what happened after what."""
    observed: list[str] = []
    tombstoned: list[str] = []
    late: list[str] = []
    retired: set[str] = set()
    for record in records:
        if isinstance(record, ArtifactObservation):
            observed.append(record.sha256)
            if record.sha256 in retired:
                late.append(record.sha256)
        elif isinstance(record, Tombstone):
            tombstoned.append(record.sha256)
            retired.add(record.sha256)
    return _Timeline(
        observed=tuple(observed),
        tombstoned=tuple(tombstoned),
        observed_after_tombstone=tuple(late),
    )


def _classify_blobs(
    log: ObservationLog, timeline: _Timeline
) -> tuple[list[str], list[str], list[str]]:
    """Sort observed hashes into missing, altered and sanctioned-removal buckets."""
    retired = set(timeline.tombstoned)
    missing: list[str] = []
    mismatched: list[str] = []
    removed: list[str] = []
    for sha256 in timeline.observed:
        blob = log.blob_path(sha256)
        if not blob.exists():
            (removed if sha256 in retired else missing).append(sha256)
        elif hashlib.sha256(blob.read_bytes()).hexdigest() != sha256:
            mismatched.append(sha256)
    return missing, mismatched, removed


def verify_snapshot(log: ObservationLog) -> SnapshotVerification:
    """Audit a snapshot: bytes and log must fully account for each other.

    Implements the hash-integrity and immutability checks ADR-0010 lists under
    Validation, and then some the log's own guarantees imply: an orphan blob
    is raw material with no provenance record, and a tombstone for a hash the
    log never observed is a removal record that explains nothing.

    A log that cannot be read to the end is reported rather than raised. The
    audit exists to answer "how bad is it?", and an operator whose disk
    vanished mid-write needs that answer most precisely when the log is
    damaged — which is exactly when a raising audit gives nothing at all.
    """
    scan = log.scan()
    timeline = _walk(scan.records)
    if not scan.complete:
        # Classifying blobs against a log that could not be read to the end
        # would report every blob the unread lines account for as an orphan.
        # That is fiction, not a finding, and it would bury the one defect
        # that actually needs attention under a list of invented ones.
        return SnapshotVerification(
            artifacts_checked=len(timeline.observed),
            incomplete_final_record=scan.incomplete_final_record,
            malformed_lines=scan.malformed_lines,
        )
    missing, mismatched, removed = _classify_blobs(log, timeline)
    observed = set(timeline.observed)
    retired = set(timeline.tombstoned)
    return SnapshotVerification(
        artifacts_checked=len(timeline.observed),
        missing_blobs=tuple(missing),
        hash_mismatches=tuple(mismatched),
        tombstoned=tuple(removed),
        unremoved_tombstones=tuple(s for s in sorted(retired) if log.blob_path(s).exists()),
        orphan_blobs=tuple(sorted(log.stored_hashes() - observed)),
        tombstones_without_observation=tuple(sorted(retired - observed)),
        observations_after_tombstone=tuple(timeline.observed_after_tombstone),
    )
