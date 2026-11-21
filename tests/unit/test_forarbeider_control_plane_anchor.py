"""AR5a: durable control-plane state, anchored by an independent witness (#738; §9.9 AR5).

The witness here is the offline :class:`OfflineAnchorWitness`: its own private
directory, outside every workspace, and its own key, whose only trust-root
role is ``anchor_witness``. The runner's workspace holds no witness key and no
anchor. Every attack below is real: files deleted, rolled back, rewritten or
forged on disk, a witness folder removed, concurrent processes.
"""

import json
import shutil
import subprocess
import sys
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import control_plane_witness, publication_attempts
from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorPin,
    AnchorQuery,
    anchor_pending,
    durable_append,
    initialize,
    verified_state,
    verify,
)
from lovspor.forarbeider.control_plane_anchor_models import (
    ControlPlaneAnchor,
    WitnessedAnchor,
    anchor_bytes,
    anchor_digest,
)
from lovspor.forarbeider.control_plane_chains import (
    control_chain,
    ledger_chain,
    quarantine_chain,
    workspace_chain,
)
from lovspor.forarbeider.control_plane_witness import AnchorKey, OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import (
    ANCHOR_NAMESPACE,
    CONTROL_NAMESPACE,
    POLICY_NAMESPACE,
)
from lovspor.forarbeider.publication_execute import Simulation, attempt_id_of, execute
from lovspor.forarbeider.publication_execution_models import AttemptRecord, StopLatch
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash, SimulatedGitHub
from lovspor.forarbeider.release_ledger_models import LedgerEntry
from lovspor.forarbeider.release_quarantine_models import ExclusionEntry, QuarantineLedger
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_authorization_support import (
    BOT_NAMESPACES,
    CHECKED_AT,
    Key,
    new_key,
    signer_line,
    trust_root,
)
from tests.unit.forarbeider_execution_support import APP, NOW, KeySigner, make_rig
from tests.unit.forarbeider_release_control_support import control, operation, signed_by

REPOSITORY = "bartoszkobylinski/lovverk-forarbeider"
TID = "1" * 64
WITNESS = "test-witness"
REPO_ROOT = Path(__file__).resolve().parents[2]


class World:
    """One workspace, one witness outside it, one trust root, the keys."""

    def __init__(self, tmp: Path) -> None:
        keys = tmp / "keys"
        self.tmp = tmp
        self.witness_key = new_key(keys, "witness")
        self.bot = new_key(keys, "bot")
        self.owner = new_key(keys, "owner")
        self.lines = [
            signer_line(WITNESS, self.witness_key, ANCHOR_NAMESPACE),
            signer_line("test-bot", self.bot, BOT_NAMESPACES),
            signer_line("test-owner", self.owner, f"{POLICY_NAMESPACE},{CONTROL_NAMESPACE}"),
        ]
        self.root = trust_root(tmp / "root", self.lines)
        self.store = tmp / "witness"
        self.store.mkdir(mode=0o700)
        self.workspace = tmp / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.witness = OfflineAnchorWitness(self.store, KeySigner(WITNESS, self.witness_key))

    def authority(self, **changes: Any) -> AnchorAuthority:
        fields: dict[str, Any] = {
            "witness": self.witness,
            "root": self.root,
            "checked_at": CHECKED_AT,
            "workspace": self.workspace,
        }
        return AnchorAuthority(**(fields | changes))

    def attempts(self, workspace: Path | None = None) -> AnchorQuery:
        chain = workspace_chain("publication_attempts", workspace or self.workspace, TID)
        return AnchorQuery("publication_attempts", REPOSITORY, TID, chain)

    def latch(self) -> AnchorQuery:
        chain = workspace_chain("stop_latch", self.workspace, "workspace")
        return AnchorQuery("stop_latch", REPOSITORY, "workspace", chain)

    def anchor_files(self) -> list[Path]:
        return sorted(p for p in self.store.rglob("*.json") if p.is_file())


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def attempt(sequence: int, previous: AttemptRecord | None, state: str = "denied") -> AttemptRecord:
    return AttemptRecord.model_validate(
        {
            "transaction_id": TID,
            "release_manifest_sha256": "2" * 64,
            "base_commit": "3" * 40,
            "path": "A",
            "attempt_id": attempt_id_of(TID, sequence),
            "sequence": sequence,
            "state": state,
            "recorded_at": CHECKED_AT + timedelta(seconds=sequence),
            "authorization_sha256": None,
            "commit": None,
            "reasons": (f"synthetic-{state}",),
            "previous_sha256": None if previous is None else digest_of(previous),
        }
    )


