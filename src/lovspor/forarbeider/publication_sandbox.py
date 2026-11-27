"""AR4b-1: the only adapters a simulation may send through (#738).

A simulation waives the AR1, FC7b and AR5-AR7 checks, so its label is not a
boundary. This is: :mod:`~lovspor.forarbeider.publication_execute` sends a
simulated publication only through these two, each checked by type
identity and, for path B, on disk before every use.

- :class:`SimulatedGitHub` is the GraphQL endpoint held **in memory**. It
  imports nothing that can open a connection; its methods are its own (a
  slotted, final class: an instance cannot replace them and a subclass is
  refused, since the type must be exactly this one). Any other transport, a
  real GitHub client included, is refused before anything is read from it or
  posted to it.
- AR6b: the same endpoint holds the repository's tags (:meth:`SimulatedGitHub.create_ref`),
  where publication attestations are published create-only.
- :class:`DisposableRemote` is a bare repository that
  :func:`create_disposable_remote` made, fresh, as the only entry of a
  private, empty sandbox directory.
"""

import base64
import hmac
import os
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, final

from lovspor.forarbeider import publication_path_b
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_execution_models import AppIdentity

REMOTE = "remote.git"
MARKER = "lovspor-disposable-remote"
NOT_SANDBOXED = "simulation_transport_not_sandboxed"
NOT_DISPOSABLE = "simulation_remote_not_disposable"
CONFIG = (
    b"[core]\n\trepositoryformatversion = 0\n\tbare = true\n\tlogAllRefUpdates = always\n"
    b"[receive]\n\tdenyNonFastForwards = true\n\tdenyDeletes = true\n"
)


SimulatedSigner = tuple[AppIdentity | None, bool]
_STALE_ANSWER: dict[str, Any] = {
    "data": {"createCommitOnBranch": None},
    "errors": [{"type": "STALE_DATA", "message": "Expected branch to point to another commit"}],
}


class SimulatedLostResponseError(Exception):
    """The simulated connection dropped after the mutation was applied."""


class SimulatedCrash(BaseException):
    """The simulated process died after the mutation was applied."""


@dataclass(frozen=True, slots=True)
class SimulatedCommit:
    """One commit as the simulated endpoint reports it, with its ref-update event.

    ``app`` and ``verified`` are what GitHub would report about the signature
    (§9.3a 2a, ``github_app``); ``at`` is the time of the ref update GitHub
    would assert in its activity record (§9.3a 2b ii), ``None`` when it kept none.
    """

    oid: str
    parent: str
    headline: str
    body: str
    changes: tuple[tuple[str, bytes], ...]
    app: AppIdentity | None
    verified: bool
    at: datetime | None


@dataclass(frozen=True, slots=True)
class SimulatedObject:
    """AR6b: a root commit made through the Git Data API, as GitHub would report it."""

    oid: str
    message: str
    files: tuple[tuple[str, bytes], ...]
    app: AppIdentity | None
    verified: bool


_REF_EXISTS: dict[str, Any] = {"message": "Reference already exists", "status": "422"}


@final
@dataclass(slots=True)
class SimulatedGitHub:
    """``main`` of one repository, held in memory, answering as GitHub would.

    ``post`` refuses a stale ``expectedHeadOid`` with ``STALE_DATA``; otherwise
    it creates a commit signed as ``identity`` and moves ``main``. ``fault``
    loses the answer or kills the process after applying; ``response`` replaces
    the answer; ``reported_head`` replaces what ``head`` reads; ``now`` is the
    time the endpoint records for a ref update. ``advance`` is someone else
    moving ``main``: another App, a person, or a commit GitHub did not verify.
    """

    head_oid: str
    identity: AppIdentity
    repository: str = "bartoszkobylinski/lovverk-forarbeider"
    calls: list[dict[str, Any]] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    fault: Literal["lost", "crash"] | None = None
    response: Any = None
    reported_head: str | None = None
    now: datetime | None = None
    commits: dict[str, SimulatedCommit] = field(default_factory=dict)
    #: AR6b: the repository's tags and the root commits they name.
    refs: dict[str, str] = field(default_factory=dict)
    objects: dict[str, SimulatedObject] = field(default_factory=dict)
    ref_calls: list[dict[str, Any]] = field(default_factory=list)
    ref_fault: Literal["lost", "crash"] | None = None

    def _apply(
        self, changes: Mapping[str, bytes], message: tuple[str, str], signer: SimulatedSigner
    ) -> str:
        oid = format(len(self.commits) + len(self.objects) + 1, "040x")
        headline, body = message
        app, verified = signer
        made = SimulatedCommit(
            oid,
            self.head_oid,
            headline,
            body,
            tuple(sorted(changes.items())),
            app,
            verified,
            self.now,
        )
        self.commits[oid] = made
        self.files.update(changes)
        self.head_oid = oid
        return oid

    def advance(
        self, changes: Mapping[str, bytes], message: tuple[str, str], signer: SimulatedSigner
    ) -> str:
        """Someone else moves ``main`` by one commit: ``signer`` is (App or none, verified)."""
        return self._apply(changes, message, signer)

    def commit(self, oid: str) -> SimulatedCommit | None:
        return self.commits.get(oid)

    def head(self, repository: str, branch: str) -> str | None:
        if (repository, branch) != (self.repository, "main"):
            return None
        return self.reported_head or self.head_oid

    def ref(self, name: str) -> str | None:
        """The object a tag names, ``None`` when there is no such tag."""
        return self.refs.get(name)

    def object(self, oid: str) -> SimulatedObject | None:
        return self.objects.get(oid)

    def put_object(self, files: Mapping[str, bytes], message: str, signer: SimulatedSigner) -> str:
        """Someone makes a root commit of ``files`` (``signer`` as for :meth:`advance`)."""
        oid = format(len(self.commits) + len(self.objects) + 1, "040x")
        app, verified = signer
        made = SimulatedObject(oid, message, tuple(sorted(files.items())), app, verified)
        self.objects[oid] = made
        return oid

    def create_ref(self, payload: dict[str, Any]) -> Any:
        """AR6b: blobs, a root tree, a commit signed as the App and a new tag, as the Git Data
        API would; an existing tag is never moved (``Reference already exists``)."""
        self.ref_calls.append(payload)
        name = payload["ref"]
        if name in self.refs:
            return _REF_EXISTS
        files = {path: base64.b64decode(data) for path, data in payload["files"].items()}
        oid = self.put_object(files, payload["message"], (self.identity, True))
        self.refs[name] = oid
        if self.ref_fault == "lost":
            raise SimulatedLostResponseError
        if self.ref_fault == "crash":
            raise SimulatedCrash
        return {"ref": name, "object": {"sha": oid}}

    def post(self, payload: dict[str, Any]) -> Any:
        self.calls.append(payload)
        given = payload["variables"]["input"]
        if given["expectedHeadOid"] != self.head_oid:
            return _STALE_ANSWER
        changes = {
            item["path"]: base64.b64decode(item["contents"])
            for item in given["fileChanges"]["additions"]
        }
        message = given["message"]
        self._apply(changes, (message["headline"], message["body"]), (self.identity, True))
        if self.fault == "lost":
            raise SimulatedLostResponseError
        if self.fault == "crash":
            raise SimulatedCrash
        if self.response is not None:
            return self.response
        return {"data": {"createCommitOnBranch": {"commit": {"oid": self.head_oid}}}}


