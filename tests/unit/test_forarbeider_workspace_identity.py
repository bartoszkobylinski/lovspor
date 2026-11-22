"""AR5d-1: workspace identity and anchor isolation (#738, #1013).

Every workspace family (``stop_latch``, ``recovery_receipts``) is anchored in a
scope derived from the workspace's own identity, a canonical record the
witness itself has anchored. Two workspaces therefore share one witness
store without sharing a chain, and a copy, a substitution, a removal or a
rollback of the identity fails closed. The attacks are real: files copied,
moved, rewritten and deleted in real directories, a witness moved away, a
crash injected at each boundary of the identity's creation, several processes
at once.
"""

import json
import multiprocessing
import shutil
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import (
    publication_attempts,
    publication_ledger,
    workspace_identity,
    workspace_identity_models,
)
from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorQuery,
    anchor_pending,
    durable_append,
    initialize,
    verify,
    workspace_scope,
)
from lovspor.forarbeider.control_plane_anchor_models import WitnessedAnchor
from lovspor.forarbeider.control_plane_chains import workspace_chain
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.publication_execute import (
    ExecutionOutcome,
    Production,
    Simulation,
    execute,
)
from lovspor.forarbeider.publication_execution_models import LatchEvent
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash, SimulatedGitHub
from lovspor.forarbeider.release_ledger_models import LedgerEntry, LedgerOperation
from lovspor.forarbeider.release_limits import LimitsRequest, verify_limits
from tests.unit.forarbeider_execution_support import (
    APP,
    NOW,
    WITNESS,
    KeySigner,
    Rig,
    make_rig,
)

IDENTITY = "workspace-identity.json"
LEGACY = "workspace"


def endpoint(rig: Rig) -> SimulatedGitHub:
    return SimulatedGitHub(head_oid=rig.base, identity=APP, now=NOW)


def sim(github: SimulatedGitHub) -> Simulation:
    return Simulation(clock=lambda: NOW, transport=github)


def refused(action: Any) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    return raised.value


def anchor_reasons(outcome: ExecutionOutcome) -> tuple[str, ...]:
    assert outcome.evidence is not None
    found = next(c for c in outcome.evidence.checks if c.name == "ar5_control_anchor")
    return found.reasons


def repository(rig: Rig) -> str:
    return rig.inputs.settings.repository


def identity_api() -> Any:
    """The AR5d-1 module (imported inside each test in the red run, when it did not exist)."""
    return workspace_identity


def scope_of(rig: Rig, workspace: Path | None = None) -> str:
    held = workspace or rig.workspace
    return str(workspace_scope(held, repository(rig), rig.anchoring(workspace=held)))


def latch_event(rig: Rig, reason: str = "synthetic_halt") -> LatchEvent:
    return LatchEvent(
        sequence=1,
        previous_sha256=None,
        kind="set",
        at=NOW,
        transaction_id=rig.transaction.transaction_id,
        reasons=(reason,),
    )


def halt(rig: Rig, workspace: Path | None = None) -> str:
    """A workspace's latch set and anchored through AR5a's public durable append."""
    held = workspace or rig.workspace
    return durable_append(
        held, latch_event(rig), repository(rig), rig.anchoring(workspace=held)
    ).status


def anchors_of(witness: Path, family: str) -> list[WitnessedAnchor]:
    folder = witness / family
    if not folder.is_dir():
        return []
    return [
        WitnessedAnchor.model_validate_json(path.read_bytes())
        for path in sorted(folder.glob("*/*.json"))
    ]


def folder_of(witness: Path, family: str, repository_: str, scope: str) -> Path:
    return witness / family / sha256(f"{repository_}\n{scope}".encode()).hexdigest()


