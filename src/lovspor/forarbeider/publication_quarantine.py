"""AR5 integration: the publishing pipeline's quarantine, from the anchored chain (#738; §9.9).

AR3's :func:`~lovspor.forarbeider.release_quarantine_run.run_quarantine` judges
whatever ledger bytes its caller hands it, and seals the result. That stays as
it is for independent AR3 use. This module is the narrow adapter at the
publishing boundary, so that no plan, authorization or send rests on a ledger
a caller supplied:

- :func:`witnessed_quarantine` reads the ledger only through
  :func:`~lovspor.forarbeider.quarantine_succession.read_quarantine` (the
  AR5d-4 chain verified against the witness, its genesis agreed with the
  ``quarantine_ledger`` anchor, every transition replayed with the real AR3
  engine), then verifies **now** the owner exceptions its newest entry carries:
  every clearance must still be granted by a currently valid, unrevoked owner
  exception for the exact policy (``settle`` is a fixed point at the check
  time). An expired or revoked clearance, a clock before the ledger's newest
  event, a pending, rolled-back, diverged, unanchored or unavailable chain, or
  a policy the reader lacks refuses.
- :func:`plan_witnessed` runs the AR3 engine on that ledger, never on caller
  bytes, and lets the AR2 planner plan only when the run leaves the anchored
  ledger exactly as it is. A run that would change it (new findings, a new or
  lapsed clearance, an exclusion) returns ``successor_required`` with the
  successor and the exception material to anchor first
  (:func:`~lovspor.forarbeider.quarantine_succession.append_successor`); no
  plan is made from an unanchored ledger.
- :func:`quarantine_reasons` is what authorization (``ar5_quarantine_anchor``)
  and the final check before a send ask: the quarantine the transaction's
  sealed plan honours is exactly the set of the anchored head, read now, for
  the plan's policy.

The quarantine chain is anchored on the budget ledger's witness when the
ledger names one (as the transaction's attempts are, AR5d-3), else on the
request's own: one chain per repository and ledger id, shared by every
workspace. Nothing here writes, except that the caller decides to anchor.
"""

from datetime import datetime
from typing import Literal, NamedTuple

from lovspor.forarbeider.control_plane_anchor import AnchorAuthority
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.quarantine_succession import (
    QuarantineStore,
    VerifiedQuarantine,
    policies_of,
    read_quarantine,
)
from lovspor.forarbeider.release_contract_models import PlanEvidence
from lovspor.forarbeider.release_limits import utc
from lovspor.forarbeider.release_quarantine import latest_event, quarantine_set, settle
from lovspor.forarbeider.release_quarantine_models import NO_QUARANTINE, QuarantineSet
from lovspor.forarbeider.release_quarantine_run import (
    ExceptionMaterial,
    QuarantineOutcome,
    QuarantineRequest,
    run_quarantine,
    verify_exceptions,
)
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.version_release_models import PolicyReference, digest_of


class WitnessedSource(NamedTuple):
    """Where the anchored quarantine lives; its policies are matched by digest only."""

    store: QuarantineStore
    #: Every policy the chain's entries name, to replay and re-verify them.
    policies: tuple[bytes, ...] = ()


class WitnessedQuarantine(NamedTuple):
    """The anchored head, re-verified now, and the set the planner honours."""

    verified: VerifiedQuarantine
    found: QuarantineSet
    #: The newest entry's exceptions and the policy bytes they are judged under.
    material: ExceptionMaterial
    policy: bytes | None


class WitnessedPlan(NamedTuple):
    status: Literal["planned", "successor_required", "halted"]
    outcome: QuarantineOutcome
    anchored: VerifiedQuarantine
    #: What an anchored successor needs: the head's exceptions and the offered ones.
    material: ExceptionMaterial


def _refused(detail: str) -> ForarbeiderRefusedError:
    reason = RefusalReason.RELEASE_NOT_ELIGIBLE
    return ForarbeiderRefusedError(reason, f"quarantine_witness:{detail}")


def _head_material(
    verified: VerifiedQuarantine, source: WitnessedSource
) -> tuple[ExceptionMaterial, bytes | None]:
    head = verified.head
    if head is None:
        return ExceptionMaterial(), None
    material = ExceptionMaterial(
        tuple(item.signed() for item in head.exceptions),
        tuple(item.signed() for item in head.revocations),
    )
    if head.policy_sha256 is None:
        return material, None
    policy = policies_of(*source.policies).get(head.policy_sha256)
    # Defensive: ``read_quarantine`` has replayed this entry, which already refuses
    # an entry whose exceptions name a policy the reader lacks.
    if policy is None:
        raise _refused("policy_unavailable")
    return material, policy


