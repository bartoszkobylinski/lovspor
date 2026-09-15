"""``reconcile`` and ``prune``: the crash table's resolutions (ADR-0014 Decision 6).

A crash after the fragment rename and before the reload leaves D = new,
R = M = old: ``reconcile`` offers exactly two resolutions — *complete*
(validate D, reload so R = new, then M = new) or *abandon* (restore D
from M's release fragment, kept immutable in ``<M>/release.caddy``,
reload so R = D = M = old). A crash after the reload and before the
marker leaves D = R = new, M = old: the served truth is already new, so
``reconcile`` completes by writing M — the only safe resolution, applied
without a choice. Any other disagreement — R equal to neither file, a
hand reload of an edited configuration — is foreign: ``reconcile``
prints the triple and acts only on an explicit flag; either resolution
ends reconciled, and both go through validate + reload. Admin
unreachable is not a disagreement and is refused before any of this.

While no marker exists — the first migration's window — ``reconcile``
given a :class:`MigrationHost` reaches the admin endpoint on whichever
address answers, the socket first and then TCP, and records which one
did. R old on TCP with D naming the envelope is the crash after (a):
*complete* runs the cutover (b)-(f), *abandon* restores the previous
Caddyfile without a reload, since R never moved. R = D = new over the
socket is the crash after (c): *complete* finishes (d)-(f), the
``ExecReload=`` pair included, without a choice. Anything else on TCP
is foreign and is not resolved automatically. A host on the socket
running a configuration that binds admin there, and not mid-cutover — a
box provisioned with the envelope — is resolved by the rows above.

The socket answering a configuration that binds admin elsewhere is the
cutover's load refused after Caddy moved its admin endpoint: the
droplet's Caddy v2.11.4 started the socket endpoint, failed to start the
site, stopped TCP and kept the previous configuration running (#302).
Staged there, *abandon* is the rollback after (c) — the previous
Caddyfile delivered to the socket, which moves the endpoint back to TCP,
then the file restore — and *complete* is refused, the file on disk
being the one Caddy just refused. That pairing in any other row is
refused, naming the offline rollback — but for the row below.

A first-migration way back stopped before its file restore finished —
the rollback puts the backup's bytes at the Caddyfile's own path before
it reloads (#316) — leaves no marker, R = D naming no release, and a
Caddyfile backup holding the Caddyfile's own bytes; so does (a) stopped
before it installs the new Caddyfile. On TCP, or on a
socket stranded as above, that row is resolved by *abandon* alone: the
file restore, after the load back to TCP while the endpoint is still on
the socket. *report* and *complete* are refused naming it, and anything
at the backup's name that is not a regular file with those bytes is
refused for every flag, naming what differs.

``prune`` runs only when reconciled and never deletes the release named
by R, D or M, nor the marker's ``previous``; it removes every other
id-named directory — a staged release that never went live included,
which is thereby asserted never to become the rollback target — and
every ``.build-*`` directory but the running build's own.
"""

import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.atomic_io import atomic_write_text
from lovspor.release.caddy import adapt
from lovspor.release.caddy import validate as validate_caddy
from lovspor.release.control import (
    ControlPlane,
    Situation,
    Triple,
    read_triple,
    reload_expecting,
    revert_source,
    situation,
)
from lovspor.release.envelope import (
    WORLD_READABLE,
    Marker,
    is_build_dir,
    is_release_id,
    read_marker,
    write_marker,
)
from lovspor.release.errors import CommitRefusedError, ReloadFailedError, UnreconciledError
from lovspor.release.migrate import (
    MigrationHost,
    abandon_first_migration,
    complete_first_migration,
    detect_admin,
    rollback_first_migration,
    stranded_on_socket,
)

ReconcileAction = Literal["report", "complete", "abandon"]


class ReconcileReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    situation: Situation
    live: str | None
    action: str
    triple: str
    admin: str | None = None
    """Which address answered, when the marker's absence made that a question."""


