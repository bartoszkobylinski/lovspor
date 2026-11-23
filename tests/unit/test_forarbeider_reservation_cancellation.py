"""AR5d-3: an abandoned reservation is cancelled only on proof that nothing was sent (#738).

Every rig holds an anchored ledger with its transaction's open reservation, an
anchored workspace identity and a disposable remote at the transaction's base
(path A: the in-memory GitHub; path B: a bare repository on disk). The owner
instruction is signed with the rig's throwaway owner key. Crashes are injected
at each persistence boundary; races run in real processes.

For the red run the cancellation module was imported inside each test, so on
code without it each test failed on its own; the imports were hoisted after.
"""

import multiprocessing
import shutil
import time
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import (
    publication_anchors,
    publication_attempts,
    publication_cancel,
    publication_execute,
    publication_ledger,
    publication_path_a,
    publication_remote_recovery,
)
from lovspor.forarbeider.control_plane_anchor import (
    AnchorQuery,
    durable_append,
    verify,
    workspace_scope,
)
from lovspor.forarbeider.control_plane_chains import workspace_chain
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import CONTROL_NAMESPACE, POLICY_NAMESPACE
from lovspor.forarbeider.publication_authorize import authorize
from lovspor.forarbeider.publication_cancel import cancel_reservation
from lovspor.forarbeider.publication_cancel_models import CANCELLED, ReservationCancellation
from lovspor.forarbeider.publication_execute import (
    ExecutionRequest,
    Simulation,
    _context,
    _step,
    execute,
    publish_authorized,
)
from lovspor.forarbeider.publication_execution_models import AttemptRecord, LatchEvent
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash, SimulatedGitHub
from lovspor.forarbeider.release_contract_models import BudgetUsage
from lovspor.forarbeider.release_ledger_models import LedgerEntry, LedgerOperation
from lovspor.forarbeider.release_limits import LimitsRequest, verify_limits
from lovspor.forarbeider.workspace_writer_models import LEASE
from tests.unit.forarbeider_authorization_support import raw
from tests.unit.forarbeider_execution_support import (
    APP,
    NOW,
    KeySigner,
    Rig,
    _root,
    identified_workspace,
    ledger_bytes,
    ledger_query,
    make_rig,
    record_cancel,
)
from tests.unit.forarbeider_release_control_support import BOT, OWNER, signed_by

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --- rigs and adapters ---------------------------------------------------------------------------


@pytest.fixture(params=["A", "B"])
def either(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / f"path-{request.param.lower()}", request.param)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


def endpoint(rig: Rig, **changes: Any) -> SimulatedGitHub:
    fields: dict[str, Any] = {"head_oid": rig.base, "identity": APP, "now": NOW}
    return SimulatedGitHub(**(fields | changes))


def environment_of(rig: Rig, at: datetime = NOW, **changes: Any) -> Simulation:
    if rig.path == "A":
        return Simulation(clock=lambda: at, transport=endpoint(rig, **changes))
    return Simulation(clock=lambda: at, remote=rig.remote, signer=KeySigner(BOT, rig.bot))


def calls(environment: Simulation) -> int:
    return len(environment.transport.calls) if environment.transport is not None else 0


def remote_state(rig: Rig, environment: Simulation) -> Any:
    if environment.transport is not None:
        return environment.transport.head_oid
    return rig.remote_files()


def refused(action: Any) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    return raised.value


def crash(*_: Any, **__: Any) -> Any:
    raise SimulatedCrash


# --- the owner instruction -----------------------------------------------------------------------


def tid(rig: Rig) -> str:
    return rig.transaction.transaction_id


def instruction(rig: Rig, workspace: Path | None = None, **changes: Any) -> Any:
    """The owner's cancellation of the rig's reservation, naming what is on record now."""

    held = workspace or rig.workspace
    stored = publication_ledger.entries(rig.ledger)
    reservation = next(
        r
        for r in stored
        if (e := LedgerEntry.model_validate_json(r)).operation == "reserve"
        and e.release_id == tid(rig)
    )
    repository = rig.inputs.settings.repository
    content = rig.transaction.content
    fields: dict[str, Any] = {
        "instruction_id": "synthetic-cancellation-1",
        "repository": repository,
        "ledger_id": rig.ledger.ledger_id,
        "transaction_id": tid(rig),
        "batch_id": content.batch_id,
        "path": content.publication.path,
        "base_commit": content.publication.base_commit,
        "reservation_sha256": sha256(reservation).hexdigest(),
        "ledger_head_sha256": sha256(stored[-1]).hexdigest(),
        "attempts_head_sha256": publication_attempts.head(held, tid(rig))[1],
        "workspace_scope": workspace_scope(held, repository, rig.anchoring(workspace=held)),
        "reason": "synthetic: the batch was abandoned before any send",
        "signer_principal": OWNER,
        "signed_at": NOW - timedelta(minutes=1),
        "valid_until": NOW + timedelta(hours=1),
    }
    return ReservationCancellation.model_validate(fields | changes)


