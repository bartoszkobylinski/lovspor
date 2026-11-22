"""AR5b: sealed authorization evidence never outlives a later STOP (#738; §9.3a, §9.9 AR5).

:func:`publish_authorized` accepts genuine evidence while it is fresh. Every
case here first obtains genuine ``simulation_only`` evidence at T0, then lets
the owner (or the latch) move the control state, and then presents the
original evidence within its freshness window. Nothing may be sent, nothing
may be recorded as ``prepared_to_send``, and the refusal must name the state
that moved; on both paths. Genuine evidence for the current state still
publishes.
"""

import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import publication_anchors, publication_attempts, publication_path_b
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import CONTROL_NAMESPACE
from lovspor.forarbeider.publication_anchors import sync_control
from lovspor.forarbeider.publication_authorize import authorize
from lovspor.forarbeider.publication_execute import (
    ExecutionRequest,
    Simulation,
    _context,
    _step,
    publish_authorized,
)
from lovspor.forarbeider.publication_execution_models import AuthorizationEvidence
from lovspor.forarbeider.publication_sandbox import SimulatedGitHub
from lovspor.forarbeider.publication_stop_resume import record_stop, resume
from tests.unit.forarbeider_execution_support import (
    APP,
    NOW,
    KeySigner,
    Rig,
    make_rig,
    policy_id_of,
)
from tests.unit.forarbeider_release_control_support import BOT, control, digest, signed_by
from tests.unit.test_forarbeider_publication_stop_resume import (
    WAIT,
    Pause,
    first,
    in_thread,
    resume_of,
    stop_of,
    with_control,
)

T2 = NOW + timedelta(seconds=30)


@pytest.fixture(params=["A", "B"])
def rig(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / request.param.lower(), request.param)


