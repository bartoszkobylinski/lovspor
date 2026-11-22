"""AR5c: the anchored budget ledger, its reservations and its exactly-once settlement (#738).

Every rig holds a durable ledger outside its workspace
(``<tmp>/ledger/synthetic-ledger/NNNNNN.json``) with the rig's reservation
appended and anchored by the rig's offline witness (AR5a). The attacks are
real: files deleted, rewritten or forged, a witness removed, a crash injected
at a boundary, several processes reserving at once.
"""

import hashlib
import multiprocessing
import shutil
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import publication_attempts, publication_ledger, publication_receipts
from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    anchor_pending,
    verify,
)
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import TrustRoot
from lovspor.forarbeider.publication_authorize import authorize
from lovspor.forarbeider.publication_execute import (
    ExecutionOutcome,
    Simulation,
    _context,
    _step,
    execute,
    publish_authorized,
)
from lovspor.forarbeider.publication_execution_models import (
    WAIVABLE_IN_SIMULATION,
    AttemptRecord,
)
from lovspor.forarbeider.publication_ledger import BudgetLedger
from lovspor.forarbeider.publication_recovery_models import RecoveryReceipt
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash, SimulatedGitHub
from lovspor.forarbeider.release_contract_models import BudgetUsage, LimitsEvidence
from lovspor.forarbeider.release_ledger import entry_bytes, plan_entry
from lovspor.forarbeider.release_ledger_models import LedgerEntry, LedgerOperation
from lovspor.forarbeider.release_limits import LimitsRequest, verify_limits
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_execution_support import (
    APP,
    LEDGER_ID,
    NOW,
    WITNESS,
    KeySigner,
    Rig,
    _root,
    anchor_queries,
    batch_usage,
    ledger_bytes,
    ledger_folder,
    ledger_query,
    make_rig,
    seed_ledger,
    seeded_ledger,
    write_entry,
)
from tests.unit.forarbeider_release_control_support import BOT

ONE = BudgetUsage(documents=1, files=1, bytes=1, commits=1)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


@pytest.fixture
def rig_b(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "b", "B")


def endpoint(rig: Rig, **changes: Any) -> SimulatedGitHub:
    fields: dict[str, Any] = {"head_oid": rig.base, "identity": APP, "now": NOW}
    return SimulatedGitHub(**(fields | changes))


def sim(github: SimulatedGitHub) -> Simulation:
    return Simulation(clock=lambda: NOW, transport=github)


def sim_b(rig: Rig) -> Simulation:
    return Simulation(clock=lambda: NOW, remote=rig.remote, signer=KeySigner(BOT, rig.bot))


def check(outcome: ExecutionOutcome, name: str) -> tuple[str, str, tuple[str, ...]]:
    assert outcome.evidence is not None
    found = next(c for c in outcome.evidence.checks if c.name == name)
    return found.name, found.status, found.reasons


def reasons_of(outcome: ExecutionOutcome, name: str) -> tuple[str, ...]:
    return check(outcome, name)[2]


def refused(action: Any) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    return raised.value


def entries(rig: Rig, ledger: BudgetLedger | None = None) -> list[LedgerEntry]:
    held = ledger_bytes(rig if ledger is None else replace(rig, ledger=ledger))
    return [LedgerEntry.model_validate_json(raw) for raw in held]


def operations(rig: Rig, ledger: BudgetLedger | None = None) -> list[str]:
    return [e.operation for e in entries(rig, ledger)]


def anchored(rig: Rig, ledger: BudgetLedger | None = None) -> str:
    held = rig if ledger is None else replace(rig, ledger=ledger)
    return verify(ledger_query(held), rig.anchoring(workspace=held.ledger.root)).status


def limits_of(rig: Rig, root: TrustRoot | None = None) -> LimitsEvidence:
    request = LimitsRequest(rig.inputs.policy.policy, rig.inputs.limits, NOW)
    return verify_limits(request, root or rig.root)


def empty_ledger(rig: Rig, name: str = "ledger-empty") -> BudgetLedger:
    """Another ledger, anchored empty, under its own id (the witness keys a chain by id)."""
    other = replace(rig, ledger=BudgetLedger(rig.tmp / name, f"{LEDGER_ID}-{name}"))
    seed_ledger(other, ())
    return other.ledger


def operation(release: str, **changes: Any) -> LedgerOperation:
    fields: dict[str, Any] = {
        "operation": "reserve",
        "ledger_id": LEDGER_ID,
        "day": NOW.date(),
        "release_id": release,
        "batch_id": "synthetic-batch",
        "amounts": ONE,
        "recorded_at": NOW - timedelta(minutes=4),
    }
    return LedgerOperation.model_validate(fields | changes)


def release(n: int) -> str:
    return hashlib.sha256(f"synthetic-release-{n}".encode()).hexdigest()


def anchoring(rig: Rig) -> tuple[AnchorAuthority, str]:
    return rig.anchoring(workspace=rig.ledger.root), rig.inputs.settings.repository


