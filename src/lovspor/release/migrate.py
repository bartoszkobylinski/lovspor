"""The first envelope release: the cutover with explicit admin addresses (ADR-0014 Migration).

Every later release is the transaction of ``control``; the first one is
that transaction with one extra step, the new Caddyfile, and one
difference of address: the running admin endpoint is still Caddy's
default TCP ``localhost:2019``, and the new configuration binds it to the
permissioned Unix socket. So the load that crosses from the one to the
other is never ``systemctl reload caddy`` — the stock line derives the
address from the file it supplies and would reach nothing — but an
explicit ``caddy reload --address localhost:2019``, and its reverse is
the previous Caddyfile delivered explicitly to the socket.

The order is the ADR's, with D moving at (a), R at (c) and M at (f):

* preflight — the marker and the socket absent, Caddy answering on TCP
  with the pre-envelope configuration the old Caddyfile adapts to, the
  socket's group present, this Caddy accepting the ``|0660`` creation-mode
  suffix, the new Caddyfile binding admin to the socket and importing the
  fragment, the envelope complete and naming itself; nothing has moved
  when this refuses;
* stage the release's fragment as ``.next``;
* (a) keep the previous Caddyfile beside the new one, install the new one
  and rename ``.next`` into place; create the runtime directory
  ``caddy:<release group>`` mode ``2770`` by hand — ``RuntimeDirectory=``
  takes effect at Caddy's next start and this cuts over by reload — and
  load the drop-in's runtime-directory lines with a ``daemon-reload``.
  The ``ExecReload=`` pair is deliberately not installed yet: a plain
  ``systemctl reload caddy`` through a line naming the socket would reach
  nothing while TCP is running;
* (b) ``caddy validate`` the composed file as it stands; (c) ``caddy
  reload --address <TCP>``: R = new, and the admin endpoint moves;
* (d) verify — the socket answers with the expected pair, TCP refuses,
  the socket file is ``0660`` in the release group; (e) only now the
  ``ExecReload=`` pair and a ``daemon-reload``, asserted through
  ``systemctl show``; (f) the marker, ``previous`` empty.

Retiring the pre-envelope layout — the symlink, ``/var/www/lovspor`` and
the old flat release directories — is a separate, explicit step run only
once the host is reconciled: it deletes the first migration's only way
back.
"""

import grp
import os
import pwd
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from lovspor.atomic_io import atomic_write_bytes, atomic_write_text
from lovspor.release.caddy import (
    DEFAULT_ADMIN,
    DEFAULT_CADDYFILE,
    FRAGMENT_ENV,
    AdminClient,
    ConfigPair,
    HttpxAdminClient,
    Runner,
    adapt,
    adapt_config,
    admin_listen,
    config_pair,
)
from lovspor.release.caddy import validate as validate_caddy
from lovspor.release.control import Checkpoint, ControlPlane
from lovspor.release.envelope import (
    FRAGMENT_NAME,
    WORLD_READABLE,
    Marker,
    fragment_release_id,
    is_complete,
    read_fragment,
    read_marker,
    release_dir,
    write_marker,
)
from lovspor.release.errors import (
    CommitRefusedError,
    ControlPlaneError,
    MigrationFailedError,
    MigrationRefusedError,
    UnobservableError,
)

MIGRATION_STEPS = (
    "staged",
    "installed",
    "validated",
    "reloaded",
    "verified",
    "exec_reload",
    "marked",
)
"""The checkpoint names of the first migration, in order."""
DEFAULT_TCP_ADMIN = "localhost:2019"
"""Caddy's default admin address: what the pre-envelope Caddyfile, having no global block, gets."""
DEFAULT_CADDYFILE_SOURCE = Path("/opt/lovspor/app/deploy/digitalocean/Caddyfile")
DEFAULT_DROP_IN = Path("/etc/systemd/system/caddy.service.d/lovspor.conf")
DEFAULT_RUNTIME_DIR = Path("/run/caddy")
DEFAULT_RELEASE_GROUP = "lovspor-release"
DEFAULT_SITE_ROOT = Path("/var/www/lovspor")
DEFAULT_CURRENT_SYMLINK = Path("/var/www/lovspor-current")
ENVIRONMENT_FILE = Path("/etc/default/caddy-lovspor")
CADDY_USER = "caddy"
PREVIOUS_SUFFIX = ".pre-envelope"
"""The previous Caddyfile is kept beside the new one: the rollback's source."""
SOCKET_MODE = 0o660
RUNTIME_DIR_MODE = 0o2770
PRE_ENVELOPE_DROP_IN = f"[Service]\nEnvironmentFile={ENVIRONMENT_FILE}\n"
"""What provisioning wrote before the envelope: restored by abandon and rollback."""
_UNIX_PREFIX = "unix/"
_STAGED_HINT = (
    "D is the new configuration, R the old; resolve with `lovspor release reconcile --complete` "
    "(run the cutover) or `--abandon` (restore the previous Caddyfile)"
)


