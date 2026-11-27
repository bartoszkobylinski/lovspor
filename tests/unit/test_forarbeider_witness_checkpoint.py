"""AR7a: an independently pinned, monotonic checkpoint for a witness log (#738, #1037).

Every attack is real: stores copied, restored and forked on disk, pins pushed
to a real Git authority, forced, replayed and deleted, processes spawned to
race and to die mid-advance. The owner and the witness sign with real
``ssh-keygen``; nothing a verifier judges is mocked. Each attack here is one
the AR5 witness alone cannot detect (``test_forarbeider_ar5_rollback_gap``).
"""

import ast
import shutil
from pathlib import Path

import pytest

import lovspor
from lovspor import git_checkpoint
from lovspor.forarbeider import witness_checkpoint
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_trust import LOG_NAMESPACE, POLICY_NAMESPACE
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.witness_checkpoint import (
    CheckpointStore,
    TrustedLog,
    consistent,
    require_current,
    standing,
)
from lovspor.forarbeider.witness_checkpoint_models import (
    AuthorityLocator,
    Checkpoint,
    genesis,
    log_id_of,
    successor,
)
from tests.unit.forarbeider_authorization_support import sign, trust_root
from tests.unit.forarbeider_checkpoint_support import (
    REF,
    World,
    client,
    crash_in_child,
    current_in_child,
    entry,
    git,
    in_children,
    make_authority,
    make_world,
    pin_commit,
    push_commit,
    push_pin,
    restore,
    signed,
    snapshot,
    tree,
)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return make_world(tmp_path)


def refused(action: object, detail: str) -> None:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()  # type: ignore[operator]
    assert detail in raised.value.detail


def chain_of(world: World, count: int) -> list[Checkpoint]:
    checkpoints = [genesis(log_id_of(world.bootstrap))]
    for n in range(1, count + 1):
        checkpoints.append(successor(checkpoints[-1], entry(n)))
    return checkpoints


# --- bootstrap: no trust on first use ------------------------------------------------------------


def test_a_bootstrapped_log_is_current_at_its_genesis(world: World) -> None:
    verdict = world.current()

    assert verdict.status == "verified"
    assert verdict.pinned == genesis(log_id_of(world.bootstrap))
    assert require_current(world.witnessing()) == verdict.pinned


def test_a_log_never_bootstrapped_is_pin_missing_and_verification_creates_nothing(
    tmp_path: Path,
) -> None:
    world = make_world(tmp_path, bootstrap=False)
    before = (tree(world.bare), tree(world.store.location))

    assert world.current().status == "pin_missing"
    refused(lambda: world.advance(1), "checkpoint_pin_missing")
    refused(lambda: require_current(world.witnessing()), "checkpoint_pin_missing")
    assert (tree(world.bare), tree(world.store.location)) == before
    assert world.tip() == ""


def test_bootstrapping_twice_is_already_applied_and_never_after_an_advance(world: World) -> None:
    again = witness_checkpoint.bootstrap(world.witnessing(), world.signer)
    world.advance(1)

    assert again.status == "already_applied"
    refused(
        lambda: witness_checkpoint.bootstrap(world.witnessing(), world.signer),
        "checkpoint_verified",
    )


@pytest.mark.parametrize("signer", ["witness", "bot", "owner_other_namespace"])
def test_a_bootstrap_the_owner_did_not_sign_in_the_log_namespace_is_invalid(
    world: World, signer: str
) -> None:
    raw = canonical_text(world.bootstrap).encode()
    key, namespace = {
        "witness": (world.witness_key, LOG_NAMESPACE),
        "bot": (world.bot, LOG_NAMESPACE),
        "owner_other_namespace": (world.owner, POLICY_NAMESPACE),
    }[signer]
    forged = TrustedLog(raw, sign(key, raw, namespace), world.root)

    verdict = world.current(log=forged)

    assert verdict.status == "bootstrap_invalid"
    refused(lambda: world.advance(1, log=forged), "checkpoint_bootstrap_invalid")


