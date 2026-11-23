"""AR4b-1: execute one prepared publication transaction, in simulation only (#738; §9.3).

:func:`execute` is the only way to send, and it authorizes **itself**,
immediately before sending; a caller cannot hand it a decision:

1. The transaction's attempt records are read and proved linked. Already
   ``published``: ``unchanged``, nothing is sent again. A ``prepared_to_send``
   with nothing after it, or an ``outcome_unknown``: ``recovery_required``;
   nothing is ever sent blindly (§9.3a recovery is AR4b-2).
2. The attempt id is derived from the transaction and the next sequence
   number. Path B first builds and validates its local commit (B6, B7).
3. :func:`~lovspor.forarbeider.publication_authorize.authorize` runs every check now.
   ``denied`` is recorded and returned; a STOP, a revocation or an unknown
   outcome sets the persistent stop latch.
4. :func:`publish_authorized` requires the authorizing verifier's seal, the
   ``simulation_only`` decision, this transaction and attempt and a fresh
   check time; re-reads, under the lock, the STOP state the evidence was
   issued against (latch, latch transitions, owner control records, the
   witness), so sealed evidence never outlives a later STOP (AR5b);
   claims the workspace identity's one writer slot at the witness (AR5d-1,
   :mod:`~lovspor.forarbeider.workspace_writer`), so a copy of the workspace
   never sends as a second writer; records ``prepared_to_send`` durably
   **before** the mutation; proves the claim still current; sends once;
   records ``published``, ``refused`` or ``outcome_unknown``; and releases
   the claim.

**Production is disabled.** :class:`Production` carries no transport, no
remote and no signer: its authorization is always denied (every AR5-AR7 check
is unavailable) and nothing can be sent. A :class:`Simulation` waives the
AR1, FC7b and AR5-AR7 checks, so its label is no boundary: it sends only
through :mod:`~lovspor.forarbeider.publication_sandbox` adapters, proved before
authorization, before ``prepared_to_send`` is recorded and again right before
the mutation. Path A: exactly the in-memory
:class:`~lovspor.forarbeider.publication_sandbox.SimulatedGitHub`, by type
identity; any other transport is refused before it is read or posted to.
Path B: a :class:`~lovspor.forarbeider.publication_sandbox.DisposableRemote`
made fresh in a private sandbox and re-verified on disk (no symlink or ``..``,
exactly its configuration, no hooks or alternates, its token); a signer over a
throwaway key. :mod:`lovspor.git_publish` cannot reach a network remote or
force a push.

A document-local failure never reaches here: AR4a returns it to AR3 before a
proposal exists, and a clone without a ``prepared`` proposal is denied.
"""

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, NamedTuple

from lovspor.forarbeider import (
    publication_anchors,
    publication_attempts,
    publication_ledger,
    publication_path_a,
    publication_path_b,
    publication_receipts,
    publication_sandbox,
    publication_stage,
    workspace_writer,
)
from lovspor.forarbeider.control_plane_anchor import AnchorAuthority, verify
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import TrustRoot
from lovspor.forarbeider.publication_authorize import (
    AdapterFacts,
    AuthorizationContext,
    AuthorizationInputs,
    authorize,
)
from lovspor.forarbeider.publication_execution_models import (
    FRESH_SECONDS,
    AttemptRecord,
    AttemptState,
    AuthorizationEvidence,
)
from lovspor.forarbeider.publication_ledger import BudgetLedger
from lovspor.forarbeider.publication_path_b import Signer
from lovspor.forarbeider.publication_sandbox import DisposableRemote, SimulatedGitHub
from lovspor.forarbeider.release_control import ControlRequest, verify_control
from lovspor.forarbeider.release_provenance import authentic
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.workspace_writer_models import WriterEvent

Status = Literal[
    "published", "unchanged", "denied", "refused", "outcome_unknown", "recovery_required"
]


