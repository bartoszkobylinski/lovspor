"""AR4b-2: the append-only, hash-linked recovery receipts (#738; §9.3a).

``<workspace>/receipts/<sequence>.json``: one canonical
:class:`~lovspor.forarbeider.publication_recovery_models.RecoveryReceipt` per
classified remote commit, created once (``O_EXCL``), owner-only, never
replaced and never removed. Each names the digest of the one before it, so a
receipt cut, reordered, edited or rewritten in place is refused when the
folder is read (``corpus_tree_mismatch: receipts``).

Limitation, as for the attempt records and the AR3 ledger: a local folder
proves neither that it was not withheld nor that its newest receipt was not
replaced by an equally canonical one. Recovery re-derives every receipt it
relies on from the remote and refuses one that disagrees; an external anchor
of the head is AR5.
"""

import os
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from lovspor.forarbeider.corpus_leaves import directory, read_leaf
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_attempts import create_once
from lovspor.forarbeider.publication_recovery_models import RecoveryReceipt
from lovspor.forarbeider.version_models import canonical_text

FOLDER = "receipts"


def _refused() -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, FOLDER)


def _name(sequence: int) -> str:
    return f"{sequence:06d}.json"


def _folder(workspace: Path) -> Path:
    folder = workspace / FOLDER
    if os.path.lexists(folder) and (folder.is_symlink() or not folder.is_dir()):
        raise _refused()
    return folder


def _read(folder: Path, name: str) -> tuple[RecoveryReceipt, bytes]:
    try:
        with directory(folder, FOLDER) as parent:
            raw = read_leaf(parent, name, FOLDER)
        receipt = RecoveryReceipt.model_validate_json(raw)
    except (OSError, ValidationError, ForarbeiderRefusedError) as error:
        raise _refused() from error
    if canonical_text(receipt).encode() != raw:
        raise _refused()
    return receipt, raw


def _chain(workspace: Path) -> list[tuple[RecoveryReceipt, bytes]]:
    folder = _folder(workspace)
    if not os.path.lexists(folder):
        return []
    names = sorted(item.name for item in folder.iterdir())
    if names != [_name(n) for n in range(1, len(names) + 1)]:
        raise _refused()
    chain: list[tuple[RecoveryReceipt, bytes]] = []
    previous: str | None = None
    for index, name in enumerate(names, start=1):
        receipt, raw = _read(folder, name)
        if (receipt.sequence, receipt.previous_sha256) != (index, previous):
            raise _refused()
        previous = sha256(raw).hexdigest()
        chain.append((receipt, raw))
    return chain


def receipts(workspace: Path) -> list[RecoveryReceipt]:
    """Every receipt, in order, each proved linked to the one before.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: receipts``.
    """
    return [receipt for receipt, _ in _chain(workspace)]


def head(workspace: Path) -> tuple[int, str | None]:
    """The next sequence number and the digest the next receipt must name."""
    chain = _chain(workspace)
    if not chain:
        return 1, None
    return len(chain) + 1, sha256(chain[-1][1]).hexdigest()


def append(workspace: Path, receipt: RecoveryReceipt) -> None:
    """Persist ``receipt`` durably as the next one.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: receipts`` when it is not the next.
    """
    if head(workspace) != (receipt.sequence, receipt.previous_sha256):
        raise _refused()
    folder = _folder(workspace)
    if not os.path.lexists(folder):
        folder.mkdir(mode=0o700)
    create_once(folder, _name(receipt.sequence), canonical_text(receipt).encode())
