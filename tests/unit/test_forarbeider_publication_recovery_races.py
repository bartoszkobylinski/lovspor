"""AR4b-2 races: a remote that moves while recovery reads it, and concurrent recovery (#738).

Every scenario runs against the AR4b-1 sandbox only (the in-memory
SimulatedGitHub for path A, a DisposableRemote for path B) and changes the
remote for real, mid-read: a foreign commit, a rewritten ``main`` or a deleted
ref. Concurrency is real too: a second thread, or a second process holding the
workspace lock, races the first; nothing here mocks a helper's return value
to stand for the race.
"""

import fcntl
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lovspor.forarbeider import publication_attempts, publication_path_b, publication_receipts
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_authorize import AdapterFacts, AuthorizationContext, authorize
from lovspor.forarbeider.publication_execute import (
    Simulation,
    execute,
    next_attempt_id,
    publish_authorized,
)
from lovspor.forarbeider.publication_remote_recovery import RecoveryOutcome, recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash, SimulatedGitHub
from lovspor.forarbeider.release_limits import LimitsRequest, verify_limits
from tests.unit.forarbeider_execution_support import (
    APP,
    LEDGER_ID,
    NOW,
    KeySigner,
    Rig,
    batch_usage,
    identified_workspace,
    make_rig,
    reserve,
    seeded_ledger,
)
from tests.unit.forarbeider_publication_support import commit_files, git
from tests.unit.forarbeider_release_control_support import BOT

LOCK_NAME = "workspace.lock"
WAIT = 20.0