def test_a_non_canonical_bootstrap_is_invalid(world: World) -> None:
    raw = canonical_text(world.bootstrap).encode() + b"\n"
    log = TrustedLog(raw, sign(world.owner, raw, LOG_NAMESPACE), world.root)

    assert world.current(log=log).reasons == ("bootstrap_not_canonical",)


def test_the_log_id_is_the_approval_not_the_directory(world: World, tmp_path: Path) -> None:
    world.advance(1)
    moved = tmp_path / "elsewhere"
    shutil.move(world.store.location, moved)

    verdict = world.current(store=CheckpointStore(moved))

    assert verdict.status == "verified"
    assert verdict.log_id == log_id_of(world.bootstrap)


def test_another_approved_log_on_the_same_key_and_authority_shares_nothing(world: World) -> None:
    world.advance(1)
    other = world.bootstrap.model_copy(update={"nonce": "e" * 64})
    raw = canonical_text(other).encode()
    log = TrustedLog(raw, sign(world.owner, raw, LOG_NAMESPACE), world.root)

    verdict = world.current(log=log)

    assert verdict.status == "pin_invalid"
    assert verdict.log_id == log_id_of(other) != log_id_of(world.bootstrap)


# --- A/C: the authority is the owner's choice and independent ------------------------------------


@pytest.mark.parametrize("where", ["inside_workspace", "is_the_store", "url", "relative"])
def test_an_authority_that_is_not_independent_or_local_is_refused(
    world: World, tmp_path: Path, where: str
) -> None:
    remote = {
        "inside_workspace": str(make_authority(world.workspace / "pin.git")),
        "is_the_store": str(world.store.location),
        "url": "https://github.com/bartoszkobylinski/lovverk-forarbeider.git",
        "relative": "authority/witness.git",
    }[where]
    moved = world.bootstrap.model_copy(
        update={"authority": AuthorityLocator(remote=remote, ref=REF)}
    )
    raw = canonical_text(moved).encode()
    log = TrustedLog(raw, sign(world.owner, raw, LOG_NAMESPACE), world.root)

    assert world.current(log=log).status == "authority_not_independent"
    refused(lambda: world.advance(1, log=log), "checkpoint_authority_not_independent")


def test_a_store_inside_the_workspace_is_not_independent(world: World) -> None:
    inner = world.workspace / "store"
    shutil.copytree(world.store.location, inner)

    assert world.current(store=CheckpointStore(inner)).status == "authority_not_independent"


@pytest.mark.parametrize("setting", ["receive.denyNonFastForwards", "receive.denyDeletes"])
def test_an_authority_that_allows_a_rewind_or_a_deletion_is_not_monotonic(
    world: World, setting: str
) -> None:
    git(world.bare, "config", setting, "false")

    assert world.current().status == "authority_not_monotonic"
    refused(lambda: world.advance(1), "checkpoint_authority_not_monotonic")


# --- the eleven attacks --------------------------------------------------------------------------


def test_a_store_rolled_back_with_its_witness_is_not_current(world: World) -> None:
    """Red 1: the AR5 witness and its store restored together verify; here the pin refuses."""
    world.advance(1)
    saved = snapshot(world.store.location, "t1")
    world.advance(2)
    restore(world.store.location, saved)

    verdict = world.current()

    assert verdict.status == "store_rolled_back"
    assert verdict.pinned is not None and verdict.pinned.sequence == 2
    refused(lambda: world.advance(3), "checkpoint_store_rolled_back")
    refused(lambda: require_current(world.witnessing()), "checkpoint_store_rolled_back")


def test_two_stores_with_one_key_and_one_log_never_both_become_authoritative(
    world: World,
) -> None:
    """Red 2: two copies of the store, the same witness key and the same log id."""
    world.advance(1)
    clone = CheckpointStore(snapshot(world.store.location, "clone"))
    world.advance(2)

    refused(lambda: world.advance(20, store=clone), "checkpoint_store_rolled_back")
    assert world.current().status == "verified"
    assert world.current(store=clone).status == "store_rolled_back"