def signed(
    rig: Rig, record: Any = None, key: Any = None, namespace: str = CONTROL_NAMESPACE
) -> Any:
    return signed_by(
        record if record is not None else instruction(rig), key or rig.owner, namespace
    )


def cancel(rig: Rig, offered: SignedRecord, environment: Simulation, **request: Any) -> Any:

    return cancel_reservation(rig.request(**request), offered, environment)


def operations(rig: Rig) -> list[str]:
    return [LedgerEntry.model_validate_json(r).operation for r in ledger_bytes(rig)]


def ledger_status(rig: Rig) -> str:
    return verify(ledger_query(rig), rig.anchoring(workspace=rig.ledger.root)).status


def attempts(rig: Rig, workspace: Path | None = None) -> list[AttemptRecord]:
    return publication_attempts.records(workspace or rig.workspace, tid(rig))


def attempts_status(rig: Rig, workspace: Path | None = None) -> str:
    held = workspace or rig.workspace
    chain = workspace_chain("publication_attempts", held, tid(rig))
    query = AnchorQuery("publication_attempts", rig.inputs.settings.repository, tid(rig), chain)
    return verify(query, rig.anchoring(workspace=held)).status


def cancel_records(rig: Rig, workspace: Path | None = None) -> list[AttemptRecord]:

    return [r for r in attempts(rig, workspace) if CANCELLED in r.reasons]


def unchanged_ledger(rig: Rig, before: tuple[bytes, ...]) -> None:
    assert ledger_bytes(rig) == before
    assert ledger_status(rig) == "verified"


# --- the cancellation itself, both paths --------------------------------------------------------


def test_an_abandoned_reservation_with_an_unchanged_remote_is_cancelled(either: Rig) -> None:
    environment = environment_of(either)
    before = remote_state(either, environment)
    offered = signed(either)

    outcome = cancel(either, offered, environment)

    assert outcome.status == "cancelled"
    assert operations(either) == ["reserve", "cancel"]
    assert ledger_status(either) == "verified"
    assert publication_ledger.outstanding(ledger_bytes(either), None) == ()
    [record] = cancel_records(either)
    assert record.state == "refused"
    assert record.authorization_sha256 == sha256(offered.record).hexdigest()
    assert attempts_status(either) == "verified"
    assert remote_state(either, environment) == before
    assert calls(environment) == 0


def test_a_retry_of_the_same_instruction_cancels_nothing_twice(either: Rig) -> None:
    environment = environment_of(either)
    offered = signed(either)
    assert cancel(either, offered, environment).status == "cancelled"

    again = cancel(either, offered, environment)

    assert again.status == "already_cancelled"
    assert operations(either) == ["reserve", "cancel"]
    assert len(cancel_records(either)) == 1


def test_a_cancelled_reservation_never_sends_and_is_never_reserved_again(either: Rig) -> None:
    environment = environment_of(either)
    before = remote_state(either, environment)
    assert cancel(either, signed(either), environment).status == "cancelled"

    outcome = execute(either.request(), environment)
    reserve = refused(lambda: publication_ledger.reserve(either.request(), NOW))

    assert outcome.status == "refused"
    assert outcome.attempt is not None and CANCELLED in outcome.attempt.reasons
    assert "ledger_reservation_after_cancel" in reserve.detail
    assert remote_state(either, environment) == before
    assert calls(environment) == 0
    assert operations(either) == ["reserve", "cancel"]


def test_evidence_sealed_before_a_cancellation_never_sends(either: Rig) -> None:

    environment = environment_of(either)
    request = either.request()
    evidence = authorize(_context(request, environment, _step(request)))
    assert evidence.decision == "simulation_only", evidence.denied_by
    assert cancel(either, signed(either), environment).status == "cancelled"

    error = refused(lambda: publish_authorized(evidence, either.request(), environment))

    assert "reservation" in error.detail
    assert "prepared_to_send" not in [r.state for r in attempts(either)]
    assert calls(environment) == 0