class Ownership(Protocol):
    """The identities the runtime directory and the socket are checked against."""

    def uid_of(self, user: str) -> int:
        """The uid, or :class:`KeyError` as ``pwd`` raises."""

    def gid_of(self, group: str) -> int:
        """The gid, or :class:`KeyError` as ``grp`` raises."""

    def chown(self, path: Path, uid: int, gid: int) -> None: ...


class SystemOwnership:
    def uid_of(self, user: str) -> int:
        return pwd.getpwnam(user).pw_uid

    def gid_of(self, group: str) -> int:
        return grp.getgrnam(group).gr_gid

    def chown(self, path: Path, uid: int, gid: int) -> None:
        os.chown(path, uid, gid)


def socket_path(address: str) -> Path:
    """The file behind Caddy's ``unix/<path>`` spelling."""
    if not address.startswith(_UNIX_PREFIX):
        raise ControlPlaneError(f"not a Unix-socket admin address: {address}")
    return Path(address[len(_UNIX_PREFIX) :])


@dataclass(frozen=True)
class MigrationHost:
    """Where the first migration's files, addresses and identities are on this host."""

    caddyfile: Path = DEFAULT_CADDYFILE
    caddyfile_source: Path = DEFAULT_CADDYFILE_SOURCE
    drop_in: Path = DEFAULT_DROP_IN
    runtime_dir: Path = DEFAULT_RUNTIME_DIR
    tcp_admin: str = DEFAULT_TCP_ADMIN
    socket_admin: str = DEFAULT_ADMIN
    release_group: str = DEFAULT_RELEASE_GROUP
    caddy_user: str = CADDY_USER
    unit: str = "caddy"
    site_root: Path = DEFAULT_SITE_ROOT
    current_symlink: Path = DEFAULT_CURRENT_SYMLINK
    admin_client: Callable[[str], AdminClient] = HttpxAdminClient
    ownership: Ownership = field(default_factory=SystemOwnership)

    def __post_init__(self) -> None:
        socket_path(self.socket_admin)

    @property
    def previous_caddyfile(self) -> Path:
        return self.caddyfile.with_name(self.caddyfile.name + PREVIOUS_SUFFIX)

    @property
    def socket(self) -> Path:
        return socket_path(self.socket_admin)


class Preflight(BaseModel):
    """What the preflight read: enough for the operator to compare with the box."""

    model_config = ConfigDict(frozen=True)

    content_id: str | None
    running: str
    tcp_admin: str
    socket_admin: str
    release_group: str
    gid: int

    def describe(self) -> str:
        release = f"release {self.content_id}" if self.content_id else "no release named"
        return (
            f"{release}; R={self.running} on {self.tcp_admin}; socket {self.socket_admin} absent; "
            f"group {self.release_group} (gid {self.gid}); Caddy accepts |{SOCKET_MODE:04o}"
        )


class MigrationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    active: str
    admin: str
    running: str
    previous_caddyfile: Path


@dataclass(frozen=True)
class Cutover:
    """What (c) delivered: the envelope, and the pair R must show over the socket."""

    content_id: str
    expected: ConfigPair


def _no_checkpoint(step: str) -> None:
    del step