def test_a_competing_signed_successor_is_conflicting_never_current(world: World) -> None:
    """Red 3: two validly signed successors of one checkpoint; the pin names one."""
    world.advance(1)
    head = world.current().pinned
    assert head is not None
    clone = CheckpointStore(snapshot(world.store.location, "clone"))
    world.advance(2)
    competing = successor(head, entry(20))
    assert clone.write(log_id_of(world.bootstrap), 2, signed(world, competing))

    assert world.current(store=clone).status == "diverged"
    assert standing(world.witnessing(), signed(world, competing)) == "conflicting"
    refused(lambda: world.advance(3, store=clone), "checkpoint_diverged")


def test_a_stale_but_correctly_signed_checkpoint_is_stale_never_current(world: World) -> None:
    """Red 4: a genuine older checkpoint is consistent, and still not the current one."""
    first = world.advance(1).checkpoint
    newest = world.advance(2).checkpoint

    assert standing(world.witnessing(), signed(world, first)) == "stale"
    assert standing(world.witnessing(), signed(world, newest)) == "current"
    assert standing(world.witnessing(), signed(world, successor(newest, entry(3)))) == "unpinned"
    assert standing(world.witnessing(), signed(world, first, world.bot)) == "invalid"


def test_a_missing_pin_is_refused_and_cannot_be_deleted(world: World) -> None:
    """Red 5: the AR5 pin is optional; here a pin is required and the authority keeps it."""
    world.advance(1)
    pushed = git(client(world), "push", str(world.bare), f":{REF}")

    assert "deletion prohibited" in pushed
    assert world.current().status == "verified"
    shutil.rmtree(world.bare / "refs")
    (world.bare / "refs" / "heads").mkdir(parents=True)
    (world.bare / "packed-refs").unlink(missing_ok=True)
    assert world.current().status == "pin_missing"
    refused(lambda: world.advance(2), "checkpoint_pin_missing")


def test_a_pin_rewound_or_forked_by_a_push_is_refused_by_the_authority(world: World) -> None:
    """Red 6a: forced pushes of an older pin or a sibling never land."""
    first = world.advance(1)
    old_tip = git(world.bare, "rev-parse", f"{REF}~1").strip()
    world.advance(2)
    tip = world.tip()

    rewound = git(client(world), "push", str(world.bare), f"+{old_tip}:{REF}")
    sibling = push_pin(world, signed(world, successor(first.checkpoint, entry(9))), old_tip, True)

    assert "non-fast-forward" in rewound and "non-fast-forward" in sibling
    assert world.tip() == tip
    assert world.current().status == "verified"


@pytest.mark.parametrize(
    "pin",
    ["replayed_older", "garbage", "bot_signed", "unlisted_witness", "skipping", "extra_file"],
)
def test_a_fast_forward_pin_that_is_not_the_next_checkpoint_is_invalid(
    world: World, pin: str
) -> None:
    """Red 6b: a writer of the authority replays, corrupts or forges the pin; it fails closed."""
    chain = chain_of(world, 3)
    world.advance(1)
    world.advance(2)
    raw = {
        "replayed_older": signed(world, chain[1]),
        "garbage": b"{}",
        "bot_signed": signed(world, chain[3], world.bot),
        "unlisted_witness": signed(world, chain[3], world.second_witness),
        "skipping": signed(world, successor(chain[3], entry(4))),
        "extra_file": signed(world, chain[3]),
    }[pin]
    extra = b"more\n" if pin == "extra_file" else None

    pushed = push_commit(world, pin_commit(world, raw, world.tip(), extra))

    assert "rejected" not in pushed
    assert world.current().status == "pin_invalid"
    refused(lambda: world.advance(3), "checkpoint_pin_invalid")
    refused(lambda: require_current(world.witnessing()), "checkpoint_pin_invalid")