def legacy(rig: Rig) -> None:
    """A pre-AR5d workspace: no identity, its families anchored under the literal scope."""
    (rig.workspace / IDENTITY).unlink(missing_ok=True)
    authority = rig.anchoring()
    for family in ("stop_latch", "recovery_receipts"):
        chain = workspace_chain(family, rig.workspace, LEGACY)  # type: ignore[arg-type]
        query = AnchorQuery(family, repository(rig), LEGACY, chain)  # type: ignore[arg-type]
        if verify(query, authority).status == "unanchored":
            assert initialize(query, authority).status == "verified"


def published_and_settled(rig: Rig) -> SimulatedGitHub:
    github = endpoint(rig)
    assert execute(rig.request(), sim(github)).status == "published"
    settled = recover(rig.request(), sim(github))
    assert settled.settlement is not None and settled.settlement.status == "settled"
    return github


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return make_rig(tmp_path / "a", "A")


@pytest.fixture
def neighbour(rig: Rig, tmp_path: Path) -> Rig:
    """Another workspace and transaction on ``rig``'s keys, ledger **and witness store**."""
    return make_rig(tmp_path / "b", "A", share=rig, one_witness=True)


# --- two workspaces, one witness (#1013) --------------------------------------------------------


def test_two_workspaces_on_one_witness_both_publish_and_settle(rig: Rig, neighbour: Rig) -> None:
    first = published_and_settled(rig)
    assert publication_ledger.reserve(neighbour.request(), NOW).status == "appended"
    second = published_and_settled(neighbour)

    assert len(first.calls) == len(second.calls) == 1
    assert neighbour.witness.location == rig.witness.location


def test_each_workspace_family_has_its_own_scope_in_the_one_witness(
    rig: Rig, neighbour: Rig
) -> None:
    witness = rig.witness.location
    for family in ("stop_latch", "recovery_receipts"):
        scopes = {a.anchor.scope for a in anchors_of(witness, family)}
        assert len(scopes) == 2, family
        assert all(scope.startswith("workspace:") for scope in scopes), scopes
        assert LEGACY not in scopes


def test_a_latch_in_one_workspace_never_stops_the_other(rig: Rig, neighbour: Rig) -> None:
    assert halt(rig) == "appended"
    stopped = execute(rig.request(), sim(endpoint(rig)))
    published = published_and_settled_after_reserve(rig, neighbour)

    assert stopped.status != "published"
    assert published == 1


def published_and_settled_after_reserve(rig: Rig, neighbour: Rig) -> int:
    """``neighbour`` publishes while ``rig`` holds no open reservation (``rig``'s is cancelled)."""
    cancel_reservation(rig)
    assert publication_ledger.reserve(neighbour.request(), NOW).status == "appended"
    github = published_and_settled(neighbour)
    return len(github.calls)


def cancel_reservation(rig: Rig) -> None:
    reserve = LedgerEntry.model_validate_json(publication_ledger.entries(rig.ledger)[-1])
    cancel = LedgerOperation(
        operation="cancel",
        ledger_id=rig.ledger.ledger_id,
        day=reserve.day,
        release_id=reserve.release_id,
        batch_id=reserve.batch_id,
        amounts=reserve.amounts,
        recorded_at=NOW,
    )
    limits = verify_limits(
        LimitsRequest(rig.inputs.policy.policy, rig.inputs.limits, NOW), rig.root
    )
    authority = rig.anchoring(workspace=rig.ledger.root)
    publication_ledger.record(rig.ledger, cancel, limits, (authority, repository(rig)))


def test_the_other_workspace_latch_stays_its_own(rig: Rig, neighbour: Rig) -> None:
    assert halt(neighbour) == "appended"

    assert published_and_settled(rig).calls
    assert execute(neighbour.request(), sim(endpoint(neighbour))).status != "published"


# --- identity: missing, malformed, substituted, rolled back --------------------------------------


def test_a_removed_identity_denies_and_sends_nothing(rig: Rig) -> None:
    (rig.workspace / IDENTITY).unlink(missing_ok=True)
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "workspace_identity:missing" in anchor_reasons(outcome)
    assert github.calls == []


