"""AR5d-4: anchored, independently re-provable quarantine successors (#738).

Synthetic only: an invented quarantined document (the AR3 fixtures), owner
exceptions signed with throwaway keys, an offline witness in ``tmp_path``.
Every proof is checked by the real AR3 engine and the real OpenSSH signature
verification; nothing is mocked to succeed.

For the red run the succession imports were made optional (bound to ``None``
when the module is missing), so on code without it each test failed on its own.
"""

import json
import multiprocessing
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import publication_ledger, quarantine_succession
from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorQuery,
    anchor_pending,
    initialize,
    verify,
)
from lovspor.forarbeider.control_plane_anchor_models import anchor_digest
from lovspor.forarbeider.control_plane_chains import quarantine_chain, succession_chain
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import (
    ANCHOR_NAMESPACE,
    POLICY_NAMESPACE,
    QUARANTINE_NAMESPACE,
    TrustRoot,
)
from lovspor.forarbeider.publication_sandbox import SimulatedCrash
from lovspor.forarbeider.quarantine_succession import (
    QuarantineStore,
    SuccessionRequest,
    append_successor,
    policies_of,
    read_quarantine,
)
from lovspor.forarbeider.quarantine_succession_models import (
    QuarantineSuccession,
    SignedMaterial,
    document_sha256,
)
from lovspor.forarbeider.release_quarantine import (
    EMPTY_LEDGER,
    VerifiedException,
    merge,
    settle,
)
from lovspor.forarbeider.release_quarantine_models import (
    ExclusionEntry,
    QuarantineException,
    QuarantineLedger,
)
from lovspor.forarbeider.release_quarantine_run import ExceptionMaterial, verify_exceptions
from lovspor.forarbeider.version_models import canonical_text
from tests.unit.forarbeider_authorization_support import (
    BOT_NAMESPACES,
    Key,
    new_key,
    revocation,
    scope,
    signer_line,
)
from tests.unit.forarbeider_authorization_support import trust_root as make_root
from tests.unit.forarbeider_execution_support import KeySigner
from tests.unit.forarbeider_quarantine_support import (
    LATER,
    OWNER_QUARANTINE,
    exception,
    owner_signed,
    planned,
)
from tests.unit.forarbeider_release_control_support import (
    BOT,
    CHECKED_AT,
    OWNER,
    Keys,
    make_keys,
    policy,
    policy_bytes,
    signed_by,
)
from tests.unit.test_forarbeider_release_quarantine import findings_of, quarantined_ledger

REPOSITORY = "synthetic/forarbeider"
LEDGER_ID = "quarantine-main"
WITNESS = "test-witness"
EVEN_LATER = LATER + (LATER - CHECKED_AT)


@dataclass
class Rig:
    tmp: Path
    keys: Keys
    witness_key: Key
    root: TrustRoot
    witness: OfflineAnchorWitness
    store: QuarantineStore

    def authority(self, **changes: Any) -> AnchorAuthority:
        fields: dict[str, Any] = {
            "witness": self.witness,
            "root": self.root,
            "checked_at": EVEN_LATER,
            "workspace": None,
        }
        return AnchorAuthority(**(fields | changes))


def root_of(directory: Path, keys: Keys, witness: Key, revoked: tuple[Any, ...] = ()) -> TrustRoot:
    lines = [
        signer_line(OWNER, keys.owner, OWNER_QUARANTINE),
        signer_line(BOT, keys.bot, BOT_NAMESPACES),
        signer_line(WITNESS, witness, ANCHOR_NAMESPACE),
    ]
    return make_root(directory, lines, revoked)


@pytest.fixture(scope="module")
def keys(tmp_path_factory: pytest.TempPathFactory) -> Keys:
    return make_keys(tmp_path_factory.mktemp("keys"))


@pytest.fixture(scope="module")
def witness_key(tmp_path_factory: pytest.TempPathFactory) -> Key:
    return new_key(tmp_path_factory.mktemp("witness-key"), "witness")