def test_a_replayed_pair_of_older_linked_checkpoints_is_invalid(world: World) -> None:
    """Red 6b: genesis then checkpoint 1, replayed as two fast-forward commits, link to each
    other; only the whole lineage shows that they are not the next checkpoint."""
    chain = chain_of(world, 2)
    world.advance(1)
    world.advance(2)
    first = pin_commit(world, signed(world, chain[0]), world.tip())

    pushed = push_commit(world, pin_commit(world, signed(world, chain[1]), first))

    assert "rejected" not in pushed
    assert world.current().status == "pin_invalid"


def test_a_forked_signed_successor_pushed_as_a_fast_forward_is_invalid(world: World) -> None:
    """D: a clone's own signed history (one key) pushed on top of the pin never becomes it."""
    world.advance(1)
    clone = CheckpointStore(snapshot(world.store.location, "fork"))
    world.advance(2)
    log_id = log_id_of(world.bootstrap)
    forked = [chain_of(world, 1)[1]]
    for n in (20, 30):
        forked.append(successor(forked[-1], entry(n)))
        assert clone.write(log_id, forked[-1].sequence, signed(world, forked[-1]))

    pushed = push_pin(world, signed(world, forked[2]), world.tip())

    assert "rejected" not in pushed
    assert world.current().status == "pin_invalid"
    assert world.current(store=clone).status == "pin_invalid"


def test_a_tampered_bootstrap_under_the_owners_signature_is_invalid(world: World) -> None:
    tampered = world.bootstrap.model_copy(update={"witness_keys": ("SHA256:" + "B" * 43,)})
    log = TrustedLog(canonical_text(tampered).encode(), world.signature, world.root)

    verdict = world.current(log=log)

    assert verdict.status == "bootstrap_invalid"
    assert verdict.reasons == ("bootstrap_signature_invalid",)


def test_a_pin_rewound_by_the_authoritys_administrator_is_behind_a_known_checkpoint(
    world: World,
) -> None:
    """Red 6c: outside receive-pack the authority can be rewound; a known checkpoint catches it."""
    world.advance(1)
    known = signed(world, world.advance(2).checkpoint)
    git(world.bare, "update-ref", REF, f"{REF}~1")

    assert world.current().status == "pending_advance"
    assert world.current(known=known).status == "pin_behind_known"
    refused(lambda: world.advance(3, known=known), "checkpoint_pin_behind_known")


def test_a_known_checkpoint_outside_the_history_diverges(world: World) -> None:
    world.advance(1)
    stranger = signed(world, successor(chain_of(world, 0)[0], entry(77)))

    verdict = world.current(known=stranger)

    assert verdict.status == "diverged"
    assert verdict.reasons == ("known_checkpoint_not_in_history",)


@pytest.mark.parametrize("defect", ["gap", "reordered", "forged_root", "other_log"])
def test_an_inconsistent_consistency_proof_never_links(world: World, defect: str) -> None:
    """Red 7: a proof with a gap, out of order, with a forged root or of another log."""
    chain = chain_of(world, 4)
    forged = chain[2].model_copy(update={"root_sha256": "f" * 64})
    proof = {
        "gap": [chain[2], chain[4]],
        "reordered": [chain[3], chain[2], chain[4]],
        "forged_root": [forged, chain[3], chain[4]],
        "other_log": [c.model_copy(update={"log_id": "0" * 64}) for c in chain[2:]],
    }[defect]

    assert consistent(chain[1], chain[4], chain[2:])
    assert not consistent(chain[1], chain[4], proof)


def test_a_signed_but_inconsistent_store_checkpoint_is_invalid(world: World) -> None:
    world.advance(1)
    forged = chain_of(world, 2)[2].model_copy(update={"root_sha256": "f" * 64})
    assert world.store.write(log_id_of(world.bootstrap), 2, signed(world, forged))

    verdict = world.current()

    assert verdict.status == "store_invalid"
    assert verdict.reasons == ("checkpoint_inconsistent",)


