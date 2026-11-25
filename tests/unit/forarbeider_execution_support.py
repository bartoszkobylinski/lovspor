"""Synthetic fixtures for the AR4b-1 authorization and execution tests (#738).

Everything is disposable: throwaway SSH keys and a trust root in pytest's
directory, a source repository and a disposable **bare remote** made by
:func:`~lovspor.forarbeider.publication_sandbox.create_disposable_remote` in a
private sandbox, and an AR4a proposal prepared from the invented fixture
documents. The GraphQL endpoint is
:class:`~lovspor.forarbeider.publication_sandbox.SimulatedGitHub`, in memory. No network, no
production credential, no real repository.
"""

from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from lovspor.forarbeider import publication_ledger
from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorQuery,
    anchor_pending,
    initialize,
    verify,
    workspace_scope,
)
from lovspor.forarbeider.control_plane_chains import control_chain, ledger_chain, workspace_chain
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import (
    ANCHOR_NAMESPACE,
    CONTROL_NAMESPACE,
    INVENTORY_NAMESPACE,
    LIMITS_NAMESPACE,
    POLICY_NAMESPACE,
    QUARANTINE_NAMESPACE,
    TrustRoot,
)
from lovspor.forarbeider.publication_authorize import AuthorizationInputs
from lovspor.forarbeider.publication_execute import (
    ExecutionOutcome,
    ExecutionRequest,
    Simulation,
    execute,
)
from lovspor.forarbeider.publication_execution_models import AppIdentity, PublicationSettings
from lovspor.forarbeider.publication_ledger import BudgetLedger
from lovspor.forarbeider.publication_models import PublicationTransaction
from lovspor.forarbeider.publication_prepare import PreparationRequest, prepare_publication
from lovspor.forarbeider.publication_quarantine import WitnessedSource
from lovspor.forarbeider.publication_sandbox import DisposableRemote, create_disposable_remote
from lovspor.forarbeider.quarantine_succession import QuarantineStore, initialize_quarantine
from lovspor.forarbeider.release_contract_models import BudgetUsage, LimitsEvidence
from lovspor.forarbeider.release_ledger import entry_bytes, plan_entry
from lovspor.forarbeider.release_ledger_models import LedgerOperation
from lovspor.forarbeider.release_limits import LimitsMaterial, LimitsRequest, verify_limits
from lovspor.forarbeider.release_plan import build_plan
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.release_quarantine import EMPTY_LEDGER, quarantine_set
from lovspor.forarbeider.workspace_identity import establish
from lovspor.forarbeider.workspace_writer_models import LEASE
from tests.unit.forarbeider_authorization_support import (
    BOT_NAMESPACES,
    Key,
    full_request,
    new_key,
    sign,
    signer_line,
    trust_root,
)
from tests.unit.forarbeider_publication_support import (
    git,
    sealed_plan,
    sealed_quarantine,
    source_repository,
    synthetic_row,
)
from tests.unit.forarbeider_release_control_support import (
    BOT,
    CHECKED_AT,
    OWNER,
    control,
    limits,
    policy,
    policy_bytes,
    signed_by,
)
from tests.unit.forarbeider_release_plan_support import Store, context

NOW = CHECKED_AT
OWNER_KEYS = ",".join(
    [
        POLICY_NAMESPACE,
        LIMITS_NAMESPACE,
        CONTROL_NAMESPACE,
        INVENTORY_NAMESPACE,
        QUARANTINE_NAMESPACE,
    ]
)
APP = AppIdentity(app_id=424242, slug="lovspor-forarbeider-synthetic", installation_id=777)


@dataclass(frozen=True)
class KeySigner:
    """A test-only bot signer over a throwaway key."""

    principal: str
    key: Key

    def sign(self, payload: bytes, namespace: str) -> bytes:
        return sign(self.key, payload, namespace)


@dataclass
class Rig:
    """One prepared AR4a proposal, its trust root, signed records and both remotes."""

    tmp: Path
    owner: Key
    bot: Key
    stranger: Key
    root: TrustRoot
    store: Store
    source: Path
    base: str
    remote: DisposableRemote
    workspace: Path
    path: str
    transaction: PublicationTransaction
    clone: Path
    inputs: AuthorizationInputs
    witness: OfflineAnchorWitness
    witness_key: Key
    ledger: BudgetLedger
    #: AR5 integration: the anchored quarantine chain the publication reads.
    quarantine: WitnessedSource | None = None

    def anchoring(self, **changes: Any) -> AnchorAuthority:
        fields: dict[str, Any] = {
            "witness": self.witness,
            "root": self.root,
            "checked_at": NOW,
            "workspace": self.workspace,
        }
        return AnchorAuthority(**(fields | changes))

    @property
    def bare(self) -> Path:
        return self.remote.path

    def request(self, **changes: Any) -> ExecutionRequest:
        fields: dict[str, Any] = {
            "inputs": self.inputs,
            "root": self.root,
            "clone": self.clone,
            "workspace": self.workspace,
            "anchoring": self.anchoring(),
            "ledger": self.ledger,
            "quarantine": self.quarantine,
        }
        return ExecutionRequest(**(fields | changes))

    def remote_files(self) -> dict[str, str]:
        listing = git(self.bare, "ls-tree", "-r", "main")
        return {line.split("\t")[1]: line.split()[2] for line in listing.splitlines()}


