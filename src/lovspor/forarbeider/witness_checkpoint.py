"""AR7a: an independently pinned, monotonic checkpoint for a witness log (#738, #1037).

AR5's witness signs anchors, and a signature proves who made them, not that
they are the newest or the only ones: whoever holds the witness directory can
copy it, restore an older copy, or keep two copies that each grow their own
signed history (``tests/unit/test_forarbeider_ar5_rollback_gap.py``). AR7a
adds what a signature cannot give, as a foundation an AR5 family can adopt
later. It migrates nothing and changes no AR5 verification.

Four properties, kept apart:

A. **Log identity.** The owner approves one log by signing a
   :class:`~lovspor.forarbeider.witness_checkpoint_models.LogBootstrap` in the
   log namespace. The log id is the digest of those bytes, so it names what the
   owner approved, never a key, a namespace or a directory. The bootstrap also
   names the witness keys and **the authority**, so a caller cannot choose
   where the pin is read.
B. **History consistency.** Every checkpoint names its sequence, the chained
   root over every entry and its predecessor's digest, and is signed in the
   checkpoint namespace by a witness key the bootstrap lists and the trust
   root holds, unrevoked, for the role ``anchor_witness``.
C. **Independent latest-state pin.** The authority is a Git ref
   ``refs/heads/witness/<name>`` in a repository outside the request, the
   workspace and the witness store. Its commit holds the newest signed
   checkpoint. The pin moves only by a fast-forward push of a child of the pin
   the writer read: that push is the compare-and-swap. The authority's
   ``receive.denyNonFastForwards`` and ``receive.denyDeletes`` refuse a rewind,
   a sibling and a deletion from any client, and :func:`current` refuses an
   authority that does not set both (``authority_not_monotonic``).
D. **Non-equivocation.** Two signed successors of one checkpoint can exist
   (two stores, one key), but the pin names one. The other store then holds a
   checkpoint the pin contradicts and is ``diverged``, for good.

Because any writer of the authority can push fast-forward commits (an older,
validly signed checkpoint replayed, an invalid commit, a fork under a valid
last link), the tip, its depth and its last link prove nothing about the
commits beneath. The verifier reads the ref's whole first-parent lineage: every
commit has one parent at most, holds exactly the pin file and holds a
witness-signed checkpoint of this log extending the one before it, from
genesis (#1051). A commit that cannot be read is ``authority_unavailable``; an
invalid one fails the log closed for good.

**Selection is not validity.** The bootstrap comes from the caller. Two logs
the owner approved for one repository and purpose each verify: a verdict says
the offered log is owner-approved and pinned, never that it is *the* log.
Authoritative selection is the engine's reviewed registry, outside the request
and the store: :mod:`~lovspor.forarbeider.witness_log_registry` (AR7b-1, #1052).

**Currentness is not consistency.** :func:`current` decides whether the
store's head is the pinned checkpoint (``verified``). :func:`standing` judges a
checkpoint someone presents: ``current``, ``stale`` (consistent, older),
``conflicting`` or ``unpinned``. A consistency proof shows that one checkpoint
extends another; it never shows that no newer one exists. Only the pin does.

**Trust assumptions.** The pin is only as monotonic as the authority:
whoever administers the authority repository can change its settings or
rewrite the ref outside receive-pack. Against that, a caller may pass the
newest checkpoint it already trusts (``known``): a pin behind it is
``pin_behind_known``. In this PR the only authority is a local bare repository
(simulation). A production authority (a GitHub ref whose ruleset forbids force
pushes and deletion, with no bypass for the runner's App) needs those settings
verified (AR7 settings verifier, #1046) and is not implemented.

**Bootstrap and advancement.** Nothing is trusted on first use:
:func:`current` never creates a pin, and a missing pin is ``pin_missing``.
:func:`bootstrap` creates the pin once, from the owner-approved genesis.
:func:`advance` verifies the current state, writes the signed successor to the
store, then moves the pin. A crash between the two leaves ``pending_advance``
with the last trusted pin intact; the next :func:`advance` completes it. An
authority that cannot be read or moved denies the advance, and every status
other than ``verified`` makes :func:`require_current` refuse, which is how a
dependent publication is denied.

**Lock ordering.** :func:`advance` and :func:`bootstrap` hold the store's own
lock, a leaf, while they read and move the pin.
"""

import fcntl
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import ValidationError