@pytest.fixture
def rig(tmp_path: Path, keys: Keys, witness_key: Key) -> Rig:
    store_dir = tmp_path / "witness"
    store_dir.mkdir(mode=0o700)
    witness = OfflineAnchorWitness(store_dir, KeySigner(WITNESS, witness_key))
    root = root_of(tmp_path / "root", keys, witness_key)
    store = QuarantineStore(tmp_path / "quarantine", LEDGER_ID)
    return Rig(tmp_path, keys, witness_key, root, witness, store)


# --- the ledgers: a quarantine, then the owner's clearance of it ---------------------------------


def first_ledger() -> QuarantineLedger:
    return quarantined_ledger()[2]


def clearance(rig: Rig, **changes: Any) -> SignedRecord:
    (record,) = first_ledger().records
    return owner_signed(rig.keys, exception(record, **changes))


def verified(rig: Rig, *offered: SignedRecord) -> tuple[VerifiedException, ...]:
    material = ExceptionMaterial(records=offered)
    found = verify_exceptions(material, policy_bytes(), LATER, rig.root)
    assert found.halts == ()
    return found.verified


def moved_ledger() -> QuarantineLedger:
    """A legitimate successor that needs no exception: the finding seen on new evidence."""
    store, rows, ledger = quarantined_ledger()
    (finding,) = findings_of(planned(store, rows, (1, 2)), rows, store).local
    return merge(ledger, [finding.model_copy(update={"evidence_sha256s": ("b" * 64,)})], LATER)


def cleared_ledger(rig: Rig) -> QuarantineLedger:
    return settle(first_ledger(), verified(rig, clearance(rig)), LATER)


def request(
    successor: QuarantineLedger,
    expected: QuarantineLedger,
    offered: tuple[SignedRecord, ...] = (),
    **changes: Any,
) -> SuccessionRequest:
    fields: dict[str, Any] = {
        "repository": REPOSITORY,
        "expected_predecessor_sha256": document_sha256(expected),
        "successor": successor,
        "exceptions": ExceptionMaterial(records=offered),
        "policy": policy_bytes() if offered else None,
        "at": CHECKED_AT if successor == first_ledger() else LATER,
        "policies": (policy_bytes(),),
    }
    return SuccessionRequest(**(fields | changes))


def append(rig: Rig, wanted: SuccessionRequest, **authority: Any) -> Any:
    return append_successor(rig.store, wanted, rig.authority(**authority))


def read(rig: Rig, root: TrustRoot | None = None) -> Any:
    authority = rig.authority() if root is None else rig.authority(root=root)
    return read_quarantine(rig.store, REPOSITORY, authority, policies_of(policy_bytes()))


def refused(action: Any) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    return raised.value


def quarantined(rig: Rig) -> None:
    """The first successor: the empty ledger, then the quarantine of document 2."""
    assert append(rig, request(first_ledger(), EMPTY_LEDGER)).status == "appended"


def stored(rig: Rig) -> tuple[bytes, ...]:
    return publication_ledger.entries(publication_ledger.BudgetLedger(*rig.store))


def legacy_query(document: bytes) -> AnchorQuery:
    return AnchorQuery("quarantine_ledger", REPOSITORY, LEDGER_ID, quarantine_chain(document))


def bytes_of(ledger: QuarantineLedger) -> bytes:
    return canonical_text(ledger).encode()


# --- the current refusal, kept for the AR5a document family ---------------------------------------


def test_an_anchored_document_alone_never_accepts_a_legitimate_successor(rig: Rig) -> None:
    """Reproduction and compatibility: under ``quarantine_ledger`` (AR5a) a legitimate
    successor is ``diverged`` and cannot be anchored; that stays so."""
    authority = rig.authority()
    initialize(legacy_query(b""), authority)
    anchor_pending(legacy_query(bytes_of(first_ledger())), authority)

    assert verify(legacy_query(bytes_of(cleared_ledger(rig))), authority).status == "diverged"
    error = refused(lambda: anchor_pending(legacy_query(bytes_of(cleared_ledger(rig))), authority))
    assert error.detail == "anchor:diverged"