def append_anchored(rig: Rig, op: LedgerOperation) -> None:
    """Test setup with AR1b and AR5a primitives only: plan, write, anchor."""
    planned = plan_entry(ledger_bytes(rig), op, limits_of(rig))
    assert planned.entry is not None, planned.reasons
    write_entry(rig, entry_bytes(planned.entry))
    assert anchor_pending(ledger_query(rig), anchoring(rig)[0]).status == "verified"


def forged(rig: Rig, op: LedgerOperation) -> bytes:
    """A canonical, correctly linked next entry nobody anchored."""
    planned = plan_entry(ledger_bytes(rig), op, limits_of(rig))
    assert planned.entry is not None, planned.reasons
    return entry_bytes(planned.entry)


def forged_for(rig: Rig, ledger_id: str, op: LedgerOperation) -> bytes:
    """The first entry of another ledger, planned with AR1b only."""
    planned = plan_entry((), op, limits_of(rig))
    assert planned.entry is not None and planned.entry.ledger_id == ledger_id, planned.reasons
    return entry_bytes(planned.entry)


def authorized(request: Any, environment: Simulation) -> Any:
    evidence = authorize(_context(request, environment, _step(request)))
    assert evidence.decision == "simulation_only", evidence.denied_by
    return evidence


def published_elsewhere(rig: Rig, tid: str) -> None:
    """Another transaction of this workspace, recorded as published."""
    content = rig.transaction.content
    record = AttemptRecord.model_validate(
        {
            "transaction_id": tid,
            "release_manifest_sha256": content.release_manifest_sha256,
            "base_commit": content.publication.base_commit,
            "path": content.publication.path,
            "attempt_id": hashlib.sha256(tid.encode()).hexdigest(),
            "sequence": 1,
            "state": "published",
            "previous_sha256": None,
            "authorization_sha256": None,
            "commit": "e" * 40,
            "recorded_at": NOW - timedelta(minutes=3),
            "reasons": (),
        }
    )
    publication_attempts.append(rig.workspace, record)


def receipt_digest(rig: Rig, workspace: Path | None = None) -> str:
    folder = (workspace or rig.workspace) / "receipts"
    newest = sorted(folder.iterdir())[0]
    return hashlib.sha256(newest.read_bytes()).hexdigest()


# --- the anchored ledger before a simulation publishes ------------------------------------------


def test_an_anchored_reservation_publishes_and_the_ledger_check_is_enforced(rig: Rig) -> None:
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "published"
    assert check(outcome, "ar5_ledger_anchor")[1] == "passed"
    assert outcome.evidence is not None
    assert "ar5_ledger_anchor" not in outcome.evidence.waived


def test_the_ledger_anchor_is_never_waivable_in_simulation() -> None:
    assert "ar5_ledger_anchor" not in WAIVABLE_IN_SIMULATION


def test_no_durable_ledger_denies_and_sends_nothing(rig: Rig) -> None:
    github = endpoint(rig)

    outcome = execute(rig.request(ledger=None), sim(github))

    assert outcome.status == "denied"
    assert "ledger_store_missing" in reasons_of(outcome, "ar5_ledger_anchor")
    assert github.calls == []


def test_a_ledger_never_anchored_is_not_zero_usage(rig: Rig) -> None:
    github = endpoint(rig)
    nowhere = BudgetLedger(rig.tmp / "nowhere", f"{LEDGER_ID}-nowhere")

    outcome = execute(rig.request(ledger=nowhere), sim(github))

    assert outcome.status == "denied"
    assert "budget_ledger:unanchored" in reasons_of(outcome, "ar5_ledger_anchor")
    assert check(outcome, "budget")[1] == "failed"
    assert github.calls == []


def test_a_reservation_the_caller_offers_is_not_the_durable_ledger(rig: Rig) -> None:
    github = endpoint(rig)

    outcome = execute(rig.request(ledger=empty_ledger(rig)), sim(github))

    assert outcome.status == "denied"
    assert "budget_not_reserved_for_this_transaction" in reasons_of(outcome, "budget")
    assert github.calls == []
    nothing_offered = rig.request(inputs=rig.inputs._replace(ledger=()))
    assert execute(nothing_offered, sim(github)).status == "published"


def test_a_missing_witness_denies(rig: Rig) -> None:
    github = endpoint(rig)
    shutil.rmtree(rig.tmp / "witness")

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "budget_ledger:store_unavailable" in reasons_of(outcome, "ar5_ledger_anchor")
    assert github.calls == []


def test_a_truncated_ledger_is_rolled_back_and_denies(rig: Rig) -> None:
    github = endpoint(rig)
    (ledger_folder(rig) / "000001.json").unlink()

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "budget_ledger:local_rolled_back" in reasons_of(outcome, "ar5_ledger_anchor")
    assert github.calls == []


def test_a_gap_in_the_ledger_denies_without_changing_remaining_entries(rig: Rig) -> None:
    github = endpoint(rig)
    append_anchored(rig, operation(release(1)))
    folder = ledger_folder(rig)
    remaining = (folder / "000002.json").read_bytes()
    (folder / "000001.json").unlink()

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert check(outcome, "ar5_ledger_anchor")[1] == "failed"
    assert github.calls == []
    assert sorted(path.name for path in folder.iterdir()) == ["000002.json"]
    assert (folder / "000002.json").read_bytes() == remaining


