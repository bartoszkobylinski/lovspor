"""The admin socket's four facts, asked in one place (ADR-0014 Decision 6).

After a Caddy restart the socket is a *new* file: ``RuntimeDirectory=``
clears its directory and the process creates it again, so a mode or a
group set once by hand is gone and only the address's ``|0660``
creation-mode suffix and the setgid directory put them back. Four facts
say the permission model still holds:

1. the socket exists with mode ``0660``;
2. it carries the release group;
3. the release/reconcile identity can ``GET /config/`` over it;
4. the unprivileged service user cannot — asked by *attempting* the same
   call as that user, never by deriving it from the bits — and nothing
   listens on the pre-envelope TCP address.

The rehearsal asserts them across two restarts before the production
cutover (Validation (g)(v)), and they are the drift timer's first action
(Decision 4), so a permission regression surfaces within the hour rather
than at the next release.

The first fact that fails is the refusal, and it names the address, the
mode or the identity it asked about; nothing here writes anything.
"""

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.release.caddy import (
    DEFAULT_ADMIN,
    AdminClient,
    HttpxAdminClient,
    Runner,
    SubprocessRunner,
)
from lovspor.release.errors import AdminSocketError, ControlPlaneError, UnobservableError
from lovspor.release.migrate import (
    DEFAULT_RELEASE_GROUP,
    DEFAULT_TCP_ADMIN,
    SOCKET_MODE,
    Ownership,
    SystemOwnership,
    socket_path,
)

DEFAULT_UNPRIVILEGED_USER = "lovspor"
"""The network-facing MCP service's identity: the one that must *not* reach the admin API."""
CONFIG_URL = "http://localhost/config/"
"""The path ``curl --unix-socket`` asks for; the host part is ignored, as the caddy CLI's is."""


@dataclass(frozen=True)
class AdminSocket:
    """Where the admin socket is, who may open it, and the boundaries that ask."""

    socket_admin: str = DEFAULT_ADMIN
    tcp_admin: str = DEFAULT_TCP_ADMIN
    release_group: str = DEFAULT_RELEASE_GROUP
    unprivileged_user: str = DEFAULT_UNPRIVILEGED_USER
    runner: Runner = field(default_factory=SubprocessRunner)
    admin_client: Callable[[str], AdminClient] = HttpxAdminClient
    ownership: Ownership = field(default_factory=SystemOwnership)

    def __post_init__(self) -> None:
        socket_path(self.socket_admin)

    @property
    def socket(self) -> Path:
        return socket_path(self.socket_admin)


class SocketFacts(BaseModel):
    """The four facts as they were found; the operator reads one line and compares."""

    model_config = ConfigDict(frozen=True)

    socket: Path
    mode: int
    gid: int
    release_group: str
    unprivileged_user: str
    tcp_admin: str

    def describe(self) -> str:
        return (
            f"{self.socket} mode {self.mode:04o} group {self.release_group} (gid {self.gid}); "
            f"the release identity reads /config/, {self.unprivileged_user} cannot; "
            f"nothing answers on {self.tcp_admin}"
        )


def _refuse(detail: str) -> AdminSocketError:
    """One sentence for every fact that does not hold, under one named precondition."""
    return AdminSocketError(f"admin socket precondition unmet: {detail}")


def _stat(access: AdminSocket) -> os.stat_result:
    try:
        return access.socket.stat()
    except OSError as error:
        raise _refuse(f"the admin socket {access.socket} cannot be read: {error}") from error


def _gid(access: AdminSocket) -> int:
    try:
        return access.ownership.gid_of(access.release_group)
    except KeyError as error:
        raise _refuse(
            f"group {access.release_group} does not exist, so nothing can carry it"
        ) from error


def _mode_and_group(access: AdminSocket, found: os.stat_result) -> tuple[int, int]:
    """Facts 1 and 2: the creation-mode suffix's mode, and the setgid directory's group."""
    mode = stat.S_IMODE(found.st_mode)
    if mode != SOCKET_MODE:
        raise _refuse(
            f"{access.socket} has mode {mode:04o}, not {SOCKET_MODE:04o}; the admin address "
            f"needs the |{SOCKET_MODE:04o} creation-mode suffix"
        )
    gid = _gid(access)
    if found.st_gid != gid:
        raise _refuse(
            f"{access.socket} has gid {found.st_gid}, not {access.release_group}'s {gid}; "
            "the runtime directory needs the setgid bit and the group"
        )
    return mode, gid


def _require_release_identity(access: AdminSocket) -> None:
    """Fact 3: the identity that publishes and reconciles can read the running configuration."""
    try:
        access.admin_client(access.socket_admin).running_config()
    except UnobservableError as error:
        raise _refuse(
            f"the release identity cannot GET /config/ over {access.socket_admin}: {error.detail}"
        ) from error


def unprivileged_argv(access: AdminSocket) -> tuple[str, ...]:
    """The same call, made as the unprivileged user; fixed argv, never a shell."""
    return (
        "sudo",
        "-u",
        access.unprivileged_user,
        "curl",
        "--silent",
        "--show-error",
        "--fail",
        "--unix-socket",
        str(access.socket),
        CONFIG_URL,
    )


def _require_unprivileged_refused(access: AdminSocket) -> None:
    """Fact 4: the same open, attempted as the service user, is refused.

    Attempted rather than derived. The bits and the group memberships
    would have to be read the way the kernel reads them — supplementary
    groups, the directory's own modes, whatever a later drop-in adds —
    and a derivation that is subtly wrong is a permission check that
    passes while the door is open.
    """
    try:
        done = access.runner.run(unprivileged_argv(access), {})
    except ControlPlaneError as error:
        raise _refuse(f"the call as {access.unprivileged_user} cannot run: {error}") from error
    if done.returncode == 0:
        raise _refuse(
            f"{access.unprivileged_user} can GET /config/ over {access.socket}; that user must "
            f"not be in {access.release_group}, and the socket must not be world-reachable"
        )


def _require_tcp_silent(access: AdminSocket) -> None:
    """The fourth fact's other half: the endpoint moved, so the old address must be gone."""
    try:
        access.admin_client(access.tcp_admin).running_config()
    except UnobservableError:
        return
    raise _refuse(
        f"{access.tcp_admin} answers; on that address every process on the box can rewrite "
        "the running configuration"
    )


def check_admin_socket(access: AdminSocket) -> SocketFacts:
    """The four facts in the ADR's order; the first that does not hold is the refusal."""
    mode, gid = _mode_and_group(access, _stat(access))
    _require_release_identity(access)
    _require_unprivileged_refused(access)
    _require_tcp_silent(access)
    return SocketFacts(
        socket=access.socket,
        mode=mode,
        gid=gid,
        release_group=access.release_group,
        unprivileged_user=access.unprivileged_user,
        tcp_admin=access.tcp_admin,
    )