def digest_of(record: Any) -> str:
    return sha256(canonical_text(record).encode()).hexdigest()


def appended(world: World, count: int) -> list[AttemptRecord]:
    """``count`` attempt records, each appended durably and anchored."""
    assert initialize(world.attempts(), world.authority()).status == "verified"
    records: list[AttemptRecord] = []
    for sequence in range(1, count + 1):
        record = attempt(sequence, records[-1] if records else None)
        outcome = durable_append(world.workspace, record, REPOSITORY, world.authority())
        assert outcome.status == "appended"
        records.append(record)
    return records


def tree(path: Path) -> dict[str, bytes]:
    """Every record file below ``path``; the persistent, empty lock files are not records."""
    locks = {"workspace.lock", ".witness.lock"}
    return {
        str(p.relative_to(path)): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file() and p.name not in locks
    }


def assert_refused(action: Any, detail: str) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    assert detail in raised.value.detail
    return raised.value


# --- a missing anchor is never an empty history ------------------------------------------------


def test_an_empty_family_without_an_anchor_is_unanchored_not_clean(world: World) -> None:
    verdict = verify(world.attempts(), world.authority())

    assert verdict.status == "unanchored"
    assert "anchor_missing" in verdict.reasons
    assert_refused(lambda: verified_state(world.attempts(), world.authority()), "unanchored")


def test_records_without_an_anchor_are_unanchored(world: World) -> None:
    publication_attempts.append(world.workspace, attempt(1, None))

    verdict = verify(world.attempts(), world.authority())

    assert verdict.status == "unanchored"
    assert_refused(lambda: initialize(world.attempts(), world.authority()), "unanchored")


def test_initializing_anchors_an_empty_family_once(world: World) -> None:
    first = initialize(world.attempts(), world.authority())

    assert first.status == "verified" and first.latest is not None
    assert first.latest.record_count == 0 and first.latest.head_sha256 is None
    assert first.latest.anchor_sequence == 1
    state = verified_state(world.attempts(), world.authority())
    assert (state.record_count, state.head_sha256) == (0, None)
    assert_refused(lambda: initialize(world.attempts(), world.authority()), "already_anchored")
    assert len(world.anchor_files()) == 1


def test_a_durable_append_is_anchored_and_its_anchor_is_witness_signed(world: World) -> None:
    records = appended(world, 2)

    verdict = verify(world.attempts(), world.authority())
    assert verdict.status == "verified" and verdict.latest is not None
    assert verdict.latest.record_count == 2
    assert verdict.latest.head_sha256 == digest_of(records[-1])
    raw = world.anchor_files()[-1].read_bytes()
    witnessed = WitnessedAnchor.model_validate_json(raw)
    assert witnessed.anchor == verdict.latest
    assert canonical_text(witnessed).encode() == raw
    assert not any(world.workspace.rglob("*anchor*"))


def test_repeated_verification_is_identical_and_writes_nothing(world: World) -> None:
    appended(world, 1)
    before = (tree(world.store), tree(world.workspace))

    results = [verify(world.attempts(), world.authority()) for _ in range(3)]

    assert len(set(results)) == 1 and results[0].status == "verified"
    assert (tree(world.store), tree(world.workspace)) == before


def test_retrying_an_appended_record_is_already_applied(world: World) -> None:
    records = appended(world, 1)
    before = tree(world.store)

    again = durable_append(world.workspace, records[0], REPOSITORY, world.authority())

    assert again.status == "already_applied"
    assert tree(world.store) == before


def test_a_durable_append_refuses_an_unanchored_family_and_writes_nothing(world: World) -> None:
    assert_refused(
        lambda: durable_append(world.workspace, attempt(1, None), REPOSITORY, world.authority()),
        "unanchored",
    )
    assert tree(world.workspace) == {}


# --- truncation, deletion, rollback, substitution -----------------------------------------------


