"""Append-only observation log and hash-addressed blob store (ADR-0010 §7).

Fetching the live web is not reproducible — a municipal page as it stood last
Tuesday cannot be re-fetched, by any design. So the determinism boundary sits
one step downstream, at the **observation snapshot**: the append-only log plus
the hash-addressed blobs it references. Everything derived from a snapshot
must be a pure function of it (``derived = f(observation_snapshot,
derivation_version, config)``); this module owns the snapshot half of that
contract and deliberately owns nothing else.

Two consequences shape the API. The log is only ever opened for append, so
there is no code path here that can rewrite history. And a blob is addressed
by the hash of its own bytes, so observing unchanged content twice appends a
second record without duplicating the payload — a re-crawl is always an
addition, never an edit.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from lovspor.atomic_io import atomic_write_bytes
from lovspor.errors import LogIntegrityError
from lovspor.observatory.model import (
    ArtifactObservation,
    ObservationRecord,
    Tombstone,
    record_to_json_line,
)

LOG_FILENAME = "observations.jsonl"
BLOBS_DIRNAME = "blobs"

_RECORD_ADAPTER: TypeAdapter[ObservationRecord] = TypeAdapter(ObservationRecord)


class SnapshotVerification(BaseModel):
    """Result of auditing a snapshot's internal consistency.

    Returned rather than raised: validation is independent from
    transformation (Design Principle §17), so the audit reports everything it
    found instead of stopping at the first defect.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    artifacts_checked: int
    missing_blobs: tuple[str, ...] = ()
    hash_mismatches: tuple[str, ...] = ()
    tombstoned: tuple[str, ...] = ()
    unremoved_tombstones: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when no blob is missing without a tombstone and none is altered.

        ``tombstoned`` does not count against a snapshot: a recorded, explained
        removal is the sanctioned way for bytes to disappear. A blob gone with
        no tombstone is exactly the silent mutation the log exists to make
        impossible to hide.
        """
        return not (self.missing_blobs or self.hash_mismatches or self.unremoved_tombstones)


class ObservationLog:
    """The append-only log and its blob store, rooted at one directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    @property
    def log_path(self) -> Path:
        return self._root / LOG_FILENAME

    def blob_path(self, sha256: str) -> Path:
        """Locate a blob by its content hash, sharded on the first two digits."""
        return self._root / BLOBS_DIRNAME / sha256[:2] / sha256

    def append(self, record: ObservationRecord) -> None:
        """Append one record. The only write path; there is no update path."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(record_to_json_line(record) + "\n")

    def append_artifact(self, record: ArtifactObservation, payload: bytes) -> None:
        """Store captured bytes, then append the record that describes them.

        The payload is hashed here and checked against the record: a record
        claiming a hash its bytes do not have would make the whole log
        unverifiable, so it is refused at the door rather than discovered
        later by an audit. An existing blob is left untouched — identical
        bytes are identical evidence, and rewriting it could only ever
        replace a good copy with a worse one.

        Raises:
            LogIntegrityError: ``payload`` does not hash to ``record.sha256``.
        """
        digest = hashlib.sha256(payload).hexdigest()
        if digest != record.sha256:
            raise LogIntegrityError(
                f"payload for {record.url} hashes to {digest}, record claims {record.sha256}",
            )
        blob = self.blob_path(digest)
        if not blob.exists():
            atomic_write_bytes(blob, payload)
        self.append(record)

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

    def read_blob(self, sha256: str) -> bytes:
        """Return stored bytes.

        Raises:
            FileNotFoundError: the blob is absent. Absence is not necessarily
                corruption — it may be a tombstoned removal — so the caller,
                not this method, decides what it means.
        """
        return self.blob_path(sha256).read_bytes()


def _partition(log: ObservationLog) -> tuple[list[str], set[str]]:
    """Split the log into observed artifact hashes and tombstoned hashes."""
    artifacts: list[str] = []
    tombstoned: set[str] = set()
    for record in log.records():
        if isinstance(record, ArtifactObservation):
            artifacts.append(record.sha256)
        elif isinstance(record, Tombstone):
            tombstoned.add(record.sha256)
    return artifacts, tombstoned


def verify_snapshot(log: ObservationLog) -> SnapshotVerification:
    """Audit a snapshot: every artifact's bytes present and unaltered, or tombstoned.

    Implements the hash-integrity and immutability checks ADR-0010 lists under
    Validation. Re-hashing is the point: a blob that still exists but no longer
    matches its recorded digest is a mutated one, and nothing else in the
    system would notice.
    """
    artifacts, tombstoned = _partition(log)
    missing: list[str] = []
    mismatched: list[str] = []
    removed: list[str] = []
    for sha256 in artifacts:
        blob = log.blob_path(sha256)
        if not blob.exists():
            (removed if sha256 in tombstoned else missing).append(sha256)
            continue
        if hashlib.sha256(blob.read_bytes()).hexdigest() != sha256:
            mismatched.append(sha256)
    unremoved = [sha256 for sha256 in sorted(tombstoned) if log.blob_path(sha256).exists()]
    return SnapshotVerification(
        artifacts_checked=len(artifacts),
        missing_blobs=tuple(missing),
        hash_mismatches=tuple(mismatched),
        tombstoned=tuple(removed),
        unremoved_tombstones=tuple(unremoved),
    )
