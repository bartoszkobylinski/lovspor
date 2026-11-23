"""AR5d-1 review blocker: one anchored identity, one writer (#738, #1013, PR #1020).

A workspace identity names one lineage. A byte-for-byte copy of the workspace,
or of its identity file, holds the same anchored identity, but its workspace
lock is another file, and the attempt records it writes are scoped per
transaction. So four protections are told apart here, each tested alone:

- **same-transaction exactly-once**: two workspaces attempting one
  transaction meet in its one attempt chain at the witness;
- **budget serialization**: a transaction with no reservation in the ledger
  is denied, whatever the workspace;
- **remote expected-head protection**: a second commit on a moved remote is
  refused by the remote itself;
- **workspace writer exclusivity**: two copies of one identity, each with its
  own transaction, its own reserved ledger and its own remote, so that none of
  the three protections above applies, still authorize one writer only.
"""

import hashlib
import multiprocessing
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import publication_execute, workspace_writer, workspace_writer_models
from lovspor.forarbeider.control_plane_anchor import AnchorQuery, verify, workspace_scope
from lovspor.forarbeider.control_plane_chains import workspace_chain
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_execute import (
    ExecutionRequest,
    Simulation,
    execute,
)
from lovspor.forarbeider.publication_ledger import BudgetLedger
from lovspor.forarbeider.publication_sandbox import SimulatedCrash, SimulatedGitHub
from lovspor.forarbeider.release_limits import LimitsRequest, verify_limits
from lovspor.forarbeider.workspace_writer_models import LEASE, SEND_MARGIN
from tests.unit.forarbeider_execution_support import (
    APP,
    LEDGER_ID,
    NOW,
    Rig,
    batch_usage,
    make_rig,
    reserve,
    seeded_ledger,
)


def endpoint(head: str) -> SimulatedGitHub:
    return SimulatedGitHub(head_oid=head, identity=APP, now=NOW)


def sim(github: SimulatedGitHub) -> Simulation:
    return Simulation(clock=lambda: NOW, transport=github)


def own_ledger(other: Rig, name: str = "own") -> BudgetLedger:
    """A durable ledger of ``other``'s own, holding exactly its transaction's reservation."""
    limits = verify_limits(
        LimitsRequest(other.inputs.policy.policy, other.inputs.limits, NOW), other.root
    )
    entries = reserve(
        limits, other.transaction, batch_usage(other.transaction), f"{LEDGER_ID}-{name}"
    )
    return seeded_ledger(other, name, entries)


def outcome_of(request: ExecutionRequest, github: SimulatedGitHub) -> str:
    try:
        return execute(request, sim(github)).status
    except ForarbeiderRefusedError as error:
        return f"refused:{error.detail}"


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


@pytest.fixture
def copy(rig: Rig, tmp_path: Path) -> Path:
    """``rig``'s workspace copied whole while every workspace family is at its first head."""
    target = tmp_path / "copy"
    shutil.copytree(rig.workspace, target)
    return target


@pytest.fixture
def other(rig: Rig, tmp_path: Path) -> Rig:
    """Another transaction (and its own workspace and identity) on ``rig``'s keys and witness."""
    found = make_rig(tmp_path / "b", "A", share=rig, one_witness=True)
    assert found.transaction.transaction_id != rig.transaction.transaction_id
    return found


# --- the three protections that are not writer exclusivity -----------------------------------


def test_same_transaction_from_two_identities_is_sent_once(rig: Rig, other: Rig) -> None:
    """Exactly-once is the transaction's attempt chain: another identity cannot resend it."""
    first, second = endpoint(rig.base), endpoint(rig.base)
    assert execute(rig.request(), sim(first)).status == "published"

    later = execute(rig.request(workspace=other.workspace), sim(second))

    assert later.status == "denied"
    assert later.evidence is not None
    assert "ar5_control_anchor" in later.evidence.denied_by
    assert (len(first.calls), second.calls) == (1, [])


def test_a_transaction_without_a_reservation_is_denied_in_any_workspace(
    rig: Rig, other: Rig
) -> None:
    """Budget serialization: ``other``'s transaction holds no reservation in the shared ledger."""
    github = endpoint(other.base)

    outcome = execute(other.request(), sim(github))

    assert outcome.status == "denied"
    assert outcome.evidence is not None
    assert "budget" in outcome.evidence.denied_by
    assert github.calls == []