def test_a_rewritten_entry_diverges_and_denies(rig: Rig) -> None:
    github = endpoint(rig)
    other = replace(rig, ledger=BudgetLedger(rig.tmp / "scratch", LEDGER_ID))
    other.ledger.root.mkdir(mode=0o700)
    ledger_folder(other).mkdir(mode=0o700)
    bigger = BudgetUsage(documents=5, files=9, bytes=40_000, commits=1)
    raw = forged(other, operation(rig.transaction.transaction_id, amounts=bigger))
    (ledger_folder(rig) / "000001.json").write_bytes(raw)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "budget_ledger:diverged" in reasons_of(outcome, "ar5_ledger_anchor")
    assert github.calls == []


def test_a_forged_unanchored_entry_denies(rig: Rig) -> None:
    github = endpoint(rig)
    write_entry(rig, forged(rig, operation(release(1))))

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "budget_ledger:pending_anchor" in reasons_of(outcome, "ar5_ledger_anchor")
    assert github.calls == []


def test_a_published_but_unsettled_transaction_blocks_further_spending(rig: Rig) -> None:
    github = endpoint(rig)
    other = release(7)
    append_anchored(rig, operation(other))
    published_elsewhere(rig, other)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert f"ledger_settlement_pending:{other}" in reasons_of(outcome, "budget")
    assert github.calls == []


# --- reservations ------------------------------------------------------------------------------


def test_a_reservation_is_appended_anchored_and_idempotent(rig: Rig) -> None:
    ledger = empty_ledger(rig)
    request = rig.request(ledger=ledger)

    first = publication_ledger.reserve(request, NOW)
    again = publication_ledger.reserve(request, NOW + timedelta(seconds=5))

    assert (first.status, again.status) == ("appended", "already_applied")
    assert operations(rig, ledger) == ["reserve"]
    assert first.entry.release_id == rig.transaction.transaction_id
    assert first.entry.batch_id == rig.transaction.content.batch_id
    assert first.entry.amounts == batch_usage(rig.transaction)
    assert anchored(rig, ledger) == "verified"
    assert execute(request, sim(endpoint(rig))).status == "published"


def test_a_conflicting_reservation_is_refused_and_writes_nothing(rig: Rig) -> None:
    before = ledger_bytes(rig)
    conflicting = operation(rig.transaction.transaction_id, batch_id="another-batch")

    error = refused(
        lambda: publication_ledger.record(rig.ledger, conflicting, limits_of(rig), anchoring(rig))
    )

    assert "ledger_conflicting_reservation" in error.detail
    assert ledger_bytes(rig) == before


def test_an_exhausted_daily_limit_refuses_the_next_reservation(rig: Rig) -> None:
    """Three releases reserved and settled one after another use today's three commits."""
    ledger = empty_ledger(rig, "daily")
    held = replace(rig, ledger=ledger)
    for n in (1, 2, 3):
        for kind in ("reserve", "commit"):
            step = operation(release(n), ledger_id=ledger.ledger_id, operation=kind)
            publication_ledger.record(ledger, step, limits_of(rig), anchoring(held))
    before = ledger_bytes(held)

    error = refused(
        lambda: publication_ledger.record(
            ledger,
            operation(release(4), ledger_id=ledger.ledger_id),
            limits_of(rig),
            anchoring(held),
        )
    )

    assert "budget_commits_exceeded" in error.detail
    assert ledger_bytes(held) == before
    assert anchored(rig, ledger) == "verified"


def test_revoked_owner_limits_refuse_reservation_and_publication(rig: Rig) -> None:
    revoked = _root(rig.tmp / "revoked-root", rig.owner, rig.bot, rig.witness_key, (rig.owner,))
    ledger = empty_ledger(rig)

    error = refused(
        lambda: publication_ledger.reserve(rig.request(ledger=ledger, root=revoked), NOW)
    )

    assert "limits_unverified" in error.detail
    assert operations(rig, ledger) == []


def test_a_reservation_while_the_witness_is_down_writes_nothing(rig: Rig) -> None:
    ledger = empty_ledger(rig)
    shutil.rmtree(rig.tmp / "witness")

    error = refused(lambda: publication_ledger.reserve(rig.request(ledger=ledger), NOW))

    assert "budget_ledger:store_unavailable" in error.detail
    assert operations(rig, ledger) == []


def test_a_held_ledger_lock_refuses_a_second_writer_from_another_workspace(rig: Rig) -> None:
    github = endpoint(rig)
    elsewhere = rig.tmp / "elsewhere"
    elsewhere.mkdir(mode=0o700)

    with publication_ledger.ledger_lock(rig.ledger):
        error = refused(lambda: execute(rig.request(workspace=elsewhere), sim(github)))
        writer = refused(
            lambda: publication_ledger.record(
                rig.ledger, operation(release(1)), limits_of(rig), anchoring(rig)
            )
        )

    assert error.reason is RefusalReason.CORPUS_LOCKED and "ledger" in error.detail
    assert writer.reason is RefusalReason.CORPUS_LOCKED
    assert github.calls == []


