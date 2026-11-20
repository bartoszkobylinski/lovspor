"""Synthetic fixtures for the AR4b-1 authorization and execution tests (#738).

Everything is disposable: throwaway SSH keys and a trust root in pytest's
directory, a source repository and a **bare remote** made here, an AR4a
proposal prepared from the invented fixture documents, and a stub of the
GitHub GraphQL endpoint that keeps its own state in memory. No network, no
production credential, no real repository.
"""

import base64
import subprocess
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from lovspor.forarbeider.policy_authorization import SignedRecord
from lovspor.forarbeider.policy_trust import (
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


class Crash(BaseException):
    """The process dying: no handler under test catches it."""


class TransportLostError(Exception):
    """Stand-in for a connection dropped after the request left."""


@dataclass
class StubGitHub:
    """The GraphQL endpoint, stubbed: ``main`` of one repository, held in memory.

    ``post`` applies a ``createCommitOnBranch`` exactly as GitHub would: refused
    unless ``expectedHeadOid`` is the head, otherwise a new commit moves ``main``.
    The ``after`` hook runs once the commit is applied, so a test can lose the
    response or crash there.
    """

    head_oid: str
    identity: AppIdentity = APP
    repository: str = "bartoszkobylinski/lovverk-forarbeider"
    calls: list[dict[str, Any]] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    after: Any = None
    response: Any = None
    reported_head: str | None = None

    def head(self, repository: str, branch: str) -> str | None:
        assert (repository, branch) == (self.repository, "main")
        return self.reported_head or self.head_oid

    def post(self, payload: dict[str, Any]) -> Any:
        self.calls.append(payload)
        given = payload["variables"]["input"]
        if given["expectedHeadOid"] != self.head_oid:
            return {
                "data": {"createCommitOnBranch": None},
                "errors": [
                    {"type": "STALE_DATA", "message": "Expected branch to point to another commit"}
                ],
            }
        for item in given["fileChanges"]["additions"]:
            self.files[item["path"]] = base64.b64decode(item["contents"])
        self.head_oid = format(len(self.calls), "040x")
        if self.after is not None:
            self.after()
        if self.response is not None:
            return self.response
        return {"data": {"createCommitOnBranch": {"commit": {"oid": self.head_oid}}}}


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
    bare: Path
    workspace: Path
    path: str
    transaction: PublicationTransaction
    clone: Path
    inputs: AuthorizationInputs

    def request(self, **changes: Any) -> ExecutionRequest:
        fields: dict[str, Any] = {
            "inputs": self.inputs,
            "root": self.root,
            "clone": self.clone,
            "workspace": self.workspace,
        }
        return ExecutionRequest(**(fields | changes))

    def remote_files(self) -> dict[str, str]:
        listing = git(self.bare, "ls-tree", "-r", "main")
        return {line.split("\t")[1]: line.split()[2] for line in listing.splitlines()}


def _root(tmp: Path, owner: Key, bot: Key, revoked: tuple[Key, ...] = ()) -> TrustRoot:
    lines = [signer_line(OWNER, owner, OWNER_KEYS), signer_line(BOT, bot, BOT_NAMESPACES)]
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
    owner, bot, stranger = (new_key(keys, name) for name in ("owner", "bot", "stranger"))
    root = _root(tmp / "root", owner, bot)
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
    bare = tmp / "remote.git"
    subprocess.run(["git", "clone", "--bare", "--quiet", str(source), str(bare)], check=True)
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
    return Rig(
        tmp,
        owner,
        bot,
        stranger,
        root,
        store,
        source,
        base,
        bare,
        workspace,
        path,
        transaction,
        prepared.clone,
        inputs,
    )