@dataclass(frozen=True)
class Window:
    """The pre-marker window as read: the triple, the address it was read on, and whether
    that address is the socket running a configuration that binds admin elsewhere."""

    triple: Triple
    answered: str
    stranded: bool


class PruneReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    removed: tuple[str, ...]
    retained: tuple[str, ...]


def _complete(plane: ControlPlane, triple: Triple) -> ReconcileReport:
    """D becomes the truth: validate, reload, M = D."""
    if triple.disk.release_id is None:
        raise CommitRefusedError("the configuration on disk names no release; cannot complete")
    validate_caddy(plane.runner, plane.caddyfile, plane.fragment)
    failure = reload_expecting(plane, triple.disk)
    if failure is not None:
        raise ReloadFailedError(f"complete failed: {failure}")
    write_marker(plane.releases, Marker(active=triple.disk.release_id, previous=triple.marked))
    return _report(triple, Situation.reconciled, triple.disk.release_id, "completed")


def _abandon(plane: ControlPlane, triple: Triple) -> ReconcileReport:
    """M becomes the truth: D = M's fragment, validate, reload; M unchanged.

    ``.next`` is validated before the rename; the pair R must equal is adapted
    after it, from the path the reload reads (#317). Adapting writes nothing, so
    a kill after the rename leaves D = M's fragment and R as it was — a re-run
    abandons again.
    """
    atomic_write_text(plane.next_fragment, revert_source(plane, triple.marker), mode=WORLD_READABLE)
    validate_caddy(plane.runner, plane.caddyfile, plane.next_fragment)
    plane.next_fragment.replace(plane.fragment)
    failure = reload_expecting(plane, adapt(plane.runner, plane.caddyfile, plane.fragment))
    if failure is not None:
        raise ReloadFailedError(f"abandon failed: {failure}")
    return _report(triple, Situation.reconciled, triple.marked, "abandoned")


def _report(triple: Triple, found: Situation, live: str | None, action: str) -> ReconcileReport:
    return ReconcileReport(situation=found, live=live, action=action, triple=triple.describe())


def _resolve(plane: ControlPlane, triple: Triple, action: ReconcileAction) -> ReconcileReport:
    """The crash table of Decision 6, on a host whose admin address is settled."""
    found = situation(triple)
    live = triple.running.release_id
    if found == Situation.reconciled:
        return _report(triple, found, live, "none")
    # R is untrusted input and the marker takes only a release id; config_pair
    # yields None for anything else, so every other R ends in a named refusal.
    if found == Situation.reloaded and live is not None:
        write_marker(plane.releases, Marker(active=live, previous=triple.marked))
        return _report(triple, Situation.reconciled, live, "marker_written")
    if action == "complete":
        return _complete(plane, triple)
    if action == "abandon":
        return _abandon(plane, triple)
    raise UnreconciledError(
        f"host is {found}: {triple.describe()}; resolve with --complete (D becomes the truth) "
        "or --abandon (M's release is restored)"
    )


def _resolve_staged(
    plane: ControlPlane, host: MigrationHost, window: Window, action: ReconcileAction
) -> ReconcileReport:
    """The crash after (a): D names the envelope, R is the pre-envelope configuration on TCP."""
    triple, answered = window.triple, window.answered
    if action == "complete":
        active = complete_first_migration(plane, host, answered).active
        return _report(triple, Situation.reconciled, active, "completed").model_copy(
            update={"admin": host.socket_admin}
        )
    if action == "abandon":
        abandon_first_migration(plane, host)
        return _report(triple, Situation.reconciled, None, "abandoned").model_copy(
            update={"admin": answered}
        )
    raise UnreconciledError(
        f"host is {Situation.staged}: {triple.describe()} on {answered}; resolve with --complete "
        f"(the cutover: validate, caddy reload --address {answered}, verify over "
        f"{host.socket_admin}, the ExecReload pair, the marker) or --abandon (the previous "
        "Caddyfile restored; no reload)"
    )