def test_a_truncated_chain_is_rolled_back(world: World) -> None:
    records = appended(world, 3)
    (world.workspace / TID / "000003.json").unlink()

    verdict = verify(world.attempts(), world.authority())

    assert verdict.status == "local_rolled_back"
    assert verdict.local_count == 2
    again = attempt(3, records[1], state="refused")
    assert_refused(
        lambda: durable_append(world.workspace, again, REPOSITORY, world.authority()),
        "local_rolled_back",
    )


def test_a_deleted_family_is_rolled_back_never_empty(world: World) -> None:
    appended(world, 2)
    shutil.rmtree(world.workspace / TID)

    assert verify(world.attempts(), world.authority()).status == "local_rolled_back"


def test_a_workspace_restored_from_an_older_copy_is_rolled_back(world: World) -> None:
    records = appended(world, 1)
    snapshot = world.tmp / "snapshot"
    shutil.copytree(world.workspace, snapshot)
    record = attempt(2, records[0])
    assert durable_append(world.workspace, record, REPOSITORY, world.authority()).status == (
        "appended"
    )
    shutil.rmtree(world.workspace)
    shutil.copytree(snapshot, world.workspace)

    assert verify(world.attempts(), world.authority()).status == "local_rolled_back"


def test_a_canonically_rewritten_chain_diverges(world: World) -> None:
    appended(world, 2)
    shutil.rmtree(world.workspace / TID)
    first = attempt(1, None, state="refused")
    publication_attempts.append(world.workspace, first)
    publication_attempts.append(world.workspace, attempt(2, first))

    verdict = verify(world.attempts(), world.authority())

    assert verdict.status == "diverged"


def test_an_edited_head_diverges_and_a_non_canonical_record_is_invalid(world: World) -> None:
    appended(world, 1)
    path = world.workspace / TID / "000001.json"
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"denied"', b'"refused"', 1))
    assert verify(world.attempts(), world.authority()).status == "diverged"

    path.write_bytes(original + b" ")
    assert verify(world.attempts(), world.authority()).status == "local_invalid"


def test_a_deleted_stop_latch_is_rolled_back(world: World) -> None:
    assert initialize(world.latch(), world.authority()).status == "verified"
    latch = StopLatch(reasons=("synthetic_stop",), set_at=CHECKED_AT, transaction_id=TID)
    assert durable_append(world.workspace, latch, REPOSITORY, world.authority()).status == (
        "appended"
    )
    assert publication_attempts.latched(world.workspace)

    (world.workspace / "stop-latch.json").unlink()

    assert verify(world.latch(), world.authority()).status == "local_rolled_back"


# --- the witness's own history ------------------------------------------------------------------


def test_a_witness_rolled_back_under_a_newer_workspace_is_not_verified(world: World) -> None:
    appended(world, 2)
    world.anchor_files()[-1].unlink()

    verdict = verify(world.attempts(), world.authority())

    assert verdict.status == "pending_anchor"
    assert_refused(lambda: verified_state(world.attempts(), world.authority()), "pending_anchor")


def test_a_consistent_rollback_of_workspace_and_witness_is_caught_by_the_pin(world: World) -> None:
    records = appended(world, 1)
    snapshot_ws, snapshot_store = world.tmp / "ws1", world.tmp / "store1"
    shutil.copytree(world.workspace, snapshot_ws)
    shutil.copytree(world.store, snapshot_store)
    durable_append(world.workspace, attempt(2, records[0]), REPOSITORY, world.authority())
    latest = verify(world.attempts(), world.authority()).latest
    assert latest is not None
    pin = AnchorPin(latest.anchor_sequence, anchor_digest(latest))
    for live, old in ((world.workspace, snapshot_ws), (world.store, snapshot_store)):
        shutil.rmtree(live)
        shutil.copytree(old, live)

    verdict = verify(world.attempts(), world.authority(pin=pin))

    assert verdict.status == "anchor_rolled_back"


def _rewrite_anchor(path: Path, signer_key: Key, **changes: Any) -> None:
    witnessed = WitnessedAnchor.model_validate_json(path.read_bytes())
    anchor = witnessed.anchor.model_copy(update=changes)
    body = anchor_bytes(anchor)
    signature = KeySigner("someone", signer_key).sign(body, ANCHOR_NAMESPACE)
    forged = WitnessedAnchor(anchor=anchor, signature=signature.decode())
    path.write_bytes(canonical_text(forged).encode())


