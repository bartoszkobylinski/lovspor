"""AR5d-2: legacy state admission (#738; §9.9 AR5).

No populated pre-AR5d workspace is ever adopted. A new identity comes only from
:func:`~lovspor.forarbeider.workspace_identity.establish`, only for an empty
workspace, and every family the identity's scope holds starts there, anchored
empty, while the workspace is provably empty. Evidence the witness holds under
the legacy literal scope ``workspace`` is never anchored again as the current
state of an identity, whoever copies an identity file next to it.

The legacy state is real here: records written by the AR4/AR5 writers and
anchored under the literal scope as pre-AR5d code anchored them; an identity
file copied by hand; recovery, STOP and the durable append run on top.
"""

import shutil
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import (
    publication_attempts,
    publication_ledger,
    workspace_identity,
    workspace_identity_models,
    workspace_writer,
)
from lovspor.forarbeider.control_plane_anchor import (
    AnchorQuery,
    anchor_pending,
    durable_append,
    initialize,
    verify,
)
from lovspor.forarbeider.control_plane_chains import workspace_chain
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.publication_execute import ExecutionOutcome, Simulation, execute
from lovspor.forarbeider.publication_execution_models import LatchEvent
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash, SimulatedGitHub
from lovspor.forarbeider.publication_stop_resume import record_stop, resume
from tests.unit.forarbeider_execution_support import APP, NOW, Rig, make_rig
from tests.unit.test_forarbeider_publication_stop_resume import (
    LATER,
    first,
    resume_of,
    stop_of,
    with_control,
)
from tests.unit.test_forarbeider_workspace_identity import cancel_reservation

IDENTITY = "workspace-identity.json"
LEGACY = "workspace"
#: When the pre-AR5d writer wrote its records: before any identity existed.
EARLIER = NOW - timedelta(days=30)
FAMILIES = ("stop_latch", "recovery_receipts")


def endpoint(rig: Rig) -> SimulatedGitHub:
    return SimulatedGitHub(head_oid=rig.base, identity=APP, now=NOW)


def sim(github: SimulatedGitHub) -> Simulation:
    return Simulation(clock=lambda: NOW, transport=github)


def refused(action: Any) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    return raised.value


def repository(rig: Rig) -> str:
    return rig.inputs.settings.repository


def query(rig: Rig, family: Any, workspace: Path, scope: str) -> AnchorQuery:
    return AnchorQuery(family, repository(rig), scope, workspace_chain(family, workspace, scope))


def status(rig: Rig, family: Any, workspace: Path, scope: str) -> str:
    return verify(query(rig, family, workspace, scope), rig.anchoring(workspace=workspace)).status


def scope_of(workspace: Path) -> str:
    raw = workspace_identity_models.read_identity(workspace)
    assert raw is not None
    return workspace_identity_models.scope_of(raw)


def anchor_reasons(outcome: ExecutionOutcome) -> tuple[str, ...]:
    assert outcome.evidence is not None
    found = next(c for c in outcome.evidence.checks if c.name == "ar5_control_anchor")
    return found.reasons


def fresh(tmp_path: Path, name: str = "fresh") -> Path:
    workspace = tmp_path / name
    workspace.mkdir(mode=0o700)
    return workspace


def legacy_anchor(rig: Rig, workspace: Path, family: Any) -> None:
    """Anchor ``workspace``'s one-record chain of ``family`` under the literal scope, as
    pre-AR5d code did: the empty family first, then the record."""
    authority = rig.anchoring(workspace=workspace)
    whole = query(rig, family, workspace, LEGACY)
    assert len(whole.local.digests) == 1
    empty = whole._replace(local=whole.local._replace(digests=(), last=None))
    if verify(empty, authority).status == "unanchored":
        assert initialize(empty, authority).status == "verified"
    assert anchor_pending(whole, authority).status == "verified"


def legacy_workspace(neighbour: Rig) -> Path:
    """``neighbour`` made pre-AR5d: no identity, no writer chain, a latch set by the old writer
    and anchored under the literal scope."""
    workspace = neighbour.workspace
    (workspace / IDENTITY).unlink()
    shutil.rmtree(workspace / "workspace-writer", ignore_errors=True)
    tid = neighbour.transaction.transaction_id
    publication_attempts.latch(workspace, ("legacy_halt",), EARLIER, tid)
    legacy_anchor(neighbour, workspace, "stop_latch")
    return workspace