@final
@dataclass(frozen=True, slots=True)
class DisposableRemote:
    """A bare repository :func:`create_disposable_remote` made in ``sandbox``.

    Holding one proves nothing by itself: :func:`verified_remote` checks it on
    disk before every use.
    """

    sandbox: Path
    token: str = field(repr=False)

    @property
    def path(self) -> Path:
        return self.sandbox / REMOTE


def _not_disposable() -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, NOT_DISPOSABLE)


def _private_directory(path: Path) -> bool:
    """A real directory of this user, no one else's to read, named with no symlink or ``..``."""
    if not path.is_absolute() or os.path.realpath(path) != str(path):
        return False
    status = os.lstat(path)
    return (
        stat.S_ISDIR(status.st_mode)
        and status.st_uid == os.geteuid()
        and not status.st_mode & 0o077
    )


def _read(path: Path) -> bytes | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def _intact(remote: DisposableRemote) -> bool:
    bare = remote.path
    try:
        alone = [entry.name for entry in remote.sandbox.iterdir()] == [REMOTE]
        if not (_private_directory(remote.sandbox) and alone and _private_directory(bare)):
            return False
    except OSError:
        return False
    unexpected = ("hooks", "objects/info/alternates", "objects/info/http-alternates", "commondir")
    if any(os.path.lexists(bare / name) for name in unexpected):
        return False
    marker = _read(bare / MARKER)
    return (
        _read(bare / "config") == CONFIG
        and marker is not None
        and hmac.compare_digest(marker, remote.token.encode())
    )


def verified_remote(remote: object) -> Path:
    """The bare repository of ``remote``, once it is proved to be a disposable one, unchanged.

    Exactly a :class:`DisposableRemote`; its sandbox a private directory named
    with no symlink or ``..`` holding only the repository; the repository a
    private directory with exactly the configuration it was made with, no
    hooks, no alternates, and the marker holding this remote's token.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: simulation_remote_not_disposable``.
    """
    if type(remote) is not DisposableRemote or not _intact(remote):
        raise _not_disposable()
    return remote.path


def _write_new(path: Path, payload: bytes, flags: int) -> None:
    descriptor = os.open(path, flags | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def create_disposable_remote(sandbox: Path, clone: Path, base: str) -> DisposableRemote:
    """A fresh bare repository in the private, empty ``sandbox``; ``main`` = ``base``.

    The repository gets no template (so no hooks), a configuration fixed here
    (bare, refusing non-fast-forward updates and deletions) and a marker
    holding a random token that only the returned object knows.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: simulation_remote_not_disposable``
            for a sandbox that is not private, empty and named plainly, or a
            repository that could not be made and seeded with ``base``.
    """
    try:
        fresh = _private_directory(sandbox) and not any(sandbox.iterdir())
    except OSError:
        fresh = False
    if not fresh:
        raise _not_disposable()
    remote = DisposableRemote(sandbox, secrets.token_hex(32))
    try:
        publication_path_b.init_bare(remote.path)
        remote.path.chmod(0o700)
        _write_new(remote.path / "config", CONFIG, os.O_TRUNC)
        _write_new(remote.path / MARKER, remote.token.encode(), os.O_CREAT | os.O_EXCL)
        seeded = publication_path_b.push(clone, verified_remote(remote), base).status
    except (OSError, ForarbeiderRefusedError) as error:
        raise _not_disposable() from error
    if seeded != "published":
        raise _not_disposable()
    return remote


def verified_transport(transport: object) -> SimulatedGitHub:
    """``transport``, once it is proved to be exactly the in-memory endpoint.

    Checked by type identity before anything is read from it: a subclass,
    a real client or any other object with the same methods is refused.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: simulation_transport_not_sandboxed``.
    """
    if type(transport) is not SimulatedGitHub:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, NOT_SANDBOXED)
    return transport