@pytest.mark.parametrize("signer", ["bot", "owner"])
def test_an_anchor_signed_by_the_runner_or_the_owner_is_forged(world: World, signer: str) -> None:
    records = appended(world, 1)
    forger = world.bot if signer == "bot" else world.owner
    _rewrite_anchor(world.anchor_files()[-1], forger)

    verdict = verify(world.attempts(), world.authority())

    assert verdict.status == "anchor_invalid"
    assert records


def test_an_edited_anchor_breaks_its_signature(world: World) -> None:
    appended(world, 2)
    path = world.anchor_files()[-1]
    witnessed = WitnessedAnchor.model_validate_json(path.read_bytes())
    anchor = witnessed.anchor.model_copy(update={"record_count": 1, "last_sequence": 1})
    edited = witnessed.model_copy(update={"anchor": anchor})
    path.write_bytes(canonical_text(edited).encode())

    assert verify(world.attempts(), world.authority()).status == "anchor_invalid"


def test_a_witness_signed_anchor_replayed_from_another_scope_is_refused(world: World) -> None:
    assert initialize(world.attempts(), world.authority()).status == "verified"
    other = AnchorKey("publication_attempts", REPOSITORY, "4" * 64)
    genesis = ControlPlaneAnchor(
        family="publication_attempts",
        family_schema="forarbeider_publication_attempt/1",
        repository=REPOSITORY,
        scope_kind="transaction",
        scope=other.scope,
        head_sha256=None,
        record_count=0,
        last_sequence=0,
        anchor_sequence=1,
        previous_anchor_sha256=None,
    )
    foreign = world.witness.submit(genesis)
    target = scope_files(world, TID)[0]
    target.write_bytes(canonical_text(foreign).encode())

    assert verify(world.attempts(), world.authority()).status == "anchor_invalid"


def scope_files(world: World, scope: str) -> list[Path]:
    return [
        p for p in world.anchor_files() if json.loads(p.read_text())["anchor"]["scope"] == scope
    ]


def test_a_revoked_witness_key_invalidates_its_anchors(world: World) -> None:
    appended(world, 1)
    revoked = trust_root(world.tmp / "revoked", world.lines, (world.witness_key,))

    assert verify(world.attempts(), world.authority(root=revoked)).status == "anchor_invalid"


def test_a_partially_written_anchor_is_invalid(world: World) -> None:
    appended(world, 1)
    path = world.anchor_files()[-1]
    path.write_bytes(path.read_bytes()[:40])

    assert verify(world.attempts(), world.authority()).status == "anchor_invalid"


def test_a_gap_in_the_witness_chain_is_invalid(world: World) -> None:
    appended(world, 2)
    world.anchor_files()[1].unlink()

    assert verify(world.attempts(), world.authority()).status == "anchor_invalid"


def test_an_unavailable_witness_refuses_and_nothing_is_written(world: World) -> None:
    records = appended(world, 1)
    shutil.rmtree(world.store)
    before = tree(world.workspace)

    assert verify(world.attempts(), world.authority()).status == "store_unavailable"
    assert_refused(
        lambda: durable_append(
            world.workspace, attempt(2, records[0]), REPOSITORY, world.authority()
        ),
        "store_unavailable",
    )
    assert tree(world.workspace) == before


def test_a_witness_inside_the_workspace_is_not_independent(world: World) -> None:
    inside = world.workspace / "anchors"
    inside.mkdir(mode=0o700)
    witness = OfflineAnchorWitness(inside, KeySigner(WITNESS, world.witness_key))

    verdict = verify(world.attempts(), world.authority(witness=witness))

    assert verdict.status == "store_not_independent"
    assert_refused(
        lambda: initialize(world.attempts(), world.authority(witness=witness)),
        "store_not_independent",
    )
    assert not any(inside.iterdir())


def test_the_witness_never_replaces_an_anchored_head(world: World) -> None:
    appended(world, 1)
    latest = verify(world.attempts(), world.authority()).latest
    assert latest is not None
    stale = latest.model_copy(update={"record_count": 0, "head_sha256": None, "last_sequence": 0})
    before = tree(world.store)

    error = assert_refused(lambda: world.witness.submit(stale), "anchor_head_moved")

    assert error.reason is RefusalReason.RELEASE_CONFLICT
    assert tree(world.store) == before