def _reserve_in_process(job: tuple[Any, ...]) -> str:
    """One process: reserve one synthetic release, retrying only while the ledger is locked."""
    root, ledger, witness_dir, witness_key, material, repository, number, start = job
    policy, offered = material
    limits = verify_limits(LimitsRequest(policy, offered, NOW), root)  # the seal is per process
    witness = OfflineAnchorWitness(witness_dir, KeySigner(WITNESS, witness_key))
    authority = AnchorAuthority(witness, root, NOW, None, ledger.root)
    while time.time() < start:
        time.sleep(0.001)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            mine = operation(release(number), ledger_id=ledger.ledger_id)
            done = publication_ledger.record(ledger, mine, limits, (authority, repository))
        except ForarbeiderRefusedError as error:
            if error.reason is RefusalReason.CORPUS_LOCKED:
                time.sleep(0.002)
                continue
            return f"refused:{error.reason.value}:{error.detail}:{error.__cause__!r}"
        return done.status
    return "timeout"


# --- settlement ------------------------------------------------------------------------------


def test_a_lost_response_is_settled_once_from_the_verified_receipt(rig: Rig) -> None:
    github = endpoint(rig, fault="lost")
    assert execute(rig.request(), sim(github)).status == "outcome_unknown"
    github.fault = None

    outcome = recover(rig.request(), sim(github))
    again = recover(rig.request(), sim(github))

    assert outcome.settlement is not None and outcome.settlement.status == "settled"
    assert again.settlement is not None and again.settlement.status == "already_settled"
    assert operations(rig) == ["reserve", "commit"]
    commit = entries(rig)[-1]
    assert commit.commit == github.head_oid
    assert commit.receipt_sha256 == receipt_digest(rig)
    assert commit.amounts == entries(rig)[0].amounts
    assert anchored(rig) == "verified"
    assert len(github.calls) == 1


def test_an_api_response_alone_never_settles(rig: Rig) -> None:
    github = endpoint(rig)
    assert execute(rig.request(), sim(github)).status == "published"
    assert operations(rig) == ["reserve"]

    outcome = recover(rig.request(), sim(github))

    assert outcome.settlement is not None and outcome.settlement.status == "settled"
    assert operations(rig) == ["reserve", "commit"]


def test_path_b_is_settled_from_the_remote(rig_b: Rig) -> None:
    assert execute(rig_b.request(), sim_b(rig_b)).status == "published"

    outcome = recover(rig_b.request(), sim_b(rig_b))

    assert outcome.settlement is not None and outcome.settlement.status == "settled"
    assert operations(rig_b) == ["reserve", "commit"]
    assert anchored(rig_b) == "verified"


def test_an_unknown_authorization_is_never_settled_as_authorized(rig: Rig) -> None:
    github = endpoint(rig)
    assert execute(rig.request(), sim(github)).status == "published"
    elsewhere = rig.tmp / "elsewhere"
    elsewhere.mkdir(mode=0o700)

    outcome = recover(rig.request(workspace=elsewhere), sim(github))

    assert outcome.classification == "published_authorization_unknown"
    assert outcome.settlement is not None and outcome.settlement.status == "withheld"
    assert "published_authorization_unknown" in outcome.settlement.reasons
    assert operations(rig) == ["reserve"]


def test_an_integrity_failure_is_never_settled(rig: Rig) -> None:
    github = endpoint(rig)
    github.advance({"other.txt": b"x"}, ("not ours", ""), (APP, True))

    outcome = recover(rig.request(), sim(github))

    assert outcome.classification in ("halted_foreign_commit", "halted_integrity_failed")
    assert outcome.settlement is None or outcome.settlement.status != "settled"
    assert operations(rig) == ["reserve"]


def test_a_second_different_settlement_is_refused(rig: Rig) -> None:
    github = endpoint(rig)
    execute(rig.request(), sim(github))
    recover(rig.request(), sim(github))
    first = entries(rig)[-1]
    before = ledger_bytes(rig)

    for changes in ({"commit": "f" * 40}, {"receipt_sha256": "a" * 64}):
        duplicate = operation(
            rig.transaction.transaction_id,
            operation="commit",
            batch_id=first.batch_id,
            amounts=first.amounts,
            recorded_at=first.recorded_at,
            commit=first.commit,
            receipt_sha256=first.receipt_sha256,
        ).model_copy(update=changes)
        error = refused(
            lambda d=duplicate: publication_ledger.record(
                rig.ledger, d, limits_of(rig), anchoring(rig)
            )
        )
        assert "ledger_conflicting_settlement" in error.detail
    assert ledger_bytes(rig) == before


def test_a_settlement_waits_for_its_receipt_to_be_anchored(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    execute(rig.request(), sim(github))
    real = OfflineAnchorWitness.submit

    def refuse_receipts(self: OfflineAnchorWitness, anchor: Any) -> Any:
        if anchor.family == "recovery_receipts":
            raise ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, "witness_busy")
        return real(self, anchor)

    monkeypatch.setattr(OfflineAnchorWitness, "submit", refuse_receipts)
    first = recover(rig.request(), sim(github))
    monkeypatch.undo()

    assert first.settlement is not None and first.settlement.status == "refused"
    assert any("recovery_receipts" in r for r in first.settlement.reasons)
    assert operations(rig) == ["reserve"]
    again = recover(rig.request(), sim(github))
    assert again.settlement is not None and again.settlement.status == "settled"


