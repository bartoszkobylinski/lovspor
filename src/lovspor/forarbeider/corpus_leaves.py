"""Files inside the sandbox's ``.git``, reached only through a directory descriptor (#738).

A staged file, its copy and the journal are created with ``O_CREAT | O_EXCL |
O_NOFOLLOW`` relative to their directory's descriptor, so a symlink, FIFO or
hard link planted at that name is never followed, truncated or written
through. A leftover this user owns, with one link and writable by no one else, is
removed first: an interrupted run's. Anything else at the name is refused,
and so is a directory that is a symlink or that others can write.
"""

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from lovspor.forarbeider.corpus_tree import MAX_CORPUS_FILE_BYTES
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason

_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _unsafe(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.UNSAFE_OUTPUT_PATH, detail)


def _private(status: os.stat_result) -> bool:
    return status.st_uid == os.geteuid() and not status.st_mode & 0o022


@contextmanager
def directory(path: Path, detail: str, parent: int | None = None) -> Iterator[int]:
    """``path`` (a name below ``parent`` when given) opened as a private directory.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path`` for a symlink, not a
            directory, or one others can write.
        OSError: ``FileNotFoundError`` when nothing is there.
    """
    try:
        descriptor = os.open(path, _DIRECTORY, dir_fd=parent)
    except OSError as error:
        if error.errno == errno.ENOENT:
            raise
        raise _unsafe(detail) from error
    try:
        if not _private(os.fstat(descriptor)):
            raise _unsafe(detail)
        yield descriptor
    finally:
        os.close(descriptor)


def _remove_leftover(parent: int, name: str, detail: str) -> None:
    try:
        status = os.lstat(name, dir_fd=parent)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or not _private(status):
        raise _unsafe(detail)
    os.unlink(name, dir_fd=parent)


def write_leaf(parent: int, name: str, payload: bytes, detail: str) -> None:
    """Create ``name`` below ``parent`` holding ``payload``, owner-only, never through a link.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path`` for anything but a
            private leftover file at ``name``, or one that appears meanwhile.
        OSError: the write itself failed.
    """
    _remove_leftover(parent, name, detail)
    try:
        descriptor = os.open(name, _CREATE, 0o600, dir_fd=parent)
    except FileExistsError as error:
        raise _unsafe(detail) from error
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def read_leaf(parent: int, name: str, detail: str) -> bytes:
    """The bytes of the private regular file ``name`` below ``parent``, size-bounded.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path`` for a symlink, FIFO,
            hard link, a file others can write, or one over the size bound.
        OSError: ``FileNotFoundError`` when nothing is there.
    """
    try:
        descriptor = os.open(name, _READ, dir_fd=parent)
    except OSError as error:
        if error.errno == errno.ENOENT:
            raise
        raise _unsafe(detail) from error
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or not _private(status):
            raise _unsafe(detail)
        if status.st_size > MAX_CORPUS_FILE_BYTES:
            raise _unsafe(detail)
        # Bounded although fstat said the file fits: it can grow meanwhile.
        return os.read(descriptor, MAX_CORPUS_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