def folder_of(rig: Rig, family: str, scope: str) -> Path:
    name = sha256(f"{repository(rig)}\n{scope}".encode()).hexdigest()
    return rig.witness.location / family / name


def plant(source: Path, target: Path) -> None:
    """The hand migration the policy forbids: an anchored identity copied next to legacy state."""
    shutil.copy2(source / IDENTITY, target / IDENTITY)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


@pytest.fixture
def neighbour(rig: Rig, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "b", "A", share=rig, one_witness=True)


# --- a new identity is complete when establish returns --------------------------------------


def test_establish_anchors_every_family_of_the_new_scope_empty(rig: Rig, tmp_path: Path) -> None:
    workspace = fresh(tmp_path)

    outcome = workspace_identity.establish(
        workspace, repository(rig), rig.anchoring(workspace=workspace), NOW
    )

    for family in (*FAMILIES, "workspace_writer"):
        assert status(rig, family, workspace, outcome.scope) == "verified", family


def test_a_workspace_given_its_identity_by_establish_alone_publishes(
    rig: Rig, tmp_path: Path
) -> None:
    workspace = fresh(tmp_path)
    workspace_identity.establish(
        workspace, repository(rig), rig.anchoring(workspace=workspace), NOW
    )
    github = endpoint(rig)

    outcome = execute(
        rig.request(workspace=workspace, anchoring=rig.anchoring(workspace=workspace)), sim(github)
    )

    assert outcome.status == "published"
    assert len(github.calls) == 1


