"""AR5b: anchored STOP/RESUME and owner authorization of publication (#738; §9.3a, §9.9 AR5).

Every rig has an offline anchor witness outside its workspace, and its empty
families and owner control records are anchored at setup (AR5a). Every attack
here is real: records signed by the wrong key, files deleted, a witness
removed, a crash injected at a boundary, a second thread racing the first.
"""

import hashlib
import shutil
import threading
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import publication_attempts
from lovspor.forarbeider.control_plane_anchor import (
    AnchorQuery,
    durable_append,
    verify,
)
from lovspor.forarbeider.control_plane_chains import control_chain
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness, unavailable
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import CONTROL_NAMESPACE
from lovspor.forarbeider.publication_anchors import sync_control
from lovspor.forarbeider.publication_authorize import AdapterFacts, AuthorizationContext, authorize
from lovspor.forarbeider.publication_execute import (
    ExecutionOutcome,
    Simulation,
    execute,
    next_attempt_id,
)
from lovspor.forarbeider.publication_execution_models import LatchEvent
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash, SimulatedGitHub
from lovspor.forarbeider.publication_stop_resume import record_stop, resume
from lovspor.forarbeider.release_control_models import ControlScope
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_execution_support import (
    APP,
    NOW,
    Rig,
    _root,
    anchor_queries,
    make_rig,
    policy_id_of,
)
from tests.unit.forarbeider_release_control_support import control, digest, signed_by

WAIT = 20.0
LATER = NOW + timedelta(minutes=1)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


def endpoint(rig: Rig, **changes: Any) -> SimulatedGitHub:
    fields: dict[str, Any] = {"head_oid": rig.base, "identity": APP, "now": NOW}
    return SimulatedGitHub(**(fields | changes))


def sim(github: SimulatedGitHub, at: Any = NOW) -> Simulation:
    return Simulation(clock=lambda: at, transport=github)


def with_control(rig: Rig, *records: SignedRecord, **changes: Any) -> Any:
    return rig.request(inputs=rig.inputs._replace(control=records), **changes)


def first(rig: Rig) -> SignedRecord:
    return rig.inputs.control[0]


def stop_of(
    rig: Rig, previous: SignedRecord, sequence: int = 2, key: Any = None, **changes: Any
) -> SignedRecord:
    record = control("stop", sequence, previous, **changes)
    return signed_by(record, key or rig.owner, CONTROL_NAMESPACE)


def resume_of(
    rig: Rig,
    previous: SignedRecord,
    sequence: int,
    resumes: SignedRecord | None,
    at: Any = LATER,
    key: Any = None,
    **changes: Any,
) -> SignedRecord:
    record = control(
        "resume",
        sequence,
        previous,
        resumes_control_sha256=digest(resumes) if resumes else None,
        signed_at=at,
        valid_until=at + timedelta(hours=1),
        **changes,
    )
    return signed_by(record, key or rig.owner, CONTROL_NAMESPACE)


def check(outcome: ExecutionOutcome, name: str) -> tuple[str, ...]:
    assert outcome.evidence is not None
    found = next(c for c in outcome.evidence.checks if c.name == name)
    return () if found.status == "passed" else found.reasons


def assert_refused(action: Callable[[], Any], detail: str) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    assert detail in raised.value.detail, raised.value.detail
    return raised.value


def latch_kinds(rig: Rig) -> list[str]:
    return [event.kind for event in publication_attempts.latch_events(rig.workspace)]


def stopped(rig: Rig) -> tuple[SignedRecord, SignedRecord, Any]:
    """An owner STOP, recorded: the latch is set and the control history anchored."""
    stop = stop_of(rig, first(rig))
    request = with_control(rig, first(rig), stop)
    outcome = record_stop(request, NOW)
    assert outcome.status == "stop_recorded" and outcome.anchored
    return first(rig), stop, request


def states(rig: Rig) -> list[str]:
    found = publication_attempts.records(rig.workspace, rig.transaction.transaction_id)
    return [r.state for r in found]


# --- verified control state before authorization ------------------------------------------------


def test_an_anchored_rig_still_publishes_and_anchors_its_records(rig: Rig) -> None:
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "published"
    assert check(outcome, "ar5_control_anchor") == ()
    attempts = anchor_queries(rig)["publication_attempts"]
    assert verify(attempts, rig.anchoring()).status == "verified"


