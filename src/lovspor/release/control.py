"""The control-plane transaction and the reconciled triple (ADR-0014 Decision 6).

What is live is never a pointer. Three sources say which release Caddy
serves — **R**, the running configuration read from the admin endpoint;
**D**, the same pair adapted from the composed Caddyfile on disk; **M**,
the marker — and ``live_release()`` is the id iff ``release_id_R ==
release_id_D == M.active`` and ``config_hash_R == config_hash_D``. The
hash equality is what proves the root, the redirect map and the proxy
handlers Caddy runs are the ones the fragment on disk composes: one
release, not a mix. Anything else is *unreconciled*, and every mutating
command refuses and prints the three values; an unreachable admin
endpoint is *unobservable*, a state of its own, in which nothing can be
compared and every command refuses with the named precondition.

The transaction (``commit_release``) starts only on a finalized
``<releases>/<id>/``, never on a ``.build-*``: (1) *stage* the release's
own fragment as ``<fragment>.next``, ``caddy validate`` the composed
configuration and ``caddy adapt`` it, asserting its ``release_id`` is
the candidate's; (2) *commit* — rename ``.next`` over the active
fragment (D = new), ``systemctl reload caddy`` (R = new), read R back to
confirm, and only then write M = new with ``previous`` = the release that
was active; (3) on reload failure Caddy keeps serving the old
configuration (R = old), the old release's own fragment — kept immutable
in ``<previous>/release.caddy`` — is put back (D = old), Caddy reloaded,
M unchanged, and the command exits non-zero.

``reconcile`` and ``prune``, the resolutions of the crash table, live in
``lovspor.release.reconcile`` on top of the same triple.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.atomic_io import atomic_write_text
from lovspor.release.caddy import AdminClient, ConfigPair, Runner, adapt, config_pair
from lovspor.release.caddy import reload as reload_caddy
from lovspor.release.caddy import validate as validate_caddy
from lovspor.release.envelope import (
    FRAGMENT_NAME,
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
    IncompleteEnvelopeError,
    ReloadFailedError,
    UnobservableError,
    UnreconciledError,
)

Checkpoint = Callable[[str], None]

TRANSACTION_STEPS = ("staged", "validated", "committed", "reloaded", "marked")
"""The checkpoint names of one commit, in order."""
NEXT_SUFFIX = ".next"
PREVIOUS_SUFFIX = ".previous"
"""A copy of a foreign active fragment, taken when no marker names its release."""


@dataclass(frozen=True)
class ControlPlane:
    """Where the three sources live, and the two injected boundaries."""

    releases: Path
    caddyfile: Path
    fragment: Path
    runner: Runner
    admin: AdminClient

    @property
    def next_fragment(self) -> Path:
        return self.fragment.with_name(self.fragment.name + NEXT_SUFFIX)

    @property
    def previous_fragment(self) -> Path:
        return self.fragment.with_name(self.fragment.name + PREVIOUS_SUFFIX)


class Triple(BaseModel):
    """R, D and M as read; ``old`` is M's own fragment adapted, the reference for R."""

    model_config = ConfigDict(frozen=True)

    running: ConfigPair
    disk: ConfigPair
    marker: Marker | None
    old: ConfigPair | None

    def describe(self) -> str:
        active = self.marker.active if self.marker else "none"
        previous = (self.marker.previous or "none") if self.marker else "none"
        return (
            f"R={self.running.describe()} D={self.disk.describe()} "
            f"M=(active {active}, previous {previous})"
        )

    @property
    def marked(self) -> str | None:
        return self.marker.active if self.marker else None


class Situation(StrEnum):
    reconciled = "reconciled"
    staged = "staged_not_reloaded"
    reloaded = "reloaded_marker_missing"
    foreign = "foreign"


class CommitReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    active: str
    previous: str | None
    already_live: bool


def _no_checkpoint(step: str) -> None:
    del step


def _disk_and_marker(plane: ControlPlane) -> tuple[ConfigPair, Marker | None]:
    return adapt(plane.runner, plane.caddyfile, plane.fragment), read_marker(plane.releases)


def _old_pair(plane: ControlPlane, marker: Marker | None) -> ConfigPair | None:
    if marker is None:
        return None
    fragment = release_dir(plane.releases, marker.active) / FRAGMENT_NAME
    if not fragment.is_file():
        return None
    return adapt(plane.runner, plane.caddyfile, fragment)


def read_triple(plane: ControlPlane) -> Triple:
    """D and M from disk, then R from Caddy; unreachable prints D and M for the operator."""
    disk, marker = _disk_and_marker(plane)
    try:
        running = config_pair(plane.admin.running_config())
    except UnobservableError as error:
        active = marker.active if marker else "none"
        detail = f"{error.detail}; D={disk.describe()} M=(active {active})"
        raise UnobservableError(error.reason, detail) from error
    return Triple(running=running, disk=disk, marker=marker, old=_old_pair(plane, marker))


