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
is foreign and is not resolved automatically; a host already on the
socket and not mid-cutover — a box provisioned with the envelope — is
resolved by the rows above.

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
    """The pre-marker window as read: the triple, and the address it was read on."""

    triple: Triple
    answered: str


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
    """M becomes the truth: D = M's fragment, validate, reload; M unchanged."""
    atomic_write_text(plane.next_fragment, revert_source(plane, triple.marker), mode=WORLD_READABLE)
    validate_caddy(plane.runner, plane.caddyfile, plane.next_fragment)
    expected = adapt(plane.runner, plane.caddyfile, plane.next_fragment)
    plane.next_fragment.replace(plane.fragment)
    failure = reload_expecting(plane, expected)
    if failure is not None:
        raise ReloadFailedError(f"abandon failed: {failure}")
    return _report(triple, Situation.reconciled, triple.marked, "abandoned")


def _report(triple: Triple, found: Situation, live: str | None, action: str) -> ReconcileReport:
    return ReconcileReport(situation=found, live=live, action=action, triple=triple.describe())


def _resolve(plane: ControlPlane, triple: Triple, action: ReconcileAction) -> ReconcileReport:
    """The crash table of Decision 6, on a host whose admin address is settled."""
    found = situation(triple)
    if found == Situation.reconciled:
        return _report(triple, found, triple.running.release_id, "none")
    if found == Situation.reloaded:
        active = triple.running.release_id or ""
        write_marker(plane.releases, Marker(active=active, previous=triple.marked))
        return _report(triple, Situation.reconciled, active, "marker_written")
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


def _reconcile_unmarked(
    plane: ControlPlane, host: MigrationHost, action: ReconcileAction
) -> ReconcileReport:
    """No marker: whichever address answers is read, and the migration's rows apply."""
    answered = detect_admin(host)
    bound = replace(plane, admin=host.admin_client(answered))
    triple = read_triple(bound)
    found = situation(triple)
    if answered == host.socket_admin and found != Situation.reloaded:
        # Already on the socket and not mid-cutover: a box provisioned with the envelope.
        return _resolve(bound, triple, action).model_copy(update={"admin": answered})
    if found == Situation.reconciled:
        return _report(triple, found, None, "none").model_copy(update={"admin": answered})
    if found == Situation.reloaded:
        active = complete_first_migration(plane, host, answered).active
        return _report(triple, Situation.reconciled, active, "completed").model_copy(
            update={"admin": host.socket_admin}
        )
    if found == Situation.staged:
        return _resolve_staged(plane, host, Window(triple, answered), action)
    raise UnreconciledError(
        f"host is {found} on {answered}: {triple.describe()}; the first migration's "
        "precondition — the pre-envelope configuration serving — is unmet; nothing is resolved "
        "automatically"
    )


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