# --- crash safety --------------------------------------------------------------------------------


def test_a_crash_before_the_append_leaves_the_verified_state(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = appended(world, 1)

    def crash(workspace: Path, record: Any) -> None:
        raise SimulatedCrash

    monkeypatch.setattr(publication_attempts, "append", crash)
    with pytest.raises(SimulatedCrash):
        durable_append(world.workspace, attempt(2, records[0]), REPOSITORY, world.authority())
    monkeypatch.undo()

    assert verify(world.attempts(), world.authority()).status == "verified"
    outcome = durable_append(world.workspace, attempt(2, records[0]), REPOSITORY, world.authority())
    assert outcome.status == "appended"


def test_a_crash_after_the_append_before_the_anchor_is_completed_exactly(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = appended(world, 1)
    second = attempt(2, records[0])

    def crash(self: OfflineAnchorWitness, anchor: ControlPlaneAnchor) -> WitnessedAnchor:
        raise SimulatedCrash

    monkeypatch.setattr(OfflineAnchorWitness, "submit", crash)
    with pytest.raises(SimulatedCrash):
        durable_append(world.workspace, second, REPOSITORY, world.authority())
    monkeypatch.undo()

    assert verify(world.attempts(), world.authority()).status == "pending_anchor"
    other = attempt(2, records[0], state="refused")
    assert_refused(
        lambda: durable_append(world.workspace, other, REPOSITORY, world.authority()),
        "pending_anchor",
    )
    finished = durable_append(world.workspace, second, REPOSITORY, world.authority())
    assert finished.status == "completed"
    assert verify(world.attempts(), world.authority()).status == "verified"


def test_a_crash_during_the_anchor_update_leaves_no_half_anchor(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = appended(world, 1)
    anchors_before = world.anchor_files()

    def crash(*args: Any, **kwargs: Any) -> None:
        raise SimulatedCrash

    monkeypatch.setattr(control_plane_witness.os, "link", crash)
    with pytest.raises(SimulatedCrash):
        durable_append(world.workspace, attempt(2, records[0]), REPOSITORY, world.authority())
    monkeypatch.undo()

    assert world.anchor_files() == anchors_before
    assert verify(world.attempts(), world.authority()).status == "pending_anchor"
    assert anchor_pending(world.attempts(), world.authority()).status == "verified"


def test_a_crash_after_the_anchor_before_the_acknowledgement_is_already_applied(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = appended(world, 1)
    second = attempt(2, records[0])
    real = OfflineAnchorWitness.submit

    def acknowledged_never(self: OfflineAnchorWitness, anchor: ControlPlaneAnchor) -> Any:
        real(self, anchor)
        raise SimulatedCrash

    monkeypatch.setattr(OfflineAnchorWitness, "submit", acknowledged_never)
    with pytest.raises(SimulatedCrash):
        durable_append(world.workspace, second, REPOSITORY, world.authority())
    monkeypatch.undo()

    assert verify(world.attempts(), world.authority()).status == "verified"
    assert durable_append(world.workspace, second, REPOSITORY, world.authority()).status == (
        "already_applied"
    )


def test_more_than_one_unanchored_record_halts(world: World) -> None:
    records = appended(world, 1)
    second = attempt(2, records[0])
    publication_attempts.append(world.workspace, second)
    publication_attempts.append(world.workspace, attempt(3, second))

    assert verify(world.attempts(), world.authority()).status == "unanchored_tail"
    assert_refused(lambda: anchor_pending(world.attempts(), world.authority()), "unanchored_tail")


# --- the families the caller holds -------------------------------------------------------------


def _entries(count: int) -> tuple[bytes, ...]:
    out: list[bytes] = []
    for sequence in range(1, count + 1):
        kind = "reserve" if sequence % 2 else "commit"
        release = f"{sequence // 2 + 1:064x}"
        entry = LedgerEntry.model_validate(
            operation(kind, release).model_dump()
            | {
                "sequence": sequence,
                "previous_sha256": sha256(out[-1]).hexdigest() if out else None,
                "limits_sha256": "5" * 64,
            }
        )
        out.append(canonical_text(entry).encode())
    return tuple(out)


def _ledger_query(entries: tuple[bytes, ...]) -> AnchorQuery:
    return AnchorQuery(
        "budget_ledger", REPOSITORY, "synthetic-ledger", ledger_chain(entries, "synthetic-ledger")
    )


def test_a_budget_ledger_is_anchored_entry_by_entry_and_truncation_is_caught(world: World) -> None:
    entries = _entries(3)
    authority = world.authority(workspace=None)
    assert initialize(_ledger_query(()), authority).status == "verified"
    for count in range(1, 4):
        assert anchor_pending(_ledger_query(entries[:count]), authority).status == "verified"

    assert verify(_ledger_query(entries), authority).status == "verified"
    assert verify(_ledger_query(entries[:2]), authority).status == "local_rolled_back"
    edited = (*entries[:2], entries[2] + b" ")
    assert verify(_ledger_query(edited), authority).status == "local_invalid"
    foreign = AnchorQuery(
        "budget_ledger", REPOSITORY, "synthetic-ledger", ledger_chain(entries, "x")
    )
    assert verify(foreign, authority).status == "local_invalid"


def test_a_withheld_newest_stop_is_caught(world: World) -> None:
    resume = signed_by(control("resume", 1, None), world.owner, CONTROL_NAMESPACE)
    stop = signed_by(control("stop", 2, resume), world.owner, CONTROL_NAMESPACE)
    authority = world.authority(workspace=None)

    def query(history: tuple[Any, ...]) -> AnchorQuery:
        return AnchorQuery(
            "control_history", REPOSITORY, "policy-1", control_chain(history, "policy-1")
        )

    assert initialize(query(()), authority).status == "verified"
    assert anchor_pending(query((resume,)), authority).status == "verified"
    assert anchor_pending(query((resume, stop)), authority).status == "verified"

    assert verify(query((resume,)), authority).status == "local_rolled_back"
    assert verify(query((stop, resume)), authority).status == "local_invalid"


def test_a_replaced_quarantine_ledger_diverges(world: World) -> None:
    empty = canonical_text(QuarantineLedger()).encode()
    exclusion = ExclusionEntry(
        document_id="regjeringen-1",
        reason_code="owner_excluded",
        exception_sha256="6" * 64,
        excluded_at=CHECKED_AT,
    )
    other = canonical_text(QuarantineLedger(exclusions=(exclusion,))).encode()
    authority = world.authority(workspace=None)

    def query(ledger: bytes) -> AnchorQuery:
        return AnchorQuery("quarantine_ledger", REPOSITORY, "default", quarantine_chain(ledger))

    assert initialize(query(b""), authority).status == "verified"
    assert anchor_pending(query(empty), authority).status == "verified"

    assert verify(query(empty), authority).status == "verified"
    assert verify(query(other), authority).status == "diverged"
    assert verify(query(empty.replace(b"\n", b" ")), authority).status == "local_invalid"


def test_verified_state_names_the_anchored_head(world: World) -> None:
    records = appended(world, 2)

    state = verified_state(world.attempts(), world.authority())

    assert (state.family, state.scope, state.record_count) == ("publication_attempts", TID, 2)
    assert state.head_sha256 == digest_of(records[-1])
    assert state.anchor_sequence == 3


# --- real concurrent processes --------------------------------------------------------------------

_CHILD = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from lovspor.forarbeider.control_plane_anchor import AnchorAuthority, durable_append
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_trust import load_trust_root
from lovspor.forarbeider.publication_execution_models import AttemptRecord
from tests.unit.forarbeider_authorization_support import CHECKED_AT, Key
from tests.unit.forarbeider_execution_support import APP, NOW, KeySigner, make_rig
job = json.loads(sys.argv[1])
signer = KeySigner("test-witness", Key(Path(job["key"]), job["public"]))
witness = OfflineAnchorWitness(Path(job["store"]), signer)
root = load_trust_root(Path(job["root"]))
authority = AnchorAuthority(witness, root, CHECKED_AT, None, Path(job["workspace"]))
record = AttemptRecord.model_validate_json(job["record"])
go = Path(job["go"])
while not go.exists():
    time.sleep(0.005)
try:
    print(durable_append(Path(job["workspace"]), record, job["repository"], authority).status)
except ForarbeiderRefusedError as error:
    print("refused:" + error.detail)
"""


def _race(world: World, jobs: list[tuple[Path, AttemptRecord]]) -> list[str]:
    go = world.tmp / "go"
    children = []
    for workspace, record in jobs:
        job = {
            "store": str(world.store),
            "key": str(world.witness_key.path),
            "public": world.witness_key.public,
            "root": str(world.tmp / "root"),
            "workspace": str(workspace),
            "record": canonical_text(record),
            "repository": REPOSITORY,
            "go": str(go),
        }
        children.append(
            subprocess.Popen(  # this interpreter, a fixed script
                [sys.executable, "-c", _CHILD, json.dumps(job), str(REPO_ROOT)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=REPO_ROOT,
            )
        )
    go.write_bytes(b"")
    results = []
    for child in children:
        out, err = child.communicate(timeout=120)
        assert child.returncode == 0, err
        results.append(out.strip())
    return results


def test_concurrent_writers_on_one_workspace_keep_one_anchored_chain(world: World) -> None:
    records = appended(world, 1)
    jobs = [(world.workspace, attempt(2, records[0], state=s)) for s in ("refused", "denied")]

    results = _race(world, jobs)

    assert sorted(r.split(":")[0] for r in results).count("appended") >= 1
    assert all(r == "appended" or r.startswith("refused:") for r in results)
    verdict = verify(world.attempts(), world.authority())
    assert verdict.status == "verified" and verdict.latest is not None
    assert verdict.latest.record_count == 1 + results.count("appended")


def test_forked_workspaces_racing_for_one_anchor_leave_one_winner(world: World) -> None:
    records = appended(world, 1)
    fork = world.tmp / "fork"
    shutil.copytree(world.workspace, fork)
    fork.chmod(0o700)
    jobs = [
        (world.workspace, attempt(2, records[0], state="refused")),
        (fork, attempt(2, records[0], state="denied")),
    ]

    results = _race(world, jobs)

    winner = sorted(results)[0]
    loser = sorted(results)[1]
    assert winner == "appended"
    assert loser in {"refused:anchor_head_moved", "refused:anchor:local_rolled_back"}
    statuses = {
        verify(world.attempts(ws), world.authority(workspace=ws)).status
        for ws in (world.workspace, fork)
    }
    assert statuses in ({"verified", "diverged"}, {"verified", "local_rolled_back"})
    anchors = [
        WitnessedAnchor.model_validate_json(p.read_bytes()).anchor for p in world.anchor_files()
    ]
    assert [a.anchor_sequence for a in anchors] == [1, 2, 3]


# --- the AR4 records, written by AR4 itself ------------------------------------------------


def test_receipts_written_by_recovery_are_anchored_and_a_lost_one_is_caught(
    world: World, tmp_path: Path
) -> None:
    rig = make_rig(tmp_path / "rig", "A")
    authority = world.authority(workspace=rig.workspace)

    def receipts() -> AnchorQuery:
        chain = workspace_chain("recovery_receipts", rig.workspace, "workspace")
        return AnchorQuery("recovery_receipts", REPOSITORY, "workspace", chain)

    def attempts() -> AnchorQuery:
        tid = rig.transaction.transaction_id
        chain = workspace_chain("publication_attempts", rig.workspace, tid)
        return AnchorQuery("publication_attempts", REPOSITORY, tid, chain)

    assert initialize(receipts(), authority).status == "verified"
    github = SimulatedGitHub(head_oid=rig.base, identity=APP, now=NOW, fault="lost")
    simulation = Simulation(clock=lambda: NOW, transport=github)
    assert execute(rig.request(), simulation).status == "outcome_unknown"
    github.fault = None
    assert recover(rig.request(), simulation).classification == "published_authorized"

    assert verify(receipts(), authority).status == "pending_anchor"
    assert anchor_pending(receipts(), authority).status == "verified"
    assert verify(attempts(), authority).status == "unanchored"
    assert_refused(lambda: initialize(attempts(), authority), "anchor:unanchored")

    shutil.rmtree(rig.workspace / "receipts")
    assert verify(receipts(), authority).status == "local_rolled_back"
    assert len(github.calls) == 1


# --- added after the first sabotage pass: each isolates one guard ------------------------------


def test_a_reformatted_anchor_is_not_canonical(world: World) -> None:
    appended(world, 1)
    path = world.anchor_files()[-1]
    path.write_bytes(json.dumps(json.loads(path.read_bytes())).encode())

    verdict = verify(world.attempts(), world.authority())
    assert verdict.status == "anchor_invalid"
    assert verdict.reasons == ("anchor_not_canonical",)


def _witness_signed(world: World, anchor: ControlPlaneAnchor) -> bytes:
    signature = KeySigner(WITNESS, world.witness_key).sign(anchor_bytes(anchor), ANCHOR_NAMESPACE)
    return canonical_text(WitnessedAnchor(anchor=anchor, signature=signature.decode())).encode()


def test_a_witness_chain_whose_count_falls_is_invalid(world: World) -> None:
    appended(world, 2)
    latest = verify(world.attempts(), world.authority()).latest
    assert latest is not None
    lower = latest.model_copy(
        update={
            "record_count": 1,
            "last_sequence": 1,
            "anchor_sequence": latest.anchor_sequence + 1,
            "previous_anchor_sha256": anchor_digest(latest),
        }
    )
    folder = world.anchor_files()[-1].parent
    (folder / f"{lower.anchor_sequence:06d}.json").write_bytes(_witness_signed(world, lower))

    verdict = verify(world.attempts(), world.authority())

    assert verdict.status == "anchor_invalid"
    assert verdict.reasons == ("anchor_count_decreased",)


def test_the_witness_refuses_a_successor_that_lowers_the_count(world: World) -> None:
    appended(world, 2)
    latest = verify(world.attempts(), world.authority()).latest
    assert latest is not None
    lower = latest.model_copy(
        update={
            "record_count": 1,
            "last_sequence": 1,
            "anchor_sequence": latest.anchor_sequence + 1,
            "previous_anchor_sha256": anchor_digest(latest),
        }
    )
    before = tree(world.store)

    assert_refused(lambda: world.witness.submit(lower), "anchor_head_moved")
    assert tree(world.store) == before


def test_a_second_latch_is_never_reported_as_appended(world: World) -> None:
    assert initialize(world.latch(), world.authority()).status == "verified"
    first = StopLatch(reasons=("synthetic_stop",), set_at=CHECKED_AT, transaction_id=TID)
    durable_append(world.workspace, first, REPOSITORY, world.authority())
    second = StopLatch(reasons=("another_stop",), set_at=CHECKED_AT, transaction_id=TID)
    anchors = tree(world.store)

    assert_refused(
        lambda: durable_append(world.workspace, second, REPOSITORY, world.authority()),
        "record_not_appended",
    )
    assert tree(world.store) == anchors


def test_a_durable_append_refuses_while_the_workspace_is_held(world: World) -> None:
    records = appended(world, 1)
    anchors = tree(world.store)

    with publication_attempts.workspace_lock(world.workspace):
        error = assert_refused(
            lambda: durable_append(
                world.workspace, attempt(2, records[0]), REPOSITORY, world.authority()
            ),
            "workspace",
        )

    assert error.reason is RefusalReason.CORPUS_LOCKED
    assert tree(world.store) == anchors
    assert verify(world.attempts(), world.authority()).local_count == 1


def test_a_leftover_temporary_anchor_is_ignored(world: World) -> None:
    appended(world, 1)
    folder = world.anchor_files()[-1].parent
    (folder / ".tmp-leftover").write_bytes(b"half an anchor")

    assert verify(world.attempts(), world.authority()).status == "verified"


def test_a_ledger_entry_naming_the_wrong_predecessor_is_invalid(world: World) -> None:
    entries = _entries(2)
    second = LedgerEntry.model_validate_json(entries[1])
    relinked = canonical_text(second.model_copy(update={"previous_sha256": "7" * 64})).encode()

    chain = ledger_chain((entries[0], relinked), "synthetic-ledger")

    assert chain.reasons == ("ledger_chain_broken",)


def test_a_non_canonical_stop_latch_is_invalid(world: World) -> None:
    latch = StopLatch(reasons=("synthetic_stop",), set_at=CHECKED_AT, transaction_id=TID)
    publication_attempts.latch(world.workspace, latch.reasons, latch.set_at, TID)
    path = world.workspace / "stop-latch.json"
    path.write_bytes(path.read_bytes() + b" ")

    assert workspace_chain("stop_latch", world.workspace, "workspace").reasons == (
        "stop_latch_not_canonical",
    )
