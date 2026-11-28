"""AR7a review: the whole authority lineage and the identity of the approved log (#738, #1037).

Two trust-boundary questions, each reproduced with real ``ssh-keygen``
signatures, a real bare Git authority and real pushes:

1. **Lineage.** The pin is a commit chain. Judging only its tip, the tip's
   parent and the commit count leaves every older commit unjudged: a writer
   holding the witness key can bury a different history under a valid last
   link. Every checkpoint from genesis to the tip must verify and link.
2. **Selection.** Two logs the owner approved for one repository and purpose
   both verify. A verdict proves that the offered log is owner-approved and
   pinned (A), never that it is *the* log (B). Since AR7b-1, B is the engine's
   reviewed registry (``witness_log_registry``), never this module's verdict.
"""

from pathlib import Path

import pytest

from lovspor.forarbeider import witness_checkpoint
from lovspor.forarbeider.policy_trust import LOG_NAMESPACE
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.witness_checkpoint import CheckpointStore, TrustedLog, require_current
from lovspor.forarbeider.witness_checkpoint_models import (
    AuthorityLocator,
    Checkpoint,
    genesis,
    log_id_of,
    successor,
)
from tests.unit.forarbeider_authorization_support import sign
from tests.unit.forarbeider_checkpoint_support import (
    REF,
    World,
    crash_in_child,
    current_in_child,
    entry,
    git,
    make_authority,
    make_world,
    pin_commit,
    push_commit,
    signed,
)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return make_world(tmp_path)


def forged_store(world: World, checkpoints: list[Checkpoint]) -> CheckpointStore:
    """A separate witness store, internally consistent, holding ``checkpoints`` signed."""
    location = world.tmp / "forged-store"
    location.mkdir(mode=0o700)
    store = CheckpointStore(location)
    for checkpoint in checkpoints:
        assert store.write(checkpoint.log_id, checkpoint.sequence, signed(world, checkpoint))
    return store


def push_all(world: World, raws: list[bytes]) -> None:
    """Push one fast-forward commit per ``raw`` on top of the pin, as any client could."""
    parent = world.tip()
    for raw in raws:
        parent = pin_commit(world, raw, parent)
    assert "rejected" not in push_commit(world, parent)


# --- 1. the whole lineage ------------------------------------------------------------------------


def test_a_valid_full_lineage_from_genesis_to_the_tip_is_verified(world: World) -> None:
    for n in range(1, 5):
        world.advance(n)

    verdict = world.current()

    assert verdict.status == "verified"
    assert verdict.pinned is not None and verdict.pinned.sequence == 4


def test_a_rewritten_middle_under_a_valid_last_link_is_never_verified(world: World) -> None:
    """The authority pinned checkpoint 1 (entry 1). A writer holding the witness key pushes,
    as fast-forwards, checkpoint 2' and 3' of a fork whose checkpoint 1' adds entry 10.
    The tip's sequence matches the commit count and 3' links to 2', and a forged store
    holding genesis, 1', 2', 3' is internally consistent; yet 2' does not extend 1."""
    world.advance(1)
    first = genesis(log_id_of(world.bootstrap))
    fork = [first, successor(first, entry(10))]
    fork += [successor(fork[-1], entry(20))]
    fork += [successor(fork[-1], entry(30))]
    push_all(world, [signed(world, fork[2]), signed(world, fork[3])])

    verdict = world.current(store=forged_store(world, fork))

    assert verdict.status == "pin_invalid"
    assert verdict.reasons == ("pin_lineage_inconsistent",)


@pytest.mark.parametrize(
    "middle", ["garbage", "bot_signed", "extra_file", "skipping", "wrong_predecessor"]
)
def test_an_invalid_historical_checkpoint_is_never_healed_by_later_ones(
    world: World, middle: str
) -> None:
    """Genesis, an invalid commit 1, then genuine checkpoints 2 and 3: the tip is sequence 3
    of 4 commits, links to 2, and the witness store holds the genuine chain 0..3."""
    chain = chain_of(world, 3)
    for checkpoint in chain[1:]:
        assert world.store.write(checkpoint.log_id, checkpoint.sequence, signed(world, checkpoint))
    tip = world.tip()
    bad = {
        "garbage": pin_commit(world, b"{}", tip),
        "bot_signed": pin_commit(world, signed(world, chain[1], world.bot), tip),
        "extra_file": pin_commit(world, signed(world, chain[1]), tip, extra=b"x\n"),
        "skipping": pin_commit(world, signed(world, chain[2]), tip),
        "wrong_predecessor": pin_commit(
            world, signed(world, chain[1].model_copy(update={"previous_sha256": "0" * 64})), tip
        ),
    }[middle]
    two = pin_commit(world, signed(world, chain[2]), bad)
    assert "rejected" not in push_commit(world, pin_commit(world, signed(world, chain[3]), two))

    verdict = world.current()

    assert verdict.status == "pin_invalid"
    refused_status(world, "checkpoint_pin_invalid")