# --- valid successors -----------------------------------------------------------------------------


def test_a_quarantine_and_its_owner_clearance_are_proved_successors(rig: Rig) -> None:
    quarantined(rig)
    outcome = append(rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)))

    assert outcome.status == "appended"
    found = read(rig)
    assert found.ledger == cleared_ledger(rig)
    assert found.sequence == 2
    (record,) = found.ledger.records
    assert record.state == "cleared"


def test_a_successor_that_only_adds_evidence_needs_no_exception(rig: Rig) -> None:
    quarantined(rig)

    assert append(rig, request(moved_ledger(), first_ledger())).status == "appended"
    (record,) = read(rig).ledger.records
    assert [e.event for e in record.history] == ["observed", "evidence_changed"]
    assert record.state == "active"


def test_an_empty_chain_reads_as_the_empty_ledger_only_without_a_document(rig: Rig) -> None:
    initialize(legacy_query(b""), rig.authority())
    assert append(rig, request(EMPTY_LEDGER, EMPTY_LEDGER, at=CHECKED_AT)).status == "appended"
    assert read(rig).ledger == EMPTY_LEDGER


# --- what never succeeds --------------------------------------------------------------------------


def test_a_successor_that_drops_a_finding_is_refused(rig: Rig) -> None:
    quarantined(rig)
    before = stored(rig)

    error = refused(lambda: append(rig, request(EMPTY_LEDGER, first_ledger())))

    assert "quarantine_record_dropped" in error.detail, error.detail
    assert stored(rig) == before


def test_a_successor_that_rewrites_a_finding_is_refused(rig: Rig) -> None:
    quarantined(rig)
    (record,) = first_ledger().records
    event = record.history[0].model_copy(update={"evidence_sha256s": ("b" * 64,)})
    edited = record.model_copy(update={"history": (event,), "evidence_sha256s": ("b" * 64,)})
    rewritten = first_ledger().model_copy(update={"records": (edited,)})

    error = refused(lambda: append(rig, request(rewritten, first_ledger(), at=LATER)))

    assert "quarantine_history_rewritten" in error.detail, error.detail


def test_a_clearance_without_its_exception_is_refused(rig: Rig) -> None:
    """A stored ``cleared`` is never trusted: the exception must be carried and verify."""
    quarantined(rig)

    error = refused(lambda: append(rig, request(cleared_ledger(rig), first_ledger())))

    assert "unjustified_clearance" in error.detail, error.detail
    assert "not_settled_under_the_verified_exceptions" in error.detail


@pytest.mark.parametrize("fault", ["bot", "stranger", "tampered", "policy_namespace"])
def test_a_clearance_the_owner_did_not_sign_is_refused(rig: Rig, fault: str) -> None:
    quarantined(rig)
    (record,) = first_ledger().records
    good = clearance(rig)
    offered = {
        "bot": signed_by(exception(record, signer_principal=BOT), rig.keys.bot, "x"),
        "stranger": signed_by(exception(record), rig.keys.other, QUARANTINE_NAMESPACE),
        "tampered": SignedRecord(
            good.record.replace(b"owner_reviewed", b"owner_forged"), good.signature
        ),
        "policy_namespace": signed_by(exception(record), rig.keys.owner, POLICY_NAMESPACE),
    }[fault]

    error = refused(lambda: append(rig, request(cleared_ledger(rig), first_ledger(), (offered,))))

    assert error.detail.startswith("quarantine_succession:"), error.detail
    assert "exception:quarantine_exception_untrusted" in error.detail, error.detail


