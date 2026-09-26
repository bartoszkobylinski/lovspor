"""The release contracts the fast gate runs at every commit (issue #323, Phase D1/D2).

Each is the cheapest deterministic form of a failure class this repository
has already shipped a fix for. The full transaction and crash-table suites
(``test_release_control.py``, ``test_release_migrate.py``) stay in the deep
gate and CI; this module is the part an agent hears about before its commit.

D1, path identity (#316, #317). Caddy hides every imported file by the path it
was imported from, so a pair adapted with the fragment at ``.next`` or inside a
release is never the pair Caddy runs once the fragment is renamed into place.
Commit, the revert after a failed reload, and reconcile's complete and abandon
must each compare R with the pair adapted where the reload reads. The fake
Caddy models that hiding, and the first D1 test proves it does, so the others
cannot pass vacuously.

D2, fail closed. Unknown, foreign, unobservable or contradictory state is never
coerced into a transition. ``situation()`` is swept over its whole domain:
every row other than foreign carries its own evidence, and the partition is
pinned. Every entry point refuses a host that is not reconciled and touches
nothing on the way.
"""

import copy
from collections import Counter
from collections.abc import Callable, Iterator
from itertools import product
from pathlib import Path
from typing import Any

import pytest

from lovspor.release.caddy import ConfigPair, adapt
from lovspor.release.control import (
    Situation,
    Triple,
    commit_release,
    live_release,
    rollback,
    situation,
)
from lovspor.release.envelope import Marker, read_fragment, write_marker
from lovspor.release.errors import (
    CommitRefusedError,
    ReloadFailedError,
    UnobservableError,
    UnreconciledError,
)
from lovspor.release.reconcile import prune, reconcile
from tests.unit.control_host import Host, make_host, two_envelopes
from tests.unit.release_fixtures import World, make_world

A, B = "a" * 64, "b" * 64
HASHES = ("1" * 64, "2" * 64)


class Killed(Exception):  # noqa: N818 — a simulated process death, not a lovspor error
    """The process died right after the named step."""


def _kill_at(step: str) -> Callable[[str], None]:
    def checkpoint(reached: str) -> None:
        if reached == step:
            raise Killed(step)

    return checkpoint


# --- D2: situation() over its whole domain -----------------------------------------------------


def _pairs() -> Iterator[ConfigPair]:
    for release_id, config_hash in product((None, A, B), HASHES):
        yield ConfigPair(release_id=release_id, config_hash=config_hash)


def _triples() -> Iterator[Triple]:
    markers = (None, Marker(active=A, previous=None), Marker(active=B, previous=A))
    for running, disk, marker in product(tuple(_pairs()), tuple(_pairs()), markers):
        yield Triple(running=running, disk=disk, marker=marker)


def _evidence(triple: Triple, row: Situation) -> bool:
    """What each row other than foreign must be able to show; None marks "no marker"."""
    running, disk, marked = triple.running, triple.disk, triple.marked
    if row == Situation.reconciled:
        return running == disk and running.release_id == marked
    if row == Situation.reloaded:
        return running == disk and running.release_id not in (None, marked)
    if row == Situation.staged:
        apart = running != disk and disk.release_id not in (None, running.release_id)
        return apart and running.release_id == marked
    return row == Situation.foreign


class TestEveryRowCarriesItsEvidence:
    @pytest.mark.parametrize("triple", tuple(_triples()), ids=lambda triple: triple.describe())
    def test_no_state_is_coerced_into_a_row_it_cannot_show(self, triple: Triple) -> None:
        row = situation(triple)

        assert _evidence(triple, row), f"{triple.describe()} read {row}"

    def test_the_partition_of_the_domain_is_pinned(self) -> None:
        """108 triples: a branch simplified to "assume the common case" moves these counts.

        Per marker (none, A, B): reconciled 2+2+2, reloaded 4+2+2, staged 8+4+4.
        """
        rows = Counter(situation(triple) for triple in _triples())

        assert rows == {
            Situation.reconciled: 6,
            Situation.reloaded: 8,
            Situation.staged: 16,
            Situation.foreign: 78,
        }


# --- D2: every entry point refuses a host that is not reconciled -------------------------------


@pytest.fixture
def placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    """A host serving the pre-envelope placeholder, with no release on disk: no build needed."""
    releases = tmp_path / "source"
    releases.mkdir()
    return make_host((releases, A, B), tmp_path / "host", monkeypatch)


def _hand_reloaded(host: Host) -> Host:
    edited: dict[str, Any] = copy.deepcopy(host.caddy.running_config())  # type: ignore[arg-type]
    edited["apps"]["http"]["servers"]["srv0"]["routes"][0]["terminal"] = False
    host.caddy.load(edited)
    return host