from lovspor import git_checkpoint
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import (
    CHECKPOINT_NAMESPACE,
    LOG_NAMESPACE,
    SignedBytes,
    TrustRoot,
    check_signature,
)
from lovspor.forarbeider.publication_path_b import Signer
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.witness_checkpoint_models import (
    PIN_FILE,
    Checkpoint,
    LogBootstrap,
    SignedCheckpoint,
    Standing,
    Status,
    checkpoint_bytes,
    checkpoint_digest,
    genesis,
    log_id_of,
    successor,
)

_LOCK = ".store.lock"
_TEMPORARY = ".tmp-"
_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


class TrustedLog(NamedTuple):
    """The owner-approved bootstrap of one log, as signed bytes, and the trust root."""

    bootstrap: bytes
    signature: bytes
    root: TrustRoot


class Witnessing(NamedTuple):
    """Everything one verification reads: the log, the witness store and the time.

    ``known`` is the canonical bytes of the newest signed checkpoint the caller
    already trusts, if any; ``workspace`` is the runner's, which neither the
    store nor the authority may share.
    """

    log: TrustedLog
    store: "CheckpointStore"
    at: datetime
    known: bytes | None = None
    workspace: Path | None = None


class CheckpointVerdict(NamedTuple):
    status: Status
    reasons: tuple[str, ...]
    log_id: str | None
    pinned: Checkpoint | None
    head: Checkpoint | None
    pin_commit: str | None = None


class AdvanceOutcome(NamedTuple):
    status: Literal["advanced", "completed", "already_applied", "bootstrapped"]
    checkpoint: Checkpoint


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, detail)


# --- the witness store ---------------------------------------------------------------------------