WITNESS = "test-witness"
LEDGER_ID = "synthetic-ledger"


def _root(
    tmp: Path, owner: Key, bot: Key, witness: Key, revoked: tuple[Key, ...] = ()
) -> TrustRoot:
    lines = [
        signer_line(OWNER, owner, OWNER_KEYS),
        signer_line(BOT, bot, BOT_NAMESPACES),
        signer_line(WITNESS, witness, ANCHOR_NAMESPACE),
    ]
    return trust_root(tmp, lines, revoked)


def reserve(
    evidence: LimitsEvidence,
    transaction: PublicationTransaction,
    amounts: BudgetUsage,
    ledger_id: str = LEDGER_ID,
) -> tuple[bytes, ...]:
    operation = LedgerOperation(
        operation="reserve",
        ledger_id=ledger_id,
        day=NOW.date(),
        release_id=transaction.transaction_id,
        batch_id=transaction.content.batch_id,
        amounts=amounts,
        recorded_at=NOW - timedelta(minutes=5),
    )
    planned = plan_entry((), operation, evidence)
    assert planned.entry is not None, planned.reasons
    return (entry_bytes(planned.entry),)


def batch_usage(transaction: PublicationTransaction) -> BudgetUsage:
    counts = transaction.content.counts
    return BudgetUsage(
        documents=counts.documents, files=counts.files, bytes=counts.bytes, commits=1
    )


def control_history(owner: Key, stopped: bool = False) -> tuple[SignedRecord, ...]:
    resume = signed_by(control("resume", 1, None), owner, CONTROL_NAMESPACE)
    if not stopped:
        return (resume,)
    return (resume, signed_by(control("stop", 2, resume), owner, CONTROL_NAMESPACE))


def settings_for(path: str, **changes: Any) -> PublicationSettings:
    fields: dict[str, Any] = {
        "path": path,
        "app": APP if path == "A" else None,
        "bot_principal": BOT,
    }
    return PublicationSettings(**(fields | changes))


def make_rig(
    tmp: Path, path: str = "A", share: "Rig | None" = None, one_witness: bool = False
) -> Rig:
    """A rig; with ``share``, another workspace and transaction on ``share``'s keys and ledger.

    The shared rig has its own workspace, its own witness store for its
    workspace families and its own documents (3 and 4), so its transaction is
    another; its ledger is ``share``'s, anchored by ``share``'s witness. With
    ``one_witness`` it also uses ``share``'s witness store for its workspace
    families (AR5d-1, #1013), and the policy's control history, which ``share``
    already anchored there, is not anchored again.
    """
    tmp.mkdir(mode=0o700, exist_ok=True)
    keys = tmp / "keys"
    if share is None:
        owner, bot, stranger, witness_key = (
            new_key(keys, name) for name in ("owner", "bot", "stranger", "witness")
        )
        root = _root(tmp / "root", owner, bot, witness_key)
    else:
        owner, bot, stranger, witness_key = (
            share.owner,
            share.bot,
            share.stranger,
            share.witness_key,
        )
        root = share.root
    numbers = [1, 2] if share is None else [3, 4]
    signed_limits = signed_by(limits(), owner, LIMITS_NAMESPACE)
    material = LimitsMaterial(records=(signed_limits,))
    limits_evidence = verify_limits(LimitsRequest(policy_bytes(), material, NOW), root)
    assert limits_evidence.status == "verified", limits_evidence.reasons
    store = Store()
    rows = [synthetic_row(store, n) for n in numbers]
    found = quarantine_set(EMPTY_LEDGER)
    limits_sha = limits_evidence.limits_sha256
    plan = build_plan(
        context(numbers, rows, limits_sha256=limits_sha), rows, store.artifacts, found
    )
    plan_evidence = sealed_plan(plan).model_copy(update={"provenance": None})
    sealed = issue_provenance(
        plan_evidence.model_copy(update={"trust_root_sha256": root.sha256}), "batch_plan"
    )
    unsealed = sealed_quarantine(found).model_copy(update={"provenance": None})
    quarantine = issue_provenance(
        unsealed.model_copy(update={"trust_root_sha256": root.sha256}), "quarantine_assessment"
    )
    (tmp / "src").mkdir(mode=0o700)
    source, base = source_repository(tmp / "src")
    work = tmp / "work"
    work.mkdir(mode=0o700)
    prepared = prepare_publication(
        PreparationRequest(
            plan=sealed,
            quarantine=quarantine,
            batch_id=plan.batches[0].batch_id,
            rows=tuple(rows),
            artifacts=dict(store.artifacts),
            path=path,  # type: ignore[arg-type]
            base_commit=base,
        ),
        source,
        work,
    )
    assert prepared.status == "prepared", prepared
    transaction = prepared.transaction
    assert transaction is not None and prepared.clone is not None
    sandbox = tmp / "sandbox"
    sandbox.mkdir(mode=0o700)
    remote = create_disposable_remote(sandbox, prepared.clone, base)
    ledger = reserve(limits_evidence, transaction, batch_usage(transaction))
    inputs = AuthorizationInputs(
        transaction=transaction,
        plan=sealed,
        quarantine=quarantine,
        policy=full_request(policy(), owner),
        limits=material,
        control=control_history(owner),
        ledger=ledger,
        settings=settings_for(path),
    )
    workspace = tmp / "attempts"
    workspace.mkdir(mode=0o700)
    witness_dir = tmp / "witness"
    witness_dir.mkdir(mode=0o700)
    witness = (
        share.witness
        if share is not None and one_witness
        else OfflineAnchorWitness(witness_dir, KeySigner(WITNESS, witness_key))
    )
    rig = Rig(
        tmp,
        owner,
        bot,
        stranger,
        root,
        store,
        source,
        base,
        remote,
        workspace,
        path,
        transaction,
        prepared.clone,
        inputs,
        witness,
        witness_key,
        BudgetLedger(tmp / "ledger", LEDGER_ID)
        if share is None
        else BudgetLedger(share.ledger.root, share.ledger.ledger_id, share.witness),
        quarantine_source(tmp) if share is None else share.quarantine,
    )
    anchor_rig(rig, seed=share is None, control=not (share is not None and one_witness))
    if share is None:
        verdict = initialize_quarantine(quarantine_store(tmp), REPOSITORY_OF_RIG, rig.anchoring())
        assert verdict.status == "verified"
    return rig


