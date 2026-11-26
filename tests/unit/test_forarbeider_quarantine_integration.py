"""AR5 integration: the publishing pipeline reads quarantine only from the anchored chain (#738).

Two layers, both on the real engines:

- the planner (:func:`plan_witnessed`): the AR3 run and the AR2 plan made on
  the AR5d-4 chain read through ``read_quarantine``, with owner exceptions
  signed by throwaway keys and an offline witness in ``tmp_path``. As in the
  AR3 run tests, only FC8b's signed-lineage preparation is replaced by
  synthetic rows (``synthetic``); attribution, exception verification, merge,
  settle, sealing and planning are real;
- the send (``execute`` and ``publish_authorized``, paths A and B): the
  execution rig with its quarantine chain anchored on the ledger's witness.

Nothing is mocked to approve. Synthetic only: invented documents, no text.
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import (
    publication_execute,
    publication_ledger,
    quarantine_succession,
    release_plan,
    release_quarantine_run,
)
from lovspor.forarbeider.control_plane_anchor import anchor_pending, initialize
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_trust import ANCHOR_NAMESPACE, QUARANTINE_NAMESPACE, TrustRoot
from lovspor.forarbeider.publication_authorize import (
    AdapterFacts,
    AuthorizationContext,
    authorize,
)
from lovspor.forarbeider.publication_execute import (
    Production,
    Simulation,
    execute,
    next_attempt_id,
    publish_authorized,
)
from lovspor.forarbeider.publication_ledger import BudgetLedger
from lovspor.forarbeider.publication_prepare import PreparationRequest
from lovspor.forarbeider.publication_quarantine import (
    QuarantineClaim,
    WitnessedPlan,
    WitnessedSource,
    Witnessing,
    plan_witnessed,
    prepare_witnessed,
    quarantine_reasons,
    witnessed_quarantine,
)
from lovspor.forarbeider.publication_remote_recovery import recover
from lovspor.forarbeider.publication_sandbox import SimulatedCrash, SimulatedGitHub
from lovspor.forarbeider.quarantine_succession import (
    QuarantineStore,
    SuccessionRequest,
    append_successor,
    initialize_quarantine,
    policies_of,
    read_quarantine,
)
from lovspor.forarbeider.quarantine_succession_models import document_sha256
from lovspor.forarbeider.release_plan import PlanRequest, Prepared
from lovspor.forarbeider.release_quarantine import EMPTY_LEDGER, merge
from lovspor.forarbeider.release_quarantine_models import (
    QuarantineFinding,
    QuarantineLedger,
    QuarantineScope,
)
from lovspor.forarbeider.release_quarantine_run import ExceptionMaterial
from lovspor.forarbeider.version_release_models import PolicyReference, digest_of
from tests.unit.forarbeider_authorization_support import (
    BOT_NAMESPACES,
    KEY_AFTER,
    Key,
    new_key,
    signer_line,
)
from tests.unit.forarbeider_authorization_support import trust_root as make_root
from tests.unit.forarbeider_execution_support import (
    APP,
    NOW,
    KeySigner,
    Rig,
    _root,
    make_rig,
)
from tests.unit.forarbeider_publication_support import document
from tests.unit.forarbeider_quarantine_support import (
    LATER,
    OWNER_QUARANTINE,
    exception,
    owner_signed,
)
from tests.unit.forarbeider_release_control_support import (
    BOT,
    CHECKED_AT,
    OWNER,
    Keys,
    make_keys,
    policy_bytes,
    signed_by,
)
from tests.unit.forarbeider_release_plan_support import Store, context, row
from tests.unit.test_forarbeider_quarantine_succession import (
    EVEN_LATER,
    LEDGER_ID,
    REPOSITORY,
    WITNESS,
    first_ledger,
    legacy_query,
    root_of,
)
from tests.unit.test_forarbeider_quarantine_succession import Rig as ChainRig
from tests.unit.test_forarbeider_release_quarantine import render_failure, request_of

#: The execution rig's quarantine chain: beside its budget ledger, one per ledger id.
CHAIN_ID = "synthetic-quarantine"


@pytest.fixture(scope="module")
def chain_keys(tmp_path_factory: pytest.TempPathFactory) -> Keys:
    return make_keys(tmp_path_factory.mktemp("keys"))


@pytest.fixture(scope="module")
def chain_witness(tmp_path_factory: pytest.TempPathFactory) -> Key:
    return new_key(tmp_path_factory.mktemp("witness-key"), "witness")


@pytest.fixture
def chain(tmp_path: Path, chain_keys: Keys, chain_witness: Key) -> ChainRig:
    """An AR5d-4 quarantine chain on an offline witness, as in the succession tests."""
    store_dir = tmp_path / "witness"
    store_dir.mkdir(mode=0o700)
    witness = OfflineAnchorWitness(store_dir, KeySigner(WITNESS, chain_witness))
    root = root_of(tmp_path / "root", chain_keys, chain_witness)
    store = QuarantineStore(tmp_path / "quarantine", LEDGER_ID)
    return ChainRig(tmp_path, chain_keys, chain_witness, root, witness, store)


@pytest.fixture
def planner(monkeypatch: pytest.MonkeyPatch) -> tuple[Store, list[Any]]:
    """Synthetic FC8b rows instead of signed lineages, as in the AR3 run tests."""
    store = Store()
    rows = [row(store, n) for n in (1, 2, 3)]

    def prepare(request: PlanRequest, root: TrustRoot) -> Prepared:
        del request, root
        return Prepared(context((1, 2, 3), rows), tuple(rows))

    monkeypatch.setattr(release_plan, "prepare_plan", prepare)
    monkeypatch.setattr(release_quarantine_run, "prepare_plan", prepare)
    return store, rows


def refused(action: Any) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as raised:
        action()
    return raised.value


# --- the planner -----------------------------------------------------------------------------


def source_of(chain: ChainRig, *policies: bytes) -> WitnessedSource:
    return WitnessedSource(chain.store, policies or (policy_bytes(),))


def bootstrapped(chain: ChainRig) -> None:
    verdict = initialize_quarantine(chain.store, REPOSITORY, chain.authority())
    assert verdict.status == "verified"


def plan(chain: ChainRig, store: Any, **changes: Any) -> WitnessedPlan:
    authority = chain.authority(**changes.pop("authority", {}))
    source = changes.pop("source", None) or source_of(chain)
    return plan_witnessed(request_of(store, **changes), source, REPOSITORY, authority)


def anchored(chain: ChainRig, result: WitnessedPlan, at: datetime) -> None:
    """The owner anchors the successor the planner asked for (AR5d-4 proves it again)."""
    assert result.status == "successor_required", result.status
    assert result.outcome.ledger is not None
    wanted = SuccessionRequest(
        repository=REPOSITORY,
        expected_predecessor_sha256=result.anchored.ledger_sha256,
        successor=result.outcome.ledger,
        exceptions=result.material,
        policy=policy_bytes(),
        at=at,
        policies=(policy_bytes(),),
    )
    assert append_successor(chain.store, wanted, chain.authority()).status == "appended"


def quarantined_two(chain: ChainRig, store: Any, rows: Any) -> None:
    """The anchored chain: empty, then document 2 quarantined (a render failure)."""
    bootstrapped(chain)
    anchored(chain, plan(chain, store, reported=(render_failure(rows, 2),)), CHECKED_AT)


def head_of(chain: ChainRig) -> Any:
    return read_quarantine(chain.store, REPOSITORY, chain.authority(), policies_of(policy_bytes()))


def clearance_of(chain: ChainRig, **changes: Any) -> Any:
    found = head_of(chain)
    (record,) = found.ledger.records
    return owner_signed(chain.keys, exception(record, **changes))


def cleared_two(chain: ChainRig, store: Any, rows: Any, **changes: Any) -> None:
    quarantined_two(chain, store, rows)
    offered = ExceptionMaterial(records=(clearance_of(chain, **changes),))
    anchored(chain, plan(chain, store, exceptions=offered, checked_at=LATER), LATER)


def batches(result: WitnessedPlan) -> list[tuple[str, ...]]:
    found = result.outcome.plan.plan
    assert found is not None
    return [batch.document_ids for batch in found.batches]


def test_a_fresh_deployment_plans_only_after_the_explicit_bootstrap(
    chain: ChainRig, planner: Any
) -> None:
    store, _ = planner
    error = refused(lambda: plan(chain, store))
    assert error.detail == "quarantine_succession:anchor:unanchored"

    bootstrapped(chain)
    result = plan(chain, store)

    assert result.status == "planned"
    assert batches(result) == [(document(1), document(2), document(3))]
    again = refused(lambda: initialize_quarantine(chain.store, REPOSITORY, chain.authority()))
    assert again.detail == "already_anchored"


def test_the_bootstrap_never_adopts_or_resets_a_history(chain: ChainRig) -> None:
    """Entries on disk, or a document anchored under the AR5a family, refuse it."""
    initialize(legacy_query(b""), chain.authority())
    anchor_pending(legacy_query(document_bytes(first_ledger())), chain.authority())

    error = refused(lambda: initialize_quarantine(chain.store, REPOSITORY, chain.authority()))

    assert error.detail == "quarantine_succession:genesis:quarantine_ledger:local_rolled_back"


def test_a_valid_successor_reaches_the_planner_only_once_anchored(
    chain: ChainRig, planner: Any
) -> None:
    store, rows = planner
    bootstrapped(chain)

    first = plan(chain, store, reported=(render_failure(rows, 2),))
    assert first.status == "successor_required"
    assert first.outcome.plan.plan is None
    assert first.outcome.quarantine.quarantine is None  # nothing sealed from an unanchored ledger

    anchored(chain, first, CHECKED_AT)
    result = plan(chain, store, reported=(render_failure(rows, 2),), checked_at=LATER)

    assert result.status == "planned"
    assert batches(result) == [(document(1), document(3))]
    found = result.outcome.quarantine.quarantine
    assert found is not None
    assert found.ledger_sha256 == head_of(chain)[1]
    assert result.outcome.metrics is not None
    assert result.outcome.metrics.documents_quarantined == 1
    # Deterministic: the same anchored state plans the same plan.
    assert plan(chain, store, checked_at=LATER).outcome.plan == result.outcome.plan


def test_an_owner_clearance_reaches_the_planner_and_is_kept_from_the_chain(
    chain: ChainRig, planner: Any
) -> None:
    store, rows = planner
    cleared_two(chain, store, rows)

    result = plan(chain, store, checked_at=LATER)  # nothing offered: the head's clearance holds

    assert result.status == "planned"
    assert batches(result) == [(document(1), document(2), document(3))]


def test_a_caller_supplied_ledger_never_reaches_the_planner(chain: ChainRig, planner: Any) -> None:
    store, rows = planner
    quarantined_two(chain, store, rows)
    empty = b'{"exclusions":[],"published":false}'
    head = read_quarantine(
        chain.store, REPOSITORY, chain.authority(), policies_of(policy_bytes())
    ).ledger

    for offered in (empty, document_bytes(EMPTY_LEDGER), document_bytes(head)):
        error = refused(lambda raw=offered: plan(chain, store, ledger=raw))
        assert error.detail == "quarantine_witness:caller_ledger_refused"


def document_bytes(ledger: QuarantineLedger) -> bytes:
    from lovspor.forarbeider.version_models import canonical_text  # noqa: PLC0415

    return canonical_text(ledger).encode()


def test_a_forged_clearance_halts_the_run_and_plans_nothing(chain: ChainRig, planner: Any) -> None:
    store, rows = planner
    quarantined_two(chain, store, rows)
    found = head_of(chain)
    (record,) = found.ledger.records
    forged = signed_by(exception(record), chain.keys.bot, QUARANTINE_NAMESPACE)

    result = plan(chain, store, exceptions=ExceptionMaterial(records=(forged,)), checked_at=LATER)

    assert result.status == "halted"
    assert [h.code for h in result.outcome.halts] == ["quarantine_exception_untrusted"]
    assert result.outcome.plan.plan is None


def test_a_clearance_signed_by_a_since_revoked_key_is_refused(
    chain: ChainRig, planner: Any
) -> None:
    store, rows = planner
    cleared_two(chain, store, rows)
    revoked = root_of(
        chain.tmp / "revoked-root", chain.keys, chain.witness_key, (chain.keys.owner,)
    )

    later = {"root": revoked}
    error = refused(lambda: plan(chain, store, checked_at=LATER, authority=later))

    assert error.detail.startswith("quarantine_succession:"), error.detail
    reasons = quarantine_reasons(
        (source_of(chain), chain.authority(root=revoked)),
        QuarantineClaim(REPOSITORY, ("0" * 64,), None),
        LATER,
    )
    assert reasons and reasons[0].startswith("quarantine_succession:"), reasons


def test_a_clearance_whose_signing_key_has_since_lapsed_is_refused_now(
    chain: ChainRig, planner: Any
) -> None:
    """Replay proves the clearance at the time it was judged; now its key's window is over."""
    store, rows = planner
    cleared_two(chain, store, rows)
    lines = [
        signer_line(OWNER, chain.keys.owner, OWNER_QUARANTINE, (KEY_AFTER, "20261115130000Z")),
        signer_line(BOT, chain.keys.bot, BOT_NAMESPACES),
        signer_line(WITNESS, chain.witness_key, ANCHOR_NAMESPACE),
    ]
    lapsed = make_root(chain.tmp / "lapsed-root", lines)
    later = LATER + timedelta(hours=2)
    authority = chain.authority(root=lapsed)

    error = refused(lambda: witnessed_quarantine(source_of(chain), REPOSITORY, authority, later))

    assert error.detail == "quarantine_witness:exception:quarantine_exception_untrusted"
    assert plan(chain, store, checked_at=later, authority={"root": lapsed}).status == "halted"