def test_a_moved_remote_is_denied_before_anything_is_sent(rig: Rig, other: Rig) -> None:
    """Remote expected-head protection, at authorization: the remote no longer has the base."""
    assert rig.base == other.base
    shared = endpoint(rig.base)
    assert execute(rig.request(), sim(shared)).status == "published"

    later = execute(other.request(ledger=own_ledger(other)), sim(shared))

    assert later.status == "denied"
    assert later.evidence is not None and later.evidence.denied_by == ("remote_head",)
    assert len(shared.calls) == 1


def test_a_remote_moved_after_authorization_refuses_the_commit_itself(rig: Rig, other: Rig) -> None:
    """Remote expected-head protection, at the remote: the stale ``expectedHeadOid`` is refused."""
    assert rig.base == other.base
    shared = endpoint(rig.base)
    assert execute(rig.request(), sim(shared)).status == "published"
    shared.reported_head = rig.base  # authorization reads the old head; the remote moved

    later = execute(other.request(ledger=own_ledger(other)), sim(shared))

    assert later.status == "refused"
    assert later.attempt is not None and later.attempt.reasons == ("expected_head_mismatch",)
    assert len(shared.commits) == 1


# --- writer exclusivity -----------------------------------------------------------------------


def test_two_copies_of_one_identity_with_different_transactions_authorize_one_writer(
    rig: Rig, copy: Path, other: Rig
) -> None:
    """Different transactions, separate reserved ledgers, separate remotes: still one writer."""
    first, second = endpoint(rig.base), endpoint(other.base)
    assert execute(rig.request(), sim(first)).status == "published"

    late = outcome_of(other.request(workspace=copy, ledger=own_ledger(other)), second)

    assert late.startswith("refused:workspace_writer:"), late
    assert (len(first.calls), second.calls) == (1, [])


def test_the_original_is_fenced_once_its_copy_has_written(rig: Rig, copy: Path, other: Rig) -> None:
    first, second = endpoint(other.base), endpoint(rig.base)
    assert execute(other.request(workspace=copy, ledger=own_ledger(other)), sim(first)).status == (
        "published"
    )

    late = outcome_of(rig.request(), second)

    assert late.startswith("refused:workspace_writer:"), late
    assert (len(first.calls), second.calls) == (1, [])


def _publish_in_process(job: tuple[ExecutionRequest, str, float]) -> tuple[str, int]:
    request, head, start = job
    github = endpoint(head)
    while time.time() < start:
        time.sleep(0.001)
    return outcome_of(request, github), len(github.calls)


def test_two_processes_on_copies_of_one_identity_authorize_one_writer(
    rig: Rig, copy: Path, other: Rig
) -> None:
    start = time.time() + 1.5
    jobs = [
        (rig.request(), rig.base, start),
        (other.request(workspace=copy, ledger=own_ledger(other)), other.base, start),
    ]
    # fork, not spawn: the sealed test evidence is authentic only under this process's seal key
    with multiprocessing.get_context("fork").Pool(2) as pool:
        results: list[Any] = pool.map(_publish_in_process, jobs)

    statuses = [status for status, _ in results]
    assert statuses.count("published") == 1, results
    assert sum(calls for _, calls in results) == 1, results
    loser = next(status for status in statuses if status != "published")
    assert loser.startswith("refused:workspace_writer:"), results


# --- added after the red run: crashes, takeover, fencing -------------------------------------


def clocked(github: SimulatedGitHub, at: datetime) -> Simulation:
    return Simulation(clock=lambda: at, transport=github)


def crash(*_: Any) -> None:
    raise SimulatedCrash


def writer_status(rig: Rig, workspace: Path) -> str:
    authority = rig.anchoring(workspace=workspace)
    scope = workspace_scope(workspace, rig.inputs.settings.repository, authority)
    chain = workspace_chain("workspace_writer", workspace, scope)
    query = AnchorQuery("workspace_writer", rig.inputs.settings.repository, scope, chain)
    return verify(query, authority).status


def kinds(workspace: Path) -> list[str]:
    return [event.kind for event, _ in workspace_writer_models.chain(workspace)]