def test_a_crash_between_the_store_write_and_the_pin_keeps_the_last_trusted_pin(
    world: World,
) -> None:
    """Red 8: the process dies after the store write, before the pin moves."""
    world.advance(1)
    tip = world.tip()

    assert crash_in_child(world, 2) == 17
    assert world.tip() == tip
    assert world.current().status == "pending_advance"
    refused(lambda: require_current(world.witnessing()), "checkpoint_pending_advance")
    assert world.advance(2).status == "completed"
    assert world.current().status == "verified"
    assert world.advance(2).status == "already_applied"


def test_a_pending_checkpoint_is_completed_before_another_entry(world: World) -> None:
    world.advance(1)
    assert crash_in_child(world, 2) == 17

    outcome = world.advance(3)

    assert outcome.status == "advanced"
    assert outcome.checkpoint.sequence == 3
    assert outcome.checkpoint.previous_sha256 is not None
    assert world.current().status == "verified"


def test_concurrent_writer_processes_on_one_store_are_serialized(world: World) -> None:
    """Red 9a: four processes advance one store at once; every entry lands once, in order."""
    outcomes = in_children([(world, n) for n in range(1, 5)])

    verdict = world.current()
    assert outcomes == ["advanced"] * 4
    assert verdict.status == "verified"
    assert verdict.pinned is not None and verdict.pinned.sequence == 4


def test_concurrent_writer_processes_on_cloned_stores_let_one_win(world: World) -> None:
    """Red 9b: two forks of one store race to the authority; exactly one pin lands."""
    world.advance(1)
    clones = [
        World(**{**world.__dict__, "store": CheckpointStore(snapshot(world.store.location, n))})
        for n in ("a", "b")
    ]

    outcomes = in_children([(clones[0], 10), (clones[1], 20)])

    statuses = [clone.current().status for clone in clones]
    winner = outcomes.index("advanced")
    loser = 1 - winner
    # the loser either lost the push (its successor is now contradicted) or read the new pin
    assert (outcomes[loser], statuses[loser]) in {
        ("checkpoint_authority_moved", "diverged"),
        ("checkpoint_store_rolled_back", "store_rolled_back"),
    }
    assert statuses[winner] == "verified"
    pinned = clones[winner].current().pinned
    assert pinned is not None and pinned.sequence == 2


def test_an_unavailable_authority_denies_the_advance_and_any_dependent(world: World) -> None:
    """Red 10: an authority that cannot be read moves nothing and makes nothing current."""
    world.advance(1)
    before = tree(world.store.location)
    hidden = world.bare.with_name("gone.git")
    world.bare.rename(hidden)

    assert world.current().status == "authority_unavailable"
    refused(lambda: world.advance(2), "checkpoint_authority_unavailable")
    refused(lambda: require_current(world.witnessing()), "checkpoint_authority_unavailable")
    assert tree(world.store.location) == before
    hidden.rename(world.bare)
    assert world.current().status == "verified"


