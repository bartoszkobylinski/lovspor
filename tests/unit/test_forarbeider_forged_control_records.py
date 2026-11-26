"""AR5 integration: hand-written control records the owner never signed are never adopted (#738).

Reproductions of #1027 (a forged stop-latch clearing anchored and adopted) and
#1028 (a forged cancellation decision that skipped the never-sent proof). The
attacker has write access to the workspace, nothing more: no owner key.
Synthetic only; the witness, signatures and recovery are the real ones.
"""

from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import publication_anchors, publication_attempts, publication_ledger
from lovspor.forarbeider.control_plane_anchor import verify
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.publication_anchors import initialize_attempts, initialize_control
from lovspor.forarbeider.publication_cancel_models import CANCELLED
from lovspor.forarbeider.publication_execute import attempt_id_of, execute
from lovspor.forarbeider.publication_execution_models import AttemptRecord, LatchEvent
from lovspor.forarbeider.publication_ledger import BudgetLedger, initialize_ledger
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_stop_resume import record_stop, resume
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_execution_support import (
    NOW,
    WITNESS,
    KeySigner,
    _root,
    anchor_queries,
    make_rig,
    policy_id_of,
)
from tests.unit.test_forarbeider_publication_stop_resume import (
    LATER,
    endpoint,
    first,
    resume_of,
    sim,
    stop_of,
    with_control,
)
from tests.unit.test_forarbeider_reservation_cancellation import (
    cancel,
    environment_of,
    operations,
    signed,
    tid,
)


def refused(action: object) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()  # type: ignore[operator]
    return raised.value


def forged_clearing(workspace: Path, transaction_id: str, resume_sha256: str) -> None:
    """One correctly linked ``cleared`` event, hand-written into the workspace."""
    sequence, previous = publication_attempts.latch_head(workspace)
    forged = LatchEvent(
        sequence=sequence,
        previous_sha256=previous,
        kind="cleared",
        at=LATER,
        transaction_id=transaction_id,
        reasons=("forged",),
        resume_control_sha256=resume_sha256,
    )
    publication_attempts.create_once(
        workspace / "stop-latch", f"{sequence:06d}.json", canonical_text(forged).encode()
    )


@pytest.mark.parametrize("adopter", ["recover", "resume", "execute"])
def test_a_forged_latch_clearing_is_never_anchored_or_adopted(tmp_path: Path, adopter: str) -> None:
    """#1027: without an owner RESUME the clearing stays unanchored and nothing is sent."""
    rig = make_rig(tmp_path / "a", "A")
    github = endpoint(rig)
    transaction = rig.transaction.transaction_id
    request = rig.request()
    publication_attempts.latch(rig.workspace, ("receipt_conflict",), github.now, transaction)
    assert publication_anchors.settle(request, "stop_latch") is not None
    assert execute(request, sim(github, LATER)).status == "denied"

    forged_clearing(rig.workspace, transaction, "d" * 64)
    if adopter == "recover":
        recover(request, sim(github, LATER))
    elif adopter == "resume":
        refused(lambda: resume(request, LATER))  # the newest owner record is no RESUME

    query = anchor_queries(rig)["stop_latch"]
    assert verify(query, rig.anchoring()).status == "pending_anchor"
    outcome = execute(request, sim(github, LATER))
    assert outcome.status == "denied"
    assert github.calls == []


def test_a_forged_clearing_naming_a_resume_older_than_the_latch_is_refused(
    tmp_path: Path,
) -> None:
    rig = make_rig(tmp_path / "a", "A")
    github = endpoint(rig)
    transaction = rig.transaction.transaction_id
    publication_attempts.latch(rig.workspace, ("receipt_conflict",), github.now, transaction)
    publication_anchors.settle(rig.request(), "stop_latch")
    newest = sha256(rig.inputs.control[-1].record).hexdigest()  # a genuine, older RESUME

    forged_clearing(rig.workspace, transaction, newest)
    query = anchor_queries(rig)["stop_latch"]
    reasons = publication_anchors.clearing_reasons(rig.request(), query, rig.anchoring())

    assert reasons == ("latch_clearing_resume_predates_latch",)
    assert publication_anchors.settle(rig.request(), "stop_latch") is None
    assert execute(rig.request(), sim(github, LATER)).status == "denied"


