"""The release clone's exclusive lock (#738, contract Amendment 1 point 5, B1b).

One change at a time in a release clone: ``.git/lovspor-forarbeider.lock``,
the name the B1a guard looks for, created exclusively and held by an advisory
``flock`` for as long as the change runs. Whoever holds the ``flock`` owns the
lock, so the rule needs no process id and survives a reused one:

- a batch apply or a withdrawal makes a **new** lock; one already there is
  ``corpus_locked``, whether its holder is alive or died;
- only the explicit recovery may take a lock over, and only one no process
  holds (its ``flock`` is free): the holder died;
- after the ``flock`` is taken, the file at the lock's name must still be the
  one locked, a single regular file, owner-only. A lock removed or replaced
  under its holder is no longer held, and is never removed by it.

Every name is reached through a descriptor of ``.git`` opened without
following a link, so a symlinked ``.git`` or lock carries nothing elsewhere.
"""

import errno
import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from lovspor.forarbeider.corpus_transaction import LOCK
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason

_NAME = Path(LOCK).name


class HeldLock(NamedTuple):
    """The lock file this process holds, by device and inode."""

    device: int
    inode: int


def _locked() -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CORPUS_LOCKED, "checkout")


def _unsafe() -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CHECKOUT_UNSAFE, "lock")


@contextmanager
def _git_directory(checkout: Path) -> Iterator[int]:
    """``.git`` as a descriptor, never through a link.

    Raises:
        ForarbeiderRefusedError: ``checkout_unsafe: lock``.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(checkout / ".git", flags)
    except OSError as error:
        raise _unsafe() from error
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _at_name(git: int) -> os.stat_result | None:
    try:
        return os.stat(_NAME, dir_fd=git, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _claim(git: int, descriptor: int) -> HeldLock:
    """Take the ``flock`` and prove the name still holds the file locked.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked`` (a live holder, or the lock
            moved), ``checkout_unsafe: lock`` (not one private regular file).
    """
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise _locked() from error
    mine, named = os.fstat(descriptor), _at_name(git)
    if named is None or (named.st_dev, named.st_ino) != (mine.st_dev, mine.st_ino):
        raise _locked()
    private = mine.st_uid == os.geteuid() and not stat.S_IMODE(mine.st_mode) & 0o077
    if not stat.S_ISREG(mine.st_mode) or mine.st_nlink != 1 or not private:
        raise _unsafe()
    return HeldLock(mine.st_dev, mine.st_ino)


def _open(git: int, *, create: bool) -> int:
    """The lock file's descriptor: a new one, or the one already there.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked`` for a new lock that exists,
            ``checkout_unsafe: lock`` for a link.
    """
    flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if create else 0)
    try:
        return os.open(_NAME, flags, 0o600, dir_fd=git)
    except FileExistsError as error:
        raise _locked() from error
    except OSError as error:
        if error.errno == errno.ENOENT:
            return _open(git, create=True)
        raise _unsafe() from error


@contextmanager
def _holding(checkout: Path, *, create: bool) -> Iterator[HeldLock]:
    with _git_directory(checkout) as git:
        descriptor = _open(git, create=create)
        try:
            held = _claim(git, descriptor)
            try:
                yield held
            finally:
                _remove(git, held)
        finally:
            os.close(descriptor)


def _remove(git: int, held: HeldLock) -> None:
    named = _at_name(git)
    if named is not None and (named.st_dev, named.st_ino) == held:
        os.unlink(_NAME, dir_fd=git)


@contextmanager
def release_lock(checkout: Path) -> Iterator[HeldLock]:
    """A new lock, held until the block ends; one already there is never taken over.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked`` or ``checkout_unsafe: lock``.
    """
    with _holding(checkout, create=True) as held:
        yield held


@contextmanager
def recovered_lock(checkout: Path) -> Iterator[HeldLock]:
    """The lock a dead holder left, taken over, or a new one when there is none.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked`` while a live process holds it,
            ``checkout_unsafe: lock`` for a link or a shared file.
    """
    with _holding(checkout, create=False) as held:
        yield held


def held_by_us(checkout: Path, held: HeldLock) -> bool:
    """Whether the lock's name is, right now, the file ``held`` names."""
    try:
        status = os.lstat(checkout / LOCK)
    except FileNotFoundError:
        return False
    return (status.st_dev, status.st_ino) == held