def test_an_expired_clearance_is_refused(rig: Rig) -> None:
    quarantined(rig)
    expired = clearance(rig, valid_until=CHECKED_AT + (LATER - CHECKED_AT) / 2)
    forged = settle(first_ledger(), (VerifiedException(_record(expired), _sha(expired)),), LATER)

    error = refused(lambda: append(rig, request(forged, first_ledger(), (expired,))))

    assert "unjustified_clearance" in error.detail, error.detail


def test_a_revoked_clearance_is_refused(rig: Rig) -> None:
    quarantined(rig)
    withdrawn = signed_by(revocation("synthetic-clearance-1"), rig.keys.owner, POLICY_NAMESPACE)
    material = ExceptionMaterial(records=(clearance(rig),), revocations=(withdrawn,))
    wanted = request(cleared_ledger(rig), first_ledger(), (clearance(rig),), exceptions=material)

    error = refused(lambda: append(rig, wanted))

    assert "unjustified_clearance" in error.detail, error.detail


def test_a_clearance_whose_key_the_owner_revoked_since_fails_every_later_read(
    rig: Rig, tmp_path: Path
) -> None:
    """Current owner authority: a reader's trust root that revokes the key refuses the history."""
    quarantined(rig)
    append(rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)))
    revoked = root_of(tmp_path / "revoked", rig.keys, rig.witness_key, revoked=(rig.keys.owner,))

    error = refused(lambda: read(rig, revoked))

    assert "entry_2" in error.detail and "quarantine_exception_untrusted" in error.detail


@pytest.mark.parametrize("field", ["policy_version", "version_id", "evidence_sha256s"])
def test_a_clearance_for_another_policy_version_or_evidence_is_refused(
    rig: Rig, field: str
) -> None:
    quarantined(rig)
    change: dict[str, Any] = {
        "policy_version": {"scope": scope(policy(), policy_version=7)},
        "version_id": {"version_id": "c" * 64},
        "evidence_sha256s": {"evidence_sha256s": ("b" * 64,)},
    }[field]
    other = clearance(rig, **change)
    forged = settle(
        first_ledger(), (VerifiedException(_record(clearance(rig)), _sha(other)),), LATER
    )

    error = refused(lambda: append(rig, request(forged, first_ledger(), (other,))))

    assert "unjustified_clearance" in error.detail, error.detail


def test_an_exclusion_without_its_exception_is_refused(rig: Rig) -> None:
    quarantined(rig)
    entry = ExclusionEntry(
        document_id="regjeringen-2",
        reason_code="owner_excluded",
        exception_sha256="6" * 64,
        excluded_at=LATER,
    )
    excluded = first_ledger().model_copy(update={"exclusions": (entry,)})

    error = refused(lambda: append(rig, request(excluded, first_ledger())))

    assert "unjustified_exclusion" in error.detail, error.detail


def test_a_successor_judged_before_its_predecessor_is_refused(rig: Rig) -> None:
    quarantined(rig)
    early = CHECKED_AT - (LATER - CHECKED_AT)

    error = refused(lambda: append(rig, request(first_ledger(), first_ledger(), at=early)))

    assert "clock_regressed" in error.detail, error.detail


def test_a_successor_of_a_predecessor_that_is_not_the_head_is_refused(rig: Rig) -> None:
    quarantined(rig)
    before = stored(rig)

    error = refused(
        lambda: append(rig, request(cleared_ledger(rig), EMPTY_LEDGER, (clearance(rig),)))
    )

    assert error.detail == "quarantine_succession:predecessor_not_the_anchored_head"
    assert stored(rig) == before


def test_a_second_successor_of_one_predecessor_is_refused(rig: Rig) -> None:
    quarantined(rig)
    append(rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)))
    other = moved_ledger()

    error = refused(lambda: append(rig, request(other, first_ledger())))

    assert error.detail == "quarantine_succession:predecessor_not_the_anchored_head"
    assert read(rig).ledger == cleared_ledger(rig)