def test_no_anchor_witness_denies_and_sends_nothing(rig: Rig) -> None:
    github = endpoint(rig)

    outcome = execute(rig.request(anchoring=None), sim(github))

    assert outcome.status == "denied"
    assert "anchor_authority_missing" in check(outcome, "ar5_control_anchor")
    assert github.calls == []


def test_an_unavailable_witness_denies_without_an_unanchorable_record(rig: Rig) -> None:
    github = endpoint(rig)
    shutil.rmtree(rig.tmp / "witness")

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "control_history:store_unavailable" in check(outcome, "ar5_control_anchor")
    assert github.calls == []
    assert states(rig) == []


def test_stop_cannot_be_bypassed_by_presenting_older_valid_control_records(rig: Rig) -> None:
    github = endpoint(rig)
    stop = stop_of(rig, first(rig))
    sync_control(with_control(rig, first(rig), stop), policy_id_of(rig), NOW)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert check(outcome, "stop_control") == ()
    assert "control_history:local_rolled_back" in check(outcome, "ar5_control_anchor")
    assert github.calls == []


def test_a_substituted_control_history_diverges_and_denies(rig: Rig) -> None:
    github = endpoint(rig)
    other = signed_by(
        control("resume", 1, None, control_id="synthetic-control-other"),
        rig.owner,
        CONTROL_NAMESPACE,
    )

    outcome = execute(with_control(rig, other), sim(github))

    assert outcome.status == "denied"
    assert "control_history:diverged" in check(outcome, "ar5_control_anchor")
    assert github.calls == []


def test_a_deleted_anchored_latch_is_not_a_cleared_latch(rig: Rig) -> None:
    github = endpoint(rig)
    event = LatchEvent(
        sequence=1,
        previous_sha256=None,
        kind="set",
        at=NOW,
        transaction_id=rig.transaction.transaction_id,
        reasons=("synthetic_stop",),
    )
    durable_append(rig.workspace, event, rig.inputs.settings.repository, rig.anchoring())
    shutil.rmtree(rig.workspace / "stop-latch")
    assert not publication_attempts.latched(rig.workspace)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "stop_latch:local_rolled_back" in check(outcome, "ar5_control_anchor")
    assert github.calls == []


# --- STOP ---------------------------------------------------------------------------------------


def test_an_owner_stop_is_recorded_anchored_and_denies_publication(rig: Rig) -> None:
    github = endpoint(rig)
    _, stop, request = stopped(rig)

    outcome = execute(request, sim(github))

    assert outcome.status == "denied"
    assert check(outcome, "stop_control") and check(outcome, "stop_latch")
    assert github.calls == []
    events = publication_attempts.latch_events(rig.workspace)
    assert [e.kind for e in events] == ["set"]
    assert events[0].reasons == (f"owner_stop:{digest(stop)}",)
    control_query = anchor_queries(rig)
    assert len(control_query["control_history"].local.digests) == 1
    state = AnchorQuery(
        "control_history",
        rig.inputs.settings.repository,
        policy_id_of(rig),
        control_chain((first(rig), stop), policy_id_of(rig)),
    )
    assert verify(state, rig.anchoring()).status == "verified"


def test_an_owner_stop_takes_effect_while_the_witness_is_down(rig: Rig) -> None:
    github = endpoint(rig)
    stop = stop_of(rig, first(rig))
    request = with_control(rig, first(rig), stop)
    shutil.rmtree(rig.tmp / "witness")

    outcome = record_stop(request, NOW)

    assert outcome.status == "stop_recorded" and not outcome.anchored
    assert publication_attempts.latched(rig.workspace)
    assert execute(rig.request(), sim(github)).status == "denied"
    assert github.calls == []


def test_a_bot_signed_stop_is_refused_and_sets_nothing(rig: Rig) -> None:
    stop = signed_by(control("stop", 2, first(rig)), rig.bot, CONTROL_NAMESPACE)

    assert_refused(lambda: record_stop(with_control(rig, first(rig), stop), NOW), "stop_not")
    assert latch_kinds(rig) == []