QUARANTINE_ID = "synthetic-quarantine"
REPOSITORY_OF_RIG = settings_for("A").repository


def quarantine_store(tmp: Path) -> QuarantineStore:
    """The rig's quarantine chain (AR5d-4), one per ledger, beside the budget ledger."""
    return QuarantineStore(tmp / "quarantine", QUARANTINE_ID)


def quarantine_source(tmp: Path) -> WitnessedSource:
    return WitnessedSource(quarantine_store(tmp), (policy_bytes(),))


def anchor_queries(rig: "Rig", workspace: Path | None = None) -> dict[str, AnchorQuery]:
    """The rig's anchored families, read now: control (policy), latch, attempts, receipts."""
    held = workspace or rig.workspace
    tid = rig.transaction.transaction_id
    policy_id = policy_id_of(rig)
    repository = rig.inputs.settings.repository
    scope = workspace_scope(held, repository, rig.anchoring(workspace=held))
    return {
        "control_history": AnchorQuery(
            "control_history", repository, policy_id, control_chain(rig.inputs.control, policy_id)
        ),
        "stop_latch": AnchorQuery(
            "stop_latch", repository, scope, workspace_chain("stop_latch", held, scope)
        ),
        "publication_attempts": AnchorQuery(
            "publication_attempts",
            repository,
            tid,
            workspace_chain("publication_attempts", held, tid),
        ),
        "recovery_receipts": AnchorQuery(
            "recovery_receipts",
            repository,
            scope,
            workspace_chain("recovery_receipts", held, scope),
        ),
    }


def identified_workspace(rig: "Rig", name: str) -> Path:
    """Another empty workspace on ``rig``'s witness, given its own identity (AR5d-2: a
    workspace without one is read-only)."""
    folder = rig.tmp / name
    folder.mkdir(mode=0o700)
    authority = rig.anchoring(workspace=folder)
    assert establish(folder, rig.inputs.settings.repository, authority, NOW).status == "established"
    return folder


def policy_id_of(rig: "Rig") -> str:
    plan = rig.inputs.plan.plan
    assert plan is not None
    return plan.basis.policy.policy_id