# --- the anchored record: rollback, divergence, witness -------------------------------------------


def test_a_rolled_back_local_chain_is_refused(rig: Rig) -> None:
    quarantined(rig)
    append(rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)))
    (rig.store.root / LEDGER_ID / "000002.json").unlink()

    assert refused(lambda: read(rig)).detail == "quarantine_succession:anchor:local_rolled_back"


def test_a_missing_predecessor_entry_is_refused(rig: Rig) -> None:
    quarantined(rig)
    append(rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)))
    (rig.store.root / LEDGER_ID / "000001.json").unlink()

    error = refused(lambda: read(rig))

    assert error.detail == "quarantine_succession:store:ledger", error.detail


def test_a_substituted_entry_is_refused(rig: Rig) -> None:
    quarantined(rig)
    path = rig.store.root / LEDGER_ID / "000001.json"
    path.write_bytes(path.read_bytes().replace(b"quarantine-main", b"quarantine-copy"))

    assert refused(lambda: read(rig)).detail == "quarantine_succession:anchor:local_invalid"


def test_a_rolled_back_witness_is_refused(rig: Rig) -> None:
    quarantined(rig)
    snapshot = rig.tmp / "witness-snapshot"
    shutil.copytree(rig.tmp / "witness", snapshot)
    append(rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)))
    shutil.rmtree(rig.tmp / "witness")
    shutil.copytree(snapshot, rig.tmp / "witness")

    assert refused(lambda: read(rig)).detail == "quarantine_succession:anchor:pending_anchor"


def test_an_unavailable_witness_refuses_and_writes_nothing(rig: Rig) -> None:
    quarantined(rig)
    before = stored(rig)
    shutil.move(str(rig.tmp / "witness"), str(rig.tmp / "witness-away"))

    error = refused(
        lambda: append(rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)))
    )

    assert error.detail == "quarantine_succession:anchor:store_unavailable"
    assert stored(rig) == before
    assert refused(lambda: read(rig)).detail == "quarantine_succession:anchor:store_unavailable"


def test_a_copied_store_cannot_add_a_competing_successor(rig: Rig) -> None:
    """One successor per anchored predecessor across stores: the copy is rolled back."""
    quarantined(rig)
    copy = QuarantineStore(rig.tmp / "quarantine-copy", LEDGER_ID)
    shutil.copytree(rig.store.root, copy.root)
    append(rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)))
    other = moved_ledger()

    error = refused(lambda: append_successor(copy, request(other, first_ledger()), rig.authority()))

    assert error.detail == "quarantine_succession:anchor:local_rolled_back"


# --- crashes and retries --------------------------------------------------------------------------


def test_a_crash_before_the_append_changes_nothing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    quarantined(rig)
    before = stored(rig)
    wanted = request(cleared_ledger(rig), first_ledger(), (clearance(rig),))
    monkeypatch.setattr(publication_ledger, "append_entry", _crash)
    with pytest.raises(SimulatedCrash):
        append(rig, wanted)
    monkeypatch.undo()

    assert stored(rig) == before
    assert read(rig).ledger == first_ledger()
    assert append(rig, wanted).status == "appended"