def test_a_crash_between_the_family_anchors_leaves_no_identity_and_a_retry_establishes(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = fresh(tmp_path)
    authority = rig.anchoring(workspace=workspace)
    real = workspace_identity.initialize
    calls: list[str] = []

    def crashing(query_: AnchorQuery, held: Any) -> Any:
        calls.append(query_.family)
        if query_.family == "recovery_receipts":
            raise SimulatedCrash("between the family anchors")
        return real(query_, held)

    monkeypatch.setattr(workspace_identity, "initialize", crashing)
    with pytest.raises(SimulatedCrash):
        workspace_identity.establish(workspace, repository(rig), authority, NOW)
    monkeypatch.setattr(workspace_identity, "initialize", real)

    assert not (workspace / IDENTITY).exists()
    assert "recovery_receipts" in calls
    again = workspace_identity.establish(workspace, repository(rig), authority, NOW)
    assert again.status == "established"
    for family in (*FAMILIES, "workspace_writer"):
        assert status(rig, family, workspace, again.scope) == "verified", family


# --- legacy evidence is never anchored as an identity's current state -------------------------


def test_recovery_never_adopts_a_legacy_latch_under_a_copied_identity(
    rig: Rig, neighbour: Rig
) -> None:
    legacy = legacy_workspace(neighbour)
    plant(rig.workspace, legacy)
    scope = scope_of(rig.workspace)
    github = endpoint(neighbour)

    recover(neighbour.request(), sim(github))
    outcome = execute(neighbour.request(), sim(github))

    assert status(rig, "stop_latch", legacy, scope) == "pending_anchor"
    assert status(rig, "stop_latch", rig.workspace, scope) == "verified"
    assert status(rig, "stop_latch", legacy, LEGACY) == "verified"
    assert outcome.status == "denied"
    assert "stop_latch:pending_anchor" in anchor_reasons(outcome)
    assert github.calls == []
    assert publication_attempts.latched(legacy)


def test_the_identity_s_own_workspace_still_publishes_after_a_copy_meets_legacy_state(
    rig: Rig, neighbour: Rig
) -> None:
    legacy = legacy_workspace(neighbour)
    plant(rig.workspace, legacy)
    recover(neighbour.request(), sim(endpoint(neighbour)))
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "published"
    assert len(github.calls) == 1


def test_an_unprovable_legacy_history_is_never_taken_for_none(rig: Rig, neighbour: Rig) -> None:
    """A legacy history the witness holds but cannot prove (here an anchor the witness never
    signed) may hide the pending record: it is never anchored as current."""
    legacy = legacy_workspace(neighbour)
    plant(rig.workspace, legacy)
    scope = scope_of(rig.workspace)
    folder = folder_of(rig, "stop_latch", LEGACY)
    forged = folder / f"{len(list(folder.glob('*.json'))) + 1:06d}.json"
    forged.write_bytes(b'{"anchor": "not signed by the witness"}')
    forged.chmod(0o600)
    github = endpoint(neighbour)

    recover(neighbour.request(), sim(github))

    assert status(rig, "stop_latch", legacy, LEGACY) == "anchor_invalid"
    assert status(rig, "stop_latch", legacy, scope) == "pending_anchor"
    assert status(rig, "stop_latch", rig.workspace, scope) == "verified"
    assert github.calls == []


def test_a_durable_append_never_completes_a_legacy_record_under_an_identity(
    rig: Rig, neighbour: Rig
) -> None:
    legacy = legacy_workspace(neighbour)
    plant(rig.workspace, legacy)
    event = publication_attempts.latch_events(legacy)[-1]
    assert isinstance(event, LatchEvent)
    store_before = sorted(p.name for p in rig.witness.location.rglob("*.json"))

    error = refused(
        lambda: durable_append(legacy, event, repository(rig), rig.anchoring(workspace=legacy))
    )

    assert error.detail == "anchor:legacy_evidence"
    assert sorted(p.name for p in rig.witness.location.rglob("*.json")) == store_before


def test_a_legacy_receipt_is_never_adopted_under_a_copied_identity(
    rig: Rig, neighbour: Rig
) -> None:
    """A receipt the pre-AR5d writer anchored under the literal scope stays legacy evidence."""
    cancel_reservation(rig)
    assert publication_ledger.reserve(neighbour.request(), NOW).status == "appended"
    github = endpoint(neighbour)
    assert execute(neighbour.request(), sim(github)).status == "published"
    assert recover(neighbour.request(), sim(github)).classification == "published_authorized"
    workspace = neighbour.workspace
    (workspace / IDENTITY).unlink()
    shutil.rmtree(workspace / "workspace-writer", ignore_errors=True)
    legacy_anchor(neighbour, workspace, "recovery_receipts")
    plant(rig.workspace, workspace)
    scope = scope_of(rig.workspace)

    recover(neighbour.request(), sim(github))

    assert status(rig, "recovery_receipts", workspace, scope) == "pending_anchor"
    assert status(rig, "recovery_receipts", rig.workspace, scope) == "verified"


# --- preserved: legacy state stays denied, untouched and read-only ----------------------------


def test_a_populated_legacy_workspace_is_denied_and_its_evidence_kept(neighbour: Rig) -> None:
    legacy = legacy_workspace(neighbour)
    before = sorted(str(p.relative_to(legacy)) for p in legacy.rglob("*"))
    store = neighbour.witness.location
    store_before = sorted(str(p) for p in store.rglob("*.json"))
    github = endpoint(neighbour)

    outcome = execute(neighbour.request(), sim(github))
    refusal = refused(
        lambda: workspace_identity.establish(
            legacy, repository(neighbour), neighbour.anchoring(), NOW
        )
    )
    verdicts = workspace_identity.legacy_verdicts(
        legacy, repository(neighbour), neighbour.anchoring()
    )

    assert outcome.status == "denied"
    assert "workspace_identity:missing" in anchor_reasons(outcome)
    assert refusal.detail.startswith("workspace_identity:workspace_not_empty")
    assert verdicts["stop_latch"] == "verified"
    assert sorted(str(p.relative_to(legacy)) for p in legacy.rglob("*")) == before
    assert sorted(str(p) for p in store.rglob("*.json")) == store_before
    assert publication_attempts.latched(legacy)
    assert github.calls == []


def test_recovery_on_a_legacy_workspace_writes_nothing_and_advances_no_anchor(
    rig: Rig, neighbour: Rig
) -> None:
    """The old writer sent and lost the answer (``outcome_unknown``); recovery from that legacy
    workspace is refused before it writes a record or anchors anything, even the
    transaction's own attempt chain, which every workspace shares."""
    cancel_reservation(rig)
    assert publication_ledger.reserve(neighbour.request(), NOW).status == "appended"
    github = endpoint(neighbour)
    github.fault = "lost"
    assert execute(neighbour.request(), sim(github)).status == "outcome_unknown"
    github.fault = None
    workspace = neighbour.workspace
    (workspace / IDENTITY).unlink()
    shutil.rmtree(workspace / "workspace-writer", ignore_errors=True)
    before = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob("*.json")}
    store = neighbour.witness.location
    store_before = sorted(str(p) for p in store.rglob("*.json"))

    error = refused(lambda: recover(neighbour.request(), sim(github)))

    assert error.detail == "workspace_identity:missing"
    after = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob("*.json")}
    assert after == before
    assert sorted(str(p) for p in store.rglob("*.json")) == store_before
    tid = neighbour.transaction.transaction_id
    assert publication_attempts.records(workspace, tid)[-1].state == "outcome_unknown"


