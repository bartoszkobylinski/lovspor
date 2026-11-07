"""Local durable store for embedding-cache entries (#802, design §5.1 and §5.2).

Layout under the configured root, every directory ``0700`` and every entry
``0600``, owned by the running user::

    schema-1/<esi>/documents/<input_hash>.lsec
    schema-1/<esi>/units/<input_hash>/<index>-<count>-<unit_hash>.lsec

Every path component is validated hex or an integer (``EntryKey``), never
document text, so there is nothing to traverse with. Symlinks are refused at
every level: the root by ``lstat``, directories by ``lstat``, entries by
``O_NOFOLLOW``.

A write is: a unique temporary file in the entry's own directory
(``O_EXCL | O_NOFOLLOW``), ``fsync``, ``os.link`` to the final name, unlink of
the temporary, ``fsync`` of the directory. ``link`` never replaces an existing
name, so a published entry is immutable and a second writer of the same key
receives the first writer's vectors — provider output is not bit-reproducible
(ADR-0005 Amendment 1), and one key must mean one value. ``atomic_io`` is not
used: it neither fsyncs nor refuses to replace.
"""

import contextlib
import errno
import os
import secrets
import stat
from pathlib import Path
from typing import Self

from lovspor.embeddings.cache_format import (
    SCHEMA,
    CachedVectors,
    EntryKey,
    ReadPolicy,
    decode_entry,
    encode_entry,
)
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheStorageError

ENTRY_SUFFIX = ".lsec"
SCHEMA_DIR = f"schema-{SCHEMA}"
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_FOREIGN_BITS = 0o077
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
"""``O_NONBLOCK`` so a FIFO planted at an entry path cannot hang the open."""
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def entry_location(key: EntryKey) -> tuple[tuple[str, ...], str]:
    """Directory components under the root, and the file name, of ``key``."""
    if key.unit is None:
        return (SCHEMA_DIR, key.esi, "documents"), f"{key.input_hash}{ENTRY_SUFFIX}"
    unit = key.unit
    name = f"{unit.index}-{unit.count}-{unit.input_hash}{ENTRY_SUFFIX}"
    return (SCHEMA_DIR, key.esi, "units", key.input_hash), name


def check_private_dir(path: Path) -> None:
    """Refuse a symlink, a non-directory, another owner, or group/other bits."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise EmbeddingCacheError(f"embedding cache refuses a symlink: {path.name}")
    if not stat.S_ISDIR(info.st_mode):
        raise EmbeddingCacheError(f"embedding cache path is not a directory: {path.name}")
    _check_owner_only(info, path.name)


def _check_owner_only(info: os.stat_result, name: str) -> None:
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & _FOREIGN_BITS:
        raise EmbeddingCacheError(
            f"embedding cache refuses {name}: permission must be owner-only "
            f"and owned by the running user",
        )


def _check_outside(root: Path, forbidden: tuple[Path, ...]) -> None:
    resolved = root.resolve()
    for tree in forbidden:
        tree_resolved = tree.resolve()
        if resolved == tree_resolved or tree_resolved in resolved.parents:
            raise EmbeddingCacheError(
                "embedding cache root is inside a repository it must stay out of; "
                "set LOVSPOR_EMBEDDING_CACHE_DIR outside the lovverk and engine checkouts",
            )


class LocalEntryStore:
    """Immutable, validated entries in a private directory on local disk."""

    def __init__(self, root: Path, policy: ReadPolicy) -> None:
        self._root = root
        self._policy = policy

    @classmethod
    def open(cls, root: Path, forbidden: tuple[Path, ...], policy: ReadPolicy) -> Self:
        """Check (and create, owner-only) the root; refuse it inside ``forbidden``."""
        if not root.is_absolute():
            raise EmbeddingCacheError("embedding cache root must be an absolute path")
        _check_outside(root, forbidden)
        try:
            root.mkdir(mode=_DIR_MODE)
        except FileExistsError:
            pass
        except OSError as exc:
            raise EmbeddingCacheStorageError(f"cannot create embedding cache root: {exc}") from exc
        check_private_dir(root)
        return cls(root, policy)

    @property
    def root(self) -> Path:
        return self._root

    def get(self, key: EntryKey) -> CachedVectors | None:
        """The validated entry for ``key``, ``None`` if absent; refuses a bad one."""
        parts, name = entry_location(key)
        directory = self._existing_dir(parts)
        if directory is None:
            return None
        data = _read_entry(directory / name)
        return None if data is None else decode_entry(data, key, self._policy)

    def put(self, entry: CachedVectors) -> CachedVectors:
        """Persist ``entry`` durably; if the key exists, return the stored entry."""
        parts, name = entry_location(entry.key)
        payload = encode_entry(entry)
        try:
            directory = self._ensure_dir(parts)
            _publish(directory, directory / name, payload)
        except FileExistsError:
            return self._existing(entry.key)
        except OSError as exc:
            raise EmbeddingCacheStorageError(
                f"cannot persist embedding cache entry ({entry.key.label()}): {exc.strerror}",
            ) from exc
        return entry

    def _existing(self, key: EntryKey) -> CachedVectors:
        stored = self.get(key)
        if stored is None:  # pragma: no cover - link reported EEXIST, then it vanished
            raise EmbeddingCacheStorageError(f"entry vanished after a write race ({key.label()})")
        return stored

    def _existing_dir(self, parts: tuple[str, ...]) -> Path | None:
        check_private_dir(self._root)  # it may have been swapped since open()
        directory = self._root
        for part in parts:
            directory = directory / part
            if not os.path.lexists(directory):
                return None
            check_private_dir(directory)
        return directory

    def _ensure_dir(self, parts: tuple[str, ...]) -> Path:
        check_private_dir(self._root)  # it may have been swapped since open()
        directory = self._root
        for part in parts:
            directory = directory / part
            with contextlib.suppress(FileExistsError):
                directory.mkdir(mode=_DIR_MODE)
            check_private_dir(directory)
        return directory


def _read_entry(path: Path) -> bytes | None:
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise EmbeddingCacheError(f"embedding cache refuses a symlink: {path.name}") from exc
        raise EmbeddingCacheStorageError(f"cannot read {path.name}: {exc.strerror}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise EmbeddingCacheError(f"embedding cache entry is not a regular file: {path.name}")
        _check_owner_only(info, path.name)
    except EmbeddingCacheError:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def _publish(directory: Path, final: Path, payload: bytes) -> None:
    """Write, fsync, link into place (never replacing), fsync the directory."""
    staged = directory / f".{final.name}.tmp.{os.getpid()}.{secrets.token_hex()}"
    descriptor = os.open(staged, _WRITE_FLAGS, _FILE_MODE)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), _FILE_MODE)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _link_in(directory, staged.name, final.name)
    finally:
        staged.unlink(missing_ok=True)
    fsync_dir(directory)


def _link_in(directory: Path, source: str, target: str) -> None:
    """Hard-link ``source`` to ``target`` in ``directory``; never follow a symlink.

    Given directory descriptors, CPython calls linkat(2) on every platform and
    version, so ``follow_symlinks=False`` links the name itself everywhere.
    Without them, CPython before 3.14 on Linux calls link(2) and ignores the
    flag, and 3.14 follows by default (#825).
    """
    handle = os.open(directory, _DIR_FLAGS)
    try:
        os.link(source, target, src_dir_fd=handle, dst_dir_fd=handle, follow_symlinks=False)
    finally:
        os.close(handle)


def fsync_dir(directory: Path) -> None:
    """Make a directory's new or removed names durable."""
    descriptor = os.open(directory, _DIR_FLAGS)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