def _junk(path: Path) -> None:
    path.write_bytes(b"{not json")


def _pretty(path: Path) -> None:
    text = path.read_text() if path.exists() else '{"record_type": "x"}'
    path.write_text(json.dumps(json.loads(text), indent=2))


def _forged(path: Path) -> None:
    """A canonical, well-formed identity the witness never anchored: a claim, not authority."""
    held = workspace_identity_models.WorkspaceIdentity.model_validate_json(path.read_bytes())
    forged = held.model_copy(update={"workspace_id": "f" * 64})
    path.write_bytes(workspace_identity_models.identity_bytes(forged))


def _linked(path: Path) -> None:
    elsewhere = path.parent.parent / "elsewhere.json"
    if path.exists():
        path.replace(elsewhere)
    else:
        elsewhere.write_text("{}")
    path.symlink_to(elsewhere)


def _shared(path: Path) -> None:
    if not path.exists():
        path.write_text("{}")
    path.chmod(0o666)


@pytest.mark.parametrize(
    ("damage", "why"),
    [
        (_junk, "unreadable"),
        (_pretty, "not_canonical"),
        (_forged, "unanchored"),
        (_linked, "unsafe"),
        (_shared, "unsafe"),
    ],
    ids=["junk", "pretty", "forged", "linked", "shared"],
)
def test_a_malformed_or_unanchored_identity_denies(rig: Rig, damage: Any, why: str) -> None:
    damage(rig.workspace / IDENTITY)
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert f"workspace_identity:{why}" in anchor_reasons(outcome)
    assert github.calls == []


def test_the_identity_anchor_removed_from_the_witness_denies(rig: Rig) -> None:
    shutil.rmtree(rig.witness.location / "workspace_identity", ignore_errors=True)
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "workspace_identity:unanchored" in anchor_reasons(outcome)
    assert github.calls == []
    # added after the red run: an identity the witness lost is never registered again
    again = refused(
        lambda: workspace_identity.establish(rig.workspace, repository(rig), rig.anchoring(), NOW)
    )
    assert again.detail == "workspace_identity:unanchored"
    assert not (rig.witness.location / "workspace_identity").exists()


def test_another_workspace_identity_substituted_denies(rig: Rig, neighbour: Rig) -> None:
    """The neighbour's identity in ``rig``: ``rig``'s chains are compared with the neighbour's."""
    assert halt(neighbour) == "appended"
    shutil.copy2(neighbour.workspace / IDENTITY, rig.workspace / IDENTITY)
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "stop_latch:local_rolled_back" in anchor_reasons(outcome)
    assert github.calls == []


def test_an_identity_for_another_repository_is_refused(rig: Rig) -> None:
    error = refused(lambda: workspace_scope(rig.workspace, "someone/else", rig.anchoring()))
    assert error.detail == "workspace_identity:repository_mismatch"


# --- copies and relocation ------------------------------------------------------------------------


def test_a_relocated_workspace_keeps_its_identity_and_publishes(rig: Rig, tmp_path: Path) -> None:
    before = scope_of(rig)
    moved = tmp_path / "moved-workspace"
    shutil.move(str(rig.workspace), str(moved))

    github = endpoint(rig)
    outcome = execute(rig.request(workspace=moved), sim(github))

    assert outcome.status == "published"
    assert scope_of(rig, moved) == before
    assert len(github.calls) == 1


def test_a_copy_taken_after_anchoring_is_refused_once_the_original_advances(
    rig: Rig, tmp_path: Path
) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(rig.workspace, copy)
    assert execute(rig.request(), sim(endpoint(rig))).status == "published"

    late = endpoint(rig)
    outcome = execute(rig.request(workspace=copy), sim(late))

    assert outcome.status == "denied"
    assert late.calls == []