def test_one_workspace_still_publishes_its_transactions_one_after_another(
    rig: Rig, other: Rig
) -> None:
    first, second = endpoint(rig.base), endpoint(other.base)
    assert execute(rig.request(), sim(first)).status == "published"

    later = execute(other.request(workspace=rig.workspace, ledger=own_ledger(other)), sim(second))

    assert later.status == "published"
    assert kinds(rig.workspace) == ["claim", "release", "claim", "release"]
    assert writer_status(rig, rig.workspace) == "verified"


def test_a_crash_holding_the_claim_holds_every_writer_off_until_its_lease_ends(
    rig: Rig, copy: Path, other: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(publication_execute, "_prepared", crash)
    with pytest.raises(SimulatedCrash):
        execute(rig.request(), sim(endpoint(rig.base)))
    monkeypatch.undo()
    taken = tmp_copy(rig, "taken")  # a copy that holds the crashed claim

    github = endpoint(rig.base)
    held = outcome_of(rig.request(), github)
    held_copy = outcome_of(other.request(workspace=taken, ledger=own_ledger(other)), github)
    behind = outcome_of(other.request(workspace=copy, ledger=own_ledger(other, "x")), github)

    assert held == held_copy == "refused:workspace_writer:claim_held"
    assert behind == "refused:workspace_writer:local_rolled_back"
    assert github.calls == []
    assert kinds(rig.workspace) == ["claim"]


def tmp_copy(rig: Rig, name: str) -> Path:
    target = rig.tmp.parent / name
    shutil.copytree(rig.workspace, target)
    return target


def test_after_the_lease_one_writer_takes_over_and_the_old_holder_is_fenced(
    rig: Rig, other: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(publication_execute, "_prepared", crash)
    with pytest.raises(SimulatedCrash):
        execute(rig.request(), sim(endpoint(rig.base)))
    monkeypatch.undo()
    taken = tmp_copy(rig, "taken")
    later = NOW + LEASE

    takeover = endpoint(other.base)
    request = other.request(workspace=taken, ledger=own_ledger(other))
    took = execute(request, clocked(takeover, later))
    stale = endpoint(rig.base)
    try:
        fenced = execute(rig.request(), clocked(stale, later)).status
    except ForarbeiderRefusedError as error:
        fenced = f"refused:{error.detail}"

    assert took.status == "published"
    assert kinds(taken) == ["claim", "claim", "release"]
    assert fenced == "refused:workspace_writer:local_rolled_back"
    assert (len(takeover.calls), stale.calls) == (1, [])


def test_a_writer_taken_over_while_it_prepares_never_sends(
    rig: Rig, other: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim is checked again right before the send: a takeover since then fences it."""
    taker = endpoint(other.base)
    ledger = own_ledger(other)
    original = publication_execute._attempts_anchored
    taken: list[str] = []

    def takeover(request: ExecutionRequest) -> bool:
        if not taken and request.workspace == rig.workspace:
            taken.append("running")
            moved = tmp_copy(rig, "taken")  # the copy holds the claim just anchored
            later = NOW + LEASE
            outcome = execute(other.request(workspace=moved, ledger=ledger), clocked(taker, later))
            taken[0] = outcome.status
        return original(request)

    monkeypatch.setattr(publication_execute, "_attempts_anchored", takeover)
    stale = endpoint(rig.base)

    late = outcome_of(rig.request(), stale)

    assert taken == ["published"]
    assert late == "refused:workspace_writer:claim_lost:local_rolled_back"
    assert (len(taker.calls), stale.calls) == (1, [])


def test_a_claim_whose_lease_is_nearly_over_never_sends(rig: Rig) -> None:
    nearly_over = NOW + LEASE - SEND_MARGIN + timedelta(seconds=1)
    github = endpoint(rig.base)

    def clock() -> datetime:  # time passes once the claim is made
        return nearly_over if kinds(rig.workspace) else NOW

    try:
        late = execute(rig.request(), Simulation(clock=clock, transport=github))
        status = late.status
    except ForarbeiderRefusedError as error:
        status = f"refused:{error.detail}"

    assert status == "refused:workspace_writer:lease_expiring"
    assert github.calls == []
    assert kinds(rig.workspace) == ["claim", "release"]


def test_a_claim_the_witness_never_anchored_is_completed_then_held_to_its_lease(
    rig: Rig, other: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(*_: Any) -> Any:
        raise ForarbeiderRefusedError(
            RefusalReason.RELEASE_NOT_ELIGIBLE, "anchor_store_unavailable"
        )

    monkeypatch.setattr(workspace_writer, "anchor_pending", down)
    github = endpoint(rig.base)
    first = outcome_of(rig.request(), github)
    monkeypatch.undo()
    pending = writer_status(rig, rig.workspace)

    again = outcome_of(rig.request(), github)
    after = execute(rig.request(), clocked(github, NOW + LEASE)).status

    assert first == "refused:workspace_writer:anchor_store_unavailable"
    assert pending == "pending_anchor"
    assert again == "refused:workspace_writer:claim_held"
    assert after == "published"
    assert kinds(rig.workspace) == ["claim", "claim", "release"]
    assert len(github.calls) == 1


def test_a_release_the_witness_missed_is_completed_by_the_next_claim(
    rig: Rig, other: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = workspace_writer.anchor_pending
    calls: list[int] = []

    def flaky(*args: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:  # the release's anchor
            raise ForarbeiderRefusedError(
                RefusalReason.RELEASE_NOT_ELIGIBLE, "anchor_store_unavailable"
            )
        return real(*args)

    monkeypatch.setattr(workspace_writer, "anchor_pending", flaky)
    assert execute(rig.request(), sim(endpoint(rig.base))).status == "published"
    monkeypatch.undo()
    assert writer_status(rig, rig.workspace) == "pending_anchor"

    later = execute(
        other.request(workspace=rig.workspace, ledger=own_ledger(other)), sim(endpoint(other.base))
    )

    assert later.status == "published"
    assert writer_status(rig, rig.workspace) == "verified"


def test_a_removed_writer_chain_is_refused_not_restarted(rig: Rig, other: Rig) -> None:
    assert execute(rig.request(), sim(endpoint(rig.base))).status == "published"
    shutil.rmtree(rig.workspace / workspace_writer_models.FOLDER)
    github = endpoint(other.base)

    late = outcome_of(other.request(workspace=rig.workspace, ledger=own_ledger(other)), github)

    assert late == "refused:workspace_writer:local_rolled_back"
    assert github.calls == []


def test_a_clock_running_backwards_never_claims(rig: Rig, other: Rig) -> None:
    assert execute(rig.request(), sim(endpoint(rig.base))).status == "published"
    github = endpoint(other.base)
    earlier = NOW - timedelta(seconds=1)

    try:
        late = execute(
            other.request(workspace=rig.workspace, ledger=own_ledger(other)),
            clocked(github, earlier),
        ).status
    except ForarbeiderRefusedError as error:
        late = f"refused:{error.detail}"

    assert late == "refused:workspace_writer:clock_regressed"
    assert github.calls == []


def test_a_new_identity_starts_with_an_anchored_empty_writer_chain(rig: Rig) -> None:
    assert writer_status(rig, rig.workspace) == "verified"
    assert kinds(rig.workspace) == []


def test_a_claim_hand_appended_over_a_live_lease_is_never_anchored(
    rig: Rig, other: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completing a pending record never bypasses the lease: the chain itself refuses it."""
    monkeypatch.setattr(publication_execute, "_prepared", crash)
    with pytest.raises(SimulatedCrash):
        execute(rig.request(), sim(endpoint(rig.base)))
    monkeypatch.undo()
    taken = tmp_copy(rig, "taken")
    ((held, raw),) = workspace_writer_models.chain(taken)
    forged = held.model_copy(
        update={
            "sequence": 2,
            "previous_sha256": hashlib.sha256(raw).hexdigest(),
            "claim_id": "f" * 64,
        }
    )
    path = taken / workspace_writer_models.FOLDER / "000002.json"
    path.write_bytes(workspace_writer_models.event_bytes(forged))
    path.chmod(0o600)
    github = endpoint(other.base)

    late = outcome_of(other.request(workspace=taken, ledger=own_ledger(other)), github)

    assert late == "refused:workspace_writer:local_invalid"
    assert writer_status(rig, rig.workspace) == "verified"
    assert github.calls == []
