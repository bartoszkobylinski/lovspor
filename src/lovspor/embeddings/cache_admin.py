"""Operator views over the local embedding cache (#802, design §10).

``status``, ``verify``, ``prune`` and ``invalidate`` read and remove entries;
none of them calls a provider. Reports carry counts and key prefixes only —
never vector values, document text or the cache path.

Every file the walk meets is checked the way serving checks it: directories
by ``check_private_dir`` (symlinks, owner, mode), entries through the store's
own reader and :func:`decode_entry`. ``prune`` removes only what it can
classify — expired entries, unit checkpoints whose document entry exists,
and temporary files older than the grace period. A corrupt entry is reported,
never silently deleted: it is evidence, and ``invalidate`` is the deliberate
way to remove it.
"""

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from lovspor.embeddings.cache_format import EntryKey, UnitKey, decode_entry, describe_entry
from lovspor.embeddings.cache_store import (
    ENTRY_SUFFIX,
    SCHEMA_DIR,
    LocalEntryStore,
    check_private_dir,
    entry_location,
    fsync_dir,
    read_entry_bytes,
)
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheExpiredError

_ESI = re.compile(r"^[0-9a-f]{32}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_UNIT_NAME = re.compile(r"^(\d+)-(\d+)-([0-9a-f]{64})$")
_TEMP_MARK = ".tmp."
DEFAULT_TEMP_GRACE = timedelta(hours=1)


@dataclass(frozen=True)
class StoredEntry:
    """One entry file and the key its location names."""

    path: Path
    esi: str
    input_hash: str
    unit: tuple[int, int, str] | None


@dataclass
class SpaceStatus:
    documents: int = 0
    units: int = 0
    bytes: int = 0


@dataclass
class StatusReport:
    spaces: dict[str, SpaceStatus] = field(default_factory=dict)
    temporary: int = 0

    def lines(self) -> list[str]:
        rows = [
            f"esi={esi[:8]} documents={s.documents} units={s.units} bytes={s.bytes}"
            for esi, s in sorted(self.spaces.items())
        ]
        return [*rows, f"temporary={self.temporary}"]


@dataclass
class VerifyReport:
    valid: int = 0
    expired: int = 0
    refused: list[str] = field(default_factory=list)

    def line(self) -> str:
        return f"valid={self.valid} expired={self.expired} refused={len(self.refused)}"


@dataclass
class PruneReport:
    expired: int = 0
    superseded_units: int = 0
    temporary: int = 0
    refused: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"expired={self.expired} superseded_units={self.superseded_units} "
            f"temporary={self.temporary} refused={len(self.refused)}"
        )


def status(store: LocalEntryStore) -> StatusReport:
    """Entry counts and bytes per space, and stray temporary files."""
    report = StatusReport()
    for entry in iter_entries(store):
        space = report.spaces.setdefault(entry.esi, SpaceStatus())
        space.bytes += entry.path.lstat().st_size
        if entry.unit is None:
            space.documents += 1
        else:
            space.units += 1
    report.temporary = sum(1 for _ in iter_temporary(store))
    return report


def verify(store: LocalEntryStore) -> VerifyReport:
    """Validate every entry against the key its location names."""
    report = VerifyReport()
    for entry in iter_entries(store):
        try:
            check_entry(store, entry)
        except EmbeddingCacheExpiredError:
            report.expired += 1
        except EmbeddingCacheError as exc:
            report.refused.append(str(exc))
        else:
            report.valid += 1
    return report


def prune(store: LocalEntryStore, temp_grace: timedelta = DEFAULT_TEMP_GRACE) -> PruneReport:
    """Remove expired entries, superseded unit checkpoints and old temporary files."""
    report = PruneReport()
    for entry in list(iter_entries(store)):
        try:
            check_entry(store, entry)
        except EmbeddingCacheExpiredError:
            _remove(entry.path)
            report.expired += 1
        except EmbeddingCacheError as exc:
            report.refused.append(str(exc))
        else:
            report.superseded_units += _drop_if_superseded(store, entry)
    report.temporary = _remove_temporary(store, temp_grace)
    return report