def test_the_original_is_refused_once_its_copy_advances(rig: Rig, tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(rig.workspace, copy)
    assert halt(rig, copy) == "appended"

    github = endpoint(rig)
    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "stop_latch:local_rolled_back" in anchor_reasons(outcome)
    assert github.calls == []


def test_a_copied_identity_in_an_empty_workspace_is_the_same_writer_never_a_second(
    rig: Rig, tmp_path: Path
) -> None:
    """The identity names one lineage: whichever copy advances first, the other is refused."""
    clone = tmp_path / "clone"
    clone.mkdir(mode=0o700)
    shutil.copy2(rig.workspace / IDENTITY, clone / IDENTITY)
    first, second = endpoint(rig), endpoint(rig)

    winner = execute(rig.request(workspace=clone), sim(first))
    loser = execute(rig.request(), sim(second))

    assert [winner.status, loser.status] == ["published", "denied"]
    assert len(first.calls) + len(second.calls) == 1


# --- establishing an identity ---------------------------------------------------------------------


def fresh(rig: Rig, tmp_path: Path, name: str = "fresh") -> Path:
    workspace = tmp_path / name
    workspace.mkdir(mode=0o700)
    return workspace


def test_establishing_is_idempotent_and_survives_a_restart(rig: Rig, tmp_path: Path) -> None:
    api = identity_api()
    workspace = fresh(rig, tmp_path)
    authority = rig.anchoring(workspace=workspace)

    made = api.establish(workspace, repository(rig), authority, NOW)
    again = api.establish(workspace, repository(rig), authority, NOW)

    assert (made.status, again.status) == ("established", "already_established")
    assert made.scope == again.scope == scope_of(rig, workspace)
    assert made.scope != scope_of(rig)


def test_two_fresh_workspaces_never_get_the_same_identity(rig: Rig, tmp_path: Path) -> None:
    api = identity_api()
    scopes = {
        api.establish(fresh(rig, tmp_path, f"w{n}"), repository(rig), rig.anchoring(), NOW).scope
        for n in range(3)
    }
    assert len(scopes) == 3


def test_establishing_needs_the_witness(rig: Rig, tmp_path: Path) -> None:
    api = identity_api()
    workspace = fresh(rig, tmp_path)
    away = rig.witness.location.with_name("witness-away")
    shutil.move(str(rig.witness.location), str(away))

    error = refused(lambda: api.establish(workspace, repository(rig), rig.anchoring(), NOW))

    assert "anchor_store_unavailable" in error.detail or "store_unavailable" in error.detail
    assert not (workspace / IDENTITY).exists()


def test_a_crash_before_the_identity_is_written_leaves_nothing_and_a_retry_establishes(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = identity_api()
    workspace = fresh(rig, tmp_path)
    real = workspace_identity_models.write_identity

    def crash(*_: Any) -> None:
        raise SimulatedCrash("before the identity is written")

    monkeypatch.setattr(workspace_identity_models, "write_identity", crash)
    with pytest.raises(SimulatedCrash):
        api.establish(workspace, repository(rig), rig.anchoring(), NOW)
    assert not (workspace / IDENTITY).exists()

    monkeypatch.setattr(workspace_identity_models, "write_identity", real)
    assert api.establish(workspace, repository(rig), rig.anchoring(), NOW).status == "established"


def test_a_crash_after_the_identity_is_written_is_pending_and_a_retry_completes_it(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = identity_api()
    workspace = fresh(rig, tmp_path)
    real = api.anchor_pending

    def crash(*_: Any) -> None:
        raise SimulatedCrash("after the identity is written, before its anchor")

    monkeypatch.setattr(api, "anchor_pending", crash)
    with pytest.raises(SimulatedCrash):
        api.establish(workspace, repository(rig), rig.anchoring(), NOW)
    pending = refused(lambda: scope_of(rig, workspace))

    monkeypatch.setattr(api, "anchor_pending", real)
    completed = api.establish(workspace, repository(rig), rig.anchoring(), NOW)

    assert pending.detail == "workspace_identity:pending_anchor"
    assert completed.status == "completed"
    assert completed.scope == scope_of(rig, workspace)


def test_a_leftover_temporary_identity_is_never_an_identity(rig: Rig, tmp_path: Path) -> None:
    api = identity_api()
    workspace = fresh(rig, tmp_path)
    leftover = workspace / f".{IDENTITY}.tmp-0000"
    leftover.write_bytes(b"partial")
    leftover.chmod(0o600)

    made = api.establish(workspace, repository(rig), rig.anchoring(), NOW)

    assert made.status == "established"
    assert made.scope == scope_of(rig, workspace)


def _establish_in_process(job: tuple[Any, ...]) -> str:
    workspace, repository_, root, witness_dir, witness_key, start = job
    witness = OfflineAnchorWitness(witness_dir, KeySigner(WITNESS, witness_key))
    authority = AnchorAuthority(witness, root, NOW, None, workspace)
    while time.time() < start:
        time.sleep(0.001)
    try:
        made = workspace_identity.establish(workspace, repository_, authority, NOW)
    except ForarbeiderRefusedError as error:
        return f"refused:{error.reason.value}:{error.detail}"
    return f"{made.status}:{made.scope}"


def test_processes_establishing_one_workspace_at_once_make_one_identity(
    rig: Rig, tmp_path: Path
) -> None:
    identity_api()
    workspace = fresh(rig, tmp_path)
    start = time.time() + 1.5
    job = (workspace, repository(rig), rig.root, rig.witness.location, rig.witness_key, start)
    with multiprocessing.get_context("spawn").Pool(4) as pool:
        results = pool.map(_establish_in_process, [job] * 4)

    made = [r for r in results if r.startswith(("established:", "already_established:"))]
    scopes = {r.split(":", 1)[1] for r in made}
    others = [r for r in results if r not in made]
    assert len(scopes) == 1, results
    assert scopes == {scope_of(rig, workspace)}
    assert [r for r in results if r.startswith("established:")].__len__() == 1, results
    assert all("corpus_locked:workspace" in r for r in others), results


def _halt_in_process(job: tuple[Any, ...]) -> str:
    workspace, repository_, root, witness_dir, witness_key, event, start = job
    witness = OfflineAnchorWitness(witness_dir, KeySigner(WITNESS, witness_key))
    authority = AnchorAuthority(witness, root, NOW, None, workspace)
    while time.time() < start:
        time.sleep(0.001)
    try:
        return durable_append(workspace, event, repository_, authority).status
    except ForarbeiderRefusedError as error:
        return f"refused:{error.reason.value}:{error.detail}"


def test_processes_in_two_workspaces_on_one_witness_both_anchor(rig: Rig, neighbour: Rig) -> None:
    start = time.time() + 1.5
    jobs = [
        (
            r.workspace,
            repository(r),
            r.root,
            r.witness.location,
            r.witness_key,
            latch_event(r),
            start,
        )
        for r in (rig, neighbour)
    ]
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_halt_in_process, jobs)

    assert results == ["appended", "appended"]
    for each in (rig, neighbour):
        chain = workspace_chain("stop_latch", each.workspace, scope_of(each))
        query = AnchorQuery("stop_latch", repository(each), scope_of(each), chain)
        assert verify(query, each.anchoring()).status == "verified"


# --- legacy (pre-AR5d) anchors --------------------------------------------------------------------


def test_a_legacy_workspace_without_identity_is_denied_not_adopted(rig: Rig) -> None:
    legacy(rig)
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "workspace_identity:missing" in anchor_reasons(outcome)
    assert github.calls == []


def test_a_legacy_workspace_with_records_is_never_given_an_identity(rig: Rig) -> None:
    api = identity_api()
    legacy(rig)
    legacy_latch = AnchorQuery(
        "stop_latch", repository(rig), LEGACY, workspace_chain("stop_latch", rig.workspace, LEGACY)
    )
    publication_attempts.latch(rig.workspace, ("legacy_halt",), NOW, rig.transaction.transaction_id)
    after = AnchorQuery(
        "stop_latch", repository(rig), LEGACY, workspace_chain("stop_latch", rig.workspace, LEGACY)
    )
    assert anchor_pending(after, rig.anchoring()).status == "verified"
    store_before = sorted(str(p) for p in rig.witness.location.rglob("*.json"))

    error = refused(lambda: api.establish(rig.workspace, repository(rig), rig.anchoring(), NOW))

    assert error.detail.startswith("workspace_identity:workspace_not_empty")
    assert not (rig.workspace / IDENTITY).exists()
    assert publication_attempts.latched(rig.workspace)
    assert sorted(str(p) for p in rig.witness.location.rglob("*.json")) == store_before
    verdicts = api.legacy_verdicts(rig.workspace, repository(rig), rig.anchoring())
    assert verdicts == {"stop_latch": "verified", "recovery_receipts": "verified"}
    assert legacy_latch.scope == LEGACY


def test_legacy_anchors_are_never_trusted_under_an_identity_scope(rig: Rig) -> None:
    witness = rig.witness.location
    scope = scope_of(rig)
    legacy_folder = folder_of(witness, "stop_latch", repository(rig), LEGACY)
    legacy_folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    authority = rig.anchoring()
    chain = workspace_chain("stop_latch", rig.workspace, scope)
    legacy_query = AnchorQuery("stop_latch", repository(rig), LEGACY, chain)
    if verify(legacy_query, authority).status == "unanchored":
        initialize(legacy_query, authority)
    target = folder_of(witness, "stop_latch", repository(rig), scope)
    for path in target.glob("*.json"):
        path.unlink()
    for path in legacy_folder.glob("*.json"):
        shutil.copy2(path, target / path.name)
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "stop_latch:anchor_invalid" in anchor_reasons(outcome)
    assert github.calls == []


# --- preserved ------------------------------------------------------------------------------------


def test_production_stays_denied(rig: Rig) -> None:
    assert execute(rig.request(), Production()).status != "published"


def test_a_rolled_back_latch_under_the_identity_scope_still_denies(rig: Rig) -> None:
    assert halt(rig) == "appended"
    shutil.rmtree(rig.workspace / "stop-latch")
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "stop_latch:local_rolled_back" in anchor_reasons(outcome)
    assert github.calls == []
    assert (rig.workspace / IDENTITY).exists()


# --- added after the red run: crash leftovers and witness truncation of the identity -----------


def test_a_crash_between_linking_and_unlinking_is_refused_until_establish_cleans_it(
    rig: Rig,
) -> None:
    """A temporary hard link left by a crash makes the identity a shared file: refused, then
    removed under the workspace lock, and the same identity is confirmed, never redrawn."""
    before = scope_of(rig)
    leftover = rig.workspace / f".{IDENTITY}.tmp-crashed"
    leftover.hardlink_to(rig.workspace / IDENTITY)

    blocked = refused(lambda: scope_of(rig))
    again = workspace_identity.establish(rig.workspace, repository(rig), rig.anchoring(), NOW)

    assert blocked.detail == "workspace_identity:unsafe"
    assert (again.status, again.scope) == ("already_established", before)
    assert not leftover.exists()
    assert scope_of(rig) == before


def test_a_truncated_identity_history_at_the_witness_denies_until_completed(rig: Rig) -> None:
    scope = scope_of(rig)
    folder = folder_of(rig.witness.location, "workspace_identity", repository(rig), scope)
    newest = sorted(folder.glob("*.json"))[-1]
    newest.unlink()
    github = endpoint(rig)

    outcome = execute(rig.request(), sim(github))

    assert outcome.status == "denied"
    assert "workspace_identity:pending_anchor" in anchor_reasons(outcome)
    assert github.calls == []
    completed = workspace_identity.establish(rig.workspace, repository(rig), rig.anchoring(), NOW)
    assert (completed.status, completed.scope) == ("completed", scope)