class ExecutionRequest(NamedTuple):
    inputs: AuthorizationInputs
    root: TrustRoot
    clone: Path
    workspace: Path
    #: The independent anchor witness (AR5a); without one, authorization is denied.
    anchoring: AnchorAuthority | None = None
    #: AR5c: the durable budget ledger; without one, a simulation is denied.
    ledger: BudgetLedger | None = None


class Simulation(NamedTuple):
    """Explicit test adapters; production has none."""

    clock: Callable[[], datetime]
    transport: SimulatedGitHub | None = None
    remote: DisposableRemote | None = None
    signer: Signer | None = None


class Production(NamedTuple):
    """Production execution: no adapter exists, so it is always denied."""


Environment = Simulation | Production


class ExecutionOutcome(NamedTuple):
    status: Status
    commit: str | None
    evidence: AuthorizationEvidence | None
    evidence_sha256: str | None
    attempt: AttemptRecord | None


class _Step(NamedTuple):
    sequence: int
    previous: str | None
    attempt_id: str


def _not_eligible(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, detail)


def evidence_digest(evidence: AuthorizationEvidence) -> str:
    return sha256(canonical_text(evidence).encode()).hexdigest()


def attempt_id_of(transaction_id: str, sequence: int) -> str:
    """The id of a transaction's attempt at ``sequence``: derived, never chosen by a caller."""
    return sha256(f"publication-attempt/1:{transaction_id}:{sequence}".encode()).hexdigest()


def next_attempt_id(request: ExecutionRequest) -> str:
    return _step(request).attempt_id


def _step(request: ExecutionRequest) -> _Step:
    tid = request.inputs.transaction.transaction_id
    sequence, previous = publication_attempts.head(request.workspace, tid)
    return _Step(sequence, previous, attempt_id_of(tid, sequence))


def _record(
    request: ExecutionRequest, step: _Step, state: AttemptState, extra: Mapping[str, object]
) -> AttemptRecord:
    content = request.inputs.transaction.content
    fields: dict[str, object] = {
        "transaction_id": request.inputs.transaction.transaction_id,
        "release_manifest_sha256": content.release_manifest_sha256,
        "base_commit": content.publication.base_commit,
        "path": content.publication.path,
        "attempt_id": step.attempt_id,
        "sequence": step.sequence,
        "state": state,
        "previous_sha256": step.previous,
        "authorization_sha256": None,
        "commit": None,
    }
    record = AttemptRecord.model_validate({**fields, **extra})
    publication_attempts.append(request.workspace, record)
    publication_anchors.settle(request, "publication_attempts", record.transaction_id)
    return record


def _attempts_anchored(request: ExecutionRequest) -> bool:
    """Whether this transaction's attempts verify against the witness (AR5b)."""
    authority = publication_anchors.authority_of(request)
    if authority is None:
        return False
    tid = request.inputs.transaction.transaction_id
    query = publication_anchors.workspace_query(
        request.inputs, request.workspace, "publication_attempts", tid
    )
    return verify(query, authority).status == "verified"


def _set_latch(request: ExecutionRequest, reasons: tuple[str, ...], at: datetime) -> None:
    """Set the latch at once, whatever the witness says; then anchor it when it can."""
    tid = request.inputs.transaction.transaction_id
    publication_attempts.latch(request.workspace, reasons, at, tid)
    publication_anchors.settle(request, "stop_latch")


def _settled(request: ExecutionRequest) -> ExecutionOutcome | None:
    tid = request.inputs.transaction.transaction_id
    found = publication_attempts.records(request.workspace, tid)
    receipted = [
        r
        for r in publication_receipts.receipts(request.workspace)
        if r.transaction_id == tid and r.published
    ]
    if receipted:
        return ExecutionOutcome(
            "unchanged", receipted[0].commit, None, None, found[-1] if found else None
        )
    if not found:
        return None
    last = found[-1]
    if last.state == "published":
        return ExecutionOutcome("unchanged", last.commit, None, None, last)
    if last.state in ("prepared_to_send", "outcome_unknown"):
        return ExecutionOutcome("recovery_required", last.commit, None, None, last)
    return None


