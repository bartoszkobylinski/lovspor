"""AR4b-1: the append-only, hash-linked publication attempt records, and the stop latch (#738).

``<workspace>/<transaction_id>/<sequence>.json``: one canonical record per
step, created ``O_CREAT | O_EXCL | O_NOFOLLOW``, owner-only, never replaced
and never removed. Each names the digest of the one before it, so a record
cut, reordered or rewritten in place is refused when the folder is read
(``corpus_tree_mismatch: attempts``), and nothing is sent on its account.

``<workspace>/stop-latch/<sequence>.json`` is the persistent stop latch
(§9.3 step 5), since AR5b an append-only chain of transitions: ``set`` by a
STOP, a revocation, an unknown outcome or a halt, and ``cleared`` only by an
owner-signed RESUME after the cause is resolved
(:mod:`~lovspor.forarbeider.publication_stop_resume`). It is read before every
authorization. No transition is removed or replaced, so every ``set`` stays as
evidence. The AR4b-1 single-file latch (``stop-latch.json``) is read as set for
good and is never cleared.

``<workspace>/workspace.lock`` serializes everything that reads and appends
the workspace's attempt records, receipts and latch (§9.3a): an execution
from authorization to its last record, and a recovery from its first remote
read to its last record. Whoever holds the file's ``flock`` owns the
workspace; a second holder is refused at once (``corpus_locked: workspace``),
never queued, so no wait can deadlock. The file stays in place; after the
``flock`` is taken its name must still be that file, a single private regular
file. Lock ordering: the AR4a clone lock is never taken under the workspace
lock. The only lock taken while holding it is the AR5a anchor witness's
(:func:`~lovspor.forarbeider.control_plane_anchor.durable_append`), and that
one is a leaf: nothing holding it takes another lock.

Limitation, as for the AR3 ledger: a local folder proves neither that it was
not withheld nor that its newest record was not replaced by an equally
canonical one. An external anchor of the head is AR5.
"""

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from lovspor.forarbeider.corpus_leaves import directory, read_leaf
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_execution_models import AttemptRecord, LatchEvent
from lovspor.forarbeider.version_models import canonical_text

_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_LATCH = "stop-latch.json"
LATCH_FOLDER = "stop-latch"
_LOCK = "workspace.lock"
_LOCK_OPEN = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, detail)


def _name(sequence: int) -> str:
    return f"{sequence:06d}.json"


def create_once(folder: Path, name: str, payload: bytes) -> None:
    """Create ``name`` once; an existing name is a refusal, never overwritten."""
    with directory(folder, "attempts") as parent:
        try:
            descriptor = os.open(name, _CREATE, 0o600, dir_fd=parent)
        except FileExistsError as error:
            raise _refused("attempts") from error
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)


def _folder(workspace: Path, transaction_id: str, create: bool = False) -> Path:
    folder = workspace / transaction_id
    if create and not os.path.lexists(folder):
        folder.mkdir(mode=0o700)
    if os.path.lexists(folder) and (folder.is_symlink() or not folder.is_dir()):
        raise _refused("attempts")
    return folder


def _names(folder: Path) -> list[str]:
    if not os.path.lexists(folder):
        return []
    names = sorted(item.name for item in folder.iterdir())
    if names != [_name(n) for n in range(1, len(names) + 1)]:
        raise _refused("attempts")
    return names


def _parsed(folder: Path, name: str) -> tuple[AttemptRecord, bytes]:
    try:
        with directory(folder, "attempts") as parent:
            raw = read_leaf(parent, name, "attempts")
        record = AttemptRecord.model_validate_json(raw)
    except (OSError, ValidationError, ForarbeiderRefusedError) as error:
        raise _refused("attempts") from error
    if canonical_text(record).encode() != raw:
        raise _refused("attempts")
    return record, raw


def _walk(folder: Path, transaction_id: str) -> Iterator[tuple[AttemptRecord, bytes]]:
    previous: str | None = None
    for index, name in enumerate(_names(folder), start=1):
        record, raw = _parsed(folder, name)
        linked = (record.sequence, record.previous_sha256, record.transaction_id) == (
            index,
            previous,
            transaction_id,
        )
        if not linked:
            raise _refused("attempts")
        previous = sha256(raw).hexdigest()
        yield record, raw


def records(workspace: Path, transaction_id: str) -> list[AttemptRecord]:
    """Every record of the transaction, in order, each proved linked to the one before.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: attempts``.
    """
    return [record for record, _ in _walk(_folder(workspace, transaction_id), transaction_id)]


def head(workspace: Path, transaction_id: str) -> tuple[int, str | None]:
    """The next sequence number and the digest the next record must name."""
    walked = list(_walk(_folder(workspace, transaction_id), transaction_id))
    if not walked:
        return 1, None
    return len(walked) + 1, sha256(walked[-1][1]).hexdigest()


def append(workspace: Path, record: AttemptRecord) -> None:
    """Persist ``record`` durably as the transaction's next step, before anything it announces.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: attempts`` when it is not the next.
    """
    if head(workspace, record.transaction_id) != (record.sequence, record.previous_sha256):
        raise _refused("attempts")
    folder = _folder(workspace, record.transaction_id, create=True)
    create_once(folder, _name(record.sequence), canonical_text(record).encode())


def _latch_folder(workspace: Path) -> Path:
    folder = workspace / LATCH_FOLDER
    if os.path.lexists(folder) and (folder.is_symlink() or not folder.is_dir()):
        raise _refused("stop_latch")
    return folder


def _latch_event(folder: Path, name: str) -> tuple[LatchEvent, bytes]:
    try:
        with directory(folder, "stop_latch") as parent:
            raw = read_leaf(parent, name, "stop_latch")
        event = LatchEvent.model_validate_json(raw)
    except (OSError, ValidationError, ForarbeiderRefusedError) as error:
        raise _refused("stop_latch") from error
    if canonical_text(event).encode() != raw:
        raise _refused("stop_latch")
    return event, raw