class CheckpointStore:
    """The witness's own directory of signed checkpoints, one folder per log id."""

    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def location(self) -> Path:
        return self._root

    def history(self, log_id: str) -> tuple[bytes, ...]:
        """Every stored checkpoint of ``log_id`` in order, as stored.

        Raises:
            OSError: the store cannot be read.
        """
        folder = self._root / log_id
        if not self._root.is_dir():
            raise FileNotFoundError(self._root)
        if not folder.is_dir():
            return ()
        names = sorted(p.name for p in folder.iterdir() if not p.name.startswith(_TEMPORARY))
        return tuple((folder / name).read_bytes() for name in names)

    def write(self, log_id: str, sequence: int, data: bytes) -> bool:
        """Write whole, then link to the final name; ``False`` when that name exists.

        Raises:
            OSError: the store cannot be written.
        """
        folder = self._root / log_id
        folder.mkdir(mode=0o700, exist_ok=True)
        temporary = folder / f"{_TEMPORARY}{secrets.token_hex(8)}"
        descriptor = os.open(temporary, _CREATE, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, folder / f"{sequence:08d}.json")
        except FileExistsError:
            return False
        finally:
            temporary.unlink(missing_ok=True)
        _sync(folder)
        return True

    @contextmanager
    def locked(self) -> Iterator[None]:
        try:
            descriptor = os.open(self._root / _LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise _refused("store_unavailable") from error
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _sync(folder: Path) -> None:
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# --- judging records -----------------------------------------------------------------------------


class _Log(NamedTuple):
    bootstrap: LogBootstrap
    log_id: str
    root: TrustRoot
    at: datetime


def _bootstrap(log: TrustedLog, at: datetime) -> _Log | str:
    try:
        parsed = LogBootstrap.model_validate_json(log.bootstrap)
    except ValidationError:
        return "bootstrap_malformed"
    if canonical_text(parsed).encode() != log.bootstrap:
        return "bootstrap_not_canonical"
    check = check_signature(log.root, SignedBytes(log.bootstrap, log.signature), LOG_NAMESPACE, at)
    # the trust root gives the log namespace to the owner role only (policy_trust)
    if not check.verified:
        return f"bootstrap_{check.reason}"
    return _Log(parsed, log_id_of(parsed), log.root, at)


def approved_bootstrap(log: TrustedLog, at: datetime) -> LogBootstrap | str:
    """The bootstrap in ``log`` when it is canonical and owner-signed; else why not.

    Owner approval only: it never says the log is pinned, current or selected.
    """
    found = _bootstrap(log, at)
    return found if isinstance(found, str) else found.bootstrap


def _checkpoint(raw: bytes, log: _Log) -> Checkpoint | str:
    """The signed checkpoint in ``raw`` when it is canonical, this log's and witness-signed."""
    try:
        signed = SignedCheckpoint.model_validate_json(raw)
    except ValidationError:
        return "checkpoint_malformed"
    if canonical_text(signed).encode() != raw:
        return "checkpoint_not_canonical"
    checkpoint = signed.checkpoint
    if checkpoint.log_id != log.log_id:
        return "checkpoint_of_another_log"
    body = SignedBytes(checkpoint_bytes(checkpoint), signed.signature.encode())
    check = check_signature(log.root, body, CHECKPOINT_NAMESPACE, log.at)
    if not check.verified:
        return f"checkpoint_{check.reason}"
    if check.entry is None or check.entry.fingerprint not in log.bootstrap.witness_keys:
        return "checkpoint_signer_not_the_logs_witness"
    return checkpoint


def _follows(previous: Checkpoint | None, checkpoint: Checkpoint) -> bool:
    if previous is None:
        return checkpoint == genesis(checkpoint.log_id)
    entry = checkpoint.entry_sha256
    return entry is not None and checkpoint == successor(previous, entry)


def _chain(raws: tuple[bytes, ...], log: _Log) -> list[Checkpoint] | str:
    """The store's checkpoints from genesis, each extending the one before, or why not."""
    chain: list[Checkpoint] = []
    for raw in raws:
        found = _checkpoint(raw, log)
        if isinstance(found, str):
            return found
        if not _follows(chain[-1] if chain else None, found):
            return "checkpoint_inconsistent"
        chain.append(found)
    return chain


def consistent(older: Checkpoint, newer: Checkpoint, proof: list[Checkpoint]) -> bool:
    """Whether ``proof`` (every checkpoint after ``older`` up to ``newer``) links the two.

    True says only that ``newer`` extends ``older``; never that ``newer`` is the newest.
    """
    previous = older
    for checkpoint in proof:
        if not _follows(previous, checkpoint):
            return False
        previous = checkpoint
    return previous == newer


# --- the authority -------------------------------------------------------------------------------


def _authority(log: _Log) -> Path | None:
    remote = log.bootstrap.authority.remote
    path = Path(remote)
    return (
        path if git_checkpoint.allowed(("ls-remote", remote, log.bootstrap.authority.ref)) else None
    )


def _apart(paths: list[Path]) -> bool:
    resolved = [Path(os.path.realpath(p)) for p in paths]
    for index, one in enumerate(resolved):
        for other in resolved[index + 1 :]:
            if one == other or one in other.parents or other in one.parents:
                return False
    return True


class _Pin(NamedTuple):
    checkpoint: Checkpoint
    commit: str


class _NoPin(NamedTuple):
    status: Status
    reason: str


def _pin(bare: Path, log: _Log) -> _Pin | _NoPin:
    """The checkpoint the authority pins, judged with its whole lineage; or why not.

    Every commit from the root to the tip must have one parent at most, hold
    exactly the pin file, and hold a checkpoint of this log, witness-signed,
    that extends the one before it from genesis. The tip's last link, its depth
    and the absence of merges prove nothing about the commits beneath them.

    Raises:
        git_checkpoint.GitCheckpointError: the authority, or any commit of its
            history, cannot be read.
    """
    flags = git_checkpoint.settings(bare)
    if not (flags.deny_non_fast_forwards and flags.deny_deletes):
        return _NoPin("authority_not_monotonic", "authority_settings")
    tip = git_checkpoint.tip(bare, log.bootstrap.authority.ref)
    if tip is None:
        return _NoPin("pin_missing", "pin_ref_absent")
    return _lineage(git_checkpoint.lineage(bare, tip), log)


def _lineage(commits: tuple[git_checkpoint.Pinned, ...], log: _Log) -> _Pin | _NoPin:
    """The tip's checkpoint when every commit, oldest first, extends the one before it."""
    previous: Checkpoint | None = None
    for commit in commits:
        if len(commit.parents) > 1:
            return _NoPin("pin_invalid", "pin_lineage_merge")
        found = _pinned_checkpoint(commit, log)
        if isinstance(found, str):
            return _NoPin("pin_invalid", f"pin_lineage_{found}")
        if not _follows(previous, found):
            return _NoPin("pin_invalid", "pin_lineage_inconsistent")
        previous = found
    if previous is None:
        return _NoPin("pin_invalid", "pin_lineage_empty")
    return _Pin(previous, commits[-1].commit)


def _pinned_checkpoint(pinned: git_checkpoint.Pinned, log: _Log) -> Checkpoint | str:
    if set(pinned.files) != {PIN_FILE}:
        return "pin_files_unexpected"
    return _checkpoint(pinned.files[PIN_FILE], log)


# --- verification --------------------------------------------------------------------------------


def _verdict(status: Status, log: _Log | None, pinned: _Pin | None = None) -> CheckpointVerdict:
    """A verdict without reasons or a store head; callers add those with ``_replace``."""
    log_id = None if log is None else log.log_id
    if pinned is None:
        return CheckpointVerdict(status, (), log_id, None, None)
    return CheckpointVerdict(status, (), log_id, pinned.checkpoint, None, pinned.commit)


def _known(witnessing: Witnessing, log: _Log, pinned: Checkpoint) -> Checkpoint | Status | None:
    if witnessing.known is None:
        return None
    known = _checkpoint(witnessing.known, log)
    if isinstance(known, str):
        return "pin_behind_known"
    return "pin_behind_known" if known.sequence > pinned.sequence else known


def _compare(chain: list[Checkpoint], pinned: Checkpoint) -> Status:
    size = pinned.sequence + 1
    if len(chain) < size:
        return "store_rolled_back"
    if chain[pinned.sequence] != pinned:
        return "diverged"
    if len(chain) == size:
        return "verified"
    return "pending_advance" if len(chain) == size + 1 else "unpinned_tail"


def _pinned_state(witnessing: Witnessing) -> tuple[_Log, _Pin] | CheckpointVerdict:
    """The log and the checkpoint its authority pins, or the verdict that there is none."""
    log = _bootstrap(witnessing.log, witnessing.at)
    if isinstance(log, str):
        return _verdict("bootstrap_invalid", None)._replace(reasons=(log,))
    bare = _authority(log)
    places = [witnessing.store.location] + ([] if bare is None else [bare])
    places += [] if witnessing.workspace is None else [witnessing.workspace]
    if bare is None or not _apart(places):
        return _verdict("authority_not_independent", log)
    try:
        pinned = _pin(bare, log)
    except git_checkpoint.GitCheckpointError:
        return _verdict("authority_unavailable", log)
    if isinstance(pinned, _NoPin):
        return _verdict(pinned.status, log)._replace(reasons=(pinned.reason,))
    return log, pinned


def current(witnessing: Witnessing) -> CheckpointVerdict:
    """Whether the store's head is the checkpoint the independent authority pins."""
    state = _pinned_state(witnessing)
    if isinstance(state, CheckpointVerdict):
        return state
    log, pinned = state
    known = _known(witnessing, log, pinned.checkpoint)
    if isinstance(known, str):
        return _verdict(known, log, pinned)
    try:
        chain = _chain(witnessing.store.history(log.log_id), log)
    except OSError:
        return _verdict("store_unavailable", log, pinned)
    if isinstance(chain, str):
        return _verdict("store_invalid", log, pinned)._replace(reasons=(chain,))
    head = chain[-1] if chain else None
    if known is not None and (len(chain) <= known.sequence or chain[known.sequence] != known):
        reasons = ("known_checkpoint_not_in_history",)
        return _verdict("diverged", log, pinned)._replace(reasons=reasons, head=head)
    return _verdict(_compare(chain, pinned.checkpoint), log, pinned)._replace(head=head)


def require_current(witnessing: Witnessing) -> Checkpoint:
    """The pinned checkpoint, only when the store's head is it: consistency, not selection.

    It never selects the log: any owner-approved bootstrap the caller offers
    can pass (#1052). A publication-facing dependent must use
    :func:`lovspor.forarbeider.witness_log_registry.require_selected`, which
    also requires the engine's reviewed selection of this log.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: checkpoint_<status>``
            for every status but ``verified``.
    """
    verdict = current(witnessing)
    if verdict.status != "verified" or verdict.pinned is None:
        raise _refused(f"checkpoint_{verdict.status}")
    return verdict.pinned


def standing(witnessing: Witnessing, presented: bytes) -> Standing:
    """Where a presented signed checkpoint stands against the current, pinned history."""
    log = _bootstrap(witnessing.log, witnessing.at)
    verdict = current(witnessing)
    if isinstance(log, str) or verdict.status != "verified" or verdict.pinned is None:
        return "invalid"
    found = _checkpoint(presented, log)
    if isinstance(found, str):
        return "invalid"
    if found.sequence > verdict.pinned.sequence:
        return "unpinned"
    chain = _chain(witnessing.store.history(log.log_id), log)
    if isinstance(chain, str) or chain[found.sequence] != found:
        return "conflicting"
    return "current" if found == verdict.pinned else "stale"


# --- bootstrap and advancement -------------------------------------------------------------------


def _signed(checkpoint: Checkpoint, signer: Signer) -> bytes:
    signature = signer.sign(checkpoint_bytes(checkpoint), CHECKPOINT_NAMESPACE).decode()
    return canonical_text(SignedCheckpoint(checkpoint=checkpoint, signature=signature)).encode()


def _move(log: _Log, raw: bytes, expected: str | None, at: datetime) -> None:
    bare = _authority(log)
    if bare is None:
        raise _refused("checkpoint_authority_not_independent")
    target = git_checkpoint.Target(bare, log.bootstrap.authority.ref)
    outcome = git_checkpoint.advance(target, expected, {PIN_FILE: raw}, at)
    if outcome == "moved":
        raise _refused("checkpoint_authority_moved")
    if outcome != "advanced":
        raise _refused("checkpoint_authority_unavailable")


def _store_write(witnessing: Witnessing, checkpoint: Checkpoint, raw: bytes) -> None:
    try:
        written = witnessing.store.write(checkpoint.log_id, checkpoint.sequence, raw)
    except OSError as error:
        raise _refused("checkpoint_store_unavailable") from error
    if not written:
        raise _refused("checkpoint_store_head_moved")


def bootstrap(witnessing: Witnessing, signer: Signer) -> AdvanceOutcome:
    """Pin the owner-approved genesis, once; never over an existing pin or store.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: checkpoint_<reason>``.
    """
    log = _bootstrap(witnessing.log, witnessing.at)
    if isinstance(log, str):
        raise _refused(f"checkpoint_bootstrap_invalid: {log}")
    first = genesis(log.log_id)
    with witnessing.store.locked():
        verdict = current(witnessing)
        if verdict.status == "verified" and verdict.pinned == first:
            return AdvanceOutcome("already_applied", first)
        if verdict.status != "pin_missing":
            raise _refused(f"checkpoint_{verdict.status}")
        raw = _signed(first, signer)
        stored = witnessing.store.history(log.log_id)
        if stored and stored[0] != raw:
            raise _refused("checkpoint_store_not_empty")
        if not stored:
            _store_write(witnessing, first, raw)
        _move(log, raw, None, witnessing.at)
    return AdvanceOutcome("bootstrapped", first)


def advance(witnessing: Witnessing, signer: Signer, entry_sha256: str) -> AdvanceOutcome:
    """Append ``entry_sha256`` and move the pin to its signed checkpoint, by compare-and-swap.

    A pending checkpoint (a crash after the store write, before the pin moved)
    is completed first; when it already adds ``entry_sha256`` that completes
    the retry. A pinned head that adds ``entry_sha256`` is ``already_applied``.
    The pin moves only from the commit this call verified.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: checkpoint_<reason>``
            for any state that is not ``verified`` or ``pending_advance``, an
            authority that moved or cannot be moved, and a store head that moved.
    """
    log = _bootstrap(witnessing.log, witnessing.at)
    if isinstance(log, str):
        raise _refused(f"checkpoint_bootstrap_invalid: {log}")
    with witnessing.store.locked():
        verdict = current(witnessing)
        if verdict.status == "pending_advance":
            completed = _complete(witnessing, log, verdict, entry_sha256)
            if completed is not None:
                return completed
            verdict = current(witnessing)
        head, expected = verdict.head, verdict.pin_commit
        if verdict.status != "verified" or head is None or expected is None:
            raise _refused(f"checkpoint_{verdict.status}")
        if head.entry_sha256 == entry_sha256:
            return AdvanceOutcome("already_applied", head)
        following = successor(head, entry_sha256)
        raw = _signed(following, signer)
        _store_write(witnessing, following, raw)
        _move(log, raw, expected, witnessing.at)
    return AdvanceOutcome("advanced", following)


def _complete(
    witnessing: Witnessing, log: _Log, verdict: CheckpointVerdict, entry_sha256: str
) -> AdvanceOutcome | None:
    """Move the pin to the stored pending checkpoint; ``completed`` when it adds the entry."""
    pending = verdict.head
    if pending is None:
        raise _refused(f"checkpoint_{verdict.status}")
    raw = witnessing.store.history(log.log_id)[pending.sequence]
    _move(log, raw, verdict.pin_commit, witnessing.at)
    return AdvanceOutcome("completed", pending) if pending.entry_sha256 == entry_sha256 else None


def digest_of(signed: bytes) -> str:
    """The digest of the checkpoint inside canonical signed bytes (for callers' records).

    Raises:
        ValidationError: ``signed`` is no signed checkpoint.
    """
    return checkpoint_digest(SignedCheckpoint.model_validate_json(signed).checkpoint)