class Adapters:
    """One rig's adapters, and what each path would have sent."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.github = SimulatedGitHub(head_oid=rig.base, identity=APP, now=NOW)

    def at(self, when: datetime) -> Simulation:
        if self.rig.path == "A":
            return Simulation(clock=lambda: when, transport=self.github)
        signer = KeySigner(BOT, self.rig.bot)
        return Simulation(clock=lambda: when, remote=self.rig.remote, signer=signer)

    def sent(self) -> bool:
        if self.rig.path == "A":
            return bool(self.github.calls)
        return publication_path_b.remote_head(self.rig.bare) != self.rig.base


def authorized(request: ExecutionRequest, environment: Simulation) -> AuthorizationEvidence:
    """Genuine evidence for the next attempt, exactly as :func:`execute` would make it."""
    evidence = authorize(_context(request, environment, _step(request)))
    assert evidence.decision == "simulation_only", evidence.denied_by
    return evidence


def states(rig: Rig) -> list[str]:
    found = publication_attempts.records(rig.workspace, rig.transaction.transaction_id)
    return [r.state for r in found]


def refused(action: Callable[[], Any]) -> str:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    return raised.value.detail


def assert_bypass_refused(rig: Rig, adapters: Adapters, detail: str, *expected: str) -> None:
    assert any(word in detail for word in expected), detail
    assert not adapters.sent()
    assert "prepared_to_send" not in states(rig)
    assert "published" not in states(rig)


# --- STOP after authorization -------------------------------------------------------------------


def test_a_stop_recorded_after_authorization_refuses_the_old_evidence(rig: Rig) -> None:
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    stopping = with_control(rig, first(rig), stop_of(rig, first(rig)))
    assert record_stop(stopping, NOW).anchored

    for request in (rig.request(), stopping):
        detail = refused(partial(publish_authorized, evidence, request, adapters.at(T2)))
        assert_bypass_refused(rig, adapters, detail, "stop_latch")


def test_a_stop_recorded_while_the_witness_is_down_refuses_the_old_evidence(rig: Rig) -> None:
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    stopping = with_control(rig, first(rig), stop_of(rig, first(rig)))
    shutil.rmtree(rig.tmp / "witness")
    assert not record_stop(stopping, NOW).anchored

    for request in (rig.request(), stopping):
        detail = refused(partial(publish_authorized, evidence, request, adapters.at(T2)))
        assert_bypass_refused(rig, adapters, detail, "stop_latch")


def test_a_stop_whose_latch_anchor_is_pending_refuses_the_old_evidence(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    stopping = with_control(rig, first(rig), stop_of(rig, first(rig)))
    real = OfflineAnchorWitness.submit

    def refuse_latch(self: OfflineAnchorWitness, anchor: Any) -> Any:
        if anchor.family == "stop_latch":
            raise ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, "witness_busy")
        return real(self, anchor)

    monkeypatch.setattr(OfflineAnchorWitness, "submit", refuse_latch)
    assert not record_stop(stopping, NOW).anchored
    monkeypatch.undo()

    detail = refused(lambda: publish_authorized(evidence, stopping, adapters.at(T2)))
    assert_bypass_refused(rig, adapters, detail, "stop_latch")


def test_an_anchored_stop_without_a_latch_refuses_the_old_evidence(rig: Rig) -> None:
    """The control history advanced (an owner STOP the witness holds), the latch did not."""
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    stopping = with_control(rig, first(rig), stop_of(rig, first(rig)))
    sync_control(stopping, policy_id_of(rig), NOW)
    assert not publication_attempts.latched(rig.workspace)

    withheld = refused(lambda: publish_authorized(evidence, rig.request(), adapters.at(T2)))
    assert_bypass_refused(rig, adapters, withheld, "control_history:local_rolled_back")
    presented = refused(lambda: publish_authorized(evidence, stopping, adapters.at(T2)))
    assert_bypass_refused(rig, adapters, presented, "stop_control", "control_history")


# --- the state moved and came back -------------------------------------------------------------


def stop_and_resume(rig: Rig, resumed_at: datetime) -> ExecutionRequest:
    r1 = first(rig)
    stop = stop_of(rig, r1)
    assert record_stop(with_control(rig, r1, stop), NOW).anchored
    r3 = resume_of(rig, stop, 3, stop, at=resumed_at)
    request = rig.request(inputs=rig.inputs._replace(control=(r1, stop, r3)))
    assert resume(request, resumed_at + timedelta(seconds=1)).status == "resumed"
    return request


def test_evidence_from_before_a_stop_and_resume_is_stale(rig: Rig) -> None:
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    current = stop_and_resume(rig, NOW + timedelta(seconds=10))

    for request in (rig.request(), current):
        detail = refused(partial(publish_authorized, evidence, request, adapters.at(T2)))
        assert_bypass_refused(
            rig, adapters, detail, "authorization_stale", "control_history:local_rolled_back"
        )

    fresh = authorized(current, adapters.at(T2))
    assert publish_authorized(fresh, current, adapters.at(T2)).status == "published"
    assert adapters.sent()


def test_a_latch_set_and_cleared_after_authorization_makes_the_evidence_stale(
    rig: Rig,
) -> None:
    """The latch moved (an unrelated set and its clearing); the control history did not."""
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    tid = rig.transaction.transaction_id
    publication_attempts.latch(rig.workspace, ("synthetic_cause",), NOW, tid)
    publication_attempts.clear_latch(rig.workspace, NOW, "c" * 64, ("synthetic:resolved",))
    assert not publication_attempts.latched(rig.workspace)

    detail = refused(lambda: publish_authorized(evidence, rig.request(), adapters.at(T2)))
    assert_bypass_refused(rig, adapters, detail, "authorization_stale:stop_latch")


def test_a_previous_resume_never_overrides_a_later_stop(rig: Rig) -> None:
    adapters = Adapters(rig)
    current = stop_and_resume(rig, NOW + timedelta(seconds=5))
    evidence = authorized(current, adapters.at(NOW + timedelta(seconds=10)))
    r1, stop, r3 = current.inputs.control
    stop4 = stop_of(rig, r3, 4, signed_at=NOW + timedelta(seconds=12))
    again = with_control(rig, r1, stop, r3, stop4)
    assert record_stop(again, NOW + timedelta(seconds=15)).anchored

    for request in (current, again):
        detail = refused(partial(publish_authorized, evidence, request, adapters.at(T2)))
        assert_bypass_refused(rig, adapters, detail, "stop_latch")


# --- genuine, current evidence still publishes --------------------------------------------------


def test_genuine_current_evidence_still_publishes_once(rig: Rig) -> None:
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))

    outcome = publish_authorized(evidence, rig.request(), adapters.at(T2))

    assert outcome.status == "published"
    assert states(rig) == ["prepared_to_send", "published"]
    replay = refused(lambda: publish_authorized(evidence, rig.request(), adapters.at(T2)))
    assert replay == "authorization_not_this_attempt"


# --- concurrency -------------------------------------------------------------------------------


def test_old_evidence_waiting_on_a_stop_in_progress_is_refused_after_it(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    stopping = with_control(rig, first(rig), stop_of(rig, first(rig)))
    pause = Pause("stopper")
    monkeypatch.setattr(OfflineAnchorWitness, "submit", pause.wrap(OfflineAnchorWitness.submit))

    thread, result = in_thread("stopper", lambda: record_stop(stopping, NOW))
    assert pause.reached.wait(WAIT)
    locked = refused(lambda: publish_authorized(evidence, rig.request(), adapters.at(T2)))
    assert "workspace" in locked
    pause.release.set()
    thread.join(WAIT)

    assert result[0].status == "stop_recorded"
    detail = refused(lambda: publish_authorized(evidence, rig.request(), adapters.at(T2)))
    assert_bypass_refused(rig, adapters, detail, "stop_latch")


# --- added after the sabotage pass: each isolates one overlapping guard -------------------------


def test_a_legacy_latch_file_written_after_authorization_refuses_the_old_evidence(
    rig: Rig,
) -> None:
    """Only the latch read catches it: transitions, control and anchors are unchanged."""
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    (rig.workspace / "stop-latch.json").write_text("{}")

    detail = refused(lambda: publish_authorized(evidence, rig.request(), adapters.at(T2)))
    assert_bypass_refused(rig, adapters, detail, "stop_latch_set")


def test_a_resume_lease_that_lapses_after_authorization_refuses_the_old_evidence(
    rig: Rig,
) -> None:
    """Only the control state read now catches it: the records and the latch are unchanged."""
    adapters = Adapters(rig)
    r1 = first(rig)
    stop = stop_of(rig, r1)
    assert record_stop(with_control(rig, r1, stop), NOW).anchored
    lease = control(
        "resume",
        3,
        stop,
        resumes_control_sha256=digest(stop),
        signed_at=NOW + timedelta(seconds=5),
        valid_until=NOW + timedelta(seconds=20),
    )
    current = with_control(rig, r1, stop, signed_by(lease, rig.owner, CONTROL_NAMESPACE))
    assert resume(current, NOW + timedelta(seconds=6)).status == "resumed"
    evidence = authorized(current, adapters.at(NOW + timedelta(seconds=10)))

    detail = refused(lambda: publish_authorized(evidence, current, adapters.at(T2)))
    assert_bypass_refused(rig, adapters, detail, "stop_control")


def test_a_control_history_advanced_without_a_stop_makes_the_old_evidence_stale(
    rig: Rig,
) -> None:
    """Only the control head catches it: not stopped, anchored, the latch unchanged."""
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    r1 = first(rig)
    stop = stop_of(rig, r1)
    r3 = resume_of(rig, stop, 3, stop, at=NOW + timedelta(seconds=5))
    advanced = with_control(rig, r1, stop, r3)
    sync_control(advanced, policy_id_of(rig), NOW + timedelta(seconds=6))
    assert not publication_attempts.latch_events(rig.workspace)

    detail = refused(lambda: publish_authorized(evidence, advanced, adapters.at(T2)))
    assert_bypass_refused(rig, adapters, detail, "authorization_stale:control_history")


def test_a_latch_landing_after_prepared_to_send_is_honoured_before_the_send(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer that ignores the workspace lock sets the latch while the attempt is recorded."""
    adapters = Adapters(rig)
    evidence = authorized(rig.request(), adapters.at(NOW))
    real = publication_anchors.settle
    tid = rig.transaction.transaction_id

    def latch_meanwhile(request: ExecutionRequest, family: Any, scope: str) -> Any:
        found = real(request, family, scope)
        if family == "publication_attempts" and not publication_attempts.latched(rig.workspace):
            publication_attempts.latch(rig.workspace, ("out_of_band_stop",), NOW, tid)
        return found

    monkeypatch.setattr(publication_anchors, "settle", latch_meanwhile)
    detail = refused(lambda: publish_authorized(evidence, rig.request(), adapters.at(T2)))

    assert detail == "stop_latch_set"
    assert not adapters.sent()
    assert states(rig) == ["prepared_to_send"]