def test_the_raw_ledger_api_never_cancels_without_the_owner_proof(either: Rig) -> None:

    slot = LedgerEntry.model_validate_json(ledger_bytes(either)[0])
    operation = LedgerOperation(
        operation="cancel",
        ledger_id=slot.ledger_id,
        day=slot.day,
        release_id=slot.release_id,
        batch_id=slot.batch_id,
        amounts=slot.amounts,
        recorded_at=NOW,
    )
    limits = verify_limits(
        LimitsRequest(either.inputs.policy.policy, either.inputs.limits, NOW), either.root
    )
    authority = either.anchoring(workspace=either.ledger.root)
    before = ledger_bytes(either)

    error = refused(
        lambda: publication_ledger.record(
            either.ledger, operation, limits, (authority, either.inputs.settings.repository)
        )
    )

    assert "cancel_needs_owner_instruction" in error.detail
    unchanged_ledger(either, before)


# --- no proof of nonpublication: refused ---------------------------------------------------------


def test_a_send_whose_response_was_lost_is_never_cancelled(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mutation landed; the process died before recording what it proved."""
    environment = environment_of(either)
    monkeypatch.setattr(publication_execute, "_after", crash)
    with pytest.raises(SimulatedCrash):
        execute(either.request(), environment)
    monkeypatch.undo()
    before = ledger_bytes(either)

    error = refused(lambda: cancel(either, signed(either), environment))

    assert "cancel:send_initiated:prepared_to_send" in error.detail
    unchanged_ledger(either, before)
    assert cancel_records(either) == []


def test_a_lost_response_on_path_a_is_never_cancelled(rig: Rig) -> None:
    environment = environment_of(rig, fault="lost")
    assert execute(rig.request(), environment).status == "outcome_unknown"
    before = ledger_bytes(rig)

    error = refused(lambda: cancel(rig, signed(rig), environment))

    assert "cancel:send_initiated" in error.detail
    unchanged_ledger(rig, before)


def test_a_send_begun_on_an_unchanged_remote_is_never_cancelled(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``prepared_to_send``, then a crash before the call: the remote is unchanged, but a
    request may still be in flight. A timeout or a missing answer never proves nonpublication."""
    environment = environment_of(either)
    before_remote = remote_state(either, environment)
    monkeypatch.setattr(publication_execute, "_send", crash)
    with pytest.raises(SimulatedCrash):
        execute(either.request(), environment)
    monkeypatch.undo()
    assert recover(either.request(), environment).classification == "not_published"
    before = ledger_bytes(either)

    error = refused(lambda: cancel(either, signed(either), environment))

    assert "cancel:send_initiated:prepared_to_send" in error.detail
    unchanged_ledger(either, before)
    assert remote_state(either, environment) == before_remote


def test_a_published_and_settled_transaction_is_never_cancelled(either: Rig) -> None:
    environment = environment_of(either)
    assert execute(either.request(), environment).status == "published"
    assert recover(either.request(), environment).settlement.status == "settled"
    before = ledger_bytes(either)

    error = refused(lambda: cancel(either, signed(either), environment))

    assert error.detail.startswith("cancel:")
    unchanged_ledger(either, before)
    again = recover(either.request(), environment)
    assert again.settlement is not None and again.settlement.status == "already_settled"
    assert operations(either) == ["reserve", "commit"]


def test_an_unknown_authorization_stays_withheld_and_is_never_cancelled(rig: Rig) -> None:
    """A. it happened remotely; B. its authorization is unknown; C. its reservation keeps
    counting; D. the latch stops the workspace. A signed cancellation changes none of it."""
    environment = environment_of(rig)
    assert execute(rig.request(), environment).status == "published"
    elsewhere = identified_workspace(rig, "elsewhere")
    withheld = recover(rig.request(workspace=elsewhere), environment)
    assert withheld.settlement is not None and withheld.settlement.status == "withheld"
    before = ledger_bytes(rig)

    for workspace in (rig.workspace, elsewhere):
        error = refused(
            lambda w=workspace: cancel(
                rig, signed(rig, instruction(rig, workspace=w)), environment, workspace=w
            )
        )
        assert error.detail.startswith("cancel:"), error.detail

    unchanged_ledger(rig, before)
    assert operations(rig) == ["reserve"]
    assert publication_attempts.latched(elsewhere)


def test_a_receipt_on_record_refuses_the_cancellation(either: Rig) -> None:
    environment = environment_of(either)
    assert execute(either.request(), environment).status == "published"
    assert recover(either.request(ledger=None), environment).receipt is not None
    before = ledger_bytes(either)

    error = refused(lambda: cancel(either, signed(either), environment))

    assert error.detail.startswith("cancel:")
    unchanged_ledger(either, before)


def test_revoked_owner_limits_refuse_the_cancellation(either: Rig) -> None:
    environment = environment_of(either)
    revoked = _root(
        either.tmp / "revoked-root", either.owner, either.bot, either.witness_key, (either.owner,)
    )
    before = ledger_bytes(either)

    error = refused(lambda: cancel(either, signed(either), environment, root=revoked))

    assert error.detail.startswith("cancel:")
    unchanged_ledger(either, before)
    assert cancel_records(either) == []


def test_a_remote_that_moved_is_never_cancelled(rig: Rig) -> None:
    environment = environment_of(rig)
    assert environment.transport is not None
    environment.transport.advance({"other.txt": b"x"}, ("not ours", ""), (APP, True))
    before = ledger_bytes(rig)

    error = refused(lambda: cancel(rig, signed(rig), environment))

    assert error.detail == "cancel:remote_advanced"
    unchanged_ledger(rig, before)


def test_a_remote_moving_during_the_verification_is_never_cancelled(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = environment_of(rig)
    github = environment.transport
    assert github is not None
    real = SimulatedGitHub.head
    reads: list[int] = []

    def moving(self: SimulatedGitHub, *args: Any) -> Any:
        reads.append(1)
        if len(reads) == 2:
            self.advance({"other.txt": b"x"}, ("not ours", ""), (APP, True))
        return real(self, *args)

    monkeypatch.setattr(SimulatedGitHub, "head", moving)
    before = ledger_bytes(rig)

    error = refused(lambda: cancel(rig, signed(rig), environment))

    assert error.detail.startswith("cancel:remote"), error.detail
    unchanged_ledger(rig, before)


def test_an_unreadable_remote_is_never_cancelled(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    environment = environment_of(rig)
    monkeypatch.setattr(SimulatedGitHub, "head", crash)
    before = ledger_bytes(rig)

    with pytest.raises((ForarbeiderRefusedError, SimulatedCrash)):
        cancel(rig, signed(rig), environment)

    monkeypatch.undo()
    unchanged_ledger(rig, before)
    assert cancel_records(rig) == []


# --- writers, workspaces and ledgers -------------------------------------------------------------


def test_a_live_writer_claim_refuses_the_cancellation_and_its_lapse_proves_nothing_alone(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claim anchored, then a crash before ``prepared_to_send``: no send was begun. While the
    lease runs, a writer may be live: refused. After it, the attempts (not the lease) prove it."""
    monkeypatch.setattr(publication_execute, "_prepared", crash)
    with pytest.raises(SimulatedCrash):
        execute(either.request(), environment_of(either))
    monkeypatch.undo()
    before = ledger_bytes(either)

    error = refused(lambda: cancel(either, signed(either), environment_of(either)))
    assert error.detail == "cancel:workspace_writer:claim_held"
    unchanged_ledger(either, before)

    later = cancel(either, signed(either), environment_of(either, at=NOW + LEASE))
    assert later.status == "cancelled"


def test_another_workspace_on_the_witness_cannot_cancel_a_send_begun_here(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    neighbour = identified_workspace(either, "neighbour")
    monkeypatch.setattr(publication_execute, "_send", crash)
    with pytest.raises(SimulatedCrash):
        execute(either.request(), environment_of(either))
    monkeypatch.undo()
    before = ledger_bytes(either)

    error = refused(
        lambda: cancel(
            either,
            signed(either, instruction(either, workspace=neighbour)),
            environment_of(either),
            workspace=neighbour,
        )
    )

    assert error.detail.startswith("cancel:anchor:publication_attempts"), error.detail
    unchanged_ledger(either, before)


def test_a_copied_workspace_cannot_cancel_after_the_original_began_a_send(
    either: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(either.workspace, copy)
    offered = signed(either, instruction(either, workspace=copy))
    monkeypatch.setattr(publication_execute, "_send", crash)
    with pytest.raises(SimulatedCrash):
        execute(either.request(), environment_of(either))
    monkeypatch.undo()
    before = ledger_bytes(either)

    error = refused(lambda: cancel(either, offered, environment_of(either), workspace=copy))

    assert error.detail.startswith("cancel:anchor:publication_attempts"), error.detail
    unchanged_ledger(either, before)


def test_a_ledger_on_another_witness_than_the_workspace_is_never_cancelled(
    rig: Rig, tmp_path: Path
) -> None:
    """The reservation does not name its sender: attempts anchored on another witness than
    the ledger's cannot be seen there, so nothing proves no send was begun."""
    shared = make_rig(tmp_path / "shared", "A", share=rig)
    repository = rig.inputs.settings.repository
    scope = workspace_scope(shared.workspace, repository, shared.anchoring())
    offered = signed(rig, instruction(rig, workspace_scope=scope, attempts_head_sha256=None))
    before = ledger_bytes(rig)

    error = refused(
        lambda: cancel(
            rig,
            offered,
            environment_of(rig),
            ledger=shared.ledger,
            anchoring=shared.anchoring(),
            workspace=shared.workspace,
        )
    )

    assert error.detail == "cancel:ledger_witness_not_workspace_witness", error.detail
    unchanged_ledger(rig, before)


def test_a_send_during_the_remote_verification_is_refused(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:

    environment = environment_of(rig)
    real = publication_cancel.history
    found: list[str] = []

    def during(*args: Any) -> Any:
        try:
            found.append(execute(rig.request(), environment).status)
        except ForarbeiderRefusedError as error:
            found.append(error.detail)
        return real(*args)

    monkeypatch.setattr(publication_cancel, "history", during)

    assert cancel(rig, signed(rig), environment).status == "cancelled"
    assert found == ["workspace"], found
    assert calls(environment) == 0


def test_a_cancellation_during_the_send_is_refused(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = environment_of(rig)
    offered = signed(rig)
    real = publication_path_a.send
    found: list[str] = []

    def during(*args: Any) -> Any:
        try:
            found.append(cancel(rig, offered, environment).status)
        except ForarbeiderRefusedError as error:
            found.append(error.detail)
        return real(*args)

    monkeypatch.setattr(publication_path_a, "send", during)

    assert execute(rig.request(), environment).status == "published"
    assert found == ["workspace"], found
    assert operations(rig) == ["reserve"]


def _race(job: tuple[str, ExecutionRequest, Any, Any, float]) -> tuple[str, str, int]:
    kind, request, offered, adapters, start = job
    path, head, remote, bot = adapters
    if path == "A":
        environment = Simulation(
            clock=lambda: NOW,
            transport=SimulatedGitHub(head_oid=head, identity=APP, now=NOW),
        )
    else:
        environment = Simulation(clock=lambda: NOW, remote=remote, signer=KeySigner(BOT, bot))

    while time.time() < start:
        time.sleep(0.001)
    try:
        if kind == "send":
            status = execute(request, environment).status
        else:
            status = cancel_reservation(request, offered, environment).status
    except ForarbeiderRefusedError as error:
        status = f"refused:{error.detail}"
    return kind, status, calls(environment)


@pytest.mark.parametrize("sender", ["same_workspace", "neighbour"])
def test_a_cancellation_and_a_send_racing_in_two_processes_never_both_succeed(
    either: Rig, sender: str
) -> None:
    """Two processes, one sending the transaction (from this workspace, or from another
    workspace on the same witness and ledger), one cancelling it: never both."""
    offered = signed(either)
    workspace = either.workspace if sender == "same_workspace" else None
    if workspace is None:
        workspace = identified_workspace(either, "neighbour")
    adapters = (either.path, either.base, either.remote, either.bot)
    start = time.time() + 1.5
    jobs = [
        ("send", either.request(workspace=workspace), None, adapters, start),
        ("cancel", either.request(), offered, adapters, start),
    ]
    # fork, not spawn: the sealed test evidence is authentic only under this process's seal key
    with multiprocessing.get_context("fork").Pool(2) as pool:
        results = {kind: (status, n) for kind, status, n in pool.map(_race, jobs)}

    sent, cancelled = results["send"][0] == "published", results["cancel"][0] == "cancelled"
    assert sent != cancelled, results
    if cancelled:
        assert operations(either) == ["reserve", "cancel"]
        assert results["send"][1] == 0, results
        late = execute(either.request(workspace=workspace), environment_of(either))
        assert late.status in ("refused", "denied"), late.status
    else:
        assert operations(either) == ["reserve"]
        error = refused(lambda: cancel(either, signed(either), environment_of(either)))
        assert error.detail.startswith("cancel:"), error.detail


# --- the owner's authority -----------------------------------------------------------------------


REFUSALS = {
    "stranger": "signer_not_in_trust_root",
    "bot": "signer_role_not_permitted",
    "policy_namespace": "signature_namespace_mismatch",
    "expired": "expired",
    "not_yet_signed": "signed_after_check_time",
    "stop_record": "record_malformed",
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_an_instruction_the_owner_did_not_validly_sign_is_refused(rig: Rig, case: str) -> None:
    environment = environment_of(rig)
    if case == "stranger":
        offered = signed(rig, key=rig.stranger)
    elif case == "bot":
        offered = signed(rig, instruction(rig, signer_principal=BOT), key=rig.bot)
    elif case == "policy_namespace":
        offered = signed(rig, namespace=POLICY_NAMESPACE)
    elif case == "expired":
        offered = signed(
            rig,
            instruction(
                rig, signed_at=NOW - timedelta(hours=2), valid_until=NOW - timedelta(seconds=1)
            ),
        )
    elif case == "not_yet_signed":
        offered = signed(
            rig,
            instruction(
                rig, signed_at=NOW + timedelta(minutes=5), valid_until=NOW + timedelta(hours=1)
            ),
        )
    else:
        offered = SignedRecord(rig.inputs.control[0].record, rig.inputs.control[0].signature)
    before = ledger_bytes(rig)

    error = refused(lambda: cancel(rig, offered, environment))

    assert error.detail == f"cancel:instruction:{REFUSALS[case]}"
    unchanged_ledger(rig, before)
    assert cancel_records(rig) == []


def test_an_instruction_valid_for_more_than_a_day_is_refused(rig: Rig) -> None:
    good = instruction(rig)
    broad = raw(good).replace(
        good.valid_until.isoformat().replace("+00:00", "Z").encode(),
        (NOW + timedelta(days=2)).isoformat().replace("+00:00", "Z").encode(),
    )
    assert broad != raw(good)
    before = ledger_bytes(rig)

    error = refused(lambda: cancel(rig, signed(rig, broad), environment_of(rig)))

    assert error.detail.startswith("cancel:instruction"), error.detail
    unchanged_ledger(rig, before)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transaction_id", "0" * 64),
        ("batch_id", "batch-" + "0" * 56),
        ("ledger_id", "another-ledger"),
        ("base_commit", "0" * 40),
        ("path", "B"),
        ("reservation_sha256", "0" * 64),
        ("workspace_scope", "workspace:" + "0" * 64),
        ("repository", "someone/else"),
    ],
)
def test_an_instruction_for_anything_else_is_refused(rig: Rig, field: str, value: str) -> None:
    before = ledger_bytes(rig)

    error = refused(
        lambda: cancel(rig, signed(rig, instruction(rig, **{field: value})), environment_of(rig))
    )

    assert error.detail.startswith("cancel:instruction"), error.detail
    unchanged_ledger(rig, before)


def test_an_instruction_signed_before_the_evidence_changed_is_stale(rig: Rig) -> None:
    """A denial recorded after the owner signed: the attempts are no longer what was seen."""
    offered = signed(rig)
    environment = environment_of(rig)
    stale = rig.request(inputs=rig.inputs._replace(control=()))
    assert execute(stale, environment).status == "denied"
    before = ledger_bytes(rig)

    error = refused(lambda: cancel(rig, offered, environment))

    assert error.detail == "cancel:instruction_stale:attempts_head"
    unchanged_ledger(rig, before)


def test_an_instruction_signed_before_the_ledger_moved_is_stale(rig: Rig) -> None:
    offered = signed(rig)
    slot = LedgerEntry.model_validate_json(ledger_bytes(rig)[0])
    reconcile = LedgerOperation(
        operation="reconcile",
        ledger_id=slot.ledger_id,
        day=slot.day,
        release_id=None,
        batch_id=None,
        amounts=BudgetUsage(documents=0, files=0, bytes=0, commits=0),
        recorded_at=NOW,
    )
    limits = verify_limits(
        LimitsRequest(rig.inputs.policy.policy, rig.inputs.limits, NOW), rig.root
    )
    authority = rig.anchoring(workspace=rig.ledger.root)
    repository = rig.inputs.settings.repository
    assert publication_ledger.record(rig.ledger, reconcile, limits, (authority, repository))
    before = ledger_bytes(rig)

    error = refused(lambda: cancel(rig, offered, environment_of(rig)))

    assert error.detail == "cancel:instruction_stale:ledger_head"
    unchanged_ledger(rig, before)


def test_an_instruction_replayed_elsewhere_cancels_nothing(rig: Rig, tmp_path: Path) -> None:
    offered = signed(rig)
    assert cancel(rig, offered, environment_of(rig)).status == "cancelled"
    other = make_rig(tmp_path / "other", "A")
    before = ledger_bytes(other)

    error = refused(lambda: cancel(other, offered, environment_of(other)))

    assert error.detail.startswith("cancel:instruction"), error.detail
    unchanged_ledger(other, before)


def test_a_second_instruction_after_the_cancellation_is_refused(rig: Rig) -> None:
    first = signed(rig)
    assert cancel(rig, first, environment_of(rig)).status == "cancelled"
    second = signed(rig, instruction(rig, instruction_id="synthetic-cancellation-2"))

    error = refused(lambda: cancel(rig, second, environment_of(rig)))

    assert error.detail.startswith("cancel:"), error.detail
    assert len(cancel_records(rig)) == 1


def test_a_cancellation_never_clears_the_latch(either: Rig) -> None:
    event = LatchEvent(
        sequence=1,
        previous_sha256=None,
        kind="set",
        at=NOW,
        transaction_id=tid(either),
        reasons=("synthetic_halt",),
    )
    repository = either.inputs.settings.repository
    assert durable_append(either.workspace, event, repository, either.anchoring()).status == (
        "appended"
    )

    assert cancel(either, signed(either), environment_of(either)).status == "cancelled"

    assert publication_attempts.latched(either.workspace)


def test_a_publication_from_outside_the_proof_after_a_cancellation_halts_unsettled(
    rig: Rig,
) -> None:
    """The residual the proof cannot see (a sender anchoring its attempts on another witness):
    its publication after the cancellation is never settled and stops the workspace."""
    environment = environment_of(rig)
    assert cancel(rig, signed(rig), environment).status == "cancelled"
    github = environment.transport
    assert github is not None
    payload = publication_path_a.payload_of(
        rig.transaction, publication_execute._files(rig.request()), rig.inputs.settings
    )
    assert publication_path_a.send(github, payload, rig.inputs.settings).status == "published"

    found = recover(rig.request(), environment)

    assert found.classification != "published_authorized"
    assert found.settlement is None or found.settlement.status != "settled"
    assert operations(rig) == ["reserve", "cancel"]
    assert publication_attempts.latched(rig.workspace)


def test_a_receipt_without_any_attempt_record_refuses_the_cancellation(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Added after sabotage: recovery wrote a receipt, then crashed before its attempt record."""
    environment = environment_of(rig)
    github = environment.transport
    assert github is not None
    payload = publication_path_a.payload_of(
        rig.transaction, publication_execute._files(rig.request()), rig.inputs.settings
    )
    assert publication_path_a.send(github, payload, rig.inputs.settings).status == "published"
    monkeypatch.setattr(publication_remote_recovery, "_record_published", crash)
    with pytest.raises(SimulatedCrash):
        recover(rig.request(), environment)
    monkeypatch.undo()
    assert attempts(rig) == []
    before = ledger_bytes(rig)

    error = refused(lambda: cancel(rig, signed(rig), environment))

    assert error.detail == "cancel:receipt_on_record"
    unchanged_ledger(rig, before)


def test_an_unanchored_ledger_refuses_before_anything_is_written(rig: Rig) -> None:
    """Added after sabotage: the ledger is proved before the decision is recorded."""
    offered = signed(rig)
    shutil.rmtree(rig.tmp / "witness" / "budget_ledger")

    error = refused(lambda: cancel(rig, offered, environment_of(rig)))

    assert error.detail.startswith("cancel:anchor:budget_ledger:"), error.detail
    assert cancel_records(rig) == []


def test_a_reservation_already_closed_is_never_claimed_by_an_instruction(rig: Rig) -> None:
    """Added after sabotage: a cancel already in the ledger gets no decision record of ours."""
    slot = LedgerEntry.model_validate_json(ledger_bytes(rig)[0])
    closing = LedgerOperation(
        operation="cancel",
        ledger_id=slot.ledger_id,
        day=slot.day,
        release_id=slot.release_id,
        batch_id=slot.batch_id,
        amounts=slot.amounts,
        recorded_at=NOW,
    )
    material = LimitsRequest(rig.inputs.policy.policy, rig.inputs.limits, NOW)
    authority = rig.anchoring(workspace=rig.ledger.root)
    repository = rig.inputs.settings.repository
    record_cancel(rig.ledger, closing, verify_limits(material, rig.root), (authority, repository))
    before = ledger_bytes(rig)

    error = refused(lambda: cancel(rig, signed(rig), environment_of(rig)))

    assert error.detail == "cancel:reservation_not_open"
    unchanged_ledger(rig, before)
    assert cancel_records(rig) == []


def test_a_cancellation_during_a_send_from_another_workspace_waits_for_the_ledger(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Added after sabotage: another workspace on the same witness and ledger sends the
    transaction; a cancellation from inside that send is refused by the ledger lock."""
    neighbour = identified_workspace(rig, "neighbour")
    environment = environment_of(rig)
    offered = signed(rig)
    real = publication_path_a.send
    found: list[str] = []

    def during(*args: Any) -> Any:
        try:
            found.append(cancel(rig, offered, environment).status)
        except ForarbeiderRefusedError as error:
            found.append(error.detail)
        return real(*args)

    monkeypatch.setattr(publication_path_a, "send", during)

    assert execute(rig.request(workspace=neighbour), environment).status == "published"
    assert found == ["ledger"], found
    assert operations(rig) == ["reserve"]


# --- the witness ---------------------------------------------------------------------------------


def test_an_unavailable_witness_refuses_and_writes_nothing(either: Rig) -> None:
    offered = signed(either)
    shutil.move(str(either.tmp / "witness"), str(either.tmp / "witness-away"))
    before = ledger_bytes(either)
    attempts_before = attempts(either)

    with pytest.raises(ForarbeiderRefusedError):
        cancel(either, offered, environment_of(either))

    assert ledger_bytes(either) == before
    assert attempts(either) == attempts_before


def test_a_rolled_back_witness_refuses_the_cancellation(rig: Rig) -> None:
    snapshot = rig.tmp / "witness-snapshot"
    shutil.copytree(rig.tmp / "witness", snapshot)
    stale = rig.request(inputs=rig.inputs._replace(control=()))
    assert execute(stale, environment_of(rig)).status == "denied"
    offered = signed(rig)
    shutil.rmtree(rig.tmp / "witness")
    shutil.copytree(snapshot, rig.tmp / "witness")
    before = ledger_bytes(rig)

    error = refused(lambda: cancel(rig, offered, environment_of(rig)))

    assert error.detail.startswith("cancel:anchor:publication_attempts"), error.detail
    assert ledger_bytes(rig) == before


# --- crashes at every persistence boundary -------------------------------------------------------


def _crash_witness_on(monkeypatch: pytest.MonkeyPatch, family: str) -> None:
    real = OfflineAnchorWitness.submit

    def crashing(self: OfflineAnchorWitness, anchor: Any) -> Any:
        if anchor.family == family:
            raise SimulatedCrash
        return real(self, anchor)

    monkeypatch.setattr(OfflineAnchorWitness, "submit", crashing)


def _crash_after_record(monkeypatch: pytest.MonkeyPatch) -> None:
    real = publication_ledger._record

    def crashing(*args: Any, **kwargs: Any) -> Any:
        real(*args, **kwargs)
        raise SimulatedCrash

    monkeypatch.setattr(publication_ledger, "_record", crashing)


def _crash_before_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publication_ledger, "append_entry", crash)


BOUNDARIES = {
    "attempt_written_not_anchored": lambda m: _crash_witness_on(m, "publication_attempts"),
    "attempt_anchored_ledger_not_written": _crash_before_ledger,
    "ledger_written_not_anchored": lambda m: _crash_witness_on(m, "budget_ledger"),
    "ledger_anchored_not_acknowledged": _crash_after_record,
}


@pytest.mark.parametrize("boundary", sorted(BOUNDARIES))
def test_a_crash_at_each_boundary_never_sends_and_a_retry_cancels_once(
    either: Rig, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    environment = environment_of(either)
    before = remote_state(either, environment)
    offered = signed(either)
    BOUNDARIES[boundary](monkeypatch)
    with pytest.raises(SimulatedCrash):
        cancel(either, offered, environment)
    monkeypatch.undo()

    try:
        between = execute(either.request(), environment).status
    except ForarbeiderRefusedError as error:
        between = f"refused:{error.detail}"
    assert between == "refused", between
    assert calls(environment) == 0

    retried = cancel(either, offered, environment)

    assert retried.status in ("cancelled", "already_cancelled")
    assert operations(either) == ["reserve", "cancel"]
    assert ledger_status(either) == "verified"
    assert len(cancel_records(either)) == 1
    assert attempts_status(either) == "verified"
    assert remote_state(either, environment) == before
    assert cancel(either, offered, environment).status == "already_cancelled"


def test_a_crash_during_the_remote_verification_writes_nothing(
    either: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:

    environment = environment_of(either)
    offered = signed(either)
    monkeypatch.setattr(publication_cancel, "history", crash)
    with pytest.raises(SimulatedCrash):
        cancel(either, offered, environment)
    monkeypatch.undo()
    assert operations(either) == ["reserve"]
    assert cancel_records(either) == []

    assert cancel(either, offered, environment).status == "cancelled"


def test_a_cancellation_anchors_through_the_identity_scope_only(either: Rig) -> None:
    """Preservation: the cancel record is the transaction's attempt, anchored per transaction."""
    assert cancel(either, signed(either), environment_of(either)).status == "cancelled"
    assert attempts_status(either) == "verified"
    assert publication_anchors.identified(either.request())
