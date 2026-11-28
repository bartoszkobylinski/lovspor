"""AR7a red: what the AR5 anchor witness cannot detect on its own (#738, #1037).

Each test runs a real attack against the shipped AR5 witness
(:class:`OfflineAnchorWitness`, :func:`verify`) with genuine witness
signatures and real file operations, and asserts the property an independent,
monotonic checkpoint must give. AR5 has no such checkpoint: its pin is an
optional value the caller keeps in memory and never persists, and the witness
is a directory that whoever holds it can copy or restore. So these tests fail
on AR5, and they stay ``xfail(strict=True)`` until an AR5 family adopts the
AR7a checkpoint (:mod:`~lovspor.forarbeider.witness_checkpoint`); this PR does
not migrate AR5 anchors or change their verification.
"""

import shutil
from pathlib import Path

import pytest

from lovspor.forarbeider.control_plane_anchor import (
    AnchorPin,
    durable_append,
    verify,
)
from lovspor.forarbeider.control_plane_anchor_models import anchor_digest
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from tests.unit.forarbeider_execution_support import KeySigner
from tests.unit.test_forarbeider_control_plane_anchor import (
    REPOSITORY,
    WITNESS,
    World,
    appended,
    attempt,
)

pytestmark = pytest.mark.xfail(
    strict=True, reason="AR5 anchors have no independent checkpoint until they adopt AR7a"
)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _snapshot(world: World, name: str) -> tuple[Path, Path]:
    workspace, store = world.tmp / f"{name}-ws", world.tmp / f"{name}-store"
    shutil.copytree(world.workspace, workspace)
    shutil.copytree(world.store, store)
    return workspace, store


def _restore(world: World, saved: tuple[Path, Path]) -> None:
    for live, old in zip((world.workspace, world.store), saved, strict=True):
        shutil.rmtree(live)
        shutil.copytree(old, live)


def test_a_witness_and_store_rolled_back_together_are_not_current(world: World) -> None:
    records = appended(world, 1)
    saved = _snapshot(world, "t1")
    durable_append(world.workspace, attempt(2, records[0]), REPOSITORY, world.authority())
    _restore(world, saved)

    verdict = verify(world.attempts(), world.authority())

    assert verdict.status != "verified"


def test_a_pin_rolled_back_with_the_witness_is_not_current(world: World) -> None:
    records = appended(world, 1)
    latest = verify(world.attempts(), world.authority()).latest
    assert latest is not None
    old_pin = AnchorPin(latest.anchor_sequence, anchor_digest(latest))
    saved = _snapshot(world, "t1")
    durable_append(world.workspace, attempt(2, records[0]), REPOSITORY, world.authority())
    _restore(world, saved)

    verdict = verify(world.attempts(), world.authority(pin=old_pin))

    assert verdict.status != "verified"


def test_two_witness_stores_with_one_key_never_both_verify(world: World) -> None:
    records = appended(world, 1)
    clone_ws, clone_store = _snapshot(world, "clone")
    clone = OfflineAnchorWitness(clone_store, KeySigner(WITNESS, world.witness_key))
    durable_append(world.workspace, attempt(2, records[0]), REPOSITORY, world.authority())
    other = attempt(2, records[0], state="refused")
    durable_append(clone_ws, other, REPOSITORY, world.authority(witness=clone, workspace=clone_ws))

    original = verify(world.attempts(), world.authority())
    cloned = verify(world.attempts(clone_ws), world.authority(witness=clone, workspace=clone_ws))

    assert (original.status, cloned.status).count("verified") <= 1


def test_a_competing_signed_successor_never_replaces_the_witnessed_one(world: World) -> None:
    records = appended(world, 1)
    saved = _snapshot(world, "t1")
    durable_append(world.workspace, attempt(2, records[0]), REPOSITORY, world.authority())
    first = verify(world.attempts(), world.authority()).latest
    assert first is not None
    _restore(world, saved)
    competing = attempt(2, records[0], state="refused")
    durable_append(world.workspace, competing, REPOSITORY, world.authority())

    verdict = verify(world.attempts(), world.authority())

    assert verdict.status != "verified" or verdict.latest == first


def test_a_restart_remembers_the_newest_checkpoint_it_saw(world: World) -> None:
    """The pin a run learnt is gone after a restart: AR5 never persists it."""
    records = appended(world, 1)
    saved = _snapshot(world, "t1")
    durable_append(world.workspace, attempt(2, records[0]), REPOSITORY, world.authority())
    assert verify(world.attempts(), world.authority()).status == "verified"
    _restore(world, saved)
    restarted = World.__new__(World)
    restarted.__dict__.update(world.__dict__)

    verdict = verify(restarted.attempts(), restarted.authority())

    assert verdict.status != "verified"
