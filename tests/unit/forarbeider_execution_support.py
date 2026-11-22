"""Synthetic fixtures for the AR4b-1 authorization and execution tests (#738).

Everything is disposable: throwaway SSH keys and a trust root in pytest's
directory, a source repository and a disposable **bare remote** made by
:func:`~lovspor.forarbeider.publication_sandbox.create_disposable_remote` in a
private sandbox, and an AR4a proposal prepared from the invented fixture
documents. The GraphQL endpoint is
:class:`~lovspor.forarbeider.publication_sandbox.SimulatedGitHub`, in memory. No network, no
production credential, no real repository.
"""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from lovspor.forarbeider.control_plane_anchor import (
    AnchorAuthority,
    AnchorQuery,
    anchor_pending,
    initialize,
)
from lovspor.forarbeider.control_plane_chains import control_chain, workspace_chain
from lovspor.forarbeider.control_plane_witness import OfflineAnchorWitness
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
from lovspor.forarbeider.publication_execute import ExecutionRequest
from lovspor.forarbeider.publication_execution_models import AppIdentity, PublicationSettings
from lovspor.forarbeider.publication_models import PublicationTransaction
from lovspor.forarbeider.publication_prepare import PreparationRequest, prepare_publication
from lovspor.forarbeider.publication_sandbox import DisposableRemote, create_disposable_remote
from lovspor.forarbeider.release_contract_models import BudgetUsage, LimitsEvidence
from lovspor.forarbeider.release_ledger import entry_bytes, plan_entry
from lovspor.forarbeider.release_ledger_models import LedgerOperation
from lovspor.forarbeider.release_limits import LimitsMaterial, LimitsRequest, verify_limits
from lovspor.forarbeider.release_plan import build_plan
from lovspor.forarbeider.release_provenance import issue_provenance
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
    quarantine_of,
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
        }
        return ExecutionRequest(**(fields | changes))

    def remote_files(self) -> dict[str, str]:
        listing = git(self.bare, "ls-tree", "-r", "main")
        return {line.split("\t")[1]: line.split()[2] for line in listing.splitlines()}


WITNESS = "test-witness"


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
    evidence: LimitsEvidence, transaction: PublicationTransaction, amounts: BudgetUsage
) -> tuple[bytes, ...]:
    operation = LedgerOperation(
        operation="reserve",
        ledger_id="synthetic-ledger",
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


def make_rig(tmp: Path, path: str = "A") -> Rig:
    tmp.mkdir(mode=0o700, exist_ok=True)
    keys = tmp / "keys"
    owner, bot, stranger, witness_key = (
        new_key(keys, name) for name in ("owner", "bot", "stranger", "witness")
    )
    root = _root(tmp / "root", owner, bot, witness_key)
    signed_limits = signed_by(limits(), owner, LIMITS_NAMESPACE)
    material = LimitsMaterial(records=(signed_limits,))
    limits_evidence = verify_limits(LimitsRequest(policy_bytes(), material, NOW), root)
    assert limits_evidence.status == "verified", limits_evidence.reasons
    store = Store()
    rows = [synthetic_row(store, 1), synthetic_row(store, 2)]
    found = quarantine_of()
    limits_sha = limits_evidence.limits_sha256
    plan = build_plan(context([1, 2], rows, limits_sha256=limits_sha), rows, store.artifacts, found)
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
    witness = OfflineAnchorWitness(witness_dir, KeySigner(WITNESS, witness_key))
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
    )
    anchor_rig(rig)
    return rig


def anchor_queries(rig: "Rig", workspace: Path | None = None) -> dict[str, AnchorQuery]:
    """The rig's anchored families, read now: control (policy), latch, attempts, receipts."""
    held = workspace or rig.workspace
    tid = rig.transaction.transaction_id
    policy_id = policy_id_of(rig)
    repository = rig.inputs.settings.repository
    return {
        "control_history": AnchorQuery(
            "control_history", repository, policy_id, control_chain(rig.inputs.control, policy_id)
        ),
        "stop_latch": AnchorQuery(
            "stop_latch", repository, "workspace", workspace_chain("stop_latch", held, "workspace")
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
            "workspace",
            workspace_chain("recovery_receipts", held, "workspace"),
        ),
    }


def policy_id_of(rig: "Rig") -> str:
    plan = rig.inputs.plan.plan
    assert plan is not None
    return plan.basis.policy.policy_id


def anchor_rig(rig: "Rig") -> None:
    """Test setup: anchor the rig's empty families and its owner control records (AR5a)."""
    authority = rig.anchoring()
    queries = anchor_queries(rig)
    for name in ("stop_latch", "publication_attempts", "recovery_receipts"):
        assert initialize(queries[name], authority).status == "verified"
    control = queries["control_history"]
    policy_id = policy_id_of(rig)
    repository = rig.inputs.settings.repository
    empty = AnchorQuery("control_history", repository, policy_id, control_chain((), policy_id))
    assert initialize(empty, authority).status == "verified"
    for count in range(1, len(rig.inputs.control) + 1):
        prefix = control_chain(rig.inputs.control[:count], policy_id)
        step = AnchorQuery("control_history", repository, policy_id, prefix)
        assert anchor_pending(step, authority).status == "verified"
    assert control.local.reasons == ()
