"""AR5a: the anchor witness, an authority outside the runner's workspace (#738; §9.9 AR5).

The witness is what makes an anchor independent of the state it anchors. It
holds the only key with the ``anchor_witness`` role, keeps one append-only
chain of anchors per family and scope, and accepts the next anchor only by
**compare-and-swap**: the anchor must name the witness's current head as its
previous anchor and the next sequence. It never replaces an anchor, never
removes one and never lowers a record count. It sees no workspace and judges
no record: whether the head is right is the runner's verification, signed off
as ``verification: verified`` in the anchor itself.

:class:`OfflineAnchorWitness` is the offline, test-and-pilot witness: a
private directory of its own, which a verifier refuses when it lies inside the
workspace it is asked to anchor (or holds it), and a signer over the witness
key that never enters the workspace. Each anchor is written as a temporary
file, flushed, then linked to its final name, so a crash leaves either the
whole anchor or none; leftovers are ignored. Writers are serialized by an
``flock`` on the witness's own lock file; that lock is a leaf, taken last and
holding no other.

What the offline witness cannot prove: that its directory was not rolled back
by someone holding it. A production witness (AR7) must be append-only on its
own authority; until then a verifier can pin the newest anchor it has seen
(:class:`~lovspor.forarbeider.control_plane_anchor.AnchorPin`).
"""

import fcntl
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import NamedTuple, Protocol

from pydantic import ValidationError

from lovspor.forarbeider.control_plane_anchor_models import (
    ControlPlaneAnchor,
    Family,
    WitnessedAnchor,
    anchor_bytes,
    anchor_digest,
)
from lovspor.forarbeider.corpus_leaves import directory
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import ANCHOR_NAMESPACE
from lovspor.forarbeider.publication_path_b import Signer
from lovspor.forarbeider.version_models import canonical_text

UNAVAILABLE = "anchor_store_unavailable"
HEAD_MOVED = "anchor_head_moved"
_LOCK = ".witness.lock"
_TEMPORARY = ".tmp-"
_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


class AnchorKey(NamedTuple):
    family: Family
    repository: str
    scope: str


class AnchorWitness(Protocol):
    """An authority outside the workspace that keeps witnessed anchors append-only."""

    @property
    def location(self) -> Path | None: ...

    def history(self, key: AnchorKey) -> tuple[bytes, ...]: ...

    def submit(self, anchor: ControlPlaneAnchor) -> WitnessedAnchor: ...


def unavailable() -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_MISSING, UNAVAILABLE)


def _moved() -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_CONFLICT, HEAD_MOVED)


def key_of(anchor: ControlPlaneAnchor) -> AnchorKey:
    return AnchorKey(anchor.family, anchor.repository, anchor.scope)


class OfflineAnchorWitness:
    """The offline witness: its own private directory and its own signing key."""

    __slots__ = ("_root", "_signer")

    def __init__(self, root: Path, signer: Signer) -> None:
        self._root = root
        self._signer = signer

    @property
    def location(self) -> Path:
        return self._root

    def _folder(self, key: AnchorKey) -> Path:
        name = sha256(f"{key.repository}\n{key.scope}".encode()).hexdigest()
        return self._root / key.family / name

    def history(self, key: AnchorKey) -> tuple[bytes, ...]:
        """Every anchor of ``key`` in order, as stored; judging them is the verifier's.

        Raises:
            ForarbeiderRefusedError: ``release_record_missing: anchor_store_unavailable``.
        """
        try:
            with directory(self._root, "anchor_store"):
                folder = self._folder(key)
                if not folder.is_dir():
                    return ()
                names = sorted(p.name for p in folder.iterdir())
                kept = [name for name in names if not name.startswith(_TEMPORARY)]
                return tuple((folder / name).read_bytes() for name in kept)
        except (OSError, ForarbeiderRefusedError) as error:
            raise unavailable() from error

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            descriptor = os.open(self._root / _LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise unavailable() from error
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _head(self, key: AnchorKey) -> tuple[int, str | None, int]:
        stored = self.history(key)
        if not stored:
            return 1, None, 0
        try:
            latest = WitnessedAnchor.model_validate_json(stored[-1]).anchor
        except ValidationError as error:
            raise _moved() from error
        return len(stored) + 1, anchor_digest(latest), latest.record_count

    def submit(self, anchor: ControlPlaneAnchor) -> WitnessedAnchor:
        """Witness ``anchor`` as the next one of its family and scope, or refuse.

        Raises:
            ForarbeiderRefusedError: ``release_conflict: anchor_head_moved`` when
                it does not extend the current head exactly;
                ``release_record_missing: anchor_store_unavailable``.
        """
        key = key_of(anchor)
        with self._locked():
            sequence, previous, count = self._head(key)
            extends = (anchor.anchor_sequence, anchor.previous_anchor_sha256) == (
                sequence,
                previous,
            )
            if not extends or anchor.record_count < count:
                raise _moved()
            signature = self._signer.sign(anchor_bytes(anchor), ANCHOR_NAMESPACE)
            witnessed = WitnessedAnchor(anchor=anchor, signature=signature.decode())
            self._publish(self._folder(key), f"{sequence:06d}.json", canonical_text(witnessed))
        return witnessed

    def _publish(self, folder: Path, name: str, text: str) -> None:
        """Write whole, then link to the final name; an existing name is never replaced."""
        try:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = folder / f"{_TEMPORARY}{secrets.token_hex(8)}"
            descriptor = os.open(temporary, _CREATE, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(text.encode())
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise unavailable() from error
        try:
            os.link(temporary, folder / name)
        except FileExistsError as error:
            raise _moved() from error
        finally:
            temporary.unlink(missing_ok=True)
        _sync(folder)


def _sync(folder: Path) -> None:
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