def test_revoked_limits_leave_a_publication_unsettled_and_block_spending(rig: Rig) -> None:
    github = endpoint(rig)
    execute(rig.request(), sim(github))
    revoked = _root(rig.tmp / "revoked-root", rig.owner, rig.bot, rig.witness_key, (rig.owner,))

    outcome = recover(rig.request(root=revoked), sim(github))

    # The revoked owner key also leaves the authorization unknown: withheld, not settled.
    assert outcome.settlement is not None and outcome.settlement.status in ("refused", "withheld")
    assert operations(rig) == ["reserve"]
    error = refused(lambda: publication_ledger.reserve(rig.request(root=revoked), NOW))
    assert "limits_unverified" in error.detail


# --- crash at every boundary -----------------------------------------------------------------


def crash_on(monkeypatch: pytest.MonkeyPatch, family: str) -> None:
    real = OfflineAnchorWitness.submit

    def crash(self: OfflineAnchorWitness, anchor: Any) -> Any:
        if anchor.family == family:
            raise SimulatedCrash
        return real(self, anchor)

    monkeypatch.setattr(OfflineAnchorWitness, "submit", crash)


def test_a_crash_before_the_reservation_leaves_no_entry_and_denies(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = empty_ledger(rig)
    request = rig.request(ledger=ledger)

    def die(*args: Any) -> None:
        raise SimulatedCrash

    monkeypatch.setattr(publication_ledger, "append_entry", die)
    with pytest.raises(SimulatedCrash):
        publication_ledger.reserve(request, NOW)
    monkeypatch.undo()

    assert operations(rig, ledger) == []
    assert anchored(rig, ledger) == "verified"
    assert execute(request, sim(endpoint(rig))).status == "denied"


def test_a_crash_after_the_reservation_before_its_anchor_denies_until_retried(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    ledger = empty_ledger(rig)
    request = rig.request(ledger=ledger)
    crash_on(monkeypatch, "budget_ledger")
    with pytest.raises(SimulatedCrash):
        publication_ledger.reserve(request, NOW)
    monkeypatch.undo()

    denied = execute(request, sim(github))
    assert denied.status == "denied"
    assert "budget_ledger:pending_anchor" in reasons_of(denied, "ar5_ledger_anchor")
    assert publication_ledger.reserve(request, NOW).status == "completed"
    assert operations(rig, ledger) == ["reserve"]
    assert execute(request, sim(github)).status == "published"
    assert len(github.calls) == 1


def test_a_crash_after_the_anchor_before_the_send_is_sent_once_after_recovery(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    original = publication_attempts.append

    def die(workspace: Path, record: Any) -> None:
        original(workspace, record)
        if record.state == "prepared_to_send":
            raise SimulatedCrash

    monkeypatch.setattr(publication_attempts, "append", die)
    with pytest.raises(SimulatedCrash):
        execute(rig.request(), sim(github))
    monkeypatch.undo()

    assert github.calls == []
    assert recover(rig.request(), sim(github)).classification == "not_published"
    assert execute(rig.request(), sim(github)).status == "published"
    settled = recover(rig.request(), sim(github))
    assert settled.settlement is not None and settled.settlement.status == "settled"
    assert operations(rig) == ["reserve", "commit"]
    assert len(github.calls) == 1


def test_a_crash_after_the_mutation_before_the_response_is_settled_once(rig: Rig) -> None:
    github = endpoint(rig, fault="crash")
    with pytest.raises(SimulatedCrash):
        execute(rig.request(), sim(github))
    github.fault = None

    first = recover(rig.request(), sim(github))
    again = recover(rig.request(), sim(github))

    assert first.settlement is not None and first.settlement.status == "settled"
    assert again.settlement is not None and again.settlement.status == "already_settled"
    assert operations(rig) == ["reserve", "commit"]
    assert execute(rig.request(), sim(github)).status == "unchanged"
    assert len(github.calls) == 1


def test_a_crash_after_the_receipt_before_the_ledger_commit_is_settled_on_retry(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    execute(rig.request(), sim(github))

    def die(*args: Any) -> None:
        raise SimulatedCrash

    monkeypatch.setattr(publication_ledger, "append_entry", die)
    with pytest.raises(SimulatedCrash):
        recover(rig.request(), sim(github))
    monkeypatch.undo()

    assert operations(rig) == ["reserve"]
    assert execute(rig.request(), sim(github)).status == "unchanged"
    again = recover(rig.request(), sim(github))
    assert again.classification == "already_receipted"
    assert again.settlement is not None and again.settlement.status == "settled"
    assert operations(rig) == ["reserve", "commit"]
    assert len(github.calls) == 1


def test_a_crash_after_the_ledger_commit_before_its_anchor_is_completed_once(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    execute(rig.request(), sim(github))
    crash_on(monkeypatch, "budget_ledger")
    with pytest.raises(SimulatedCrash):
        recover(rig.request(), sim(github))
    monkeypatch.undo()

    assert operations(rig) == ["reserve", "commit"]
    assert anchored(rig) == "pending_anchor"
    again = recover(rig.request(), sim(github))
    assert again.settlement is not None and again.settlement.status == "settled"
    assert operations(rig) == ["reserve", "commit"]
    assert anchored(rig) == "verified"


def test_a_crash_after_the_anchor_before_the_acknowledgement_charges_nothing_more(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    execute(rig.request(), sim(github))
    real = publication_ledger.settle

    def settle_then_die(*args: Any) -> Any:
        real(*args)
        raise SimulatedCrash

    monkeypatch.setattr(publication_ledger, "settle", settle_then_die)
    with pytest.raises(SimulatedCrash):
        recover(rig.request(), sim(github))
    monkeypatch.undo()

    before = ledger_bytes(rig)
    again = recover(rig.request(), sim(github))
    assert again.settlement is not None and again.settlement.status == "already_settled"
    assert ledger_bytes(rig) == before
    assert operations(rig) == ["reserve", "commit"]


def test_a_forged_entry_pending_is_never_completed_by_another_operation(rig: Rig) -> None:
    write_entry(rig, forged(rig, operation(release(1))))

    error = refused(
        lambda: publication_ledger.record(
            rig.ledger, operation(release(2)), limits_of(rig), anchoring(rig)
        )
    )

    assert "budget_ledger:pending_anchor" in error.detail
    assert anchored(rig) == "pending_anchor"


# --- added after the first sabotage pass: each isolates one guard -------------------------------


def test_a_reservation_under_another_batch_denies(rig: Rig) -> None:
    github = endpoint(rig)
    other_batch = seeded_ledger(
        rig,
        "other-batch",
        (
            forged_for(
                rig,
                f"{LEDGER_ID}-other-batch",
                operation(
                    rig.transaction.transaction_id,
                    ledger_id=f"{LEDGER_ID}-other-batch",
                    batch_id="another-batch",
                    amounts=batch_usage(rig.transaction),
                ),
            ),
        ),
    )

    outcome = execute(rig.request(ledger=other_batch), sim(github))

    assert outcome.status == "denied"
    assert "reservation_not_this_batch" in reasons_of(outcome, "budget")
    assert github.calls == []


def test_a_settlement_records_the_exact_usage_not_the_reservation(rig: Rig) -> None:
    github = endpoint(rig)
    usage = batch_usage(rig.transaction)
    roomy = usage.model_copy(update={"bytes": usage.bytes + 1000})
    name = f"{LEDGER_ID}-roomy"
    ledger = seeded_ledger(
        rig,
        "roomy",
        (
            forged_for(
                rig,
                name,
                operation(
                    rig.transaction.transaction_id,
                    ledger_id=name,
                    batch_id=rig.transaction.content.batch_id,
                    amounts=roomy,
                ),
            ),
        ),
    )
    request = rig.request(ledger=ledger)
    assert execute(request, sim(github)).status == "published"

    outcome = recover(request, sim(github))

    assert outcome.settlement is not None and outcome.settlement.status == "settled"
    assert entries(rig, ledger)[-1].amounts == usage
    assert entries(rig, ledger)[0].amounts == roomy


def test_evidence_issued_before_a_ledger_rollback_is_refused(rig: Rig) -> None:
    github = endpoint(rig)
    evidence = authorized(rig.request(), sim(github))
    (ledger_folder(rig) / "000001.json").unlink()

    error = refused(lambda: publish_authorized(evidence, rig.request(), sim(github)))

    assert "budget_ledger:local_rolled_back" in error.detail
    assert github.calls == []


def test_a_settlement_against_a_reservation_of_another_batch_is_refused(rig: Rig) -> None:
    """Added after the second sabotage pass: authorization already refuses this batch."""
    github = endpoint(rig)
    assert execute(rig.request(), sim(github)).status == "published"
    name = f"{LEDGER_ID}-misfiled"
    misfiled = seeded_ledger(
        rig,
        "misfiled",
        (
            forged_for(
                rig,
                name,
                operation(
                    rig.transaction.transaction_id,
                    ledger_id=name,
                    batch_id="another-batch",
                    amounts=batch_usage(rig.transaction),
                ),
            ),
        ),
    )

    outcome = recover(rig.request(ledger=misfiled), sim(github))

    assert outcome.settlement is not None and outcome.settlement.status == "refused"
    assert outcome.settlement.reasons == ("ledger_settlement_batch_mismatch",)
    assert operations(rig, misfiled) == ["reserve"]


# --- review fix 1: a settlement rests on a receipt of the anchored chain --------------------------


@pytest.fixture(params=["A", "B"])
def either(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / f"path-{request.param.lower()}", request.param)


def environment_of(rig: Rig) -> Simulation:
    return sim(endpoint(rig)) if rig.path == "A" else sim_b(rig)


def recovered_unsettled(rig: Rig, environment: Simulation) -> RecoveryReceipt:
    """Publish, then recover without a ledger: the receipt is written and anchored, not settled."""
    assert execute(rig.request(), environment).status == "published"
    found = recover(rig.request(ledger=None), environment)
    assert found.receipt is not None and found.settlement is None
    assert found.receipt.classification == "published_authorized"
    assert operations(rig) == ["reserve"]
    return found.receipt


def altered(receipt: RecoveryReceipt, **changes: Any) -> RecoveryReceipt:
    return RecoveryReceipt.model_validate(receipt.model_dump() | changes)


def anchor_receipts(rig: Rig) -> None:
    """Test setup with AR5a primitives only: anchor the workspace's receipts as they stand."""
    query = anchor_queries(rig)["recovery_receipts"]
    assert anchor_pending(query, rig.anchoring()).status == "verified"


def test_a_forged_published_authorized_receipt_never_settles(either: Rig) -> None:
    genuine = recovered_unsettled(either, environment_of(either))
    forged_receipt = altered(genuine, remote_event_at=NOW - timedelta(days=1))
    assert forged_receipt.classification == "published_authorized"

    outcome = publication_ledger.settle(either.request(), forged_receipt, NOW)

    assert outcome.status == "refused"
    assert "receipt_not_in_anchored_chain" in outcome.reasons
    assert operations(either) == ["reserve"]


def test_an_anchored_chain_holding_another_receipt_settles_nothing_for_a_substitute(
    either: Rig,
) -> None:
    genuine = recovered_unsettled(either, environment_of(either))
    substitute = altered(genuine, commit="f" * 40)

    outcome = publication_ledger.settle(either.request(), substitute, NOW)

    assert outcome.status == "refused"
    assert "receipt_not_in_anchored_chain" in outcome.reasons
    assert operations(either) == ["reserve"]


def test_a_receipt_of_another_transaction_never_settles(rig: Rig, shared: Rig) -> None:
    genuine = recovered_unsettled(rig, sim(endpoint(rig)))
    publication_receipts.append(shared.workspace, genuine)  # the other chain truly holds it
    anchor_receipts(shared)

    outcome = publication_ledger.settle(shared.request(), genuine, NOW)

    assert outcome.status == "refused"
    assert "receipt_transaction_mismatch" in outcome.reasons
    assert operations(rig) == ["reserve"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("release_manifest_sha256", "a" * 64, "receipt_manifest_mismatch"),
        ("path", "B", "receipt_path_mismatch"),
        ("expected_head_oid", "c" * 40, "receipt_base_mismatch"),
    ],
)
def test_a_receipt_of_another_manifest_path_or_base_never_settles(
    rig: Rig, field: str, value: str, reason: str
) -> None:
    genuine = recovered_unsettled(rig, sim(endpoint(rig)))
    content = rig.transaction.content
    if field == "release_manifest_sha256":
        content = content.model_copy(update={field: value})
    else:
        publication = content.publication.model_copy(update={field: value})
        content = content.model_copy(update={"publication": publication})
    transaction = rig.transaction.model_copy(update={"content": content})
    inputs = rig.inputs._replace(transaction=transaction)

    outcome = publication_ledger.settle(rig.request(inputs=inputs), genuine, NOW)

    assert outcome.status == "refused"
    assert reason in outcome.reasons
    assert operations(rig) == ["reserve"]


def test_conflicting_published_receipts_for_one_transaction_settle_nothing(rig: Rig) -> None:
    genuine = recovered_unsettled(rig, sim(endpoint(rig)))
    first = (rig.workspace / "receipts" / "000001.json").read_bytes()
    second = altered(
        genuine, sequence=2, previous_sha256=hashlib.sha256(first).hexdigest(), commit="f" * 40
    )
    publication_receipts.append(rig.workspace, second)
    anchor_receipts(rig)

    outcome = publication_ledger.settle(rig.request(), genuine, NOW)

    assert outcome.status == "refused"
    assert "receipt_ambiguous" in outcome.reasons
    assert operations(rig) == ["reserve"]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"ours": False}, "receipt_pipeline_commit_mismatch"),
        ({"parent": "d" * 40}, "receipt_parent_mismatch"),
    ],
)
def test_an_anchored_receipt_not_of_this_pipeline_commit_on_its_base_never_settles(
    rig: Rig, changes: dict[str, Any], reason: str
) -> None:
    """Added after the implementation: each isolates one binding of the remote commit."""
    genuine = recovered_unsettled(rig, sim(endpoint(rig)))
    first = (rig.workspace / "receipts" / "000001.json").read_bytes()
    linked = {"sequence": 2, "previous_sha256": hashlib.sha256(first).hexdigest()}
    unbound = altered(genuine, **(linked | changes))
    assert unbound.classification == "published_authorized"
    publication_receipts.append(rig.workspace, unbound)
    anchor_receipts(rig)

    outcome = publication_ledger.settle(rig.request(), unbound, NOW)

    assert outcome.status == "refused"
    assert reason in outcome.reasons
    assert operations(rig) == ["reserve"]


def test_the_exact_anchored_receipt_settles_once(either: Rig) -> None:
    genuine = recovered_unsettled(either, environment_of(either))

    first = publication_ledger.settle(either.request(), genuine, NOW)
    again = publication_ledger.settle(either.request(), genuine, NOW)

    assert (first.status, again.status) == ("settled", "already_settled")
    commit = entries(either)[-1]
    assert operations(either) == ["reserve", "commit"]
    assert commit.commit == genuine.commit
    assert commit.receipt_sha256 == hashlib.sha256(canonical_text(genuine).encode()).hexdigest()
    assert anchored(either) == "verified"


# --- review fix 2: one ledger, several workspaces ------------------------------------------------


@pytest.fixture
def shared(rig: Rig, tmp_path: Path) -> Rig:
    """Another workspace, transaction and workspace witness, on ``rig``'s ledger and keys."""
    return make_rig(tmp_path / "workspace-b", "A", share=rig)


def test_a_publication_unsettled_in_one_workspace_denies_spending_in_another(
    rig: Rig, shared: Rig
) -> None:
    assert execute(rig.request(), sim(endpoint(rig))).status == "published"
    pending = rig.transaction.transaction_id

    error = refused(lambda: publication_ledger.reserve(shared.request(), NOW))
    other = endpoint(shared)
    outcome = execute(shared.request(), sim(other))

    assert f"ledger_reservation_open:{pending}" in error.detail
    assert outcome.status == "denied"
    assert f"ledger_settlement_pending:{pending}" in reasons_of(outcome, "budget")
    assert other.calls == []
    assert operations(rig) == ["reserve"]


def test_once_settled_another_workspace_reserves_and_publishes(rig: Rig, shared: Rig) -> None:
    github = endpoint(rig)
    assert execute(rig.request(), sim(github)).status == "published"
    settled = recover(rig.request(), sim(github))
    assert settled.settlement is not None and settled.settlement.status == "settled"
    other = endpoint(shared)

    reserved = publication_ledger.reserve(shared.request(), NOW)
    published = execute(shared.request(), sim(other))
    accounted = recover(shared.request(), sim(other))

    assert reserved.status == "appended"
    assert published.status == "published"
    assert check(published, "ar5_ledger_anchor")[1] == "passed"
    assert accounted.settlement is not None and accounted.settlement.status == "settled"
    assert operations(rig) == ["reserve", "commit", "reserve", "commit"]
    assert anchored(rig) == "verified"
    assert len(github.calls) == len(other.calls) == 1


def test_a_rolled_back_shared_ledger_denies_the_other_workspace(rig: Rig, shared: Rig) -> None:
    github = endpoint(rig)
    execute(rig.request(), sim(github))
    recover(rig.request(), sim(github))
    publication_ledger.reserve(shared.request(), NOW)
    (ledger_folder(rig) / "000002.json").unlink()
    (ledger_folder(rig) / "000003.json").rename(ledger_folder(rig) / "000002.json")
    other = endpoint(shared)

    outcome = execute(shared.request(), sim(other))

    assert outcome.status == "denied"
    assert any("budget_ledger:" in r for r in reasons_of(outcome, "ar5_ledger_anchor"))
    assert reasons_of(outcome, "ar5_ledger_anchor") != ("budget_ledger:unanchored",)
    assert other.calls == []


def test_the_ledger_witness_unavailable_denies_the_other_workspace(rig: Rig, shared: Rig) -> None:
    github = endpoint(rig)
    execute(rig.request(), sim(github))
    recover(rig.request(), sim(github))
    shutil.move(str(rig.tmp / "witness"), str(rig.tmp / "witness-away"))
    other = endpoint(shared)

    error = refused(lambda: publication_ledger.reserve(shared.request(), NOW))
    outcome = execute(shared.request(), sim(other))

    assert "anchor:budget_ledger:store_unavailable" in error.detail
    assert outcome.status == "denied"
    assert "budget_ledger:store_unavailable" in reasons_of(outcome, "ar5_ledger_anchor")
    assert other.calls == []


def test_a_crash_while_settling_blocks_the_other_workspace_until_retried(
    rig: Rig, shared: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    execute(rig.request(), sim(github))
    crash_on(monkeypatch, "budget_ledger")
    with pytest.raises(SimulatedCrash):
        recover(rig.request(), sim(github))
    monkeypatch.undo()

    blocked = refused(lambda: publication_ledger.reserve(shared.request(), NOW))
    again = recover(rig.request(), sim(github))
    reserved = publication_ledger.reserve(shared.request(), NOW)

    assert "anchor:budget_ledger:pending_anchor" in blocked.detail
    assert again.settlement is not None and again.settlement.status == "settled"
    assert reserved.status == "appended"
    assert operations(rig) == ["reserve", "commit", "reserve"]


def test_concurrent_processes_hold_at_most_one_open_reservation(rig: Rig) -> None:
    """Six processes (any workspace) on one anchored, empty ledger: one reserves, five wait."""
    ledger = empty_ledger(rig, "race")
    start = time.time() + 2.0
    jobs = [
        (
            rig.root,
            ledger,
            rig.tmp / "witness",
            rig.witness_key,
            (rig.inputs.policy.policy, rig.inputs.limits),
            rig.inputs.settings.repository,
            n,
            start,
        )
        for n in range(1, 7)
    ]
    with multiprocessing.get_context("spawn").Pool(6) as pool:
        results = pool.map(_reserve_in_process, jobs)

    assert results.count("appended") == 1, results
    assert all(r == "appended" or "ledger_reservation_open" in r for r in results), results
    assert operations(rig, ledger) == ["reserve"]
    assert anchored(rig, ledger) == "verified"