def test_a_forged_cancel_decision_after_a_begun_send_never_cancels(tmp_path: Path) -> None:
    """#1028: the decision must follow the signed attempts head, with nothing sent before it."""
    rig = make_rig(tmp_path / "a", "A")
    offered = signed(rig)  # the owner signed while nothing was sent (attempts head None)
    assert execute(rig.request(), environment_of(rig, fault="lost")).status == "outcome_unknown"
    sequence, previous = publication_attempts.head(rig.workspace, tid(rig))
    content = rig.transaction.content
    forged = AttemptRecord(
        transaction_id=tid(rig),
        release_manifest_sha256=content.release_manifest_sha256,
        base_commit=content.publication.base_commit,
        path=content.publication.path,
        attempt_id=attempt_id_of(tid(rig), sequence),
        sequence=sequence,
        state="refused",
        recorded_at=NOW,
        authorization_sha256=sha256(offered.record).hexdigest(),
        commit=None,
        reasons=(CANCELLED,),
        previous_sha256=previous,
    )
    publication_attempts.append(rig.workspace, forged)

    error = refused(lambda: cancel(rig, offered, environment_of(rig)))

    assert error.detail.endswith("cancellation_record_not_the_owners"), error.detail
    assert operations(rig) == ["reserve"]


# --- explicit bootstrap: never adopts or resets a history ----------------------------------------


def fresh_witness(rig: Any, name: str) -> OfflineAnchorWitness:
    folder = rig.tmp / name
    folder.mkdir(mode=0o700)
    return OfflineAnchorWitness(folder, KeySigner(WITNESS, rig.witness_key))


def test_the_attempts_bootstrap_runs_once_and_never_adopts_records(tmp_path: Path) -> None:
    rig = make_rig(tmp_path / "a", "A")  # the rig bootstraps through initialize_attempts
    assert refused(lambda: initialize_attempts(rig.request())).detail == "already_anchored"

    github = endpoint(rig)
    assert execute(rig.request(), sim(github)).status == "published"
    elsewhere = rig.request(anchoring=rig.anchoring(witness=fresh_witness(rig, "w2")), ledger=None)

    error = refused(lambda: initialize_attempts(elsewhere))

    assert error.detail == "anchor:unanchored"  # records on disk, nothing anchored: refused


def test_the_ledger_bootstrap_runs_once_and_never_adopts_entries(tmp_path: Path) -> None:
    rig = make_rig(tmp_path / "a", "A")  # its ledger was bootstrapped by initialize_ledger
    repository = rig.inputs.settings.repository
    again = refused(lambda: initialize_ledger(rig.ledger, rig.anchoring(), repository))
    assert again.detail == "already_anchored"

    elsewhere = rig.anchoring(witness=fresh_witness(rig, "w2"))
    error = refused(lambda: initialize_ledger(rig.ledger, elsewhere, repository))
    assert error.detail == "anchor:unanchored"

    empty = BudgetLedger(rig.tmp / "new-ledger", "synthetic-ledger-new")
    assert initialize_ledger(empty, rig.anchoring(), repository).status == "verified"


def test_a_forged_clearing_naming_a_real_resume_never_lifts_an_unresolvable_latch(
    tmp_path: Path,
) -> None:
    """Even an anchored owner RESUME after the latch does not resolve a receipt conflict."""
    rig = make_rig(tmp_path / "a", "A")
    github = endpoint(rig)
    transaction = rig.transaction.transaction_id
    publication_attempts.latch(rig.workspace, ("receipt_conflict",), NOW, transaction)
    publication_anchors.settle(rig.request(), "stop_latch")
    stop = stop_of(rig, first(rig))
    owner_resume = resume_of(rig, stop, 3, stop)
    request = with_control(rig, first(rig), stop, owner_resume)
    publication_anchors.sync_control(request, policy_id_of(rig), LATER)

    forged_clearing(rig.workspace, transaction, sha256(owner_resume.record).hexdigest())
    query = anchor_queries(rig)["stop_latch"]

    reasons = publication_anchors.clearing_reasons(request, query, rig.anchoring())
    assert reasons == ("latch_clearing_cause_unresolved",)
    assert publication_anchors.settle(request, "stop_latch") is None
    assert execute(request, sim(github, LATER)).status == "denied"
    assert github.calls == []


# --- #1029: one control history per shared ledger ----------------------------------------------


def test_a_stop_anchored_by_one_workspace_binds_another_on_its_own_witness(
    tmp_path: Path,
) -> None:
    """B uses its own witness and shares A's ledger: withholding A's STOP no longer publishes."""
    rig = make_rig(tmp_path / "a", "A")
    shared = make_rig(tmp_path / "b", "A", share=rig)  # its own witness, rig's ledger
    github = endpoint(rig)
    assert execute(rig.request(), sim(github)).status == "published"
    assert recover(rig.request(), sim(github)).settlement.status == "settled"
    stop = stop_of(rig, first(rig))
    recorded = record_stop(with_control(rig, first(rig), stop), NOW)
    assert recorded.status == "stop_recorded" and recorded.anchored
    assert publication_ledger.reserve(shared.request(), NOW).status == "appended"
    other = endpoint(shared)

    outcome = execute(shared.request(), sim(other))  # offers only the first record

    assert outcome.status == "denied"
    assert outcome.evidence is not None
    assert "ar5_control_anchor" in outcome.evidence.denied_by
    assert other.calls == []