def situation(triple: Triple) -> Situation:
    """The row of the crash table this host is on."""
    running, disk = triple.running, triple.disk
    if running == disk:
        if running.release_id == triple.marked:
            return Situation.reconciled
        if running.release_id is not None:
            return Situation.reloaded
        return Situation.foreign
    r_is_old = running == triple.old or (triple.marker is None and running.release_id is None)
    if disk.release_id is not None and r_is_old:
        return Situation.staged
    return Situation.foreign


def live_release(plane: ControlPlane) -> str | None:
    """The reconciled live release, ``None`` when nothing is live; else a named refusal."""
    triple = read_triple(plane)
    if situation(triple) != Situation.reconciled:
        raise UnreconciledError(f"host is {situation(triple)}: {triple.describe()}")
    return triple.running.release_id


def _stage(plane: ControlPlane, text: str, marker: Marker | None) -> None:
    """``.next`` beside the active fragment; a foreign active fragment is kept for a revert."""
    if marker is None and plane.fragment.is_file():
        atomic_write_text(plane.previous_fragment, plane.fragment.read_text(encoding="utf-8"))
    atomic_write_text(plane.next_fragment, text)


def _candidate_pair(plane: ControlPlane, content_id: str) -> ConfigPair:
    validate_caddy(plane.runner, plane.caddyfile, plane.next_fragment)
    pair = adapt(plane.runner, plane.caddyfile, plane.next_fragment)
    if pair.release_id != content_id:
        raise CommitRefusedError(
            f"the composed configuration names {pair.release_id or 'no release'}, "
            f"not {content_id}; does the Caddyfile import the fragment?"
        )
    return pair


def revert_source(plane: ControlPlane, marker: Marker | None) -> str:
    if marker is not None:
        return read_fragment(release_dir(plane.releases, marker.active))
    if plane.previous_fragment.is_file():
        return plane.previous_fragment.read_text(encoding="utf-8")
    raise ControlPlaneError("no previous fragment to restore: nothing was live before")


def reload_expecting(plane: ControlPlane, expected: ConfigPair) -> str | None:
    """Reload, then read R back: the failure text, or ``None`` when R is ``expected``."""
    done = reload_caddy(plane.runner)
    if done.returncode != 0:
        return f"systemctl reload caddy failed: {done.stderr.strip() or done.returncode}"
    running = config_pair(plane.admin.running_config())
    if running != expected:
        return f"after the reload Caddy runs {running.describe()}, not {expected.describe()}"
    return None


def _restore(plane: ControlPlane, marker: Marker | None, old: ConfigPair | None) -> None:
    """D = old, then R = old; the previous fragment is immutable inside its release."""
    atomic_write_text(plane.next_fragment, revert_source(plane, marker))
    plane.next_fragment.replace(plane.fragment)
    if old is None:
        reload_caddy(plane.runner)
        return
    failure = reload_expecting(plane, old)
    if failure is not None:
        raise ReloadFailedError(f"revert did not restore the previous configuration: {failure}")


def _commit(plane: ControlPlane, content_id: str, triple: Triple, checkpoint: Checkpoint) -> None:
    """Steps (1) stage and (2) commit of the transaction, (3) the revert on failure."""
    _stage(plane, read_fragment(release_dir(plane.releases, content_id)), triple.marker)
    checkpoint("staged")
    pair = _candidate_pair(plane, content_id)
    checkpoint("validated")
    plane.next_fragment.replace(plane.fragment)
    checkpoint("committed")
    failure = reload_expecting(plane, pair)
    if failure is not None:
        _restore(plane, triple.marker, triple.old)
        raise ReloadFailedError(f"release {content_id[:12]} not switched: {failure}")
    checkpoint("reloaded")
    write_marker(plane.releases, Marker(active=content_id, previous=triple.marked))
    checkpoint("marked")


def commit_release(
    plane: ControlPlane, content_id: str, checkpoint: Checkpoint = _no_checkpoint
) -> CommitReport:
    """Make the finalized ``<releases>/<content_id>/`` live through the transaction."""
    triple = read_triple(plane)
    if situation(triple) != Situation.reconciled:
        raise UnreconciledError(f"host is {situation(triple)}: {triple.describe()}")
    if triple.running.release_id == content_id:
        return CommitReport(active=content_id, previous=triple.marked, already_live=True)
    release = release_dir(plane.releases, content_id)
    if not is_complete(release):
        raise IncompleteEnvelopeError(f"{content_id} is not a complete envelope; not staged")
    if fragment_release_id(read_fragment(release)) != content_id:
        raise CommitRefusedError(f"{content_id}/{FRAGMENT_NAME} does not name its release")
    _commit(plane, content_id, triple, checkpoint)
    return CommitReport(active=content_id, previous=triple.marked, already_live=False)


def rollback(plane: ControlPlane, checkpoint: Checkpoint = _no_checkpoint) -> CommitReport:
    """The marker's ``previous`` through the same transaction; rolling forward is the same."""
    triple = read_triple(plane)
    if situation(triple) != Situation.reconciled:
        raise UnreconciledError(f"host is {situation(triple)}: {triple.describe()}")
    if triple.marker is None or triple.marker.previous is None:
        raise ControlPlaneError("no previous release in the marker; nothing to roll back to")
    return commit_release(plane, triple.marker.previous, checkpoint)