def _anchored(
    source: WitnessedSource, repository: str, authority: AnchorAuthority
) -> tuple[VerifiedQuarantine, ExceptionMaterial, bytes | None]:
    """The verified, replayed head, with the exceptions its newest entry carries."""
    verified = read_quarantine(source.store, repository, authority, policies_of(*source.policies))
    material, policy = _head_material(verified, source)
    return verified, material, policy


def witnessed_quarantine(
    source: WitnessedSource, repository: str, authority: AnchorAuthority, at: datetime
) -> WitnessedQuarantine:
    """The anchored quarantine ledger, its clearances re-verified at ``at``.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible`` with
            ``quarantine_succession:<why>`` (the chain is not verified or does not
            replay) or ``quarantine_witness:<why>`` (``clock_regressed``,
            ``policy_unavailable``, ``exception:<halt>``, ``clearance_not_current``);
            ``invalid_timestamp`` for a check time that is not UTC.
    """
    at = utc(at)
    verified, material, policy = _anchored(source, repository, authority)
    ledger = verified.ledger
    latest = latest_event(ledger)
    if latest is not None and at < latest:
        raise _refused("clock_regressed")
    verdict = verify_exceptions(material, policy, at, authority.root)
    if verdict.halts:
        raise _refused("exception:" + ",".join(sorted({item.code for item in verdict.halts})))
    if settle(ledger, verdict.verified, at) != ledger:
        raise _refused("clearance_not_current")
    return WitnessedQuarantine(verified, quarantine_set(ledger), material, policy)


def plan_witnessed(
    request: QuarantineRequest,
    source: WitnessedSource,
    repository: str,
    authority: AnchorAuthority,
) -> WitnessedPlan:
    """The AR3 run and AR2 plan on the anchored ledger; a changed ledger is anchored first.

    ``request.ledger`` must be ``None``: the ledger is read from the witnessed
    chain, never taken from the caller. The exceptions the run judges are the
    anchored head's and the ones offered, so a clearance the head relies on is
    kept only while it is still valid now: one that lapsed, or was revoked,
    is reinstated, and that changed ledger is a ``successor_required``. A
    forged or untrusted exception, or a clock before the ledger's newest
    event, halts the AR3 run (``halted``).

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible`` with
            ``quarantine_witness:caller_ledger_refused``, ``quarantine_witness:policy_unavailable``
            or ``quarantine_succession:<why>`` (the chain is not verified or does not replay).
    """
    if request.ledger is not None:
        raise _refused("caller_ledger_refused")
    anchored, material, _ = _anchored(source, repository, authority)
    offered = ExceptionMaterial(
        (*material.records, *request.exceptions.records),
        (*material.revocations, *request.exceptions.revocations),
    )
    held = canonical_text(anchored.ledger).encode()
    outcome = run_quarantine(request._replace(ledger=held, exceptions=offered), authority.root)
    if outcome.status == "halted":
        return WitnessedPlan("halted", outcome, anchored, offered)
    if outcome.ledger != anchored.ledger:
        unplanned = PlanEvidence(
            status="invalid", source=None, reasons=("plan_quarantine_successor_unanchored",)
        )
        held_back = outcome._replace(quarantine=NO_QUARANTINE, plan=unplanned)
        return WitnessedPlan("successor_required", held_back, anchored, offered)
    return WitnessedPlan("planned", outcome, anchored, offered)


class QuarantineClaim(NamedTuple):
    """What a transaction's evidence says of its quarantine, to be held against the chain."""

    repository: str
    #: Every digest the evidence names for its quarantine (the sealed assessment's, the plan's).
    bound: tuple[str | None, ...]
    #: The plan's policy, which the head's exceptions must have been judged under.
    policy: PolicyReference | None


def quarantine_reasons(
    held: tuple[WitnessedSource | None, AnchorAuthority | None],
    claim: QuarantineClaim,
    at: datetime,
) -> tuple[str, ...]:
    """Why ``claim`` is not the anchored head's quarantine now; empty when it is."""
    source, authority = held
    repository, bound, policy = claim
    if source is None:
        return ("quarantine_source_missing",)
    if authority is None:
        return ("anchor_authority_missing",)
    try:
        witnessed = witnessed_quarantine(source, repository, authority, at)
    except ForarbeiderRefusedError as error:
        return (error.detail,)
    reasons = []
    if not bound or any(item != digest_of(witnessed.found) for item in bound):
        reasons.append("quarantine_not_the_anchored_head")
    head = witnessed.verified.head
    named = None if head is None else head.policy_sha256
    if named is not None and (policy is None or policy.policy_sha256 != named):
        reasons.append("quarantine_policy_not_the_plans")
    return tuple(reasons)