@pytest.fixture
def rig_a(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


@pytest.fixture
def rig_b(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "b", "B")


def endpoint(rig: Rig, **changes: Any) -> SimulatedGitHub:
    fields: dict[str, Any] = {"head_oid": rig.base, "identity": APP, "now": NOW}
    return SimulatedGitHub(**(fields | changes))


def sim_a(github: SimulatedGitHub) -> Simulation:
    return Simulation(clock=lambda: NOW, transport=github)


def sim_b(rig: Rig) -> Simulation:
    return Simulation(clock=lambda: NOW, remote=rig.remote, signer=KeySigner(BOT, rig.bot))


def states(rig: Rig, workspace: Path | None = None) -> list[str]:
    found = publication_attempts.records(workspace or rig.workspace, rig.transaction.transaction_id)
    return [item.state for item in found]


def receipt_bytes(workspace: Path) -> dict[str, bytes]:
    folder = workspace / "receipts"
    if not folder.exists():
        return {}
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def published_lost(rig: Rig) -> SimulatedGitHub:
    """Path A: our commit landed, the response was lost; the latch is set by the outcome."""
    github = endpoint(rig, fault="lost")
    assert execute(rig.request(), sim_a(github)).status == "outcome_unknown"
    github.fault = None
    return github


def foreign(github: SimulatedGitHub) -> str:
    return github.advance({"README.md": b"someone else\n"}, ("manual", ""), (None, False))


def unrelated_root(rig: Rig) -> str:
    lonely = rig.tmp / "lonely"
    subprocess.run(["git", "init", "--quiet", str(lonely)], check=True)
    root = commit_files(lonely, {"README.md": b"rewritten\n"}, "root")
    git(lonely, "push", "--quiet", str(rig.bare), f"{root}:refs/heads/rewritten")
    return root


def once(action: Callable[[], object]) -> Callable[[], None]:
    """``action`` on the first call only."""
    done: list[bool] = []

    def run() -> None:
        if not done:
            done.append(True)
            action()

    return run


def assert_halted_unverifiable(
    outcome: RecoveryOutcome, rig: Rig, workspace: Path, before: list[str]
) -> None:
    assert outcome.classification == "halted_history_unverifiable"
    assert outcome.receipt is None
    assert outcome.reasons in {("remote_head_moved_during_read",), ("remote_main_unreadable",)}
    assert receipt_bytes(workspace) == {}
    assert publication_attempts.latched(workspace)
    assert states(rig, workspace) == before


# --- remote HEAD consistency -------------------------------------------------------------------


def test_a_head_moving_from_ours_to_foreign_during_traversal_halts_without_a_receipt(
    rig_a: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = published_lost(rig_a)
    ours = github.head_oid
    original = SimulatedGitHub.commit
    push_foreign = once(lambda: foreign(github))

    def commit(self: SimulatedGitHub, oid: str) -> Any:
        found = original(self, oid)
        push_foreign()
        return found

    monkeypatch.setattr(SimulatedGitHub, "commit", commit)

    before = states(rig_a)
    outcome = recover(rig_a.request(), sim_a(github))

    assert_halted_unverifiable(outcome, rig_a, rig_a.workspace, before)
    assert outcome.reasons == ("remote_head_moved_during_read",)
    assert len(github.calls) == 1
    assert github.commit(ours) is not None and github.head_oid != ours


def test_b_head_rewritten_during_traversal_halts_without_a_receipt(
    rig_b: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert execute(rig_b.request(), sim_b(rig_b)).status == "published"
    root = unrelated_root(rig_b)
    original = publication_path_b.remote_object
    rewrite = once(lambda: git(rig_b.bare, "update-ref", "refs/heads/main", root))

    def remote_object(bare: Path, kind: str, oid: str) -> bytes:
        found = original(bare, kind, oid)
        rewrite()
        return found

    monkeypatch.setattr(publication_path_b, "remote_object", remote_object)

    before = states(rig_b)
    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert_halted_unverifiable(outcome, rig_b, rig_b.workspace, before)
    assert outcome.reasons == ("remote_head_moved_during_read",)
    assert git(rig_b.bare, "rev-parse", "main") == root


def test_a_head_moving_when_history_was_empty_halts_and_keeps_the_attempt_open(
    rig_a: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_append = publication_attempts.append

    def die(workspace: Path, record: Any) -> None:
        original_append(workspace, record)
        if record.state == "prepared_to_send":
            raise SimulatedCrash

    github = endpoint(rig_a)
    monkeypatch.setattr(publication_attempts, "append", die)
    with pytest.raises(SimulatedCrash):
        execute(rig_a.request(), sim_a(github))
    monkeypatch.setattr(publication_attempts, "append", original_append)
    assert github.calls == []
    original = SimulatedGitHub.head
    push_foreign = once(lambda: foreign(github))

    def head(self: SimulatedGitHub, repository: str, branch: str) -> str | None:
        found = original(self, repository, branch)
        push_foreign()
        return found

    monkeypatch.setattr(SimulatedGitHub, "head", head)

    before = states(rig_a)
    outcome = recover(rig_a.request(), sim_a(github))

    assert_halted_unverifiable(outcome, rig_a, rig_a.workspace, before)
    assert outcome.reasons == ("remote_head_moved_during_read",)
    assert states(rig_a) == ["prepared_to_send"]
    assert github.calls == []


def test_b_a_deleted_main_on_the_final_check_fails_closed(
    rig_b: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert execute(rig_b.request(), sim_b(rig_b)).status == "published"
    original = publication_path_b.remote_object
    delete = once(lambda: git(rig_b.bare, "update-ref", "-d", "refs/heads/main"))

    def remote_object(bare: Path, kind: str, oid: str) -> bytes:
        found = original(bare, kind, oid)
        delete()
        return found

    monkeypatch.setattr(publication_path_b, "remote_object", remote_object)

    before = states(rig_b)
    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert_halted_unverifiable(outcome, rig_b, rig_b.workspace, before)
    assert outcome.reasons == ("remote_main_unreadable",)


def test_b_a_failing_final_head_read_fails_closed(
    rig_b: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert execute(rig_b.request(), sim_b(rig_b)).status == "published"
    original = publication_path_b.remote_head
    calls: list[Path] = []

    def remote_head(bare: Path) -> str | None:
        calls.append(bare)
        if len(calls) > 1:
            git(rig_b.bare, "config", "core.repositoryformatversion", "99")
        return original(bare)

    monkeypatch.setattr(publication_path_b, "remote_head", remote_head)
    before = states(rig_b)

    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert len(calls) == 2
    assert_halted_unverifiable(outcome, rig_b, rig_b.workspace, before)
    assert outcome.reasons == ("remote_main_unreadable",)


def test_a_stable_history_reads_the_head_twice_and_still_recovers(
    rig_a: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = published_lost(rig_a)
    original = SimulatedGitHub.head
    reads: list[str | None] = []

    def head(self: SimulatedGitHub, repository: str, branch: str) -> str | None:
        found = original(self, repository, branch)
        reads.append(found)
        return found

    monkeypatch.setattr(SimulatedGitHub, "head", head)

    outcome = recover(rig_a.request(), sim_a(github))

    assert outcome.classification == "published_authorized"
    # Two reads bound the history walk; AR6b's attestation reader finds the commit from a third.
    assert reads == [github.head_oid, github.head_oid, github.head_oid]
    assert len(github.calls) == 1


def test_recovery_into_two_workspaces_writes_identical_receipt_bytes(rig_a: Rig) -> None:
    github = published_lost(rig_a)
    twin = identified_workspace(rig_a, "twin")
    tid = rig_a.transaction.transaction_id
    for record in publication_attempts.records(rig_a.workspace, tid):
        publication_attempts.append(twin, record)

    recover(rig_a.request(), sim_a(github))
    recover(rig_a.request(workspace=twin), sim_a(github))
    again = recover(rig_a.request(), sim_a(github))

    assert again.classification == "already_receipted"
    assert receipt_bytes(twin) == receipt_bytes(rig_a.workspace)
    assert len(receipt_bytes(twin)) == 1


# --- concurrent recovery -----------------------------------------------------------------------


class Pause:
    """Stops the thread named ``who`` at its first call of a patched function until released."""

    def __init__(self, who: str) -> None:
        self.who = who
        self.reached = threading.Event()
        self.release = threading.Event()

    def wrap(self, original: Callable[..., Any]) -> Callable[..., Any]:
        def paused(*args: Any, **kwargs: Any) -> Any:
            if threading.current_thread().name == self.who and not self.reached.is_set():
                self.reached.set()
                assert self.release.wait(WAIT)
            return original(*args, **kwargs)

        return paused


def in_thread(name: str, run: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    result: list[Any] = []

    def target() -> None:
        try:
            result.append(run())
        except BaseException as error:  # reported to the test
            result.append(error)

    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread, result


def assert_locked(action: Callable[[], Any]) -> None:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    assert raised.value.reason is RefusalReason.CORPUS_LOCKED
    assert raised.value.detail == "workspace"


def test_two_concurrent_recoveries_of_one_transaction_write_one_receipt(
    rig_a: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = published_lost(rig_a)
    pause = Pause("first")
    monkeypatch.setattr(publication_receipts, "head", pause.wrap(publication_receipts.head))

    thread, result = in_thread("first", lambda: recover(rig_a.request(), sim_a(github)))
    assert pause.reached.wait(WAIT)
    assert_locked(lambda: recover(rig_a.request(), sim_a(github)))
    assert receipt_bytes(rig_a.workspace) == {}
    pause.release.set()
    thread.join(WAIT)

    assert result[0].classification == "published_authorized"
    written = receipt_bytes(rig_a.workspace)
    assert len(written) == 1
    retry = recover(rig_a.request(), sim_a(github))
    assert retry.classification == "already_receipted"
    assert retry.receipt == result[0].receipt
    assert receipt_bytes(rig_a.workspace) == written
    assert states(rig_a) == ["prepared_to_send", "outcome_unknown", "published"]
    assert len(github.calls) == 1


def test_concurrent_recoveries_sharing_one_receipt_chain_link_it_in_turn(
    rig_a: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AR5d-1: a workspace is anchored by the one witness that anchored its identity, so the
    # second transaction runs on rig_a's keys and witness, with a ledger of its own.
    other = make_rig(tmp_path / "other", "B", share=rig_a, one_witness=True)
    assert other.transaction.transaction_id != rig_a.transaction.transaction_id
    shared = rig_a.workspace
    limits = verify_limits(
        LimitsRequest(other.inputs.policy.policy, other.inputs.limits, NOW), other.root
    )
    own = f"{LEDGER_ID}-other"
    entries = reserve(limits, other.transaction, batch_usage(other.transaction), own)
    ledger = seeded_ledger(other, "other", entries)
    github = endpoint(rig_a)
    assert execute(rig_a.request(), sim_a(github)).status == "published"
    assert execute(other.request(workspace=shared, ledger=ledger), sim_b(other)).status == (
        "published"
    )
    pause = Pause("first")
    monkeypatch.setattr(publication_receipts, "head", pause.wrap(publication_receipts.head))

    thread, result = in_thread("first", lambda: recover(rig_a.request(), sim_a(github)))
    assert pause.reached.wait(WAIT)
    assert_locked(lambda: recover(other.request(workspace=shared, ledger=ledger), sim_b(other)))
    pause.release.set()
    thread.join(WAIT)
    later = recover(other.request(workspace=shared, ledger=ledger), sim_b(other))

    assert result[0].classification == "published_authorized"
    assert later.classification == "published_authorized"
    chain = publication_receipts.receipts(shared)
    assert [(r.sequence, r.transaction_id) for r in chain] == [
        (1, rig_a.transaction.transaction_id),
        (2, other.transaction.transaction_id),
    ]
    assert len(github.calls) == 1


def test_recovery_refuses_while_an_execution_is_sending(
    rig_a: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig_a)
    pause = Pause("executor")
    monkeypatch.setattr(SimulatedGitHub, "post", pause.wrap(SimulatedGitHub.post))

    thread, result = in_thread("executor", lambda: execute(rig_a.request(), sim_a(github)))
    assert pause.reached.wait(WAIT)
    assert_locked(lambda: recover(rig_a.request(), sim_a(github)))
    assert states(rig_a) == ["prepared_to_send"]
    pause.release.set()
    thread.join(WAIT)

    assert result[0].status == "published"
    assert states(rig_a) == ["prepared_to_send", "published"]
    assert len(github.calls) == 1
    assert not publication_attempts.latched(rig_a.workspace)


def test_execution_refuses_without_sending_while_recovery_reads(
    rig_a: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = published_lost(rig_a)
    pause = Pause("recoverer")
    monkeypatch.setattr(SimulatedGitHub, "commit", pause.wrap(SimulatedGitHub.commit))

    thread, result = in_thread("recoverer", lambda: recover(rig_a.request(), sim_a(github)))
    assert pause.reached.wait(WAIT)
    assert_locked(lambda: execute(rig_a.request(), sim_a(github)))
    pause.release.set()
    thread.join(WAIT)

    assert result[0].classification == "published_authorized"
    assert len(github.calls) == 1


def test_a_lock_held_by_another_process_refuses_recovery_and_writes_nothing(rig_a: Rig) -> None:
    github = published_lost(rig_a)
    lock = rig_a.workspace / LOCK_NAME
    holder = (
        "import fcntl, os, sys\n"
        "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "print('held', flush=True)\n"
        "sys.stdin.read()\n"
    )
    child = subprocess.Popen(  # this interpreter, a fixed script
        [sys.executable, "-c", holder, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "held"
        before = states(rig_a)
        assert_locked(lambda: recover(rig_a.request(), sim_a(github)))
        assert receipt_bytes(rig_a.workspace) == {}
        assert states(rig_a) == before
    finally:
        child.communicate("", timeout=WAIT)

    assert recover(rig_a.request(), sim_a(github)).classification == "published_authorized"
    assert len(github.calls) == 1


def test_a_symlinked_workspace_lock_is_refused(rig_a: Rig) -> None:
    github = published_lost(rig_a)
    target = rig_a.tmp / "elsewhere.lock"
    target.write_bytes(b"")
    (rig_a.workspace / LOCK_NAME).unlink()
    (rig_a.workspace / LOCK_NAME).symlink_to(target)

    with pytest.raises(ForarbeiderRefusedError) as raised:
        recover(rig_a.request(), sim_a(github))

    assert raised.value.reason is RefusalReason.CHECKOUT_UNSAFE
    assert receipt_bytes(rig_a.workspace) == {}


def test_genuine_evidence_is_not_spent_while_the_workspace_is_held(rig_a: Rig) -> None:
    github = endpoint(rig_a)
    head = lambda: github.head(github.repository, "main")  # noqa: E731
    context = AuthorizationContext(
        rig_a.inputs,
        rig_a.root,
        rig_a.clone,
        head,
        NOW,
        next_attempt_id(rig_a.request()),
        "simulation",
        AdapterFacts(app=github.identity),
        rig_a.anchoring(),
        rig_a.workspace,
        rig_a.ledger,
        rig_a.quarantine,
    )
    evidence = authorize(context)
    assert evidence.decision == "simulation_only", evidence.denied_by

    with publication_attempts.workspace_lock(rig_a.workspace):
        assert_locked(lambda: publish_authorized(evidence, rig_a.request(), sim_a(github)))

    assert github.calls == []
    assert states(rig_a) == []
    assert publish_authorized(evidence, rig_a.request(), sim_a(github)).status == "published"


@pytest.mark.parametrize("fault", ["hard_link", "group_readable"])
def test_a_shared_workspace_lock_file_is_refused(rig_a: Rig, fault: str) -> None:
    lock = rig_a.workspace / LOCK_NAME
    lock.write_bytes(b"")
    if fault == "hard_link":
        lock.chmod(0o600)
        os.link(lock, rig_a.tmp / "second-name.lock")
    else:
        lock.chmod(0o640)
    github = endpoint(rig_a)

    with pytest.raises(ForarbeiderRefusedError) as raised:
        execute(rig_a.request(), sim_a(github))

    assert raised.value.reason is RefusalReason.CHECKOUT_UNSAFE
    assert raised.value.detail == "workspace_lock"
    assert github.calls == []
    assert states(rig_a) == []


def test_a_lock_file_replaced_before_it_is_locked_is_not_held(
    rig_a: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = rig_a.workspace / LOCK_NAME
    real = fcntl.flock

    def replaced_then_locked(descriptor: int, operation: int) -> None:
        lock.unlink()
        lock.write_bytes(b"")
        lock.chmod(0o600)
        real(descriptor, operation)

    monkeypatch.setattr(
        publication_attempts, "fcntl", SimpleNamespace(flock=replaced_then_locked, **_FLAGS)
    )
    github = endpoint(rig_a)

    assert_locked(lambda: execute(rig_a.request(), sim_a(github)))
    assert github.calls == []
    assert states(rig_a) == []


_FLAGS = {"LOCK_EX": fcntl.LOCK_EX, "LOCK_NB": fcntl.LOCK_NB}