def anchor_rig(rig: "Rig", seed: bool = True, control: bool = True) -> None:
    """Test setup: the workspace's identity, which anchors its scope's families empty
    (AR5d-1, AR5d-2), the transaction's empty attempts (on the ledger's witness when it
    names one, AR5d-3; already anchored there by another workspace of the same
    transaction: left as it is) and the owner control records anchored (AR5a)."""
    authority = rig.anchoring()
    repository = rig.inputs.settings.repository
    assert establish(rig.workspace, repository, authority, NOW).status == "established"
    queries = anchor_queries(rig)
    for name in ("stop_latch", "recovery_receipts"):
        assert verify(queries[name], authority).status == "verified"
    # AR5d-3: the transaction's attempts live on the ledger's witness, when it names one
    attempts = (
        authority if rig.ledger.witness is None else rig.anchoring(witness=rig.ledger.witness)
    )
    if verify(queries["publication_attempts"], attempts).status != "verified":
        assert initialize(queries["publication_attempts"], attempts).status == "verified"
    if not control:
        assert verify(queries["control_history"], authority).status == "verified"
        return
    control = queries["control_history"]
    policy_id = policy_id_of(rig)
    empty = AnchorQuery("control_history", repository, policy_id, control_chain((), policy_id))
    assert initialize(empty, authority).status == "verified"
    for count in range(1, len(rig.inputs.control) + 1):
        prefix = control_chain(rig.inputs.control[:count], policy_id)
        step = AnchorQuery("control_history", repository, policy_id, prefix)
        assert anchor_pending(step, authority).status == "verified"
    assert control.local.reasons == ()
    if seed:
        seed_ledger(rig, rig.inputs.ledger)


# --- AR5c: the durable budget ledger, written here with AR1b and AR5a primitives only -----------


def ledger_folder(rig: "Rig") -> Path:
    return rig.ledger.root / rig.ledger.ledger_id


def ledger_bytes(rig: "Rig") -> tuple[bytes, ...]:
    """The ledger's entries as stored, oldest first."""
    folder = ledger_folder(rig)
    if not folder.exists():
        return ()
    return tuple(path.read_bytes() for path in sorted(folder.glob("*.json")))


def ledger_query(rig: "Rig", entries: tuple[bytes, ...] | None = None) -> AnchorQuery:
    held = ledger_bytes(rig) if entries is None else entries
    chain = ledger_chain(held, rig.ledger.ledger_id)
    return AnchorQuery("budget_ledger", rig.inputs.settings.repository, rig.ledger.ledger_id, chain)


def write_entry(rig: "Rig", raw: bytes) -> None:
    """Append one entry file as the ledger stores it, without anchoring it."""
    folder = ledger_folder(rig)
    path = folder / f"{len(ledger_bytes(rig)) + 1:06d}.json"
    path.write_bytes(raw)
    path.chmod(0o600)


def seed_ledger(rig: "Rig", entries: tuple[bytes, ...]) -> None:
    """Test setup: an anchored empty ledger, then each entry appended and anchored."""
    rig.ledger.root.mkdir(mode=0o700, exist_ok=True)
    ledger_folder(rig).mkdir(mode=0o700)
    authority = rig.anchoring(workspace=rig.ledger.root)
    assert initialize(ledger_query(rig), authority).status == "verified"
    for raw in entries:
        write_entry(rig, raw)
        assert anchor_pending(ledger_query(rig), authority).status == "verified"


def record_cancel(
    ledger: BudgetLedger,
    operation: LedgerOperation,
    limits: LimitsEvidence,
    anchoring: tuple[AnchorAuthority, str],
) -> publication_ledger.LedgerOutcome:
    """Test setup: an AR1b ``cancel`` through the ledger's own locked write path.

    AR5d-3: the public :func:`~lovspor.forarbeider.publication_ledger.record`
    refuses a cancel; only ``publication_cancel.cancel_reservation`` records one,
    after its proof, through this same path. The AR5c tests of a cancel's timing
    against a send write it here.
    """
    authority = publication_ledger.ledger_authority(ledger, anchoring[0])
    with publication_ledger.ledger_lock(ledger):
        return publication_ledger._record(ledger, operation, limits, (authority, anchoring[1]))


def seeded_ledger(rig: "Rig", name: str, entries: tuple[bytes, ...] = ()) -> BudgetLedger:
    """Another durable ledger, ``<tmp>/<name>``, id ``synthetic-ledger-<name>``, anchored."""
    other = replace(rig, ledger=BudgetLedger(rig.tmp / name, f"{LEDGER_ID}-{name}"))
    seed_ledger(other, entries)
    return other.ledger


def retried_after_the_crashed_claim(
    request: ExecutionRequest, simulation: Simulation
) -> ExecutionOutcome:
    """AR5d-1: a crash left the writer claim anchored and unreleased.

    It holds every writer of the identity off, this workspace's next run too,
    until its lease ends (``workspace_writer:claim_held``); then the retry runs.
    """
    try:
        execute(request, simulation)
    except ForarbeiderRefusedError as error:
        assert error.detail == "workspace_writer:claim_held", error.detail
    else:
        raise AssertionError("the crashed claim did not hold the retry off")
    return execute(request, simulation._replace(clock=lambda: NOW + LEASE))
