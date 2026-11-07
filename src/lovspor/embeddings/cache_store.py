"""Local durable store for embedding-cache entries (#802, design §5.1 and §5.2).

Layout under the configured root, every directory ``0700`` and every entry
``0600``, owned by the running user::

    schema-1/<esi>/documents/<input_hash>.lsec
    schema-1/<esi>/units/<input_hash>/<index>-<count>-<unit_hash>.lsec

Every path component is validated hex or an integer (``EntryKey``), never
document text, so there is nothing to traverse with. Symlinks are refused at
every level. Reads and writes never resolve a path twice: the root is opened
with ``O_DIRECTORY | O_NOFOLLOW`` and checked through its descriptor, every
directory below it is opened (and created) relative to its parent's
descriptor, and the entry, its temporary and the link are named relative to
the last one. A root or directory swapped for a symlink after its check
therefore cannot redirect the operation (#825 review): the store keeps using
the directory it checked.

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
    _check_dir_info(path.lstat(), path.name)


def _check_dir_info(info: os.stat_result, name: str) -> None:
    if stat.S_ISLNK(info.st_mode):
        raise EmbeddingCacheError(f"embedding cache refuses a symlink: {name}")
    if not stat.S_ISDIR(info.st_mode):
        raise EmbeddingCacheError(f"embedding cache path is not a directory: {name}")
    _check_owner_only(info, name)


def _open_private_dir(name: Path | str, parent: int | None) -> int:
    """A descriptor for a private directory, opened without following a symlink."""
    try:
        handle = os.open(name, _DIR_FLAGS, dir_fd=parent)
    except OSError as exc:
        if exc.errno not in (errno.ELOOP, errno.ENOTDIR):
            raise
        _check_dir_info(os.stat(name, dir_fd=parent, follow_symlinks=False), Path(name).name)
        raise EmbeddingCacheError(f"embedding cache refuses {Path(name).name}") from exc
    try:
        _check_owner_only(os.fstat(handle), Path(name).name)
    except EmbeddingCacheError:
        os.close(handle)
        raise
    return handle


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

    @property
    def policy(self) -> ReadPolicy:
        return self._policy

    def get(self, key: EntryKey) -> CachedVectors | None:
        """The validated entry for ``key``, ``None`` if absent; refuses a bad one."""
        parts, name = entry_location(key)
        handle = self._existing_dir(parts)
        if handle is None:
            return None
        try:
            data = read_entry_bytes(Path(name), handle)
        finally:
            os.close(handle)
        return None if data is None else decode_entry(data, key, self._policy)

    def put(self, entry: CachedVectors) -> CachedVectors:
        """Persist ``entry`` durably; if the key exists, return the stored entry."""
        parts, name = entry_location(entry.key)
        payload = encode_entry(entry)
        try:
            handle = self._descend(parts, create=True)
            try:
                _publish(handle, name, payload)
            finally:
                os.close(handle)
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

    def _existing_dir(self, parts: tuple[str, ...]) -> int | None:
        handle = _open_private_dir(self._root, None)  # it may have been swapped since open()
        try:
            return self._descend(parts, create=False, handle=handle)
        except FileNotFoundError:
            return None

    def _descend(self, parts: tuple[str, ...], create: bool, handle: int | None = None) -> int:
        """Walk ``parts`` below the root by descriptor; the caller closes the result."""
        if handle is None:
            handle = _open_private_dir(self._root, None)  # it may have been swapped since open()
        for part in parts:
            try:
                if create:
                    with contextlib.suppress(FileExistsError):
                        os.mkdir(part, _DIR_MODE, dir_fd=handle)
                child = _open_private_dir(part, handle)
            finally:
                os.close(handle)
            handle = child
        return handle


def read_entry_bytes(path: Path, directory: int | None = None) -> bytes | None:
    """An entry's bytes through the checks serving uses; None if absent.

    ``path`` is relative to the ``directory`` descriptor when one is given.
    """
    try:
        descriptor = os.open(path, _READ_FLAGS, dir_fd=directory)
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


def _publish(directory: int, name: str, payload: bytes) -> None:
    """Write, fsync, link into place (never replacing), fsync the directory.

    Every name is relative to the ``directory`` descriptor.
    """
    staged = f".{name}.tmp.{os.getpid()}.{secrets.token_hex()}"
    descriptor = os.open(staged, _WRITE_FLAGS, _FILE_MODE, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), _FILE_MODE)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _link_in(directory, staged, name)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(staged, dir_fd=directory)
    os.fsync(directory)


def _link_in(handle: int, source: str, target: str) -> None:
    """Hard-link ``source`` to ``target`` in the ``handle`` directory; never follow a symlink.

    Given directory descriptors, CPython calls linkat(2) on every platform and
    version, so ``follow_symlinks=False`` links the name itself everywhere.
    Without them, CPython before 3.14 on Linux calls link(2) and ignores the
    flag, and 3.14 follows by default (#825).
    """
    os.link(source, target, src_dir_fd=handle, dst_dir_fd=handle, follow_symlinks=False)


def fsync_dir(directory: Path) -> None:
    """Make a directory's new or removed names durable."""
    descriptor = os.open(directory, _DIR_FLAGS)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
