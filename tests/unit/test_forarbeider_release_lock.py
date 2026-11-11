"""The release clone's exclusive lock (#738, B1b).

The lock is ``.git/lovspor-forarbeider.lock``, created exclusively and held by
an advisory ``flock`` for the whole change. Whoever holds the ``flock`` owns
the lock: a second apply is refused while the first runs, and only explicit
recovery may take over a lock whose holder died (no ``flock`` on it).
"""

import fcntl
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from lovspor.forarbeider.corpus_transaction import LOCK
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.release_lock import held_by_us, recovered_lock, release_lock


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    path = tmp_path / "clone"
    (path / ".git").mkdir(parents=True, mode=0o700)
    return path


def _refused(
    error: pytest.ExceptionInfo[ForarbeiderRefusedError], reason: str, detail: str
) -> None:
    assert (error.value.reason.value, error.value.detail) == (reason, detail)


def test_the_lock_is_made_held_and_removed(checkout: Path) -> None:
    with release_lock(checkout) as held:
        status = os.lstat(checkout / LOCK)
        assert (status.st_dev, status.st_ino) == (held.device, held.inode)
        assert oct(status.st_mode & 0o777) == "0o600"
        assert held_by_us(checkout, held)
    assert not (checkout / LOCK).exists()
    assert list((checkout / ".git").iterdir()) == []


def _refusal(lock: Callable[[Path], AbstractContextManager[object]], checkout: Path) -> str:
    with pytest.raises(ForarbeiderRefusedError) as caught, lock(checkout):
        pass
    return f"{caught.value.reason.value}: {caught.value.detail}"


def test_a_second_apply_is_refused_while_the_first_holds_the_lock(checkout: Path) -> None:
    with release_lock(checkout) as held:
        assert _refusal(release_lock, checkout) == "corpus_locked: checkout"
        assert held_by_us(checkout, held)
    assert not (checkout / LOCK).exists()


def test_recovery_cannot_take_a_lock_whose_holder_is_alive(checkout: Path) -> None:
    with release_lock(checkout) as held:
        assert _refusal(recovered_lock, checkout) == "corpus_locked: checkout"
        assert held_by_us(checkout, held)
    assert not (checkout / LOCK).exists()


def test_a_fresh_apply_never_takes_over_a_dead_holders_lock(checkout: Path) -> None:
    (checkout / LOCK).write_bytes(b"")

    with pytest.raises(ForarbeiderRefusedError) as caught, release_lock(checkout):
        pass

    _refused(caught, "corpus_locked", "checkout")
    assert (checkout / LOCK).exists()


def test_recovery_takes_over_a_dead_holders_lock_and_removes_it(checkout: Path) -> None:
    (checkout / LOCK).write_bytes(b"")
    (checkout / LOCK).chmod(0o600)

    with recovered_lock(checkout) as held:
        assert held_by_us(checkout, held)

    assert not (checkout / LOCK).exists()


def test_recovery_without_a_lock_makes_a_fresh_one(checkout: Path) -> None:
    with recovered_lock(checkout) as held:
        assert held_by_us(checkout, held)
    assert not (checkout / LOCK).exists()


def test_a_symlinked_or_hard_linked_lock_is_refused(checkout: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.write_bytes(b"")
    (checkout / LOCK).symlink_to(target)
    with pytest.raises(ForarbeiderRefusedError) as caught, recovered_lock(checkout):
        pass
    _refused(caught, "checkout_unsafe", "lock")
    (checkout / LOCK).unlink()
    os.link(target, checkout / LOCK)
    with pytest.raises(ForarbeiderRefusedError) as caught, recovered_lock(checkout):
        pass
    _refused(caught, "checkout_unsafe", "lock")
    assert target.read_bytes() == b""


def test_a_lock_replaced_under_the_holder_is_not_held_by_us(checkout: Path) -> None:
    with release_lock(checkout) as held:
        (checkout / LOCK).unlink()
        (checkout / LOCK).write_bytes(b"")
        assert not held_by_us(checkout, held)
    assert (checkout / LOCK).exists(), "a lock we no longer hold is never removed"


def test_a_symlinked_git_directory_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real-git"
    real.mkdir(mode=0o700)
    checkout = tmp_path / "clone"
    checkout.mkdir(mode=0o700)
    (checkout / ".git").symlink_to(real)

    with pytest.raises(ForarbeiderRefusedError) as caught, release_lock(checkout):
        pass

    _refused(caught, "checkout_unsafe", "lock")
    assert list(real.iterdir()) == []


def test_the_holder_keeps_an_advisory_lock_on_the_file(checkout: Path) -> None:
    with release_lock(checkout):
        descriptor = os.open(checkout / LOCK, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