def _marked_but_not_running(host: Host) -> Host:
    (host.releases / A).mkdir()
    write_marker(host.releases, Marker(active=A, previous=None))
    return host


def _admin_down(host: Host) -> Host:
    host.caddy.admin_up = False
    return host


UNRECONCILED = {
    "hand_reloaded": (_hand_reloaded, UnreconciledError),
    "marked_but_not_running": (_marked_but_not_running, UnreconciledError),
    "admin_down": (_admin_down, UnobservableError),
}
ENTRY_POINTS: dict[str, Callable[[Host], object]] = {
    "live_release": lambda host: live_release(host.plane),
    "commit_release": lambda host: commit_release(host.plane, B),
    "rollback": lambda host: rollback(host.plane),
    "prune": lambda host: prune(host.plane),
    "reconcile_report": lambda host: reconcile(host.plane),
}


class TestEveryEntryPointFailsClosed:
    @pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
    @pytest.mark.parametrize("state", sorted(UNRECONCILED))
    def test_refuses_and_touches_nothing(self, placeholder: Host, state: str, entry: str) -> None:
        make_state, refusal = UNRECONCILED[state]
        host = make_state(placeholder)
        before, reloads = host.snapshot(), host.caddy.reloads

        with pytest.raises(refusal):
            ENTRY_POINTS[entry](host)

        assert host.snapshot() == before
        assert host.caddy.reloads == reloads

    def test_a_foreign_host_is_reported_with_both_ways_out(self, placeholder: Host) -> None:
        with pytest.raises(UnreconciledError) as caught:
            reconcile(_hand_reloaded(placeholder).plane)

        assert "host is foreign" in str(caught.value)
        assert "--complete" in str(caught.value) and "--abandon" in str(caught.value)

    def test_complete_never_makes_a_disk_naming_no_release_the_truth(
        self, placeholder: Host
    ) -> None:
        host = _hand_reloaded(placeholder)
        before = host.snapshot()

        with pytest.raises(CommitRefusedError, match="names no release"):
            reconcile(host.plane, "complete")

        assert host.snapshot() == before
        assert host.caddy.reloads == 0


# --- D1: R is compared with the pair adapted where the reload reads -----------------------------


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("world"))


@pytest.fixture(scope="module")
def envelopes(world: World, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, str]:
    return two_envelopes(world, tmp_path_factory.mktemp("source") / "releases")


@pytest.fixture
def live_a(
    envelopes: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Host:
    host = make_host(envelopes, tmp_path, monkeypatch)
    host.make_live(host.a)
    return host


class TestPathIdentity:
    def test_the_fake_hides_the_fragment_by_the_path_it_was_read_from(self, live_a: Host) -> None:
        """Without this the other contracts would pass whatever path the pair came from."""
        plane = live_a.plane
        plane.next_fragment.write_text(read_fragment(live_a.releases / live_a.b), encoding="utf-8")

        at_next = adapt(plane.runner, plane.caddyfile, plane.next_fragment)

        assert at_next.release_id == live_a.b
        assert at_next != live_a.pair_of(live_a.b)

    def test_a_commit_switches_to_the_pair_caddy_runs(self, live_a: Host) -> None:
        commit_release(live_a.plane, live_a.b)

        assert live_a.running() == live_a.pair_of(live_a.b)
        assert live_release(live_a.plane) == live_a.b
        assert live_a.caddy.reloads == 1

    def test_the_revert_after_a_failed_reload_restores_the_pair_caddy_runs(
        self, live_a: Host
    ) -> None:
        live_a.caddy.fail_reloads = 1

        with pytest.raises(ReloadFailedError, match="not switched"):
            commit_release(live_a.plane, live_a.b)

        assert live_a.running() == live_a.pair_of(live_a.a)
        assert live_release(live_a.plane) == live_a.a

    @pytest.mark.parametrize(("action", "live"), [("complete", "b"), ("abandon", "a")])
    def test_reconcile_resolves_a_crash_after_the_rename_to_the_pair_caddy_runs(
        self, live_a: Host, action: str, live: str
    ) -> None:
        with pytest.raises(Killed):
            commit_release(live_a.plane, live_a.b, _kill_at("committed"))
        expected = getattr(live_a, live)

        report = reconcile(live_a.plane, action)  # type: ignore[arg-type]

        assert report.live == expected
        assert live_a.running() == live_a.pair_of(expected)
        assert live_release(live_a.plane) == expected