def drop_in_text(host: MigrationHost, with_exec_reload: bool) -> str:
    """The ``caddy.service`` drop-in: provisioning and the migration write this one text.

    ``RuntimeDirectory=`` recreates the socket's directory at every start
    and ``ExecStartPre=+chgrp`` gives it the release group, so a recreated
    socket inherits the group through the setgid bit; the ``ExecReload=``
    pair resets the stock line and names the socket explicitly.
    """
    lines = [
        "# Written by lovspor (ADR-0014 Decision 6): provisioning and the first migration.",
        "[Service]",
        f"EnvironmentFile={ENVIRONMENT_FILE}",
        f"RuntimeDirectory={host.runtime_dir.name}",
        f"RuntimeDirectoryMode={RUNTIME_DIR_MODE:04o}",
        f"ExecStartPre=+/usr/bin/chgrp {host.release_group} {host.runtime_dir}",
    ]
    if with_exec_reload:
        lines += [
            "ExecReload=",
            f"ExecReload=/usr/bin/caddy reload --config {host.caddyfile} --force "
            f"--address {host.socket_admin}",
        ]
    return "\n".join(lines) + "\n"


@contextmanager
def _probe_file(plane: ControlPlane, name: str, text: str) -> Iterator[Path]:
    """A throwaway file beside the Caddyfile, for ``caddy adapt`` alone."""
    probe = plane.caddyfile.with_name(f"{plane.caddyfile.name}.lovspor-{name}")
    probe.write_text(text, encoding="utf-8")
    try:
        yield probe
    finally:
        probe.unlink(missing_ok=True)


def _require_no_marker(plane: ControlPlane) -> None:
    marker = read_marker(plane.releases)
    if marker is not None:
        raise MigrationRefusedError(
            f"a marker exists (active {marker.active}); the first migration has already happened"
        )


def _require_files(plane: ControlPlane, host: MigrationHost) -> None:
    if plane.caddyfile != host.caddyfile:
        raise MigrationRefusedError(
            f"the plane's Caddyfile {plane.caddyfile} is not the host's {host.caddyfile}"
        )
    if host.socket.exists():
        raise MigrationRefusedError(f"the admin socket {host.socket} already exists")
    if host.previous_caddyfile.exists():
        raise MigrationRefusedError(
            f"{host.previous_caddyfile} already exists; it is the rollback's source and is not "
            "overwritten"
        )
    if not host.caddyfile_source.is_file():
        raise MigrationRefusedError(f"the new Caddyfile {host.caddyfile_source} does not exist")


def _require_old_config_on_tcp(plane: ControlPlane, host: MigrationHost) -> ConfigPair:
    """R on TCP names no release and is what the Caddyfile on disk adapts to."""
    try:
        running = config_pair(host.admin_client(host.tcp_admin).running_config())
    except UnobservableError as error:
        detail = f"{error.detail}; the first migration needs Caddy answering on {host.tcp_admin}"
        raise UnobservableError(error.reason, detail) from error
    if running.release_id is not None:
        raise MigrationRefusedError(
            f"Caddy already runs release {running.release_id} on {host.tcp_admin}"
        )
    disk = adapt(plane.runner, plane.caddyfile, plane.fragment)
    if disk != running:
        raise MigrationRefusedError(
            f"the Caddyfile on disk adapts to {disk.describe()}, Caddy runs {running.describe()} "
            f"on {host.tcp_admin}; the served configuration is not the file's"
        )
    return running


def _require_group(host: MigrationHost) -> int:
    try:
        return host.ownership.gid_of(host.release_group)
    except KeyError as error:
        raise MigrationRefusedError(
            f"group {host.release_group} does not exist; groupadd --system {host.release_group}"
        ) from error


def _require_mode_suffix(plane: ControlPlane, host: MigrationHost) -> None:
    """This Caddy must know the creation-mode suffix, or the new file never loads."""
    text = f"{{\n\tadmin {host.socket_admin}|{SOCKET_MODE:04o}\n}}\n"
    with _probe_file(plane, "probe", text) as probe:
        try:
            adapt_config(plane.runner, probe, plane.fragment)
        except ControlPlaneError as error:
            raise MigrationRefusedError(
                f"this Caddy does not accept the creation-mode suffix on the admin address "
                f"({host.socket_admin}|{SOCKET_MODE:04o}): {error}"
            ) from error


def _source_config(plane: ControlPlane, host: MigrationHost, content_id: str | None) -> object:
    """The new Caddyfile adapted with the release's fragment — an empty one with no release."""
    if content_id is not None:
        fragment = release_dir(plane.releases, content_id) / FRAGMENT_NAME
        return adapt_config(plane.runner, host.caddyfile_source, fragment)
    with _probe_file(plane, "fragment", "") as fragment:
        return adapt_config(plane.runner, host.caddyfile_source, fragment)