def unidentified(neighbour: Rig) -> Path:
    """``neighbour`` without its identity and writer chain, and nothing else."""
    (neighbour.workspace / IDENTITY).unlink()
    shutil.rmtree(neighbour.workspace / "workspace-writer", ignore_errors=True)
    return neighbour.workspace


def test_an_owner_stop_binds_a_legacy_workspace_and_a_resume_never_clears_it(
    neighbour: Rig,
) -> None:
    workspace = unidentified(neighbour)
    opening = first(neighbour)
    stop = stop_of(neighbour, opening)

    stopped = record_stop(with_control(neighbour, opening, stop), NOW)
    again = resume_of(neighbour, stop, 3, stop)
    error = refused(
        lambda: resume(with_control(neighbour, opening, stop, again), LATER + timedelta(minutes=1))
    )

    assert (stopped.status, stopped.anchored) == ("stop_recorded", False)
    assert error.detail == "workspace_identity:missing"
    assert publication_attempts.latched(workspace)
    assert not (workspace / IDENTITY).exists()


def test_a_writer_claim_on_a_legacy_workspace_is_refused(neighbour: Rig) -> None:
    workspace = unidentified(neighbour)

    error = refused(lambda: workspace_writer.claim(neighbour.request(), "a" * 64, NOW))

    assert error.detail == "workspace_identity:missing"
    assert not (workspace / "workspace-writer").exists()


def test_the_legacy_file_latch_is_denied_and_blocks_establish(rig: Rig, tmp_path: Path) -> None:
    workspace = fresh(tmp_path)
    (workspace / "stop-latch.json").write_text("{}")

    error = refused(
        lambda: workspace_identity.establish(
            workspace, repository(rig), rig.anchoring(workspace=workspace), NOW
        )
    )

    assert error.detail == "workspace_identity:workspace_not_empty:stop-latch.json"
    assert (workspace / "stop-latch.json").read_text() == "{}"


def test_a_legacy_open_reservation_is_never_cleared_by_another_workspace(
    rig: Rig, tmp_path: Path
) -> None:
    """The legacy workspace attempted and reserved; a new workspace for the same transaction
    meets its anchored attempt chain, sends nothing and leaves the reservation open."""
    assert publication_ledger.reserve(rig.request(), NOW).status in ("appended", "already_applied")
    tid = rig.transaction.transaction_id
    lost = endpoint(rig)
    lost.fault = "lost"
    assert execute(rig.request(), sim(lost)).status != "published"
    attempts_before = publication_attempts.records(rig.workspace, tid)
    entries_before = publication_ledger.entries(rig.ledger)
    workspace = fresh(tmp_path)
    workspace_identity.establish(
        workspace, repository(rig), rig.anchoring(workspace=workspace), NOW
    )
    github = endpoint(rig)

    outcome = execute(
        rig.request(workspace=workspace, anchoring=rig.anchoring(workspace=workspace)), sim(github)
    )

    assert outcome.status == "denied"
    assert "publication_attempts:local_rolled_back" in anchor_reasons(outcome)
    assert github.calls == []
    assert publication_ledger.entries(rig.ledger) == entries_before
    assert publication_attempts.records(rig.workspace, tid) == attempts_before