def test_a_crash_before_the_anchor_leaves_an_untrusted_pending_successor_completed_by_retry(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    quarantined(rig)
    wanted = request(cleared_ledger(rig), first_ledger(), (clearance(rig),))
    monkeypatch.setattr(quarantine_succession, "anchor_pending", _crash)
    with pytest.raises(SimulatedCrash):
        append(rig, wanted)
    monkeypatch.undo()

    assert refused(lambda: read(rig)).detail == "quarantine_succession:anchor:pending_anchor"
    other = request(moved_ledger(), first_ledger())
    error = refused(lambda: append(rig, other))
    assert error.detail == "quarantine_succession:pending_entry_is_another_successor"
    later = wanted._replace(at=EVEN_LATER)
    assert refused(lambda: append(rig, later)).detail == (
        "quarantine_succession:pending_entry_is_another_successor"
    )

    assert append(rig, wanted).status == "completed"
    assert append(rig, wanted).status == "already_applied"
    assert read(rig).ledger == cleared_ledger(rig)
    assert len(stored(rig)) == 2


def _crash(*_: Any, **__: Any) -> Any:
    raise SimulatedCrash


# --- concurrency in real processes, and an independent reader -------------------------------------


def _writer(job: tuple[Rig, SuccessionRequest, float]) -> str:
    rig, wanted, start = job
    while time.time() < start:
        time.sleep(0.001)
    try:
        return str(append(rig, wanted).status)
    except ForarbeiderRefusedError as error:
        return f"refused:{error.detail}"


def test_competing_successors_in_two_processes_never_both_succeed(rig: Rig) -> None:
    quarantined(rig)
    start = time.time() + 1.5
    jobs = [
        (rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)), start),
        (rig, request(moved_ledger(), first_ledger()), start),
    ]
    with multiprocessing.get_context("fork").Pool(2) as pool:
        results = pool.map(_writer, jobs)

    assert results.count("appended") == 1, results
    assert len(stored(rig)) == 2
    assert read(rig).sequence == 2