# --- adapters ------------------------------------------------------------------------------


def _files(request: ExecutionRequest) -> dict[str, bytes]:
    """The proposed bytes as the clone holds them, each re-hashed against the transaction.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: proposal_bytes``.
    """
    found = {}
    for item in request.inputs.transaction.content.files:
        data = publication_stage.read_file(request.clone, item.path)
        if sha256(data).hexdigest() != item.sha256:
            raise _not_eligible("proposal_bytes")
        found[item.path] = data
    return found


def _remote(request: ExecutionRequest, environment: Simulation) -> Callable[[], str | None]:
    """The read of the remote ``main``, through an adapter proved sandboxed first.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible`` for a missing adapter,
            a transport that is not the in-memory endpoint, or a remote that is
            not a verified disposable one.
    """
    settings = request.inputs.settings
    if request.inputs.transaction.content.publication.path == "A":
        if environment.transport is None:
            raise _not_eligible("adapter")
        transport = publication_sandbox.verified_transport(environment.transport)
        return lambda: transport.head(settings.repository, settings.branch)
    if environment.remote is None or environment.signer is None:
        raise _not_eligible("adapter")
    bare = publication_sandbox.verified_remote(environment.remote)
    return lambda: publication_path_b.remote_head(bare)


def _facts(request: ExecutionRequest, environment: Simulation, now: datetime) -> AdapterFacts:
    latched = publication_attempts.latched(request.workspace)
    transaction = request.inputs.transaction
    if transaction.content.publication.path == "A":
        transport = environment.transport
        return AdapterFacts(app=transport.identity if transport else None, latched=latched)
    signer = environment.signer
    if signer is None:
        raise _not_eligible("adapter")
    try:
        files = _files(request)
        commit = publication_path_b.build_commit(request.clone, transaction, signer, now)
        trust = (request.root, request.inputs.settings.bot_principal, now)
        check = publication_path_b.verify_commit(request.clone, commit, (transaction, files), trust)
    except ForarbeiderRefusedError as error:
        return AdapterFacts(
            local_commit=None, commit_reasons=(f"local_commit:{error.detail}",), latched=latched
        )
    return AdapterFacts(local_commit=check.commit, commit_reasons=check.reasons, latched=latched)


# --- execution -----------------------------------------------------------------------------


def _latch_reasons(evidence: AuthorizationEvidence) -> tuple[str, ...]:
    found = [
        f"{check.name}:{reason}"
        for check in evidence.checks
        if check.status == "failed"
        for reason in check.reasons
        if "revoked" in reason or reason.startswith("active_stop:")
    ]
    return tuple(sorted(found))


def execute(request: ExecutionRequest, environment: Environment) -> ExecutionOutcome:
    """Authorize now, record, send once, record; never twice, never blindly.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked: workspace`` while another
            execution or a recovery holds the workspace; forged attempt records;
            a simulation without the adapter its path needs.
    """
    with publication_attempts.workspace_lock(request.workspace), _ledger_held(request):
        settled = _settled(request)
        if settled is not None:
            return settled
        step = _step(request)
        evidence = authorize(_context(request, environment, step))
        if evidence.decision == "denied":
            return _denied(request, step, evidence)
        return _publish(evidence, request, environment)


def _context(
    request: ExecutionRequest, environment: Environment, step: _Step
) -> AuthorizationContext:
    base = (request.inputs, request.root, request.clone)
    if isinstance(environment, Production):
        return AuthorizationContext(*base, lambda: None, _utc_now(), step.attempt_id, "production")
    remote = _remote(request, environment)
    now = environment.clock()
    facts = _facts(request, environment, now)
    anchoring = publication_anchors.authority_of(request)
    return AuthorizationContext(
        *base,
        remote,
        now,
        step.attempt_id,
        "simulation",
        facts,
        anchoring,
        request.workspace,
        request.ledger,
    )