def invalidate(store: LocalEntryStore, esi: str, input_hash: str | None) -> int:
    """Remove one document's entries (and its units), or a whole space."""
    if not _ESI.match(esi) or (input_hash is not None and not _HASH.match(input_hash)):
        raise EmbeddingCacheError("invalidate needs a 32-hex ESI and, optionally, a 64-hex hash")
    removed = 0
    for entry in list(iter_entries(store)):
        if entry.esi == esi and input_hash in (None, entry.input_hash):
            _remove(entry.path)
            removed += 1
    return removed


def check_entry(store: LocalEntryStore, entry: StoredEntry) -> None:
    """Decode ``entry`` against the key its location names; raise on any refusal."""
    data = read_entry_bytes(entry.path)
    if data is None:  # pragma: no cover - removed between the walk and the read
        return
    claimed = describe_entry(data, entry.path.name)
    if (claimed.esi, claimed.input_hash, _unit_tuple(claimed.unit)) != (
        entry.esi,
        entry.input_hash,
        entry.unit,
    ):
        raise EmbeddingCacheError(
            f"embedding cache entry refused ({claimed.label()}): stored under another key",
        )
    decode_entry(data, claimed, store.policy)


def iter_entries(store: LocalEntryStore) -> Iterator[StoredEntry]:
    """Every entry file, named by the key its location claims.

    A file whose name is not a key is yielded with an impossible key, so the
    caller's check refuses it rather than the walk skipping it.
    """
    for space in _subdirs(store.root / SCHEMA_DIR):
        yield from _documents(space)
        for unit_dir in _subdirs(space / "units"):
            yield from _units(space.name, unit_dir)


def iter_temporary(store: LocalEntryStore) -> Iterator[Path]:
    for directory in _all_dirs(store.root / SCHEMA_DIR):
        yield from (p for p in directory.iterdir() if _TEMP_MARK in p.name and p.is_file())


def _documents(space: Path) -> Iterator[StoredEntry]:
    for path in _entry_files(space / "documents"):
        yield StoredEntry(path, space.name, path.name.removesuffix(ENTRY_SUFFIX), None)


def _units(esi: str, unit_dir: Path) -> Iterator[StoredEntry]:
    for path in _entry_files(unit_dir):
        match = _UNIT_NAME.match(path.name.removesuffix(ENTRY_SUFFIX))
        unit = (int(match[1]), int(match[2]), match[3]) if match else (-1, -1, "")
        yield StoredEntry(path, esi, unit_dir.name, unit)


def _unit_tuple(unit: UnitKey | None) -> tuple[int, int, str] | None:
    return None if unit is None else (unit.index, unit.count, unit.input_hash)


def _drop_if_superseded(store: LocalEntryStore, entry: StoredEntry) -> int:
    if entry.unit is None:
        return 0
    parts, name = entry_location(
        EntryKey(esi=entry.esi, input_hash=entry.input_hash, dim=1, rows=1)
    )
    if not store.root.joinpath(*parts, name).is_file():
        return 0
    _remove(entry.path)
    return 1


def _remove_temporary(store: LocalEntryStore, grace: timedelta) -> int:
    cutoff = time.time() - grace.total_seconds()
    stale = [p for p in iter_temporary(store) if p.lstat().st_mtime <= cutoff]
    for path in stale:
        _remove(path)
    return len(stale)


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)
    fsync_dir(path.parent)


def _subdirs(directory: Path) -> Iterator[Path]:
    if not directory.exists():
        return
    check_private_dir(directory)
    for child in sorted(directory.iterdir()):
        check_private_dir(child)
        yield child


def _all_dirs(directory: Path) -> Iterator[Path]:
    if not directory.exists():
        return
    check_private_dir(directory)
    yield directory
    for child in sorted(directory.iterdir()):
        if child.is_dir() or child.is_symlink():
            yield from _all_dirs(child)


def _entry_files(directory: Path) -> Iterator[Path]:
    if not directory.exists():
        return
    check_private_dir(directory)
    yield from (
        p
        for p in sorted(directory.iterdir())
        if p.name.endswith(ENTRY_SUFFIX) and _TEMP_MARK not in p.name
    )