def test_execution_and_recovery_never_clear_the_latch(rig: Rig) -> None:
    github = endpoint(rig)
    _, _, request = stopped(rig)

    execute(request, sim(github))
    recover(request, sim(github))
    execute(rig.request(), sim(github))

    assert latch_kinds(rig) == ["set"]
    assert publication_attempts.latched(rig.workspace)
    assert github.calls == []


# --- owner RESUME ---------------------------------------------------------------------------------


def test_an_owner_resume_clears_the_latch_audibly_and_permits_publication(rig: Rig) -> None:
    github = endpoint(rig)
    r1, stop, _ = stopped(rig)
    r3 = resume_of(rig, stop, 3, stop)
    request = with_control(rig, r1, stop, r3)

    outcome = resume(request, LATER + timedelta(minutes=1))

    assert outcome.status == "resumed" and outcome.anchored
    events = publication_attempts.latch_events(rig.workspace)
    assert [e.kind for e in events] == ["set", "cleared"]
    assert events[1].resume_control_sha256 == digest(r3)
    assert events[0].reasons == (f"owner_stop:{digest(stop)}",)
    published = execute(request, sim(github, LATER + timedelta(minutes=2)))
    assert published.status == "published"
    assert len(github.calls) == 1


@pytest.mark.parametrize("fault", ["bot", "forged", "other_policy", "stale_sequence"])
def test_a_bad_resume_is_refused_and_the_latch_stays(rig: Rig, fault: str) -> None:
    github = endpoint(rig)
    r1, stop, _ = stopped(rig)
    if fault == "bot":
        r3 = resume_of(rig, stop, 3, stop, key=rig.bot)
    elif fault == "forged":
        owner = resume_of(rig, stop, 3, stop)
        stranger = resume_of(rig, stop, 3, stop, key=rig.stranger)
        r3 = SignedRecord(owner.record, stranger.signature)
    elif fault == "other_policy":
        r3 = resume_of(rig, stop, 3, stop, scope=ControlScope(policy_id="another-policy"))
    else:
        r3 = resume_of(rig, stop, 2, stop)

    assert_refused(lambda: resume(with_control(rig, r1, stop, r3), LATER), "resume_not")
    assert latch_kinds(rig) == ["set"]
    after = execute(with_control(rig, r1, stop, r3), sim(github, LATER))
    assert after.status == "denied"
    assert github.calls == []


def test_a_replayed_resume_after_a_later_stop_is_refused(rig: Rig) -> None:
    r1, stop, _ = stopped(rig)
    r3 = resume_of(rig, stop, 3, stop)
    assert resume(with_control(rig, r1, stop, r3), LATER).status == "resumed"
    s4 = stop_of(rig, r3, 4, signed_at=LATER + timedelta(minutes=1))
    assert record_stop(with_control(rig, r1, stop, r3, s4), LATER + timedelta(minutes=2)).anchored

    replay = with_control(rig, r1, stop, r3)
    assert_refused(lambda: resume(replay, LATER + timedelta(minutes=3)), "control_history")
    assert latch_kinds(rig) == ["set", "cleared", "set"]


def test_a_resume_under_a_revoked_owner_key_is_refused(rig: Rig) -> None:
    r1, stop, _ = stopped(rig)
    r3 = resume_of(rig, stop, 3, stop)
    revoked = _root(rig.tmp / "revoked", rig.owner, rig.bot, rig.witness_key, (rig.owner,))

    assert_refused(
        lambda: resume(with_control(rig, r1, stop, r3, root=revoked), LATER), "resume_not"
    )
    assert latch_kinds(rig) == ["set"]


def test_a_resume_signed_before_the_latch_is_refused(rig: Rig) -> None:
    r1, stop, _ = stopped(rig)
    early = resume_of(rig, stop, 3, stop, at=NOW - timedelta(minutes=1))

    assert_refused(lambda: resume(with_control(rig, r1, stop, early), LATER), "predates")
    assert latch_kinds(rig) == ["set"]


def test_a_resume_signed_at_the_latch_time_is_refused(rig: Rig) -> None:
    r1, stop, _ = stopped(rig)
    simultaneous = resume_of(rig, stop, 3, stop, at=NOW)

    assert_refused(lambda: resume(with_control(rig, r1, stop, simultaneous), LATER), "predates")
    assert latch_kinds(rig) == ["set"]