def _ledger_held(request: ExecutionRequest) -> AbstractContextManager[None]:
    """AR5c: the budget ledger's lock, taken inside the workspace lock (never the reverse)."""
    if request.ledger is None:
        return nullcontext()
    return publication_ledger.ledger_lock(request.ledger)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _denied(
    request: ExecutionRequest, step: _Step, evidence: AuthorizationEvidence
) -> ExecutionOutcome:
    digest = evidence_digest(evidence)
    reasons = tuple(sorted(evidence.denied_by))
    record = None
    if not publication_anchors.identified(request):
        return ExecutionOutcome("denied", None, evidence, digest, None)
    if request.anchoring is None or _attempts_anchored(request):
        extra = {"authorization_sha256": digest, "reasons": reasons}
        record = _record(request, step, "denied", {**extra, "recorded_at": evidence.checked_at})
    latch = _latch_reasons(evidence)
    if latch:
        _set_latch(request, latch, evidence.checked_at)
    return ExecutionOutcome("denied", None, evidence, digest, record)


def _require_authorized(
    evidence: AuthorizationEvidence,
    request: ExecutionRequest,
    environment: Environment,
    step: _Step,
) -> datetime:
    if not isinstance(environment, Simulation):
        raise _not_eligible("production_execution_disabled")
    if (
        not authentic(evidence, "publication_authorization")
        or evidence.decision != "simulation_only"
    ):
        raise _not_eligible("authorization_not_authentic")
    if (evidence.transaction_id, evidence.attempt_id) != (
        request.inputs.transaction.transaction_id,
        step.attempt_id,
    ):
        raise _not_eligible("authorization_not_this_attempt")
    now = environment.clock()
    age = (now - evidence.checked_at).total_seconds()
    if not 0 <= age <= FRESH_SECONDS:
        raise _not_eligible("authorization_not_fresh")
    return now


def publish_authorized(
    evidence: AuthorizationEvidence, request: ExecutionRequest, environment: Environment
) -> ExecutionOutcome:
    """Record ``prepared_to_send``, send once through the simulation adapter, record the outcome.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible`` for evidence that is not
            this verifier's, not ``simulation_only``, not this attempt's or not fresh;
            ``corpus_locked: workspace`` while another execution or a recovery holds it.
    """
    with publication_attempts.workspace_lock(request.workspace), _ledger_held(request):
        return _publish(evidence, request, environment)


def _publish(
    evidence: AuthorizationEvidence, request: ExecutionRequest, environment: Environment
) -> ExecutionOutcome:
    """:func:`publish_authorized` under the workspace lock its caller holds."""
    step = _step(request)
    now = _require_authorized(evidence, request, environment, step)
    if not isinstance(environment, Simulation):  # _require_authorized proved it is
        raise _not_eligible("production_execution_disabled")
    _still_current(evidence, request, now)
    _sandboxed(environment, evidence.path)
    claim = workspace_writer.claim(request, step.attempt_id, now)
    try:
        _prepared(evidence, request, environment, (step, claim, now))
    except ForarbeiderRefusedError:
        workspace_writer.release(request, claim, environment.clock())
        raise
    sent = _send(request, environment, evidence)
    outcome = _after(request, environment, evidence, sent)
    workspace_writer.release(request, claim, environment.clock())
    return outcome


def _prepared(
    evidence: AuthorizationEvidence,
    request: ExecutionRequest,
    environment: Simulation,
    held: tuple[_Step, WriterEvent, datetime],
) -> None:
    """Record ``prepared_to_send``, then prove the attempt, the latch and the writer claim hold."""
    step, claim, now = held
    digest = evidence_digest(evidence)
    before = {"authorization_sha256": digest, "commit": evidence.local_commit, "recorded_at": now}
    _record(request, step, "prepared_to_send", before)
    if not _attempts_anchored(request):
        raise _not_eligible("anchor:publication_attempts_unanchored")
    if publication_attempts.latched(request.workspace):
        raise _not_eligible("stop_latch_set")
    workspace_writer.require_current(request, claim, environment.clock())