def _latch_chain(workspace: Path) -> list[tuple[LatchEvent, bytes]]:
    folder = _latch_folder(workspace)
    chain: list[tuple[LatchEvent, bytes]] = []
    for index, name in enumerate(_names(folder), start=1):
        event, raw = _latch_event(folder, name)
        previous = chain[-1] if chain else None
        link = sha256(previous[1]).hexdigest() if previous else None
        expected = "cleared" if previous and previous[0].kind == "set" else "set"
        if (event.sequence, event.previous_sha256, event.kind) != (index, link, expected):
            raise _refused("stop_latch")
        chain.append((event, raw))
    return chain


def latch_events(workspace: Path) -> list[LatchEvent]:
    """Every latch transition, in order: ``set`` and ``cleared`` alternating, never removed.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: stop_latch``.
    """
    return [event for event, _ in _latch_chain(workspace)]


def effective_latch(workspace: Path) -> LatchEvent | None:
    """The ``set`` in force, or ``None`` when the newest transition is a clearing."""
    chain = latch_events(workspace)
    return chain[-1] if chain and chain[-1].kind == "set" else None


def latched(workspace: Path) -> bool:
    """Whether the stop latch is in force.

    The AR4b-1 single-file latch, or anything at its name, counts as set for
    good: it predates the auditable transitions and nothing clears it.
    """
    return os.path.lexists(workspace / _LATCH) or effective_latch(workspace) is not None


def latch_head(workspace: Path) -> tuple[int, str | None]:
    """The next transition's sequence and the digest it must name."""
    chain = _latch_chain(workspace)
    return len(chain) + 1, sha256(chain[-1][1]).hexdigest() if chain else None


def append_latch(workspace: Path, event: LatchEvent) -> None:
    """Persist ``event`` as the next transition: linked, and alternating with the one before.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: stop_latch`` when it is not the next.
    """
    current = effective_latch(workspace)
    expected = "cleared" if current is not None else "set"
    if (event.sequence, event.previous_sha256) != latch_head(workspace) or event.kind != expected:
        raise _refused("stop_latch")
    folder = _latch_folder(workspace)
    if not os.path.lexists(folder):
        folder.mkdir(mode=0o700)
    create_once(folder, _name(event.sequence), canonical_text(event).encode())


def _append_latch(workspace: Path, fields: dict[str, object]) -> LatchEvent:
    sequence, previous = latch_head(workspace)
    event = LatchEvent.model_validate({**fields, "sequence": sequence, "previous_sha256": previous})
    append_latch(workspace, event)
    return event


def latch(workspace: Path, reasons: tuple[str, ...], at: datetime, transaction_id: str) -> None:
    """Set the stop latch; a latch already in force stays as it is."""
    if latched(workspace):
        return
    fields = {"kind": "set", "at": at, "transaction_id": transaction_id, "reasons": reasons}
    _append_latch(workspace, fields)


def clear_latch(
    workspace: Path, at: datetime, resume_control_sha256: str, resolution: tuple[str, ...]
) -> LatchEvent:
    """Append the clearing of the latch in force; only an owner RESUME (AR5b) may call it.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: stop_latch`` when no
            transition ``set`` is in force (the AR4b-1 file latch is never cleared).
    """
    current = effective_latch(workspace)
    if current is None or os.path.lexists(workspace / _LATCH):
        raise _refused("stop_latch")
    fields = {
        "kind": "cleared",
        "at": at,
        "transaction_id": current.transaction_id,
        "reasons": resolution,
        "resume_control_sha256": resume_control_sha256,
    }
    return _append_latch(workspace, fields)


def private_workspace(workspace: Path) -> bool:
    status = os.lstat(workspace)
    return stat.S_ISDIR(status.st_mode) and not status.st_mode & 0o077


def _lock_refused(reason: RefusalReason, detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(reason, detail)


def _claim(parent: int, descriptor: int) -> None:
    """Take the ``flock``, then prove the name still holds the one private file locked.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: workspace`` (another holder, or
            the lock moved), ``checkout_unsafe: workspace_lock``.
    """
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise _lock_refused(RefusalReason.CORPUS_LOCKED, "workspace") from error
    mine = os.fstat(descriptor)
    try:
        named = os.stat(_LOCK, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError as error:
        raise _lock_refused(RefusalReason.CORPUS_LOCKED, "workspace") from error
    if (named.st_dev, named.st_ino) != (mine.st_dev, mine.st_ino):
        raise _lock_refused(RefusalReason.CORPUS_LOCKED, "workspace")
    private = mine.st_uid == os.geteuid() and not stat.S_IMODE(mine.st_mode) & 0o077
    if not stat.S_ISREG(mine.st_mode) or mine.st_nlink != 1 or not private:
        raise _lock_refused(RefusalReason.CHECKOUT_UNSAFE, "workspace_lock")


@contextmanager
def workspace_lock(workspace: Path) -> Iterator[None]:
    """The workspace, exclusively, until the block ends; a second holder is refused.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: workspace`` while another
            execution or recovery holds it; ``checkout_unsafe: workspace_lock``
            for a link or a shared file; ``unsafe_output_path`` for a workspace
            that is not a private directory.
    """
    with directory(workspace, "workspace") as parent:
        try:
            descriptor = os.open(_LOCK, _LOCK_OPEN, 0o600, dir_fd=parent)
        except OSError as error:
            raise _lock_refused(RefusalReason.CHECKOUT_UNSAFE, "workspace_lock") from error
        try:
            _claim(parent, descriptor)
            yield
        finally:
            os.close(descriptor)