def test_a_lapsed_clearance_is_reinstated_never_honoured(chain: ChainRig, planner: Any) -> None:
    store, rows = planner
    cleared_two(chain, store, rows, valid_until=LATER + timedelta(hours=1))
    later = LATER + timedelta(hours=2)

    error = refused(
        lambda: witnessed_quarantine(source_of(chain), REPOSITORY, chain.authority(), later)
    )
    assert error.detail == "quarantine_witness:clearance_not_current"

    result = plan(chain, store, checked_at=later)
    assert result.status == "successor_required"
    assert result.outcome.ledger is not None
    (record,) = result.outcome.ledger.records
    assert (record.state, record.history[-1].event) == ("active", "reinstated")
    anchored(chain, result, later)
    assert batches(plan(chain, store, checked_at=later)) == [(document(1), document(3))]


def test_changed_evidence_ends_a_clearance_through_an_anchored_successor(
    chain: ChainRig, planner: Any
) -> None:
    store, rows = planner
    cleared_two(chain, store, rows)
    moved = render_failure(rows, 2).model_copy(update={"evidence_sha256s": ("d" * 64,)})

    result = plan(chain, store, reported=(moved,), checked_at=LATER)

    assert result.status == "successor_required"
    assert result.outcome.ledger is not None
    (record,) = result.outcome.ledger.records
    assert record.state == "active"