def _still_current(
    evidence: AuthorizationEvidence, request: ExecutionRequest, now: datetime
) -> None:
    """Under the locks: the STOP state and the reservation the evidence rests on still hold now.

    Sealed evidence stays fresh for a while; an owner STOP, a latch transition
    or an advanced control history since it was issued makes it stale. Read
    here from the workspace, the owner's records and the witness, never from
    the evidence or the caller.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible`` with ``stop_latch_set``,
            ``authorization_stale:stop_latch``, ``stop_control:<why>``,
            ``authorization_stale:control_history``, ``anchor:<family>:<status>`` or
            ``authorization_stale:reservation:<why>`` (AR5c: cancelled, settled,
            another batch, smaller than the batch, over the caps, another open one).
    """
    held = request.workspace
    if publication_attempts.latched(held):
        raise _not_eligible("stop_latch_set")
    if publication_attempts.latch_head(held)[1] != evidence.stop_latch_head_sha256:
        raise _not_eligible("authorization_stale:stop_latch")
    policy_id = evidence.policy.policy_id if evidence.policy is not None else None
    control = verify_control(ControlRequest(request.inputs.control, policy_id, now), request.root)
    if control.state != "not_stopped":
        raise _not_eligible(f"stop_control:{','.join(control.reasons) or 'stopped'}")
    if control.head_sha256 != evidence.control_head_sha256:
        raise _not_eligible("authorization_stale:control_history")
    authority = publication_anchors.authority_of(request)
    found = publication_anchors.anchor_reasons(authority, held, request.inputs, policy_id)
    repository = request.inputs.settings.repository
    found += publication_ledger.anchor_reasons(request.ledger, authority, repository)
    if found:
        raise _not_eligible(f"anchor:{','.join(found)}")
    reservation = publication_ledger.reservation_reasons(request, now)
    if reservation:
        raise _not_eligible(f"authorization_stale:reservation:{','.join(reservation)}")


def _sandboxed(environment: Simulation, path: str) -> None:
    """Prove the adapter sandboxed before anything is recorded; :func:`_send` proves it again."""
    if path == "A":
        publication_sandbox.verified_transport(environment.transport)
    else:
        publication_sandbox.verified_remote(environment.remote)


def _after(
    request: ExecutionRequest,
    environment: Simulation,
    evidence: AuthorizationEvidence,
    sent: publication_path_a.Sent | publication_path_b.Sent,
) -> ExecutionOutcome:
    """Record what the send proved; an unknown outcome sets the stop latch."""
    tid = evidence.transaction_id
    digest = evidence_digest(evidence)
    step = _Step(*publication_attempts.head(request.workspace, tid), evidence.attempt_id)
    at = environment.clock()
    extra = {"commit": sent.commit, "reasons": sent.reasons, "authorization_sha256": digest}
    record = _record(request, step, sent.status, {**extra, "recorded_at": at})
    if sent.status == "outcome_unknown":
        _set_latch(request, ("outcome_unknown",), at)
    return ExecutionOutcome(sent.status, sent.commit, evidence, digest, record)


def _send(
    request: ExecutionRequest, environment: Simulation, evidence: AuthorizationEvidence
) -> publication_path_a.Sent | publication_path_b.Sent:
    if evidence.path == "A":
        if environment.transport is None:
            raise _not_eligible("adapter")
        transport = publication_sandbox.verified_transport(environment.transport)
        payload = publication_path_a.payload_of(
            request.inputs.transaction, _files(request), request.inputs.settings
        )
        return publication_path_a.send(transport, payload, request.inputs.settings)
    if environment.remote is None or evidence.local_commit is None:
        raise _not_eligible("adapter")
    bare = publication_sandbox.verified_remote(environment.remote)
    return publication_path_b.push(request.clone, bare, evidence.local_commit, environment.clock())
