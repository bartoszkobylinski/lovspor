"""AR5d-3 review fix: a send begun from a workspace on another witness is never cancelled (#738).

The topology is AR5c's: one ledger, anchored by its own witness, shared by
workspaces that keep their own witness for their workspace families. Workspace
``A`` (the rig) is on the ledger's witness; workspace ``B`` (:func:`foreign`) has
its own witness store, its own anchored identity and the same transaction ``T``.

The defect this file was written red against: ``B`` began the send of ``T`` (its
``prepared_to_send`` anchored on ``B``'s witness, the remote unchanged), and
``A``, on the ledger's witness, cancelled the reservation, because the attempt
history it could see was empty. The fix makes the ledger's witness the one
authoritative witness of every reserved transaction's attempts.
"""

import dataclasses
import multiprocessing
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import (
    publication_attempts,
    publication_execute,
    publication_ledger,
    publication_path_a,
    publication_path_b,
)
from lovspor.forarbeider.control_plane_anchor import AnchorQuery, verify, workspace_scope
from lovspor.forarbeider.control_plane_chains import workspace_chain
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness, unavailable
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.publication_cancel import cancel_reservation
from lovspor.forarbeider.publication_execute import ExecutionRequest, execute
from lovspor.forarbeider.publication_ledger import BudgetLedger
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash
from lovspor.forarbeider.publication_stop_resume import resume
from tests.unit.forarbeider_execution_support import (
    WITNESS,
    KeySigner,
    Rig,
    anchor_rig,
    ledger_bytes,
    make_rig,
)
from tests.unit.test_forarbeider_publication_stop_resume import LATER, resume_of
from tests.unit.test_forarbeider_reservation_cancellation import (
    calls,
    cancel,
    cancel_records,
    crash,
    environment_of,
    instruction,
    operations,
    refused,
    remote_state,
    signed,
    tid,
    unchanged_ledger,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(params=["A", "B"])
def either(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / f"path-{request.param.lower()}", request.param)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


def foreign(rig: Rig, name: str = "foreign") -> Rig:
    """Workspace B: its own folder, witness store and anchored identity, on ``rig``'s keys,
    ledger and transaction. The ledger names ``rig``'s witness, as AR5c shares it; B's
    workspace families (identity, latch, receipts, writer, control history and, under the
    AR5c rule, the transaction's empty attempts) are anchored on B's own witness."""
    folder = rig.tmp / name
    folder.mkdir(mode=0o700)
    store = rig.tmp / f"{name}-witness"
    store.mkdir(mode=0o700)
    witness = OfflineAnchorWitness(store, KeySigner(WITNESS, rig.witness_key))
    ledger = BudgetLedger(rig.ledger.root, rig.ledger.ledger_id, rig.witness)
    other = dataclasses.replace(rig, workspace=folder, witness=witness, ledger=ledger)
    anchor_rig(other, seed=False)
    return other


def begin_and_die(sender: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """B begins the send of T: ``prepared_to_send`` recorded and anchored, then the process
    dies before the remote answers; the remote is unchanged."""
    monkeypatch.setattr(publication_execute, "_send", crash)
    with pytest.raises(SimulatedCrash):
        execute(sender.request(), environment_of(sender))
    monkeypatch.undo()
    assert [r.state for r in publication_attempts.records(sender.workspace, tid(sender))] == [
        "prepared_to_send"
    ]


def from_sender(rig: Rig, sender: Rig, workspace: Path | None = None) -> Any:
    """The owner's instruction carried out from B (or a copy of it): B's scope and head."""
    held = workspace or sender.workspace
    repository = rig.inputs.settings.repository
    return instruction(
        rig,
        workspace_scope=workspace_scope(held, repository, sender.anchoring(workspace=held)),
        attempts_head_sha256=publication_attempts.head(held, tid(rig))[1],
    )


def reserved(rig: Rig) -> bool:
    """The reservation still counts: open in the witnessed ledger, budget not released."""
    held = publication_ledger.entries(rig.ledger)
    return tid(rig) in publication_ledger.outstanding(held, None)


def foreign_attempts_status(sender: Rig, authority: Any) -> str:
    chain = workspace_chain("publication_attempts", sender.workspace, tid(sender))
    query = AnchorQuery(
        "publication_attempts", sender.inputs.settings.repository, tid(sender), chain
    )
    return str(verify(query, authority).status)


# --- the reproduction ----------------------------------------------------------------------------


def test_a_send_begun_from_a_workspace_on_another_witness_is_never_cancelled(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = foreign(either)
    environment = environment_of(either)
    before_remote = remote_state(either, environment)
    begin_and_die(sender, monkeypatch)
    before = ledger_bytes(either)

    error = refused(lambda: cancel(either, signed(either), environment))

    assert error.detail.startswith("cancel:anchor:publication_attempts"), error.detail
    unchanged_ledger(either, before)
    assert operations(either) == ["reserve"]
    assert reserved(either)
    assert cancel_records(either) == []
    assert remote_state(either, environment) == before_remote


def test_the_sender_on_another_witness_anchors_its_send_on_the_ledgers_witness(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The begun send is on the authoritative record: the ledger's witness holds it."""
    sender = foreign(either)
    begin_and_die(sender, monkeypatch)

    assert foreign_attempts_status(sender, either.anchoring(workspace=sender.workspace)) == (
        "verified"
    )


def _in_flight(job: tuple[Rig, Path]) -> None:
    """Child process: B sends T and hangs inside the remote call, holding its locks."""
    sender, flag = job
    hang = os.environ.get("LOVSPOR_TEST_HANG", "send")

    def stuck(*_: Any, **__: Any) -> Any:
        flag.write_text(hang)
        while True:
            time.sleep(0.05)

    publication_path_a.send = stuck  # type: ignore[assignment]
    publication_path_b.push = stuck  # type: ignore[assignment]
    execute(sender.request(), environment_of(sender))


def _cancel_job(job: tuple[ExecutionRequest, Any, str]) -> str:
    request, offered, path = job
    rig_environment = _environments[path]
    try:
        return str(cancel_reservation(request, offered, rig_environment).status)
    except ForarbeiderRefusedError as error:
        return f"refused:{error.detail}"


_environments: dict[str, Any] = {}


def test_a_send_in_flight_in_another_process_on_another_witness_is_never_cancelled(
    either: Rig,
) -> None:
    """Real processes: B's send hangs inside the remote call; a cancellation from A in a
    second process is refused while B holds the ledger, and still refused after B dies
    with the outcome unknown and the remote unchanged."""
    sender = foreign(either)
    environment = environment_of(either)
    before_remote = remote_state(either, environment)
    _environments[either.path] = environment
    offered = signed(either)
    flag = either.tmp / "in-flight"
    context = multiprocessing.get_context("fork")
    child = context.Process(target=_in_flight, args=((sender, flag),))
    child.start()
    try:
        deadline = time.time() + 30
        while not flag.exists():
            assert time.time() < deadline, "the sender never reached the remote call"
            assert child.is_alive(), child.exitcode
            time.sleep(0.01)
        with context.Pool(1) as pool:
            during = pool.apply(_cancel_job, ((either.request(), offered, either.path),))
    finally:
        if child.pid is not None and child.is_alive():
            os.kill(child.pid, signal.SIGKILL)
        child.join()
    with context.Pool(1) as pool:
        after = pool.apply(_cancel_job, ((either.request(), offered, either.path),))

    assert during == "refused:ledger", during
    assert after.startswith("refused:cancel:anchor:publication_attempts"), after
    assert operations(either) == ["reserve"]
    assert reserved(either)
    assert remote_state(either, environment) == before_remote


# --- cancellation before a stale send, and the legitimate topology --------------------------------


def test_a_cancellation_before_any_send_fences_a_stale_sender_on_another_witness(
    either: Rig,
) -> None:
    sender = foreign(either)
    assert cancel(either, signed(either), environment_of(either)).status == "cancelled"
    environment = environment_of(sender)

    late = execute(sender.request(), environment)

    assert late.status in ("refused", "denied"), late.status
    assert calls(environment) == 0
    assert operations(either) == ["reserve", "cancel"]


def test_an_idle_workspace_on_another_witness_never_blocks_a_legitimate_cancellation(
    either: Rig,
) -> None:
    """Preservation: a second workspace with its own witness that never sent T leaves the
    authoritative attempt history empty, so the owner's cancellation goes through."""
    foreign(either)
    assert cancel(either, signed(either), environment_of(either)).status == "cancelled"
    assert operations(either) == ["reserve", "cancel"]
    assert not reserved(either)


def test_the_workspace_on_another_witness_cancels_from_the_authoritative_history(
    either: Rig,
) -> None:
    """With nothing sent, the owner may cancel from B too: B's attempts of T are read on the
    ledger's witness, the history every sender of T must anchor on."""
    sender = foreign(either)
    offered = signed(either, from_sender(either, sender))

    assert cancel(sender, offered, environment_of(either)).status == "cancelled"
    assert operations(either) == ["reserve", "cancel"]
    late = execute(either.request(), environment_of(either))
    assert late.status in ("refused", "denied"), late.status


def test_a_send_begun_from_another_witness_refuses_its_own_workspace_too(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = foreign(either)
    begin_and_die(sender, monkeypatch)
    offered = signed(either, from_sender(either, sender))
    before = ledger_bytes(either)

    error = refused(lambda: cancel(sender, offered, environment_of(either)))

    assert error.detail == "cancel:send_initiated:prepared_to_send", error.detail
    unchanged_ledger(either, before)


# --- witnesses substituted or unavailable, pending anchors ---------------------------------------


def test_a_sender_naming_its_own_witness_as_the_ledgers_never_sends(either: Rig) -> None:
    """A substituted ledger witness: the ledger is not anchored there, so nothing is sent."""
    sender = foreign(either)
    substituted = BudgetLedger(either.ledger.root, either.ledger.ledger_id, sender.witness)
    environment = environment_of(sender)

    outcome = execute(sender.request(ledger=substituted), environment)

    assert outcome.status == "denied", outcome.status
    assert calls(environment) == 0
    assert operations(either) == ["reserve"]


def test_a_sender_whose_ledger_witness_is_unavailable_never_sends(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = foreign(either)
    environment = environment_of(sender)
    ledger_store = either.witness

    real = type(ledger_store).submit

    def down(self: Any, *args: Any) -> Any:
        if self is ledger_store:
            raise unavailable()
        return real(self, *args)

    monkeypatch.setattr(type(ledger_store), "submit", down)
    try:
        outcome: Any = execute(sender.request(), environment)
        status = outcome.status
    except ForarbeiderRefusedError as error:
        status = error.detail
    monkeypatch.undo()

    assert status != "published", status
    assert calls(environment) == 0
    assert operations(either) == ["reserve"]


def test_a_prepared_record_the_ledgers_witness_never_anchored_proves_no_send(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B's ``prepared_to_send`` is written but its anchor fails: B refuses before the remote
    call, so no request exists and the owner's cancellation is safe. B's stale record is
    never anchored afterwards and B never sends."""
    sender = foreign(either)
    environment = environment_of(sender)
    ledger_store = either.witness
    real = type(ledger_store).submit
    lost = {"done": False}

    def drop_first(self: Any, *args: Any) -> Any:
        if self is ledger_store and not lost["done"]:
            lost["done"] = True
            raise unavailable()
        return real(self, *args)

    monkeypatch.setattr(type(ledger_store), "submit", drop_first)
    with pytest.raises(ForarbeiderRefusedError):
        execute(sender.request(), environment)
    monkeypatch.undo()
    assert lost["done"]
    assert calls(environment) == 0

    assert cancel(either, signed(either), environment_of(either)).status == "cancelled"

    again = execute(sender.request(), environment)
    assert again.status in ("recovery_required", "refused", "denied"), again.status
    try:
        found: Any = recover(sender.request(), environment).classification
    except ForarbeiderRefusedError as error:
        found = error.detail
    assert found != "published_authorized", found
    assert calls(environment) == 0
    assert operations(either) == ["reserve", "cancel"]


# --- races in real processes ----------------------------------------------------------------------


def _race(job: tuple[str, ExecutionRequest, Any, str, float]) -> tuple[str, str, int]:
    kind, request, offered, path, start = job
    environment = _environments[path]
    while time.time() < start:
        time.sleep(0.001)
    try:
        if kind == "send":
            status = str(execute(request, environment).status)
        else:
            status = str(cancel_reservation(request, offered, environment).status)
    except ForarbeiderRefusedError as error:
        status = f"refused:{error.detail}"
    return kind, status, calls(environment)


def test_a_send_from_another_witness_and_a_cancellation_racing_never_both_succeed(
    either: Rig,
) -> None:
    sender = foreign(either)
    _environments[either.path] = environment_of(either)
    offered = signed(either)
    start = time.time() + 1.5
    jobs = [
        ("send", sender.request(), None, either.path, start),
        ("cancel", either.request(), offered, either.path, start),
    ]
    # fork, not spawn: the sealed test evidence is authentic only under this process's seal key
    with multiprocessing.get_context("fork").Pool(2) as pool:
        results = {kind: (status, n) for kind, status, n in pool.map(_race, jobs)}

    sent, cancelled = results["send"][0] == "published", results["cancel"][0] == "cancelled"
    assert sent != cancelled, results
    if cancelled:
        assert operations(either) == ["reserve", "cancel"]
        assert results["send"][1] == 0, results
    else:
        assert operations(either) == ["reserve"]


# --- copies, crashes and retries ------------------------------------------------------------------


def test_a_copy_of_the_sender_cannot_cancel_its_begun_send(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = foreign(either)
    copy = either.tmp / "foreign-copy"
    shutil.copytree(sender.workspace, copy)
    begin_and_die(sender, monkeypatch)
    offered = signed(either, from_sender(either, sender, copy))
    before = ledger_bytes(either)

    error = refused(lambda: cancel(sender, offered, environment_of(either), workspace=copy))

    assert error.detail.startswith("cancel:"), error.detail
    unchanged_ledger(either, before)


def test_a_crash_after_the_decision_retries_once_with_a_foreign_workspace_present(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign(either)
    offered = signed(either)
    monkeypatch.setattr(publication_ledger, "_record", crash)
    with pytest.raises(SimulatedCrash):
        cancel(either, offered, environment_of(either))
    monkeypatch.undo()
    assert operations(either) == ["reserve"]

    assert cancel(either, offered, environment_of(either)).status == "cancelled"
    assert cancel(either, offered, environment_of(either)).status == "already_cancelled"
    assert operations(either) == ["reserve", "cancel"]
    assert len(cancel_records(either)) == 1


def test_the_senders_recovery_resolves_its_send_on_the_ledgers_witness_and_never_cancels(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preservation (AR4b-2, AR5c): B recovers its interrupted send, recording on the ledger's
    witness; an unchanged remote after a begun send still never permits a cancellation."""
    sender = foreign(either)
    begin_and_die(sender, monkeypatch)

    found = recover(sender.request(), environment_of(sender))

    assert found.classification != "published_authorized", found.classification
    assert foreign_attempts_status(sender, either.anchoring(workspace=sender.workspace)) == (
        "verified"
    )
    error = refused(
        lambda: cancel(sender, signed(either, from_sender(either, sender)), environment_of(either))
    )
    assert error.detail.startswith("cancel:send_initiated:"), error.detail
    assert operations(either) == ["reserve"]


def test_the_sender_on_another_witness_resumes_once_its_send_is_resolved(rig: Rig) -> None:
    """Preservation (AR5b RESUME): B's unknown outcome sets its latch; after recovery settles it
    (on the ledger's witness), the owner's RESUME reads B's attempts there and clears it."""
    sender = foreign(rig)
    environment = environment_of(sender, fault="lost")
    assert execute(sender.request(), environment).status == "outcome_unknown"
    assert environment.transport is not None
    environment.transport.fault = None
    first = sender.inputs.control[0]
    r2 = resume_of(sender, first, 2, None)
    request = sender.request(inputs=sender.inputs._replace(control=(first, r2)))

    assert recover(request, environment).classification == "published_authorized"
    assert resume(request, LATER).status == "resumed"
    assert operations(rig) == ["reserve", "commit"]
