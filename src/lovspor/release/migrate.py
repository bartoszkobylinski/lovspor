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
import re
import shutil
import stat
import tempfile
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
    FallbackAdminClient,
    HttpxAdminClient,
    Runner,
    adapt,
    adapt_config,
    admin_listen,
    config_pair,
)
from lovspor.release.caddy import validate as validate_caddy
from lovspor.release.control import Checkpoint, ControlPlane, live_release
from lovspor.release.envelope import (
    FRAGMENT_NAME,
    MARKER_NAME,
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
    ReloadFailedError,
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
OFFLINE_ADMIN = "offline"
"""What the offline rollback reports it came *from*: nothing answered, so nothing was read."""
SOCKET_MODE = 0o660
RUNTIME_DIR_MODE = 0o2770
PROBE_MODE = 0o600
"""The preflight's throwaway file is the making process's alone; nothing else ever reads it."""
_EXCLUSIVE = os.O_WRONLY | os.O_CREAT | os.O_EXCL
"""Create or refuse: a taken name — a file, a directory, a symlink — is never opened."""
PRE_ENVELOPE_DROP_IN = f"[Service]\nEnvironmentFile={ENVIRONMENT_FILE}\n"
"""What provisioning wrote before the envelope: the one drop-in the preflight recognises.

Never what a rollback restores — that is the backup, which holds what
was actually on the box.
"""
_UNIX_PREFIX = "unix/"
FLAT_RELEASE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")
"""The pre-envelope release directory name, ``<YYYYMMDDTHHMMSSZ>-<sha12>``."""
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
    def previous_drop_in(self) -> Path:
        """The drop-in's backup, beside it as the Caddyfile's is beside the Caddyfile.

        systemd reads ``*.conf`` out of a drop-in directory and nothing
        else, so the suffixed name is invisible to the unit it stands in.
        """
        return self.drop_in.with_name(self.drop_in.name + PREVIOUS_SUFFIX)

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


class RollbackReport(BaseModel):
    """What the first migration's rollback found and did; the admin is TCP afterwards."""

    model_config = ConfigDict(frozen=True)

    admin_before: str
    reloaded: bool
    marker_removed: bool
    exec_reload_removed: bool
    admin: str
    restarted: str | None = None
    """The unit restarted, when the way back was the offline one; no reload happened."""


class RetireReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    removed: tuple[str, ...]


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
    """A throwaway file beside the Caddyfile, for ``caddy adapt`` alone.

    Every way this can fail on the live droplet is a named refusal that
    prints the path and the cause, and leaves the box as it found it:
    the preflight's promise is that nothing has moved when it refuses,
    and a probe that stayed behind is something that moved.
    """
    preferred = plane.caddyfile.with_name(f"{plane.caddyfile.name}.lovspor-{name}")
    probe = _probe_path(preferred, _probe_bytes(preferred, text))
    try:
        yield probe
    finally:
        _discard_probe(probe)


def _unwritable(path: Path, cause: Exception) -> MigrationRefusedError:
    """One sentence for every way the probe cannot be made: the path, then the cause."""
    return MigrationRefusedError(f"the preflight cannot write its probe file {path}: {cause}")


def _probe_bytes(preferred: Path, text: str) -> bytes:
    """The probe's bytes, encoded before anything is created.

    UTF-8 here rather than by the stream, so what Caddy reads is UTF-8
    under the C locale too; and up front, so an address a strict codec
    refuses — a socket path carrying undecodable bytes reaches this as
    surrogates — refuses the preflight instead of leaving a half-written
    file behind.
    """
    try:
        return text.encode()
    except UnicodeError as error:
        raise _unwritable(preferred, error) from error


def _probe_path(preferred: Path, payload: bytes) -> Path:
    """``payload`` in a file this call created — never one that was already there.

    The preflight runs as root on the live droplet, where a name already
    taken is someone else's: opening it would destroy its content and the
    cleanup would then delete it. So the probe is created exclusively —
    which a directory or a symlink at the name fails too — and steps
    aside to a unique name beside the taken one, which leaves the unlink
    able to take only what this call made.
    """
    try:
        descriptor = os.open(preferred, _EXCLUSIVE, PROBE_MODE)
    except FileExistsError:
        return _probe_beside(preferred, payload)
    except OSError as error:
        raise _unwritable(preferred, error) from error
    return _fill_probe(preferred, descriptor, payload)


def _probe_beside(preferred: Path, payload: bytes) -> Path:
    """A unique name in the same directory, so the same relative imports still resolve."""
    try:
        descriptor, unique = tempfile.mkstemp(prefix=f"{preferred.name}.", dir=preferred.parent)
    except OSError as error:
        raise _unwritable(preferred, error) from error
    return _fill_probe(Path(unique), descriptor, payload)


def _fill_probe(created: Path, descriptor: int, payload: bytes) -> Path:
    """Fill a file this call just created; a failed write takes it back off the box."""
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
    except OSError as error:
        _discard_probe(created)
        raise _unwritable(created, error) from error
    return created


def _discard_probe(probe: Path) -> None:
    """Take back what this call created; one already gone was taken by someone else."""
    try:
        probe.unlink(missing_ok=True)
    except OSError as error:
        raise MigrationRefusedError(
            f"the preflight cannot remove its probe file {probe}: {error}"
        ) from error


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


def _drop_in_bytes(path: Path) -> bytes | None:
    """The drop-in as it stands, or ``None`` for an absent one; unreadable is a refusal."""
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except OSError as error:
        raise MigrationRefusedError(f"{path} cannot be read: {error}") from error


def _require_drop_in(host: MigrationHost) -> None:
    """The drop-in is absent or exactly what provisioning wrote; anything else is a human's.

    (a) overwrites it whole and the rollback puts the backup back, so
    which lines of an unrecognised drop-in were wanted would be the
    module's guess. The operator makes it instead, before anything moves.
    """
    if host.previous_drop_in.exists():
        raise MigrationRefusedError(
            f"{host.previous_drop_in} already exists; it is the rollback's source and is not "
            "overwritten"
        )
    found = _drop_in_bytes(host.drop_in)
    if found is not None and found != PRE_ENVELOPE_DROP_IN.encode():
        raise MigrationRefusedError(
            f"{host.drop_in} is neither absent nor the pre-envelope drop-in; "
            "move it aside and re-run"
        )


def _require_no_fragments(plane: ControlPlane) -> None:
    """Both fragment names are free on a host that has no envelope layout yet.

    ``_stage`` writes ``.next`` and (a) renames it over the active
    fragment; migrate keeps no ``.previous`` copy of either — the
    Caddyfile backup is its whole way back — so a file already at one of
    those names would go with no record that it existed.
    """
    for path in (plane.fragment, plane.next_fragment):
        if path.exists() or path.is_symlink():
            raise MigrationRefusedError(
                f"{path} already exists; the first migration writes it and keeps no copy of what "
                "was there, so move it aside and re-run"
            )


def _require_runtime_dir(host: MigrationHost) -> None:
    """(a) creates the runtime directory and gives it ``caddy:<release group>`` mode ``2770``.

    An empty directory loses nothing to that, and is exactly what this
    migration's own rollback leaves behind — refusing it would block the
    re-run the rollback exists to allow. Anything in it belongs to
    something else, and the chown and chmod change who may reach it.
    """
    path = host.runtime_dir
    if not path.exists() and not path.is_symlink():
        return
    owners = f"{host.caddy_user}:{host.release_group} mode {RUNTIME_DIR_MODE:04o}"
    if path.is_symlink() or not path.is_dir():
        raise MigrationRefusedError(
            f"the runtime directory {path} exists and is not a directory; (a) would give what it "
            f"names {owners}"
        )
    if any(path.iterdir()):
        raise MigrationRefusedError(
            f"the runtime directory {path} is not empty; (a) would give it and everything in it "
            f"{owners}"
        )


def _require_untouched(plane: ControlPlane, host: MigrationHost) -> None:
    """The state (a) writes over: the drop-in it backs up, and the three it does not."""
    _require_drop_in(host)
    _require_no_fragments(plane)
    _require_runtime_dir(host)


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
    _require_untouched(plane, host)
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


def _backup_drop_in(host: MigrationHost) -> None:
    """The drop-in as it stands, kept beside it; an absent one is an empty backup.

    Only the first write takes it: by the second — (e)'s ``ExecReload=``
    pair, or a ``complete`` after a crash — the file on disk is already
    the migration's own, and recording *that* as the prior state is how
    the rollback came to restore an invention. Absence round-trips
    because no drop-in the preflight admits is empty: it admits absence
    and ``PRE_ENVELOPE_DROP_IN`` alone, and that text is not empty.
    """
    if host.previous_drop_in.exists():
        return
    prior = _drop_in_bytes(host.drop_in)
    atomic_write_bytes(host.previous_drop_in, b"" if prior is None else prior, mode=WORLD_READABLE)


def _load_drop_in(plane: ControlPlane, host: MigrationHost, with_exec_reload: bool) -> None:
    _backup_drop_in(host)
    atomic_write_text(host.drop_in, drop_in_text(host, with_exec_reload), mode=WORLD_READABLE)
    _daemon_reload(plane.runner)


def _runtime_dir(host: MigrationHost) -> None:
    """By hand, once: ``RuntimeDirectory=`` only acts at the next start."""
    host.runtime_dir.mkdir(parents=True, exist_ok=True)
    uid = host.ownership.uid_of(host.caddy_user)
    host.ownership.chown(host.runtime_dir, uid, host.ownership.gid_of(host.release_group))
    host.runtime_dir.chmod(RUNTIME_DIR_MODE)


def _install_files(plane: ControlPlane, host: MigrationHost) -> None:
    """(a): D = new from here; the runtime directory and the drop-in's runtime lines with it.

    The fragment is renamed into place *before* the Caddyfile that imports
    it by a plain path. The other order has a window in which the file on
    disk imports something that does not exist: ``caddy validate`` hard-
    fails there, Caddy will not start, the admin endpoint is unreachable
    on both addresses, and a re-run refuses on the backup it already
    wrote. The pre-envelope Caddyfile does not import the fragment, so
    the fragment arriving early changes nothing about what is served.

    The drop-in's backup is taken first, before the Caddyfile's: the two
    together are the way back and ``_restore_files`` needs both, so the
    window in which one exists without the other is one statement wide.
    """
    _backup_drop_in(host)
    atomic_write_bytes(host.previous_caddyfile, plane.caddyfile.read_bytes(), mode=WORLD_READABLE)
    plane.next_fragment.replace(plane.fragment)
    atomic_write_bytes(plane.caddyfile, host.caddyfile_source.read_bytes(), mode=WORLD_READABLE)
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


def _require_socket_running(host: MigrationHost, expected: ConfigPair) -> None:
    """The socket answers, and with the pair the validated configuration named."""
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


def _require_tcp_silent(host: MigrationHost) -> None:
    """The endpoint moved, so the old address must be gone, not merely superseded."""
    try:
        host.admin_client(host.tcp_admin).running_config()
    except UnobservableError:
        return
    raise MigrationFailedError(
        "reloaded", f"{host.tcp_admin} still answers; the admin endpoint did not move"
    )


def _verify(host: MigrationHost, expected: ConfigPair) -> None:
    """(d): the socket answers with the expected pair, TCP refuses, the socket file is right."""
    _require_socket_running(host, expected)
    _require_tcp_silent(host)
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


def detect_admin(host: MigrationHost) -> str:
    """The address the running instance answers on: the socket first, then TCP."""
    return FallbackAdminClient(host.socket_admin, host.tcp_admin, host.admin_client).probe()


def complete_first_migration(
    plane: ControlPlane, host: MigrationHost, answered: str
) -> MigrationReport:
    """A crash after (a): (b)-(f) while R is still on TCP; (d)-(f) once the load crossed.

    On TCP the idempotent half of (a) — the runtime directory and the
    drop-in's runtime lines — is repeated first, so a crash inside (a)
    itself is completed too.
    """
    if answered == host.socket_admin:
        return _finish(plane, host, _validated(plane), _no_checkpoint)
    _runtime_dir(host)
    _load_drop_in(plane, host, with_exec_reload=False)
    return _finish(plane, host, _cutover(plane, host, _no_checkpoint), _no_checkpoint)


def _remove_marker(plane: ControlPlane) -> bool:
    path = plane.releases / MARKER_NAME
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def _had_exec_reload(host: MigrationHost) -> bool:
    return host.drop_in.is_file() and "ExecReload=" in host.drop_in.read_text(encoding="utf-8")


def _require_backup(host: MigrationHost) -> None:
    """The rollback's only sources. ``--retire`` removes them last; after that there is no way
    back."""
    for backup in (host.previous_caddyfile, host.previous_drop_in):
        if not backup.is_file():
            raise ControlPlaneError(
                f"{backup} is missing: (a) never ran, the rollback already ran, or "
                "`migrate --retire` removed it with the pre-envelope layout; nothing to restore"
            )


def _restore_drop_in(host: MigrationHost) -> None:
    """The drop-in as the backup found it, absence included; the backup is consumed.

    An empty backup is a drop-in that was not there, and restoring it as
    a file — which the assumed ``PRE_ENVELOPE_DROP_IN`` did — leaves the
    box carrying a unit fragment it never had, while the report says the
    rollback succeeded.
    """
    prior = host.previous_drop_in.read_bytes()
    if prior:
        atomic_write_bytes(host.drop_in, prior, mode=WORLD_READABLE)
    else:
        host.drop_in.unlink(missing_ok=True)
    host.previous_drop_in.unlink()


def _restore_files(plane: ControlPlane, host: MigrationHost) -> None:
    """The reverse of (a): the previous Caddyfile back, the fragment gone, the drop-in as it
    was, loaded. Both backups are consumed, so a later migration starts clean."""
    _require_backup(host)
    host.previous_caddyfile.replace(plane.caddyfile)
    plane.fragment.unlink(missing_ok=True)
    plane.next_fragment.unlink(missing_ok=True)
    _restore_drop_in(host)
    _daemon_reload(plane.runner)


def _restore_pre_envelope(plane: ControlPlane, host: MigrationHost) -> bool:
    """M goes before the files, and neither moves without a backup to restore.

    ``_restore_files`` consumes both ``.pre-envelope`` backups, the
    rollback's only sources. Removing the marker after it left a crash window whose
    state has no way out: the marker says a release is live while the
    backup is gone, so every later rollback hits *nothing to restore* and
    ``reconcile`` — which ignores the host once a marker exists — dials
    the socket the reload just closed. The other order's window keeps the
    backup, and a marker-less host is exactly what ``reconcile`` reads on
    whichever address answers.
    """
    _require_backup(host)
    removed = _remove_marker(plane)
    _restore_files(plane, host)
    return removed


def abandon_first_migration(plane: ControlPlane, host: MigrationHost) -> RollbackReport:
    """Before (c) — D new, R old on TCP: the file restore, no reload, since R never moved."""
    had_pair = _had_exec_reload(host)
    return RollbackReport(
        admin_before=host.tcp_admin,
        reloaded=False,
        marker_removed=_restore_pre_envelope(plane, host),
        exec_reload_removed=had_pair,
        admin=host.tcp_admin,
    )


def _verify_back_on_tcp(host: MigrationHost) -> None:
    try:
        running = config_pair(host.admin_client(host.tcp_admin).running_config())
    except UnobservableError as error:
        raise ReloadFailedError(
            f"after the reload {host.tcp_admin} does not answer: {error.detail}"
        ) from error
    if running.release_id is not None:
        raise ReloadFailedError(
            f"after the reload Caddy still runs release {running.release_id} on {host.tcp_admin}"
        )
    if host.socket.exists():
        raise ReloadFailedError(f"{host.socket} still exists after the reload back to TCP")


def _require_stock_exec_reload(plane: ControlPlane, host: MigrationHost) -> None:
    done = plane.runner.run(("systemctl", "show", host.unit, "-p", "ExecReload"), {})
    if done.returncode != 0 or f"--address {host.socket_admin}" in done.stdout:
        shown = done.stdout.strip() or done.stderr.strip()
        raise ControlPlaneError(
            f"systemctl show {host.unit} -p ExecReload still names --address "
            f"{host.socket_admin} after the drop-in was restored: {shown}"
        )


def _reload_previous(plane: ControlPlane, host: MigrationHost) -> None:
    """The previous Caddyfile delivered explicitly to the socket; R moves back to TCP with it."""
    argv = ("caddy", "reload", "--config", str(host.previous_caddyfile), "--adapter", "caddyfile")
    done = plane.runner.run((*argv, "--address", host.socket_admin), {})
    if done.returncode != 0:
        failure = done.stderr.strip() or f"exit {done.returncode}"
        raise ReloadFailedError(
            f"caddy reload --address {host.socket_admin} of {host.previous_caddyfile} failed: "
            f"{failure}; the envelope is still served"
        )


def _rollback_after_cutover(plane: ControlPlane, host: MigrationHost) -> RollbackReport:
    """After (c): the previous Caddyfile delivered explicitly to the socket; then the files."""
    had_pair = _had_exec_reload(host)
    _reload_previous(plane, host)
    _verify_back_on_tcp(host)
    marker_removed = _restore_pre_envelope(plane, host)
    _require_stock_exec_reload(plane, host)
    return RollbackReport(
        admin_before=host.socket_admin,
        reloaded=True,
        marker_removed=marker_removed,
        exec_reload_removed=had_pair,
        admin=host.tcp_admin,
    )


def _systemctl_restart(plane: ControlPlane, host: MigrationHost) -> None:
    """The load that needs no reachable admin endpoint: the unit reads the file on disk."""
    done = plane.runner.run(("systemctl", "restart", host.unit), {})
    if done.returncode != 0:
        failure = done.stderr.strip() or f"exit {done.returncode}"
        raise ControlPlaneError(f"systemctl restart {host.unit} failed: {failure}")


def offline_rollback(plane: ControlPlane, host: MigrationHost) -> RollbackReport:
    """The last resort: put the files back and restart, without dialling anything.

    Every other way back reads the running configuration first, which is
    unanswerable in the one state that most needs a way back — a box
    whose Caddyfile Caddy will not load, so neither the socket nor TCP
    exists. Nothing here is observed, so nothing here can be blocked by
    an observation: the previous Caddyfile is restored and the unit is
    restarted, which loads it whole. Verify afterwards, by hand.
    """
    had_pair = _had_exec_reload(host)
    marker_removed = _restore_pre_envelope(plane, host)
    _systemctl_restart(plane, host)
    return RollbackReport(
        admin_before=OFFLINE_ADMIN,
        reloaded=False,
        marker_removed=marker_removed,
        exec_reload_removed=had_pair,
        admin=host.tcp_admin,
        restarted=host.unit,
    )


def rollback_first_migration(plane: ControlPlane, host: MigrationHost) -> RollbackReport:
    """Back to the pre-envelope host from any point of the first migration.

    Refuses once the marker names a previous release: from the second
    envelope release on, ``lovspor release rollback`` is the way back.
    """
    marker = read_marker(plane.releases)
    if marker is not None and marker.previous is not None:
        raise ControlPlaneError(
            f"the marker names a previous release ({marker.previous[:12]}); that is "
            "`lovspor release rollback`, not the first migration's"
        )
    _require_backup(host)
    if detect_admin(host) == host.tcp_admin:
        return abandon_first_migration(plane, host)
    return _rollback_after_cutover(plane, host)


def _symlink_target(path: Path) -> list[str]:
    if path.is_symlink():
        return [str(path)]
    if path.exists():
        raise ControlPlaneError(f"{path} is not a symlink; not removed")
    return []


def _tree_target(path: Path) -> list[str]:
    if path.is_dir() and not path.is_symlink():
        return [str(path)]
    if path.exists() or path.is_symlink():
        raise ControlPlaneError(f"{path} is not a directory; not removed")
    return []


def _flat_release_targets(releases: Path) -> list[str]:
    """Only the old flat names; never an id-named directory, a build or the marker."""
    return [
        str(entry)
        for entry in sorted(releases.iterdir())
        if entry.is_dir() and not entry.is_symlink() and FLAT_RELEASE.match(entry.name)
    ]


def _require_releases_outside(plane: ControlPlane, host: MigrationHost) -> None:
    """The envelopes must not live inside what retiring deletes.

    ``LOVSPOR_RELEASES_ROOT=/var/www/lovspor/releases`` — a plausible
    reading of "keep the releases under the site root" — would take every
    envelope, the marker that proves the migration finished, and the
    release the host is serving out with the pre-envelope tree. The
    symlink is the same hazard: unlinking it orphans everything beneath.
    """
    for path in (host.site_root, host.current_symlink):
        if plane.releases.is_relative_to(path):
            raise ControlPlaneError(
                f"the releases root {plane.releases} is inside {path}, which --retire removes; "
                "nothing retired"
            )


def retire_targets(plane: ControlPlane, host: MigrationHost) -> tuple[str, ...]:
    """What ``retire_pre_envelope`` removes, in the order it removes it; nothing moves here.

    The two backups come last, after every tree they root at is gone, and
    the previous Caddyfile is the very last of all. Left behind, it arms
    a rollback that puts a configuration serving deleted directories back
    into service: ``root *`` tolerates a missing directory and
    ``redirects*.caddy`` tolerates zero matches, so the reload returns 0
    and the site 404s.
    """
    _require_releases_outside(plane, host)
    targets = _symlink_target(host.current_symlink)
    targets += _tree_target(host.site_root)
    targets += _flat_release_targets(plane.releases)
    if host.previous_drop_in.is_file():
        targets.append(str(host.previous_drop_in))
    if host.previous_caddyfile.is_file():
        targets.append(str(host.previous_caddyfile))
    return tuple(targets)


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)


def _require_retirable(plane: ControlPlane) -> None:
    if read_marker(plane.releases) is None:
        raise ControlPlaneError("no marker: the first migration has not finished; nothing retired")
    live_release(plane)


def retire_preview(plane: ControlPlane, host: MigrationHost) -> RetireReport:
    """What ``retire_pre_envelope`` would remove, behind the same preconditions.

    The operator's confirmation is shown the paths, and the paths come
    from the function that removes them, so the two cannot drift.
    """
    _require_releases_outside(plane, host)
    _require_retirable(plane)
    return RetireReport(removed=retire_targets(plane, host))


def retire_pre_envelope(plane: ControlPlane, host: MigrationHost) -> RetireReport:
    """(g): the symlink, the old site root, the flat releases, the way back — once reconciled.

    This deletes the first migration's only way back, which is why it is
    its own command and refuses on anything but a marked, reconciled host.
    """
    _require_releases_outside(plane, host)
    _require_retirable(plane)
    removed = retire_targets(plane, host)
    for path in removed:
        _remove(Path(path))
    return RetireReport(removed=removed)