def test_the_control_history_bootstrap_runs_once(tmp_path: Path) -> None:
    rig = make_rig(tmp_path / "a", "A")  # bootstrapped through initialize_control
    error = refused(lambda: initialize_control(rig.request(), policy_id_of(rig)))
    # The witness holds the owner's records: an empty history would roll them back.
    assert error.detail == "anchor:local_rolled_back"


def test_a_stop_recorded_from_the_other_workspace_binds_the_ledgers_owner(
    tmp_path: Path,
) -> None:
    """The STOP goes to the ledger's witness wherever it is recorded from."""
    rig = make_rig(tmp_path / "a", "A")
    shared = make_rig(tmp_path / "b", "A", share=rig)
    stop = stop_of(shared, first(shared))
    recorded = record_stop(with_control(shared, first(shared), stop), NOW)
    assert recorded.status == "stop_recorded" and recorded.anchored
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))  # A never saw the STOP locally

    assert outcome.status == "denied"
    assert outcome.evidence is not None
    assert "ar5_control_anchor" in outcome.evidence.denied_by
    assert github.calls == []


# --- each layer of the clearing proof holds on its own -------------------------------------------


def owner_stopped(rig: Any) -> tuple[Any, Any]:
    """An owner STOP recorded (latch set, resolvable), then a genuine RESUME signed after it."""
    stop = stop_of(rig, first(rig))
    recorded = record_stop(with_control(rig, first(rig), stop), NOW)
    assert recorded.status == "stop_recorded" and recorded.anchored
    owner_resume = resume_of(rig, stop, 3, stop)
    return with_control(rig, first(rig), stop, owner_resume), owner_resume


def reasons_after_forging(rig: Any, request: Any, named: str) -> tuple[str, ...]:
    forged_clearing(rig.workspace, rig.transaction.transaction_id, named)
    query = anchor_queries(rig)["stop_latch"]
    return publication_anchors.clearing_reasons(request, query, rig.anchoring())


def test_a_clearing_on_a_resume_whose_owner_key_was_revoked_is_refused(tmp_path: Path) -> None:
    rig = make_rig(tmp_path / "a", "A")
    request, owner_resume = owner_stopped(rig)
    publication_anchors.sync_control(request, policy_id_of(rig), LATER)
    revoked = _root(rig.tmp / "revoked", rig.owner, rig.bot, rig.witness_key, (rig.owner,))
    later = request._replace(root=revoked)

    reasons = reasons_after_forging(rig, later, sha256(owner_resume.record).hexdigest())

    assert reasons == ("latch_clearing_resume_not_verified",)


def test_a_clearing_on_a_resume_the_witness_never_anchored_is_refused(tmp_path: Path) -> None:
    rig = make_rig(tmp_path / "a", "A")
    request, owner_resume = owner_stopped(rig)  # the RESUME is signed, never synced

    reasons = reasons_after_forging(rig, request, sha256(owner_resume.record).hexdigest())

    assert reasons == ("latch_clearing_resume_unanchored",)


def test_a_clearing_that_names_another_record_than_the_resume_is_refused(tmp_path: Path) -> None:
    rig = make_rig(tmp_path / "a", "A")
    request, _ = owner_stopped(rig)
    publication_anchors.sync_control(request, policy_id_of(rig), LATER)

    reasons = reasons_after_forging(rig, request, "d" * 64)

    assert reasons == ("latch_clearing_not_the_newest_resume",)


def test_a_forged_cancel_decision_after_a_send_the_owner_signed_over_never_cancels(
    tmp_path: Path,
) -> None:
    """Even an instruction bound to a head that already holds a send never cancels it."""
    rig = make_rig(tmp_path / "a", "A")
    assert execute(rig.request(), environment_of(rig, fault="lost")).status == "outcome_unknown"
    offered = signed(rig)  # bound to the head after the begun send
    sequence, previous = publication_attempts.head(rig.workspace, tid(rig))
    content = rig.transaction.content
    forged = AttemptRecord(
        transaction_id=tid(rig),
        release_manifest_sha256=content.release_manifest_sha256,
        base_commit=content.publication.base_commit,
        path=content.publication.path,
        attempt_id=attempt_id_of(tid(rig), sequence),
        sequence=sequence,
        state="refused",
        recorded_at=NOW,
        authorization_sha256=sha256(offered.record).hexdigest(),
        commit=None,
        reasons=(CANCELLED,),
        previous_sha256=previous,
    )
    publication_attempts.append(rig.workspace, forged)

    error = refused(lambda: cancel(rig, offered, environment_of(rig)))

    assert error.detail.endswith("cancellation_record_not_the_owners"), error.detail
    assert operations(rig) == ["reserve"]