_READER = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from lovspor.forarbeider.control_plane_anchor import AnchorAuthority
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.policy_trust import load_trust_root
from lovspor.forarbeider.quarantine_succession import QuarantineStore, policies_of, read_quarantine
from lovspor.forarbeider.release_quarantine import ledger_sha256
from tests.unit.forarbeider_authorization_support import Key
from tests.unit.forarbeider_execution_support import KeySigner
from datetime import datetime
job = json.loads(sys.argv[1])
key = Key(Path(job["key"]), job["public"])
witness = OfflineAnchorWitness(Path(job["store"]), KeySigner("test-witness", key))
root = load_trust_root(Path(job["root"]))
authority = AnchorAuthority(witness, root, datetime.fromisoformat(job["at"]))
store = QuarantineStore(Path(job["quarantine"]), job["ledger_id"])
policies = policies_of(Path(job["policy"]).read_bytes())
found = read_quarantine(store, job["repository"], authority, policies)
print(json.dumps({"sha": ledger_sha256(found.ledger), "sequence": found.sequence}))
"""


def test_a_fresh_process_proves_the_same_head_from_disk_alone(rig: Rig) -> None:
    quarantined(rig)
    append(rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)))
    policy_file = rig.tmp / "policy.json"
    policy_file.write_bytes(policy_bytes())
    job = {
        "store": str(rig.tmp / "witness"),
        "key": str(rig.witness_key.path),
        "public": rig.witness_key.public,
        "root": str(rig.tmp / "root"),
        "at": EVEN_LATER.isoformat(),
        "quarantine": str(rig.store.root),
        "ledger_id": LEDGER_ID,
        "repository": REPOSITORY,
        "policy": str(policy_file),
    }
    repo = Path(__file__).resolve().parents[2]
    done = subprocess.run(
        [sys.executable, "-c", _READER, json.dumps(job), str(repo)],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )

    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found == {"sha": document_sha256(cleared_ledger(rig)), "sequence": 2}


# --- the AR5a document: continued, never discarded ------------------------------------------------


def test_a_successor_continues_a_document_anchored_under_the_old_family(rig: Rig) -> None:
    authority = rig.authority()
    document = bytes_of(first_ledger())
    initialize(legacy_query(b""), authority)
    anchor_pending(legacy_query(document), authority)
    wanted = request(
        cleared_ledger(rig), first_ledger(), (clearance(rig),), predecessor_document=document
    )

    assert append(rig, wanted).status == "appended"
    assert read(rig).ledger == cleared_ledger(rig)
    # the old family is unchanged and still refuses the changed document by itself
    assert verify(legacy_query(document), authority).status == "verified"
    assert verify(legacy_query(bytes_of(cleared_ledger(rig))), authority).status == "diverged"


def test_an_empty_genesis_never_discards_an_anchored_document(rig: Rig) -> None:
    authority = rig.authority()
    initialize(legacy_query(b""), authority)
    anchor_pending(legacy_query(bytes_of(first_ledger())), authority)

    error = refused(lambda: append(rig, request(EMPTY_LEDGER, EMPTY_LEDGER, at=CHECKED_AT)))

    assert error.detail == "quarantine_succession:genesis:quarantine_ledger:local_rolled_back"
    assert stored(rig) == ()


def test_a_continuation_of_a_document_the_old_family_never_anchored_is_refused(rig: Rig) -> None:
    initialize(legacy_query(b""), rig.authority())
    document = bytes_of(first_ledger())
    wanted = request(
        cleared_ledger(rig), first_ledger(), (clearance(rig),), predecessor_document=document
    )

    error = refused(lambda: append(rig, wanted))

    assert error.detail == "quarantine_succession:genesis:quarantine_ledger:pending_anchor"


def test_a_document_anchored_after_the_chain_began_breaks_the_chain(rig: Rig) -> None:
    """The old family moving after the succession began is never silently adopted."""
    initialize(legacy_query(b""), rig.authority())
    quarantined(rig)
    anchor_pending(legacy_query(bytes_of(first_ledger())), rig.authority())

    error = refused(lambda: read(rig))

    assert error.detail == "quarantine_succession:genesis:quarantine_ledger:local_rolled_back"


def test_an_unchained_successor_anchor_of_the_old_family_stays_invalid(rig: Rig) -> None:
    """Preservation: a signed anchor over another document under the old family is refused."""
    authority = rig.authority()
    initialize(legacy_query(b""), authority)
    first = anchor_pending(legacy_query(bytes_of(first_ledger())), authority).latest
    assert first is not None
    replacement = first.model_copy(
        update={
            "head_sha256": sha256(bytes_of(cleared_ledger(rig))).hexdigest(),
            "anchor_sequence": first.anchor_sequence + 1,
            "previous_anchor_sha256": anchor_digest(first),
        }
    )
    rig.witness.submit(replacement)

    verdict = verify(legacy_query(bytes_of(cleared_ledger(rig))), authority)
    assert verdict.reasons == ("quarantine_successor_unverified",)


# --- helpers for forged ledgers -------------------------------------------------------------------


def _record(offered: SignedRecord) -> Any:
    return QuarantineException.model_validate_json(offered.record)


def _sha(offered: SignedRecord) -> str:
    return sha256(offered.record).hexdigest()


# --- a signed anchor over an unproved entry is never trusted --------------------------------------


def forge(rig: Rig, **changes: Any) -> None:
    """Write and anchor a second entry by hand, bypassing the writer's proof: the witness
    signs whatever is pending, so only the reader's replay can refuse it."""
    held = stored(rig)
    fields: dict[str, Any] = {
        "repository": REPOSITORY,
        "ledger_id": LEDGER_ID,
        "sequence": 2,
        "previous_succession_sha256": sha256(held[-1]).hexdigest(),
        "basis": "succession",
        "predecessor_sha256": document_sha256(first_ledger()),
        "predecessor": None,
        "successor_sha256": document_sha256(cleared_ledger(rig)),
        "successor": cleared_ledger(rig),
        "policy_sha256": sha256(policy_bytes()).hexdigest(),
        "exceptions": (_material(clearance(rig)),),
        "revocations": (),
        "recorded_at": LATER,
    }
    entry = QuarantineSuccession.model_construct(**(fields | changes))
    raw = canonical_text(entry).encode()
    publication_ledger.append_entry(publication_ledger.BudgetLedger(*rig.store), raw)
    chain = succession_chain((*held, raw), REPOSITORY, LEDGER_ID)
    query = AnchorQuery("quarantine_succession", REPOSITORY, LEDGER_ID, chain)
    if not chain.reasons:
        assert anchor_pending(query, rig.authority()).status == "verified"


