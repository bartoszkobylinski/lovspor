"""AR4b-1: the append-only, hash-linked publication attempt records, and the stop latch (#738).

``<workspace>/<transaction_id>/<sequence>.json``: one canonical record per
step, created ``O_CREAT | O_EXCL | O_NOFOLLOW``, owner-only, never replaced
and never removed. Each names the digest of the one before it, so a record
cut, reordered or rewritten in place is refused when the folder is read
(``corpus_tree_mismatch: attempts``), and nothing is sent on its account.

``<workspace>/stop-latch.json`` is the persistent stop latch (§9.3 step 5):
set once by a STOP, a revocation or an unknown outcome, and read before every
authorization. Clearing it needs an owner-signed resume that AR5 adds; nothing
here removes it.

Limitation, as for the AR3 ledger: a local folder proves neither that it was
not withheld nor that its newest record was not replaced by an equally
canonical one. An external anchor of the head is AR5.
"""

import os
import stat
from collections.abc import Iterator
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from lovspor.forarbeider.corpus_leaves import directory, read_leaf
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_execution_models import AttemptRecord, StopLatch
from lovspor.forarbeider.version_models import canonical_text

_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_LATCH = "stop-latch.json"


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


def latched(workspace: Path) -> bool:
    """Whether the stop latch is set; anything at its name counts as set."""
    return os.path.lexists(workspace / _LATCH)


def latch(workspace: Path, reasons: tuple[str, ...], at: datetime, transaction_id: str) -> None:
    """Set the stop latch, once; a latch already set stays as it is."""
    if latched(workspace):
        return
    record = StopLatch(reasons=reasons, set_at=at, transaction_id=transaction_id)
    create_once(workspace, _LATCH, canonical_text(record).encode())


def private_workspace(workspace: Path) -> bool:
    status = os.lstat(workspace)
    return stat.S_ISDIR(status.st_mode) and not status.st_mode & 0o077