def test_a_resume_while_the_witness_is_down_is_refused(rig: Rig) -> None:
    r1, stop, _ = stopped(rig)
    r3 = resume_of(rig, stop, 3, stop)
    shutil.rmtree(rig.tmp / "witness")

    assert_refused(lambda: resume(with_control(rig, r1, stop, r3), LATER), "anchor")
    assert latch_kinds(rig) == ["set"]


# --- latch recovery: the cause must be resolved first --------------------------------------------


def test_an_unknown_outcome_needs_recovery_before_resume(rig: Rig) -> None:
    github = endpoint(rig, fault="lost")
    assert execute(rig.request(), sim(github)).status == "outcome_unknown"
    github.fault = None
    r2 = resume_of(rig, first(rig), 2, None)
    request = with_control(rig, first(rig), r2)

    assert_refused(lambda: resume(request, LATER), "unresolved")
    assert latch_kinds(rig) == ["set"]
    assert recover(request, sim(github)).classification == "published_authorized"

    assert resume(request, LATER).status == "resumed"
    assert latch_kinds(rig) == ["set", "cleared"]
    assert execute(request, sim(github, LATER)).status == "unchanged"
    assert len(github.calls) == 1


def test_a_foreign_commit_halt_is_never_resumed(rig: Rig) -> None:
    github = endpoint(rig)
    github.advance({"README.md": b"someone else\n"}, ("manual", ""), (None, False))
    assert recover(rig.request(), sim(github)).classification == "halted_foreign_commit"
    r2 = resume_of(rig, first(rig), 2, None)

    assert_refused(lambda: resume(with_control(rig, first(rig), r2), LATER), "unresolved")
    assert latch_kinds(rig) == ["set"]


# --- crash boundaries ----------------------------------------------------------------