def _require_source(plane: ControlPlane, host: MigrationHost, content_id: str | None) -> None:
    """The new Caddyfile binds admin to the socket with the suffix and imports the fragment."""
    try:
        config = _source_config(plane, host, content_id)
    except ControlPlaneError as error:
        raise MigrationRefusedError(f"{host.caddyfile_source} does not adapt: {error}") from error
    expected = f"{host.socket_admin}|{SOCKET_MODE:04o}"
    listen = admin_listen(config)
    if listen != expected:
        raise MigrationRefusedError(
            f"{host.caddyfile_source} binds admin to {listen or 'the default address'}, "
            f"not {expected}"
        )
    named = config_pair(config).release_id
    if named != content_id:
        raise MigrationRefusedError(
            f"{host.caddyfile_source} composed with the fragment names "
            f"{named or 'no release'}, not {content_id or 'no release'}; "
            "does it import the fragment?"
        )


def _require_release(plane: ControlPlane, content_id: str) -> None:
    release = release_dir(plane.releases, content_id)
    if not is_complete(release):
        raise MigrationRefusedError(f"{content_id} is not a complete envelope; not staged")
    if fragment_release_id(read_fragment(release)) != content_id:
        raise MigrationRefusedError(f"{content_id}/{FRAGMENT_NAME} does not name its release")


def preflight(plane: ControlPlane, host: MigrationHost, content_id: str | None = None) -> Preflight:
    """Every precondition of the first migration, in the order a refusal is cheapest."""
    _require_no_marker(plane)
    _require_files(plane, host)
    running = _require_old_config_on_tcp(plane, host)
    gid = _require_group(host)
    _require_mode_suffix(plane, host)
    if content_id is not None:
        _require_release(plane, content_id)
    _require_source(plane, host, content_id)
    return Preflight(
        content_id=content_id,
        running=running.describe(),
        tcp_admin=host.tcp_admin,
        socket_admin=host.socket_admin,
        release_group=host.release_group,
        gid=gid,
    )


def _stage(plane: ControlPlane, content_id: str) -> None:
    text = read_fragment(release_dir(plane.releases, content_id))
    atomic_write_text(plane.next_fragment, text, mode=WORLD_READABLE)


def _daemon_reload(runner: Runner) -> None:
    done = runner.run(("systemctl", "daemon-reload"), {})
    if done.returncode != 0:
        raise ControlPlaneError(f"systemctl daemon-reload failed: {done.stderr.strip()}")


def _load_drop_in(plane: ControlPlane, host: MigrationHost, with_exec_reload: bool) -> None:
    atomic_write_text(host.drop_in, drop_in_text(host, with_exec_reload), mode=WORLD_READABLE)
    _daemon_reload(plane.runner)


def _runtime_dir(host: MigrationHost) -> None:
    """By hand, once: ``RuntimeDirectory=`` only acts at the next start."""
    host.runtime_dir.mkdir(parents=True, exist_ok=True)
    uid = host.ownership.uid_of(host.caddy_user)
    host.ownership.chown(host.runtime_dir, uid, host.ownership.gid_of(host.release_group))
    host.runtime_dir.chmod(RUNTIME_DIR_MODE)


def _install_files(plane: ControlPlane, host: MigrationHost) -> None:
    """(a): D = new from here; the runtime directory and the drop-in's runtime lines with it."""
    atomic_write_bytes(host.previous_caddyfile, plane.caddyfile.read_bytes(), mode=WORLD_READABLE)
    atomic_write_bytes(plane.caddyfile, host.caddyfile_source.read_bytes(), mode=WORLD_READABLE)
    plane.next_fragment.replace(plane.fragment)
    _runtime_dir(host)
    _load_drop_in(plane, host, with_exec_reload=False)


def _validated(plane: ControlPlane) -> Cutover:
    """(b): the composed file as it stands on disk — the exact file (c) supplies."""
    try:
        validate_caddy(plane.runner, plane.caddyfile, plane.fragment)
    except CommitRefusedError as error:
        raise MigrationFailedError("installed", f"{error}; {_STAGED_HINT}") from error
    expected = adapt(plane.runner, plane.caddyfile, plane.fragment)
    if expected.release_id is None:
        raise MigrationFailedError(
            "installed", f"the configuration on disk names no release; {_STAGED_HINT}"
        )
    return Cutover(expected.release_id, expected)