_STRANDED_WAY_OUT = (
    "Caddy refused the cutover's load after moving its admin endpoint to the socket and still "
    "runs the previous configuration; resolve with --abandon (the previous Caddyfile delivered "
    "to the socket, which moves the admin endpoint back to TCP, then the files restored), then "
    "fix what made Caddy refuse the load and start again from the preflight; --complete is "
    "refused: the Caddyfile on disk is the one Caddy just refused"
)


def _offline_only(host: MigrationHost) -> str:
    return (
        "the socket answers with a configuration that binds the admin endpoint elsewhere, a "
        "pairing reconcile resolves only in the staged row or beside an identical Caddyfile "
        "backup, so nothing is resolved automatically; `lovspor release migrate --rollback "
        f"--offline` puts the previous Caddyfile back and restarts {host.unit}"
    )


def _resolve_stranded(
    plane: ControlPlane, host: MigrationHost, window: Window, action: ReconcileAction
) -> ReconcileReport:
    """The cutover's load refused after Caddy moved its admin endpoint to the socket (#302).

    R never moved, but the endpoint did, so *abandon* is the reload back
    to TCP that the rollback after (c) delivers, not a file restore; and
    *complete* would deliver again the file Caddy has just refused.
    """
    found = situation(window.triple)
    described = f"host is {found} on {window.answered}: {window.triple.describe()}"
    if found != Situation.staged:
        raise UnreconciledError(f"{described}; {_offline_only(host)}")
    if action != "abandon":
        raise UnreconciledError(f"{described}; {_STRANDED_WAY_OUT}")
    rollback_first_migration(plane, host)
    return _report(window.triple, Situation.reconciled, None, "abandoned").model_copy(
        update={"admin": host.tcp_admin}
    )


def _backup_mismatch(plane: ControlPlane, host: MigrationHost) -> str | None:
    """Why the Caddyfile backup is not what a stopped first-migration step leaves; else ``None``.

    (a) writes the backup before it installs the new Caddyfile, and the way
    back writes the backup's bytes to the Caddyfile before it reloads
    (#316), so in both windows the two are identical. The name is asked,
    never a symlink's target: the restore moves the name.
    """
    backup = host.previous_caddyfile
    if backup.is_symlink():
        return "is a symlink, not the backup the migration wrote"
    if not backup.is_file():
        return "is not a regular file"
    if backup.read_bytes() != plane.caddyfile.read_bytes():
        return f"differs from {plane.caddyfile}"
    return None


def _restore_left(host: MigrationHost, triple: Triple) -> bool:
    """R = D naming no release, with something at the Caddyfile backup's name."""
    backup = host.previous_caddyfile
    return situation(triple) == Situation.reconciled and (backup.is_symlink() or backup.exists())


def _unfinished_way_out(plane: ControlPlane, host: MigrationHost) -> str:
    return (
        f"{host.previous_caddyfile} holds the bytes of {plane.caddyfile}: the first migration "
        "stopped before (a) installed the new Caddyfile, or a way back before its file restore "
        "finished; resolve with --abandon (the backup consumed, the fragment removed and the "
        "drop-in restored from its record, after the load back to TCP while the admin endpoint "
        "is still on the socket); --complete is refused: the Caddyfile on disk is the "
        "pre-envelope one, so there is no cutover to finish"
    )