@pytest.mark.parametrize("root", ["checkpoint_1", "foreign_genesis"])
def test_a_lineage_whose_root_is_not_the_logs_genesis_is_invalid(tmp_path: Path, root: str) -> None:
    """Before the owner's bootstrap, a writer creates the ref from a root that is not the
    genesis the bootstrap implies, with genuine witness signatures on every commit."""
    world = make_world(tmp_path, bootstrap=False)
    chain = chain_of(world, 2)
    first = {
        "checkpoint_1": chain[1],
        "foreign_genesis": chain[0].model_copy(update={"root_sha256": "1" * 64}),
    }[root]
    base = pin_commit(world, signed(world, first), None)
    assert "rejected" not in push_commit(world, pin_commit(world, signed(world, chain[2]), base))

    verdict = world.current()

    assert (verdict.status, verdict.reasons) == ("pin_invalid", ("pin_lineage_inconsistent",))


@pytest.mark.parametrize("missing", ["checkpoint_blob", "commit"])
def test_a_missing_ancestor_object_fails_closed(world: World, missing: str) -> None:
    """An authority whose history is incomplete cannot vouch for it: unavailable, not verified."""
    for n in (1, 2, 3):
        world.advance(n)
    name = f"{REF}~2:checkpoint.json" if missing == "checkpoint_blob" else f"{REF}~2"
    oid = git(world.bare, "rev-parse", name).strip()
    loose = world.bare / "objects" / oid[:2] / oid[2:]
    assert loose.is_file()
    loose.chmod(0o600)
    loose.unlink()

    verdict = world.current()

    assert verdict.status == "authority_unavailable"
    refused_status(world, "checkpoint_authority_unavailable")


def test_a_merge_anywhere_in_the_lineage_is_invalid(world: World) -> None:
    """A merge whose first parent is the pin and whose second is a genuine signed side branch."""
    chain = chain_of(world, 2)
    world.advance(1)
    tip = world.tip()
    side = pin_commit(world, signed(world, chain[1]), tip)
    work = world.tmp / "intruder"
    tree = git(work, "rev-parse", f"{pin_commit(world, signed(world, chain[2]), tip)}^{{tree}}")
    merge = git(work, "commit-tree", tree.strip(), "-p", tip, "-p", side, "-m", "merge").strip()
    assert "rejected" not in push_commit(world, merge)

    verdict = world.current()

    assert (verdict.status, verdict.reasons) == ("pin_invalid", ("pin_lineage_merge",))


def test_a_rollback_and_a_competing_successor_are_still_refused_by_the_authority(
    world: World,
) -> None:
    for n in (1, 2, 3):
        world.advance(n)
    tip = world.tip()
    competing = successor(chain_of(world, 2)[2], entry(99))
    rewound = push_commit(world, f"{tip}~2", force=True)
    fork = pin_commit(world, signed(world, competing), f"{tip}~1")
    sibling = push_commit(world, fork, force=True)

    assert "non-fast-forward" in rewound and "non-fast-forward" in sibling
    assert world.tip() == tip
    assert world.current().status == "verified"


def test_a_long_lineage_survives_a_restart_and_a_crash_then_retry(world: World) -> None:
    for n in range(1, 6):
        world.advance(n)

    assert current_in_child(world) == ("verified", 5)
    assert crash_in_child(world, 6) == 17
    assert current_in_child(world) == ("pending_advance", 5)
    assert world.advance(6).status == "completed"
    assert current_in_child(world) == ("verified", 6)


# --- 2. which log: selection is not signature validity -------------------------------------------


def second_log(world: World) -> TrustedLog:
    """A second log the owner genuinely approved: same repository and purpose, another nonce,
    another independent authority."""
    bare = make_authority(world.tmp / "authority-2" / "witness.git")
    approved = world.bootstrap.model_copy(
        update={"nonce": "f" * 64, "authority": AuthorityLocator(remote=str(bare), ref=REF)}
    )
    raw = canonical_text(approved).encode()
    log = TrustedLog(raw, sign(world.owner, raw, LOG_NAMESPACE), world.root)
    assert witness_checkpoint.bootstrap(world.witnessing(log=log), world.signer).status == (
        "bootstrapped"
    )
    return log


def test_two_owner_approved_logs_each_verify_their_own_pin(world: World) -> None:
    """A: each verdict proves its log is owner-approved and pinned, and nothing more."""
    other = second_log(world)
    world.advance(1)
    world.advance(7, log=other)

    first, second = world.current(), world.current(log=other)

    assert (first.status, second.status) == ("verified", "verified")
    assert first.log_id != second.log_id
    assert first.pinned is not None and second.pinned is not None
    assert first.pinned.root_sha256 != second.pinned.root_sha256


# B, the one permitted log, is not AR7a's to decide: ``current`` is consistency only, and
# selection is ``witness_log_registry.require_selected`` (AR7b-1,
# ``test_forarbeider_witness_log_selection.py``).


# --- helpers -------------------------------------------------------------------------------------


def chain_of(world: World, count: int) -> list[Checkpoint]:
    checkpoints = [genesis(log_id_of(world.bootstrap))]
    for n in range(1, count + 1):
        checkpoints.append(successor(checkpoints[-1], entry(n)))
    return checkpoints


def refused_status(world: World, detail: str) -> None:
    with pytest.raises(witness_checkpoint.ForarbeiderRefusedError) as raised:
        require_current(world.witnessing())
    assert detail in raised.value.detail