def test_an_authority_lost_after_the_store_write_leaves_a_pending_checkpoint(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.advance(1)
    tip = world.tip()
    monkeypatch.setattr(witness_checkpoint.git_checkpoint, "advance", lambda *_: "unknown")

    refused(lambda: world.advance(2), "checkpoint_authority_unavailable")
    monkeypatch.undo()

    assert world.tip() == tip
    assert world.current().status == "pending_advance"
    assert world.advance(2).status == "completed"


def test_a_restart_reads_the_independently_persisted_pin(world: World) -> None:
    """Red 11: a new process, new objects, the same pin; a rolled-back store is still caught."""
    world.advance(1)
    saved = snapshot(world.store.location, "t1")
    world.advance(2)

    assert current_in_child(world) == ("verified", 2)
    restore(world.store.location, saved)
    assert current_in_child(world) == ("store_rolled_back", 2)


# --- keys, read-only, determinism ----------------------------------------------------------------


def test_a_revoked_witness_key_invalidates_its_pin(tmp_path: Path) -> None:
    world = make_world(tmp_path)
    world.advance(1)
    lines = (world.tmp / "root" / "policy-signers").read_text().splitlines()
    root = trust_root(world.tmp / "root-revoked", lines, (world.witness_key,))
    log = TrustedLog(world.log.bootstrap, world.signature, root)

    assert world.current(log=log).status == "pin_invalid"


def test_a_checkpoint_signed_by_an_unlisted_witness_key_is_refused(world: World) -> None:
    other = world.bootstrap.model_copy(update={"witness_keys": ("SHA256:" + "A" * 43,)})
    raw = canonical_text(other).encode()
    log = TrustedLog(raw, sign(world.owner, raw, LOG_NAMESPACE), world.root)

    assert world.current(log=log).status == "pin_invalid"


def test_the_store_never_replaces_a_checkpoint(world: World) -> None:
    log_id = log_id_of(world.bootstrap)
    original = world.store.history(log_id)[0]

    assert not world.store.write(log_id, 0, b"replacement")
    assert world.store.history(log_id) == (original,)


def test_verification_writes_nothing_and_repeats_identically(world: World) -> None:
    world.advance(1)
    before = (tree(world.bare), tree(world.store.location))

    verdicts = {world.current() for _ in range(3)}
    standing(world.witnessing(), signed(world, chain_of(world, 1)[1]))

    assert len(verdicts) == 1
    assert (tree(world.bare), tree(world.store.location)) == before


def test_the_pin_moves_only_from_the_commit_the_writer_verified(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D: another store's successor lands between our verification and our push; ours is refused."""
    world.advance(1)
    rival = World(
        **{**world.__dict__, "store": CheckpointStore(snapshot(world.store.location, "r"))}
    )
    real = witness_checkpoint._store_write

    def race(*args: object) -> None:
        real(*args)  # type: ignore[arg-type]
        monkeypatch.setattr(witness_checkpoint, "_store_write", real)
        assert rival.advance(20).status == "advanced"

    monkeypatch.setattr(witness_checkpoint, "_store_write", race)

    refused(lambda: world.advance(2), "checkpoint_authority_moved")
    assert rival.current().status == "verified"
    assert world.current().status == "diverged"
    pinned = rival.current().pinned
    assert pinned is not None and pinned.entry_sha256 == entry(20)


# --- the Git capability --------------------------------------------------------------------------


@pytest.mark.structural
def test_only_the_checkpoint_module_imports_the_checkpoint_git() -> None:
    source = Path(lovspor.__file__).parent
    importers = set()
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names += [alias.name for alias in node.names]
            if any(name.split(".")[:2] == ["lovspor", "git_checkpoint"] for name in names):
                importers.add(path.relative_to(source).as_posix())
    assert importers == {"forarbeider/witness_checkpoint.py"}


@pytest.mark.parametrize(
    "argv",
    [
        ("push", "--porcelain", "/a/witness.git", "+" + "1" * 40 + ":" + REF),
        ("push", "--force", "/a/witness.git", "1" * 40 + ":" + REF),
        ("push", "--porcelain", "/a/witness.git", ":" + REF),
        ("push", "--porcelain", "/a/witness.git", "1" * 40 + ":refs/heads/main"),
        ("push", "--porcelain", "https://github.com/x/y.git", "1" * 40 + ":" + REF),
        ("push", "--porcelain", "git@github.com:x/y.git", "1" * 40 + ":" + REF),
        ("push", "--porcelain", "a/witness.git", "1" * 40 + ":" + REF),
        ("ls-remote", "/a/witness.git", "refs/witness/x"),
        ("update-ref", REF, "1" * 40),
        ("config", "--local", "receive.denyNonFastForwards", "false"),
    ],
)
def test_the_checkpoint_git_has_no_force_delete_network_or_write_shape(
    argv: tuple[str, ...],
) -> None:
    assert not git_checkpoint.allowed(argv)
    with pytest.raises(git_checkpoint.GitCheckpointError):
        git_checkpoint.run(Path("/"), argv)