def test_a_crash_after_the_stop_latch_before_its_anchor_still_stops(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    stop = stop_of(rig, first(rig))
    request = with_control(rig, first(rig), stop)
    real = OfflineAnchorWitness.submit

    def crash(self: OfflineAnchorWitness, anchor: Any) -> Any:
        if anchor.family == "stop_latch":
            raise SimulatedCrash
        return real(self, anchor)

    monkeypatch.setattr(OfflineAnchorWitness, "submit", crash)
    with pytest.raises(SimulatedCrash):
        record_stop(request, NOW)
    monkeypatch.undo()

    assert publication_attempts.latched(rig.workspace)
    assert execute(rig.request(), sim(github)).status == "denied"
    again = record_stop(request, NOW)
    assert again.status == "already_stopped" and again.anchored
    assert latch_kinds(rig) == ["set"]
    assert github.calls == []


def test_a_crash_before_the_clearing_leaves_the_latch_and_a_retry_resumes(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    r1, stop, _ = stopped(rig)
    r3 = resume_of(rig, stop, 3, stop)
    request = with_control(rig, r1, stop, r3)

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise SimulatedCrash

    monkeypatch.setattr(publication_attempts, "clear_latch", crash)
    with pytest.raises(SimulatedCrash):
        resume(request, LATER)
    monkeypatch.undo()

    assert latch_kinds(rig) == ["set"]
    assert execute(request, sim(github, LATER)).status == "denied"
    assert resume(request, LATER).status == "resumed"
    assert execute(request, sim(github, LATER)).status == "published"


def test_a_crash_after_the_clearing_before_its_anchor_denies_until_completed(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    r1, stop, _ = stopped(rig)
    r3 = resume_of(rig, stop, 3, stop)
    request = with_control(rig, r1, stop, r3)
    real = OfflineAnchorWitness.submit

    def crash(self: OfflineAnchorWitness, anchor: Any) -> Any:
        if anchor.family == "stop_latch":
            raise SimulatedCrash
        return real(self, anchor)

    monkeypatch.setattr(OfflineAnchorWitness, "submit", crash)
    with pytest.raises(SimulatedCrash):
        resume(request, LATER)
    monkeypatch.undo()

    assert latch_kinds(rig) == ["set", "cleared"]
    denied = execute(request, sim(github, LATER))
    assert denied.status == "denied"
    assert "stop_latch:pending_anchor" in check(denied, "ar5_control_anchor")
    assert resume(request, LATER).status == "resumed"
    assert latch_kinds(rig) == ["set", "cleared"]
    assert execute(request, sim(github, LATER)).status == "published"


# --- concurrency ----------------------------------------------------------------


class Pause:
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


def test_a_stop_racing_a_send_is_refused_then_recorded_and_denies_after(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    stop = stop_of(rig, first(rig))
    stopping = with_control(rig, first(rig), stop)
    pause = Pause("sender")
    monkeypatch.setattr(SimulatedGitHub, "post", pause.wrap(SimulatedGitHub.post))

    thread, result = in_thread("sender", lambda: execute(rig.request(), sim(github)))
    assert pause.reached.wait(WAIT)
    error = assert_refused(lambda: record_stop(stopping, NOW), "workspace")
    assert error.reason is RefusalReason.CORPUS_LOCKED
    pause.release.set()
    thread.join(WAIT)

    assert result[0].status == "published"
    assert record_stop(stopping, NOW).status == "stop_recorded"
    assert execute(stopping, sim(github)).status == "unchanged"
    assert len(github.calls) == 1


def test_an_execution_racing_a_resume_is_refused(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    github = endpoint(rig)
    r1, stop, _ = stopped(rig)
    r3 = resume_of(rig, stop, 3, stop)
    request = with_control(rig, r1, stop, r3)
    pause = Pause("resumer")
    monkeypatch.setattr(OfflineAnchorWitness, "submit", pause.wrap(OfflineAnchorWitness.submit))

    thread, result = in_thread("resumer", lambda: resume(request, LATER))
    assert pause.reached.wait(WAIT)
    error = assert_refused(lambda: execute(request, sim(github, LATER)), "workspace")
    assert error.reason is RefusalReason.CORPUS_LOCKED
    pause.release.set()
    thread.join(WAIT)

    assert result[0].status == "resumed"
    assert github.calls == []


# --- added after the first sabotage pass: each isolates one guard ------------------------------


def test_the_verifier_reads_the_latch_itself_not_only_the_adapter(rig: Rig) -> None:
    github = endpoint(rig)
    event = LatchEvent(
        sequence=1,
        previous_sha256=None,
        kind="set",
        at=NOW,
        transaction_id=rig.transaction.transaction_id,
        reasons=("synthetic_stop",),
    )
    durable_append(rig.workspace, event, rig.inputs.settings.repository, rig.anchoring())
    context = AuthorizationContext(
        rig.inputs,
        rig.root,
        rig.clone,
        lambda: github.head(github.repository, "main"),
        NOW,
        next_attempt_id(rig.request()),
        "simulation",
        AdapterFacts(app=APP, latched=False),
        rig.anchoring(),
        rig.workspace,
    )

    evidence = authorize(context)

    assert evidence.decision == "denied"
    assert "stop_latch" in evidence.denied_by


def test_a_stop_for_another_policy_is_not_recorded(rig: Rig) -> None:
    other = stop_of(rig, first(rig), scope=ControlScope(policy_id="another-policy"))

    assert_refused(lambda: record_stop(with_control(rig, first(rig), other), NOW), "not_in_force")
    assert latch_kinds(rig) == []


def test_a_newest_resume_is_never_recorded_as_a_stop(rig: Rig) -> None:
    stop = stop_of(rig, first(rig))
    initial = resume_of(rig, stop, 3, None)

    assert_refused(
        lambda: record_stop(with_control(rig, first(rig), stop, initial), LATER),
        "newest_is_resume",
    )
    assert latch_kinds(rig) == []


def test_a_resume_that_leaves_another_stop_active_is_refused(rig: Rig) -> None:
    r1, stop, _ = stopped(rig)
    wrong_target = resume_of(rig, stop, 3, None)

    assert_refused(
        lambda: resume(with_control(rig, r1, stop, wrong_target), LATER), "still_stopped"
    )
    assert latch_kinds(rig) == ["set"]


def test_an_unverifiable_history_halt_is_never_resumed(rig: Rig) -> None:
    github = endpoint(rig)
    github.reported_head = "f" * 40
    outcome = recover(rig.request(), sim(github))
    assert outcome.classification == "halted_history_unverifiable"
    r2 = resume_of(rig, first(rig), 2, None)

    assert_refused(
        lambda: resume(with_control(rig, first(rig), r2), LATER),
        "unresolved:remote_history_unverifiable",
    )
    assert latch_kinds(rig) == ["set"]


def test_a_resolution_resting_on_lost_receipts_is_refused(rig: Rig) -> None:
    github = endpoint(rig, fault="lost")
    execute(rig.request(), sim(github))
    github.fault = None
    assert recover(rig.request(), sim(github)).classification == "published_authorized"
    shutil.rmtree(rig.workspace / "receipts")
    r2 = resume_of(rig, first(rig), 2, None)

    assert_refused(
        lambda: resume(with_control(rig, first(rig), r2), LATER),
        "unresolved:receipts_local_rolled_back",
    )
    assert latch_kinds(rig) == ["set"]


def test_control_records_the_owner_did_not_sign_are_never_anchored(rig: Rig) -> None:
    forged = signed_by(control("stop", 2, first(rig)), rig.bot, CONTROL_NAMESPACE)
    before = sorted(p.read_bytes() for p in (rig.tmp / "witness").rglob("*.json"))

    assert_refused(
        lambda: sync_control(with_control(rig, first(rig), forged), policy_id_of(rig), NOW),
        "control_chain_not_verified",
    )
    assert sorted(p.read_bytes() for p in (rig.tmp / "witness").rglob("*.json")) == before


def test_an_unanchored_prepared_to_send_is_never_sent(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = endpoint(rig)
    real = OfflineAnchorWitness.submit

    def refuse_attempts(self: OfflineAnchorWitness, anchor: Any) -> Any:
        if anchor.family == "publication_attempts":
            raise unavailable()
        return real(self, anchor)

    monkeypatch.setattr(OfflineAnchorWitness, "submit", refuse_attempts)

    assert_refused(lambda: execute(rig.request(), sim(github)), "publication_attempts_unanchored")
    assert github.calls == []
    assert states(rig) == ["prepared_to_send"]


def test_two_sets_in_a_row_are_refused_when_the_latch_is_read(rig: Rig) -> None:
    publication_attempts.latch(
        rig.workspace, ("synthetic_stop",), NOW, rig.transaction.transaction_id
    )
    folder = rig.workspace / "stop-latch"
    first_event = (folder / "000001.json").read_bytes()
    second = LatchEvent(
        sequence=2,
        previous_sha256=hashlib.sha256(first_event).hexdigest(),
        kind="set",
        at=LATER,
        transaction_id=rig.transaction.transaction_id,
        reasons=("another_stop",),
    )
    publication_attempts.create_once(folder, "000002.json", canonical_text(second).encode())

    with pytest.raises(ForarbeiderRefusedError) as raised:
        publication_attempts.latch_events(rig.workspace)
    assert raised.value.detail == "stop_latch"


def test_a_clearing_with_the_wrong_predecessor_is_refused(rig: Rig) -> None:
    publication_attempts.latch(
        rig.workspace, ("synthetic_stop",), NOW, rig.transaction.transaction_id
    )
    folder = rig.workspace / "stop-latch"
    clearing = LatchEvent(
        sequence=2,
        previous_sha256="a" * 64,
        kind="cleared",
        at=LATER,
        transaction_id=rig.transaction.transaction_id,
        reasons=("resolved",),
        resume_control_sha256="b" * 64,
    )
    publication_attempts.create_once(folder, "000002.json", canonical_text(clearing).encode())

    with pytest.raises(ForarbeiderRefusedError) as raised:
        publication_attempts.latch_events(rig.workspace)
    assert raised.value.detail == "stop_latch"


def test_nothing_clears_a_latch_that_is_not_set_or_is_the_legacy_file(rig: Rig) -> None:
    assert_refused(
        lambda: publication_attempts.clear_latch(rig.workspace, NOW, "a" * 64, ("x",)), "stop_latch"
    )
    (rig.workspace / "stop-latch.json").write_bytes(b"{}")
    publication_attempts.latch(rig.workspace, ("synthetic",), NOW, rig.transaction.transaction_id)

    assert publication_attempts.latched(rig.workspace)
    assert_refused(
        lambda: publication_attempts.clear_latch(rig.workspace, NOW, "a" * 64, ("x",)), "stop_latch"
    )