def _material(offered: SignedRecord) -> SignedMaterial:
    assert offered.signature is not None
    return SignedMaterial(record=offered.record.decode(), signature=offered.signature.decode())


def test_the_hand_built_entry_is_the_writers_entry(rig: Rig) -> None:
    """Control: the forged entry with no change is a valid successor, so each refusal below
    is caused by its one change."""
    quarantined(rig)
    forge(rig)
    assert read(rig).ledger == cleared_ledger(rig)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"predecessor_sha256": "e" * 64}, "predecessor_mismatch"),
        ({"recorded_at": CHECKED_AT}, "event_after_the_judgement"),
    ],
)
def test_an_anchored_entry_whose_proof_fails_is_never_trusted(
    rig: Rig, change: dict[str, Any], reason: str
) -> None:
    quarantined(rig)
    forge(rig, **change)

    error = refused(lambda: read(rig))

    assert error.detail.startswith("quarantine_succession:entry_2:"), error.detail
    assert reason in error.detail, error.detail


def test_a_clearance_naming_an_exception_it_does_not_carry_is_never_trusted(rig: Rig) -> None:
    """Another valid clearance is carried, but the event names a different one."""
    quarantined(rig)
    named = VerifiedException(_record(clearance(rig)), "a" * 64)
    claimed = settle(first_ledger(), (named,), LATER)
    forge(rig, successor=claimed, successor_sha256=document_sha256(claimed))

    error = refused(lambda: read(rig))

    assert "unjustified_clearance" in error.detail, error.detail


def test_a_reader_without_the_named_policy_refuses(rig: Rig) -> None:
    quarantined(rig)
    append(rig, request(cleared_ledger(rig), first_ledger(), (clearance(rig),)))

    error = refused(lambda: read_quarantine(rig.store, REPOSITORY, rig.authority(), {}))

    assert error.detail == "quarantine_succession:entry_2:policy_unavailable", error.detail


@pytest.mark.parametrize(
    "change",
    [
        {"previous_succession_sha256": "e" * 64},
        {"successor_sha256": "e" * 64},
    ],
)
def test_an_entry_that_breaks_its_own_bindings_is_never_anchored_or_trusted(
    rig: Rig, change: dict[str, Any]
) -> None:
    quarantined(rig)
    forge(rig, **change)

    assert refused(lambda: read(rig)).detail == "quarantine_succession:anchor:local_invalid"


def test_a_continued_document_whose_digest_is_wrong_is_never_trusted(rig: Rig) -> None:
    authority = rig.authority()
    initialize(legacy_query(b""), authority)
    anchor_pending(legacy_query(bytes_of(first_ledger())), authority)
    entry = QuarantineSuccession.model_construct(
        repository=REPOSITORY,
        ledger_id=LEDGER_ID,
        sequence=1,
        previous_succession_sha256=None,
        basis="anchored_document",
        predecessor_sha256="e" * 64,
        predecessor=first_ledger(),
        successor_sha256=document_sha256(first_ledger()),
        successor=first_ledger(),
        policy_sha256=None,
        exceptions=(),
        revocations=(),
        recorded_at=LATER,
    )
    empty = succession_chain((), REPOSITORY, LEDGER_ID)
    initialize(AnchorQuery("quarantine_succession", REPOSITORY, LEDGER_ID, empty), authority)
    rig.store.root.mkdir(mode=0o700)
    raw = canonical_text(entry).encode()
    publication_ledger.append_entry(publication_ledger.BudgetLedger(*rig.store), raw)

    assert refused(lambda: read(rig)).detail == "quarantine_succession:anchor:local_invalid"