def test_a_reader_without_the_chains_policy_refuses(chain: ChainRig, planner: Any) -> None:
    store, rows = planner
    cleared_two(chain, store, rows)
    blind = WitnessedSource(chain.store, ())

    error = refused(lambda: plan(chain, store, checked_at=LATER, source=blind))

    assert "policy_unavailable" in error.detail, error.detail


def test_a_quarantine_judged_under_another_policy_is_not_the_plans(
    chain: ChainRig, planner: Any
) -> None:
    store, rows = planner
    cleared_two(chain, store, rows)
    found = witnessed_quarantine(source_of(chain), REPOSITORY, chain.authority(), LATER)
    other = PolicyReference(policy_id="other", policy_version=1, policy_sha256="9" * 64)
    held = (source_of(chain), chain.authority())
    bound = (digest_of(found.found),)

    assert quarantine_reasons(held, QuarantineClaim(REPOSITORY, bound, other), LATER) == (
        "quarantine_policy_not_the_plans",
    )


def test_a_pending_successor_is_never_planned_from_and_its_retry_completes(
    chain: ChainRig, planner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, rows = planner
    bootstrapped(chain)
    first = plan(chain, store, reported=(render_failure(rows, 2),))
    real = quarantine_succession.anchor_pending

    def crash(*args: Any) -> Any:
        raise SimulatedCrash

    monkeypatch.setattr(quarantine_succession, "anchor_pending", crash)
    with pytest.raises(SimulatedCrash):
        anchored(chain, first, CHECKED_AT)
    monkeypatch.setattr(quarantine_succession, "anchor_pending", real)

    error = refused(lambda: plan(chain, store, checked_at=LATER))
    assert error.detail == "quarantine_succession:anchor:pending_anchor"
    wanted = SuccessionRequest(
        REPOSITORY,
        first.anchored.ledger_sha256,
        first.outcome.ledger or EMPTY_LEDGER,
        first.material,
        policy_bytes(),
        CHECKED_AT,
        policies=(policy_bytes(),),
    )
    assert append_successor(chain.store, wanted, chain.authority()).status == "completed"
    assert plan(chain, store, checked_at=LATER).status == "planned"


def test_a_planner_racing_the_writer_sees_the_old_head_or_nothing(
    chain: ChainRig, planner: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From inside the writer's critical section (entry written, not yet anchored)."""
    store, rows = planner
    bootstrapped(chain)
    first = plan(chain, store, reported=(render_failure(rows, 2),))
    real = quarantine_succession.anchor_pending
    seen: list[str] = []

    def racing(*args: Any) -> Any:
        seen.append(refused(lambda: plan(chain, store, checked_at=LATER)).detail)
        return real(*args)

    monkeypatch.setattr(quarantine_succession, "anchor_pending", racing)
    anchored(chain, first, CHECKED_AT)

    assert seen == ["quarantine_succession:anchor:pending_anchor"]
    assert batches(plan(chain, store, checked_at=LATER)) == [(document(1), document(3))]


def test_a_rolled_back_chain_is_refused(chain: ChainRig, planner: Any) -> None:
    store, rows = planner
    quarantined_two(chain, store, rows)
    kept = chain.tmp / "kept"
    shutil.copytree(chain.store.root, kept)
    offered = ExceptionMaterial(records=(clearance_of(chain),))
    anchored(chain, plan(chain, store, exceptions=offered, checked_at=LATER), LATER)

    shutil.rmtree(chain.store.root)
    shutil.copytree(kept, chain.store.root)

    error = refused(lambda: plan(chain, store, checked_at=LATER))
    assert error.detail == "quarantine_succession:anchor:local_rolled_back"


def test_a_copy_of_the_chain_left_behind_is_refused(chain: ChainRig, planner: Any) -> None:
    store, rows = planner
    quarantined_two(chain, store, rows)
    copy = chain.tmp / "copy"
    shutil.copytree(chain.store.root, copy)
    offered = ExceptionMaterial(records=(clearance_of(chain),))
    anchored(chain, plan(chain, store, exceptions=offered, checked_at=LATER), LATER)

    stale = WitnessedSource(QuarantineStore(copy, LEDGER_ID), (policy_bytes(),))
    error = refused(lambda: plan(chain, store, checked_at=LATER, source=stale))

    assert error.detail == "quarantine_succession:anchor:local_rolled_back"


def test_an_unavailable_witness_refuses_planning(chain: ChainRig, planner: Any) -> None:
    store, rows = planner
    quarantined_two(chain, store, rows)
    shutil.move(str(chain.tmp / "witness"), str(chain.tmp / "witness-away"))

    error = refused(lambda: plan(chain, store, checked_at=LATER))

    assert error.detail == "quarantine_succession:anchor:store_unavailable"


def test_a_clock_before_the_anchored_ledger_is_refused(chain: ChainRig, planner: Any) -> None:
    store, rows = planner
    quarantined_two(chain, store, rows)
    earlier = CHECKED_AT - timedelta(hours=1)

    assert plan(chain, store, checked_at=earlier).status == "halted"
    error = refused(
        lambda: witnessed_quarantine(source_of(chain), REPOSITORY, chain.authority(), earlier)
    )
    assert error.detail == "quarantine_witness:clock_regressed"


_READER = """
import json, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from lovspor.forarbeider.control_plane_anchor import AnchorAuthority
from lovspor.forarbeider.control_plane_anchor import anchor_pending, initialize
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.policy_trust import load_trust_root
from lovspor.forarbeider.publication_quarantine import WitnessedSource, witnessed_quarantine
from lovspor.forarbeider.quarantine_succession import QuarantineStore
from lovspor.forarbeider.version_release_models import digest_of
from tests.unit.forarbeider_authorization_support import Key
from tests.unit.forarbeider_execution_support import KeySigner
job = json.loads(sys.argv[1])
key = Key(Path(job["key"]), job["public"])
witness = OfflineAnchorWitness(Path(job["store"]), KeySigner("test-witness", key))
at = datetime.fromisoformat(job["at"])
authority = AnchorAuthority(witness, load_trust_root(Path(job["root"])), at)
source = WitnessedSource(QuarantineStore(Path(job["quarantine"]), job["ledger_id"]),
                         (Path(job["policy"]).read_bytes(),))
found = witnessed_quarantine(source, job["repository"], authority, at)
print(json.dumps({"set": digest_of(found.found), "sequence": found.verified.sequence}))
"""


def test_an_independent_reader_after_a_restart_proves_the_same_quarantine(
    chain: ChainRig, planner: Any
) -> None:
    store, rows = planner
    cleared_two(chain, store, rows)
    expected = witnessed_quarantine(source_of(chain), REPOSITORY, chain.authority(), EVEN_LATER)
    policy_file = chain.tmp / "policy.json"
    policy_file.write_bytes(policy_bytes())
    job = {
        "store": str(chain.tmp / "witness"),
        "key": str(chain.witness_key.path),
        "public": chain.witness_key.public,
        "root": str(chain.tmp / "root"),
        "at": EVEN_LATER.isoformat(),
        "quarantine": str(chain.store.root),
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
    assert found == {"set": digest_of(expected.found), "sequence": 2}


# --- the send: paths A and B -------------------------------------------------------------------


@pytest.fixture(params=["A", "B"])
def sender(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    return make_rig(tmp_path / request.param.lower(), request.param)


def environment_of(chosen: Rig) -> tuple[Simulation, SimulatedGitHub | None]:
    if chosen.path == "A":
        github = SimulatedGitHub(head_oid=chosen.base, identity=APP, now=NOW)
        return Simulation(clock=lambda: NOW, transport=github), github
    signer = KeySigner(BOT, chosen.bot)
    return Simulation(clock=lambda: NOW, remote=chosen.remote, signer=signer), None


def chain_of(chosen: Rig) -> QuarantineStore:
    return QuarantineStore(chosen.tmp / "quarantine", CHAIN_ID)


def batch_finding(chosen: Rig) -> QuarantineFinding:
    """A document-local failure of a document in the transaction's batch."""
    planned = chosen.inputs.plan.plan
    assert planned is not None
    batch = set(planned.batches[0].document_ids)
    item = next(d for d in planned.documents if d.document_id in batch)
    return QuarantineFinding(
        document_id=item.document_id,
        version_id=item.version_id,
        scope=QuarantineScope(kind="document"),
        code="render_failed",
        category="rendering",
        evidence_sha256s=("c" * 64,),
        policy=planned.basis.policy,
    )


def quarantine_in_the_batch(chosen: Rig) -> QuarantineLedger:
    """The owner's pipeline anchors a successor quarantining a document of the batch."""
    ledger = merge(EMPTY_LEDGER, [batch_finding(chosen)], NOW)
    wanted = SuccessionRequest(
        repository=chosen.inputs.settings.repository,
        expected_predecessor_sha256=document_sha256(EMPTY_LEDGER),
        successor=ledger,
        exceptions=ExceptionMaterial(),
        policy=None,
        at=NOW,
    )
    assert append_successor(chain_of(chosen), wanted, chosen.anchoring()).status == "appended"
    return ledger


def reasons_of(outcome: Any, name: str) -> tuple[str, ...]:
    assert outcome.evidence is not None
    (check,) = [c for c in outcome.evidence.checks if c.name == name]
    return tuple(check.reasons)


def remote_unchanged(chosen: Rig, github: SimulatedGitHub | None) -> bool:
    if github is not None:
        return github.head(github.repository, "main") == chosen.base
    from tests.unit.forarbeider_publication_support import git  # noqa: PLC0415

    return git(chosen.bare, "rev-parse", "main").strip() == chosen.base


def test_a_witnessed_empty_quarantine_reaches_the_send(sender: Rig) -> None:
    environment, _ = environment_of(sender)

    outcome = execute(sender.request(), environment)

    assert outcome.status == "published"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == ()


def test_a_document_the_anchored_chain_quarantines_is_never_sent(sender: Rig) -> None:
    """Reproduction: the caller's sealed assessment (empty) is stale against the chain."""
    quarantine_in_the_batch(sender)
    environment, github = environment_of(sender)

    outcome = execute(sender.request(), environment)

    assert outcome.status == "denied"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == ("quarantine_not_the_anchored_head",)
    assert remote_unchanged(sender, github)


def test_a_publication_without_the_anchored_quarantine_is_denied(sender: Rig) -> None:
    environment, github = environment_of(sender)

    outcome = execute(sender.request(quarantine=None), environment)

    assert outcome.status == "denied"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == ("quarantine_source_missing",)
    assert remote_unchanged(sender, github)


def test_the_quarantine_anchor_is_never_waived_and_unavailable_in_production(
    sender: Rig,
) -> None:
    outcome = execute(sender.request(), Production())

    assert outcome.status == "denied"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == (
        "quarantine_anchor_production_witness_ar7_pending",
    )
    from lovspor.forarbeider.publication_execution_models import (  # noqa: PLC0415
        WAIVABLE_IN_SIMULATION,
    )

    assert "ar5_quarantine_anchor" not in WAIVABLE_IN_SIMULATION


def test_a_pending_successor_denies_the_send(sender: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(*args: Any) -> Any:
        raise SimulatedCrash

    monkeypatch.setattr(quarantine_succession, "anchor_pending", crash)
    with pytest.raises(SimulatedCrash):
        quarantine_in_the_batch(sender)
    monkeypatch.undo()
    environment, github = environment_of(sender)

    outcome = execute(sender.request(), environment)

    assert outcome.status == "denied"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == (
        "quarantine_succession:anchor:pending_anchor",
    )
    assert remote_unchanged(sender, github)


def test_a_rolled_back_quarantine_chain_denies_the_send(sender: Rig) -> None:
    kept = sender.tmp / "kept"
    shutil.copytree(chain_of(sender).root, kept)
    quarantine_in_the_batch(sender)
    shutil.rmtree(chain_of(sender).root)
    shutil.copytree(kept, chain_of(sender).root)
    environment, github = environment_of(sender)

    outcome = execute(sender.request(), environment)

    assert outcome.status == "denied"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == (
        "quarantine_succession:anchor:local_rolled_back",
    )
    assert remote_unchanged(sender, github)


def test_a_chain_anchored_only_on_another_witness_denies_the_send(
    sender: Rig, tmp_path: Path
) -> None:
    """The chain is read on the ledger's witness; one the workspace anchored elsewhere is not it."""
    elsewhere = make_rig(tmp_path / "elsewhere", sender.path)
    quarantine_in_the_batch(elsewhere)  # anchored on the other rig's witness only
    environment, github = environment_of(sender)

    outcome = execute(sender.request(quarantine=elsewhere.quarantine), environment)

    assert outcome.status == "denied"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == (
        "quarantine_succession:anchor:pending_anchor",
    )
    assert remote_unchanged(sender, github)


def test_a_workspace_sharing_the_ledger_reads_the_chain_on_the_ledgers_witness(
    tmp_path: Path,
) -> None:
    first = make_rig(tmp_path / "a", "A")
    shared = make_rig(tmp_path / "b", "A", share=first)
    environment, _ = environment_of(first)
    assert execute(first.request(), environment).status == "published"
    assert recover(first.request(), environment).settlement.status == "settled"
    assert publication_ledger.reserve(shared.request(), NOW).status == "appended"
    environment, _ = environment_of(shared)

    outcome = execute(shared.request(), environment)

    assert outcome.status == "published"
    assert reasons_of(outcome, "ar5_quarantine_anchor") == ()


def _authorized(chosen: Rig, github: SimulatedGitHub) -> Any:
    context = AuthorizationContext(
        chosen.inputs,
        chosen.root,
        chosen.clone,
        lambda: github.head(github.repository, "main"),
        NOW,
        next_attempt_id(chosen.request()),
        "simulation",
        AdapterFacts(app=github.identity),
        chosen.anchoring(),
        chosen.workspace,
        chosen.ledger,
        chosen.quarantine,
    )
    evidence = authorize(context)
    assert evidence.decision == "simulation_only", evidence.denied_by
    return evidence


def test_a_successor_anchored_after_the_authorization_makes_it_stale(tmp_path: Path) -> None:
    chosen = make_rig(tmp_path / "a", "A")
    environment, github = environment_of(chosen)
    assert github is not None
    evidence = _authorized(chosen, github)
    quarantine_in_the_batch(chosen)

    error = refused(lambda: publish_authorized(evidence, chosen.request(), environment))

    assert error.detail == "authorization_stale:quarantine:quarantine_not_the_anchored_head"
    assert remote_unchanged(chosen, github)


def test_no_successor_is_anchored_while_a_send_holds_the_chain(
    sender: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From inside the send: the successor's writer finds the chain locked."""
    real = publication_execute._send
    seen: list[str] = []

    def racing(*args: Any) -> Any:
        seen.append(str(refused(lambda: quarantine_in_the_batch(sender))))
        return real(*args)

    monkeypatch.setattr(publication_execute, "_send", racing)
    environment, _ = environment_of(sender)

    assert execute(sender.request(), environment).status == "published"
    assert len(seen) == 1 and "corpus_locked" in seen[0], seen


def test_no_send_starts_while_a_successor_holds_the_chain(sender: Rig) -> None:
    environment, github = environment_of(sender)
    store = chain_of(sender)

    with publication_ledger.ledger_lock(BudgetLedger(store.root, store.ledger_id)):
        error = refused(lambda: execute(sender.request(), environment))

    assert "corpus_locked" in str(error)
    assert remote_unchanged(sender, github)


def test_a_witness_authority_on_another_trust_root_is_refused(sender: Rig) -> None:
    """Every anchor is verified under the request's trust root, never a second one."""
    other = _root(
        sender.tmp / "other-root", sender.owner, sender.bot, sender.witness_key, (sender.stranger,)
    )
    assert other.sha256 != sender.root.sha256
    environment, github = environment_of(sender)

    outcome = execute(sender.request(anchoring=sender.anchoring(root=other)), environment)

    assert outcome.status == "denied"
    assert "trust_root" in (outcome.evidence.denied_by if outcome.evidence else ())
    assert remote_unchanged(sender, github)


def test_evidence_is_never_spent_under_another_trust_root(tmp_path: Path) -> None:
    chosen = make_rig(tmp_path / "a", "A")
    environment, github = environment_of(chosen)
    assert github is not None
    evidence = _authorized(chosen, github)
    other = _root(
        chosen.tmp / "other-root", chosen.owner, chosen.bot, chosen.witness_key, (chosen.stranger,)
    )
    moved = chosen.request(anchoring=chosen.anchoring(root=other))

    error = refused(lambda: publish_authorized(evidence, moved, environment))

    assert error.detail == "authorization_stale:trust_root"
    assert remote_unchanged(chosen, github)


# --- AR4a at the publishing boundary: membership chosen only from the anchored head -------------


def preparation_of(chosen: Rig) -> PreparationRequest:
    planned = chosen.inputs.plan.plan
    assert planned is not None
    return PreparationRequest(
        plan=chosen.inputs.plan,
        quarantine=chosen.inputs.quarantine,
        batch_id=planned.batches[0].batch_id,
        rows=(),
        artifacts={},
        path=chosen.path,  # type: ignore[arg-type]
        base_commit=chosen.base,
    )


def witnessing_of(chosen: Rig) -> Witnessing:
    assert chosen.quarantine is not None
    authority = chosen.anchoring(workspace=None)
    return Witnessing(chosen.quarantine, authority, chosen.inputs.settings.repository, NOW)


def test_a_batch_is_never_prepared_from_a_stale_quarantine(sender: Rig) -> None:
    quarantine_in_the_batch(sender)
    work = sender.tmp / "work-again"
    work.mkdir(mode=0o700)

    error = refused(
        lambda: prepare_witnessed(
            preparation_of(sender), witnessing_of(sender), sender.source, work
        )
    )

    assert error.detail == "quarantine_witness:prepare:quarantine_not_the_anchored_head"
    assert list(work.iterdir()) == []  # nothing cloned, nothing staged


def test_a_batch_is_never_prepared_on_a_pending_chain(
    sender: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(*args: Any) -> Any:
        raise SimulatedCrash

    monkeypatch.setattr(quarantine_succession, "anchor_pending", crash)
    with pytest.raises(SimulatedCrash):
        quarantine_in_the_batch(sender)
    monkeypatch.undo()
    work = sender.tmp / "work-again"
    work.mkdir(mode=0o700)

    error = refused(
        lambda: prepare_witnessed(
            preparation_of(sender), witnessing_of(sender), sender.source, work
        )
    )

    assert error.detail == (
        "quarantine_witness:prepare:quarantine_succession:anchor:pending_anchor"
    )
    assert list(work.iterdir()) == []
