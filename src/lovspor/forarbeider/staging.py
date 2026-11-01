"""Where staging output may land: outside every Git repository.

Nothing here may reach ``lovverk`` or the engine before the publication gates
of ADR-0018, so any ancestor holding ``.git`` refuses, whichever repository it
is. A ``.git`` file counts too: that is what a worktree has.
"""

import errno
import os
import stat
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason


def checked_output(raw: Path, forbidden: Sequence[Path]) -> Path:
    """The resolved output path, once it is proved safe to write.

    Args:
        raw: the path the operator gave.
        forbidden: trees refused even without a ``.git``, such as a corpus copy.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path``, before anything is written.
    """
    refused = ForarbeiderRefusedError(RefusalReason.UNSAFE_OUTPUT_PATH, str(raw))
    try:
        path = raw.expanduser()
        if not path.is_absolute() or path.suffix != ".json":
            raise refused
        path = path.resolve()
    except (OSError, RuntimeError) as error:
        raise refused from error
    if path.is_dir() or _inside_a_repository(path) or _inside_any(path, forbidden):
        raise refused
    return path


def checked_directory(raw: Path, forbidden: Sequence[Path]) -> Path:
    """The resolved path of a private directory, proved outside every repository.

    Args:
        raw: the path the operator gave.
        forbidden: trees refused even without a ``.git``, such as a corpus copy.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path``, before anything is touched.
    """
    refused = ForarbeiderRefusedError(RefusalReason.UNSAFE_OUTPUT_PATH, str(raw))
    try:
        path = raw.expanduser()
        if not path.is_absolute():
            raise refused
        path = path.resolve()
    except (OSError, RuntimeError) as error:
        raise refused from error
    trees = (path, *path.parents)
    if any((tree / ".git").exists() for tree in trees):
        raise refused
    if any(tree.resolve() in trees for tree in forbidden):
        raise refused
    return path


def private_directory_exists(path: Path) -> bool:
    """Whether ``path`` is there; a directory anyone else could reach is refused.

    ``lstat``, so a symlink put in place of a directory is not followed.

    Raises:
        ForarbeiderRefusedError: ``review_ledger_unsafe``.
    """
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    alone = status.st_uid == os.geteuid() and not _shared(status)
    if not stat.S_ISDIR(status.st_mode) or not alone:
        raise ForarbeiderRefusedError(RefusalReason.REVIEW_LEDGER_UNSAFE, str(path))
    return True


def ensure_private_directory(path: Path) -> None:
    """Create ``path`` owner-only, or prove the one already there is.

    Raises:
        ForarbeiderRefusedError: ``review_ledger_unsafe`` or ``output_write_failed``.
    """
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        private_directory_exists(path)
    except OSError as error:
        raise ForarbeiderRefusedError(RefusalReason.OUTPUT_WRITE_FAILED, str(path)) from error


def read_private(path: Path, limit: int) -> bytes:
    """The bytes of a private regular file, at most ``limit`` of them.

    The size is read from the open file, so a large one is refused without
    loading it, and a file that grows afterwards is cut at ``limit``, which no
    reader here parses as valid.

    Raises:
        ForarbeiderRefusedError: ``observation_unsafe``, ``input_too_large`` or
            ``input_unreadable``.
    """
    try:
        with _open_private(path) as stream:
            if os.fstat(stream.fileno()).st_size > limit:
                raise ForarbeiderRefusedError(RefusalReason.INPUT_TOO_LARGE, str(path))
            return stream.read(limit)
    except OSError as error:
        raise ForarbeiderRefusedError(RefusalReason.INPUT_UNREADABLE, str(path)) from error


def write_observation(path: Path, text: str) -> bool:
    """Create ``path`` holding ``text``, owner-only, and never replace what is there.

    An observation is a record of what was seen, so a later one takes its own
    path. ``os.link`` is what makes that hold: it publishes the whole staged
    file at once and fails if the name is taken, which ``os.replace`` would not.
    A file already there is only ever read: one others can reach is refused, not
    repaired, because it may already have been read and the operator must know.

    Returns:
        ``True`` when written, ``False`` when ``path`` already holds exactly ``text``
        privately.

    Raises:
        ForarbeiderRefusedError: ``observation_exists``, ``observation_unsafe``, or
            ``output_write_failed``.
    """
    payload = text.encode()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return _publish(path, payload)
    except OSError as error:
        raise ForarbeiderRefusedError(RefusalReason.OUTPUT_WRITE_FAILED, str(path)) from error


def _publish(path: Path, payload: bytes) -> bool:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, path)
    except FileExistsError:
        _require_same_private_file(path, payload)
        return False
    finally:
        staged.unlink(missing_ok=True)
    _sync_directory(path.parent)
    return True


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_same_private_file(path: Path, payload: bytes) -> None:
    """The size is compared first, so a large file at the path is never loaded."""
    with _open_own(path) as stream:
        status = os.fstat(stream.fileno())
        if status.st_size != len(payload) or stream.read(status.st_size) != payload:
            raise ForarbeiderRefusedError(RefusalReason.OBSERVATION_EXISTS, str(path))
        if _shared(status):
            raise ForarbeiderRefusedError(RefusalReason.OBSERVATION_UNSAFE, str(path))


def _open_private(path: Path) -> BinaryIO:
    stream = _open_own(path)
    if _shared(os.fstat(stream.fileno())):
        stream.close()
        raise ForarbeiderRefusedError(RefusalReason.OBSERVATION_UNSAFE, str(path))
    return stream


def _open_own(path: Path) -> BinaryIO:
    """Every check reads the one open file, so the name cannot change between them.

    ``O_NONBLOCK`` is what keeps a FIFO left at the path from hanging the open.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ForarbeiderRefusedError(RefusalReason.OBSERVATION_UNSAFE, str(path)) from error
        raise
    stream = os.fdopen(descriptor, "rb")
    if not _ours_alone(os.fstat(stream.fileno())):
        stream.close()
        raise ForarbeiderRefusedError(RefusalReason.OBSERVATION_UNSAFE, str(path))
    return stream


def _ours_alone(status: os.stat_result) -> bool:
    regular = stat.S_ISREG(status.st_mode)
    return regular and status.st_uid == os.geteuid() and status.st_nlink == 1


def _shared(status: os.stat_result) -> int:
    return stat.S_IMODE(status.st_mode) & (stat.S_IRWXG | stat.S_IRWXO)


def _inside_a_repository(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in path.parents)


def _inside_any(path: Path, trees: Sequence[Path]) -> bool:
    return any(tree.resolve() in path.parents for tree in trees)