def _cutover(plane: ControlPlane, host: MigrationHost, checkpoint: Checkpoint) -> Cutover:
    """(b) validate, then (c): the new file delivered explicitly to the running TCP endpoint."""
    cutover = _validated(plane)
    checkpoint("validated")
    argv = ("caddy", "reload", "--config", str(plane.caddyfile), "--adapter", "caddyfile")
    done = plane.runner.run(
        (*argv, "--address", host.tcp_admin), {FRAGMENT_ENV: str(plane.fragment)}
    )
    if done.returncode != 0:
        failure = done.stderr.strip() or f"exit {done.returncode}"
        raise MigrationFailedError(
            "validated",
            f"caddy reload --address {host.tcp_admin} failed: {failure}; {_STAGED_HINT}",
        )
    checkpoint("reloaded")
    return cutover


def _check_socket_file(host: MigrationHost) -> None:
    """The first two of the contract's four facts: mode ``0660``, the release group."""
    found = host.socket.stat()
    mode = stat.S_IMODE(found.st_mode)
    if mode != SOCKET_MODE:
        raise MigrationFailedError(
            "reloaded", f"{host.socket} has mode {mode:04o}, not {SOCKET_MODE:04o}"
        )
    gid = host.ownership.gid_of(host.release_group)
    if found.st_gid != gid:
        raise MigrationFailedError(
            "reloaded", f"{host.socket} has gid {found.st_gid}, not {host.release_group}'s {gid}"
        )


def _verify(host: MigrationHost, expected: ConfigPair) -> None:
    """(d): the socket answers with the expected pair, TCP refuses, the socket file is right."""
    try:
        running = config_pair(host.admin_client(host.socket_admin).running_config())
    except UnobservableError as error:
        raise MigrationFailedError(
            "reloaded",
            f"the socket does not answer: {error.detail}; `systemctl restart {host.unit}` loads "
            "the new configuration whole and recreates the socket",
        ) from error
    if running != expected:
        raise MigrationFailedError(
            "reloaded",
            f"over the socket Caddy runs {running.describe()}, not {expected.describe()}",
        )
    try:
        host.admin_client(host.tcp_admin).running_config()
    except UnobservableError:
        pass
    else:
        raise MigrationFailedError(
            "reloaded", f"{host.tcp_admin} still answers; the admin endpoint did not move"
        )
    _check_socket_file(host)


def _install_exec_reload(plane: ControlPlane, host: MigrationHost) -> None:
    """(e): the pair, loaded, and seen through ``systemctl show``."""
    _load_drop_in(plane, host, with_exec_reload=True)
    done = plane.runner.run(("systemctl", "show", host.unit, "-p", "ExecReload"), {})
    if done.returncode != 0 or f"--address {host.socket_admin}" not in done.stdout:
        shown = done.stdout.strip() or done.stderr.strip()
        raise MigrationFailedError(
            "verified",
            f"systemctl show {host.unit} -p ExecReload does not name --address "
            f"{host.socket_admin}: {shown}",
        )


def _mark(plane: ControlPlane, content_id: str) -> None:
    """(f): M names the envelope R and D agree on; ``previous`` empty."""
    write_marker(plane.releases, Marker(active=content_id, previous=None))


def _finish(
    plane: ControlPlane, host: MigrationHost, cutover: Cutover, checkpoint: Checkpoint
) -> MigrationReport:
    """(d), (e), (f) — the steps shared with completing a crash after (c)."""
    _verify(host, cutover.expected)
    checkpoint("verified")
    _install_exec_reload(plane, host)
    checkpoint("exec_reload")
    _mark(plane, cutover.content_id)
    checkpoint("marked")
    return MigrationReport(
        active=cutover.content_id,
        admin=host.socket_admin,
        running=cutover.expected.describe(),
        previous_caddyfile=host.previous_caddyfile,
    )


def first_migration(
    plane: ControlPlane,
    host: MigrationHost,
    content_id: str,
    checkpoint: Checkpoint = _no_checkpoint,
) -> MigrationReport:
    """The whole procedure in the ADR's order; a failure names the state and the way out."""
    preflight(plane, host, content_id)
    _stage(plane, content_id)
    checkpoint("staged")
    _install_files(plane, host)
    checkpoint("installed")
    return _finish(plane, host, _cutover(plane, host, checkpoint), checkpoint)