def _resolve_unfinished(
    plane: ControlPlane, host: MigrationHost, window: Window, action: ReconcileAction
) -> ReconcileReport:
    """A first-migration step stopped beside an identical Caddyfile backup: *abandon* alone.

    On TCP that is the file restore alone, since R is already the previous
    configuration. Stranded on the socket it is the rollback after (c),
    whose load back moves the admin endpoint and leaves R's pair as it is.
    """
    described = f"host is {Situation.reconciled} on {window.answered}: {window.triple.describe()}"
    mismatch = _backup_mismatch(plane, host)
    if mismatch is not None:
        raise UnreconciledError(
            f"{described}; {host.previous_caddyfile} {mismatch}, so it is not what a stopped "
            "first-migration step leaves beside the Caddyfile; nothing is resolved "
            "automatically: which file is the previous Caddyfile is for a human to say"
        )
    if action != "abandon":
        raise UnreconciledError(f"{described}; {_unfinished_way_out(plane, host)}")
    if window.stranded:
        rollback_first_migration(plane, host)
    else:
        abandon_first_migration(plane, host)
    return _report(window.triple, Situation.reconciled, None, "abandoned").model_copy(
        update={"admin": host.tcp_admin}
    )


def _resolve_window(
    plane: ControlPlane, host: MigrationHost, window: Window, action: ReconcileAction
) -> ReconcileReport:
    """The first migration's own rows, on whichever address answered."""
    if window.stranded:
        return _resolve_stranded(plane, host, window, action)
    triple, answered = window.triple, window.answered
    found = situation(triple)
    if found == Situation.reconciled:
        return _report(triple, found, None, "none").model_copy(update={"admin": answered})
    if found == Situation.reloaded:
        active = complete_first_migration(plane, host, answered).active
        return _report(triple, Situation.reconciled, active, "completed").model_copy(
            update={"admin": host.socket_admin}
        )
    if found == Situation.staged:
        return _resolve_staged(plane, host, window, action)
    raise UnreconciledError(
        f"host is {found} on {answered}: {triple.describe()}; the first migration's "
        "precondition — the pre-envelope configuration serving — is unmet; nothing is resolved "
        "automatically"
    )


def _reconcile_unmarked(
    plane: ControlPlane, host: MigrationHost, action: ReconcileAction
) -> ReconcileReport:
    """No marker: whichever address answers is read, and the migration's rows apply."""
    answered = detect_admin(host)
    bound = replace(plane, admin=host.admin_client(answered))
    triple = read_triple(bound)
    window = Window(triple, answered, stranded_on_socket(host, answered))
    on_socket = answered == host.socket_admin and not window.stranded
    if on_socket and situation(triple) != Situation.reloaded:
        # Bound to the socket and not mid-cutover: a box provisioned with the envelope.
        return _resolve(bound, triple, action).model_copy(update={"admin": answered})
    if _restore_left(host, triple):
        return _resolve_unfinished(plane, host, window, action)
    return _resolve_window(plane, host, window, action)


def reconcile(
    plane: ControlPlane, action: ReconcileAction = "report", host: MigrationHost | None = None
) -> ReconcileReport:
    """Name the state; resolve it per the crash table; refuse what needs a choice.

    With ``host`` and no marker, the first migration's window is read on
    whichever address answers and resolved by the migration's own rows.
    """
    if host is not None and read_marker(plane.releases) is None:
        return _reconcile_unmarked(plane, host, action)
    return _resolve(plane, read_triple(plane), action)


def prune(plane: ControlPlane, keep_build: Path | None = None) -> PruneReport:
    """Only when reconciled; never R, D, M.active or M.previous; stale builds go."""
    triple = read_triple(plane)
    if situation(triple) != Situation.reconciled:
        raise UnreconciledError(f"host is {situation(triple)}: {triple.describe()}; not pruning")
    named = {triple.running.release_id, triple.disk.release_id, triple.marked}
    if triple.marker is not None:
        named.add(triple.marker.previous)
    retained = {name for name in named if name is not None}
    removed: list[str] = []
    for entry in sorted(plane.releases.iterdir()):
        stale_build = is_build_dir(entry) and entry != keep_build
        stale_release = entry.is_dir() and is_release_id(entry.name) and entry.name not in retained
        if stale_build or stale_release:
            shutil.rmtree(entry)
            removed.append(entry.name)
    return PruneReport(removed=tuple(removed), retained=tuple(sorted(retained)))
