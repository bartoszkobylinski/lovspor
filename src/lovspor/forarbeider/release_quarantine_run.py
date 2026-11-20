"""AR3: one quarantine run, from verified evidence to a re-planned release (#738; §9.4, §9.6).

:func:`run_quarantine` takes the AR2 plan request, the policy bytes, the
previous ledger's bytes, the owner exceptions and revocations a source offers,
the failures later stages report, a check time and the trust root:

1. it rebuilds the AR2 plan without any quarantine (the *base*), from the
   verified inventory, limits and FC8b selections;
2. it attributes every failure (:mod:`lovspor.forarbeider.release_attribution`);
   two findings of one quarantine id that disagree on anything but their
   evidence (another policy or category) are a global failure
   (``quarantine_finding_conflict``): neither is picked by input order;
3. it verifies every offered exception (:func:`verify_exceptions`);
4. **any global failure halts the run**: nothing is merged, nothing is
   re-planned, the plan is ``invalid`` and the previous ledger is returned
   unchanged. A halt is reported for the kill switch to act on (AR5); this
   module writes no stop record;
5. otherwise it merges the local findings into the ledger, settles them under
   the verified exceptions, seals the active quarantine set for this exact
   base plan, and asks the AR2 planner for the plan that honours it.

Exceptions are verified like every owner record (OpenSSH signature, the
owner role, the quarantine namespace, the key window at the check time, the
signer's principal), then for the exact policy, inside their window and not
revoked by a verified revocation. A record that fails its *signature* checks
(a forgery, a bot key, a revoked or expired key, another namespace) halts the
run: someone offered an exception the owner did not sign. One that is signed
but expired, premature, revoked or for another policy is only rejected, and
its quarantine stays. An unverifiable revocation halts the run.

Nothing here writes, signs or publishes; the returned ledger is for the
runner's private workspace.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Literal, NamedTuple

from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_authorization import SignedRecord, _scope_reasons
from lovspor.forarbeider.policy_authorization_models import ApprovalRevocation
from lovspor.forarbeider.policy_trust import POLICY_NAMESPACE, QUARANTINE_NAMESPACE, TrustRoot
from lovspor.forarbeider.publication_policy import PublicationPolicy, parse_policy
from lovspor.forarbeider.release_attribution import StageFailure, attribute_plan
from lovspor.forarbeider.release_contract_models import PlanEvidence
from lovspor.forarbeider.release_limits import utc
from lovspor.forarbeider.release_plan import (
    PlanRequest,
    Prepared,
    build_plan,
    plan_release,
    prepare_plan,
)
from lovspor.forarbeider.release_plan_models import ReleasePlan
from lovspor.forarbeider.release_provenance import issue_provenance
from lovspor.forarbeider.release_quarantine import (
    EMPTY_LEDGER,
    VerifiedException,
    finding_conflicts,
    latest_event,
    merge,
    metrics_of,
    parse_ledger,
    quarantine_set,
    settle,
)
from lovspor.forarbeider.release_quarantine_models import (
    NO_QUARANTINE,
    GlobalCategory,
    GlobalFailure,
    QuarantineEvidence,
    QuarantineException,
    QuarantineLedger,
    QuarantineMetrics,
)
from lovspor.forarbeider.release_signed import OwnerCheck, RecordSpec, verify_owner_record
from lovspor.forarbeider.version_release_models import digest_of

_EXCEPTION = RecordSpec(QuarantineException, QUARANTINE_NAMESPACE)
_REVOCATION = RecordSpec(ApprovalRevocation, POLICY_NAMESPACE)
#: Why the AR2 planner could not start, and whose failure that is.
_PLAN_FAILURES: dict[str, GlobalCategory] = {
    "plan_inventory_unverified": "trust",
    "plan_limits_unverified": "trust",
    "plan_policy_mismatch": "policy",
    "plan_trust_root_conflict": "trust",
    "plan_offers_refused": "source_wide",
    "plan_inputs_incomplete": "unknown",
}


class ExceptionMaterial(NamedTuple):
    """What an exception source offers: signed exceptions and signed revocations."""

    records: tuple[SignedRecord, ...] = ()
    revocations: tuple[SignedRecord, ...] = ()


class ExceptionVerdict(NamedTuple):
    verified: tuple[VerifiedException, ...]
    rejected: tuple[str, ...]
    halts: tuple[GlobalFailure, ...]


class QuarantineRequest(NamedTuple):
    """One run's inputs; none of them is trusted until verified."""

    plan: PlanRequest
    policy: bytes | None
    ledger: bytes | None
    checked_at: datetime
    exceptions: ExceptionMaterial = ExceptionMaterial()
    reported: tuple[StageFailure, ...] = ()


class QuarantineOutcome(NamedTuple):
    """The run: re-planned, or halted by a global failure with the ledger untouched.

    ``ledger`` is the ledger to keep: the new one after a re-plan, the previous
    one after a halt, and ``None`` when the previous bytes could not be read,
    so that nothing ever replaces an unreadable ledger with an empty one.
    """

    status: Literal["replanned", "halted"]
    halts: tuple[GlobalFailure, ...]
    ledger: QuarantineLedger | None
    quarantine: QuarantineEvidence
    plan: PlanEvidence
    metrics: QuarantineMetrics | None
    rejected_exceptions: tuple[str, ...]


class _Run(NamedTuple):
    request: QuarantineRequest
    root: TrustRoot
    at: datetime


def run_quarantine(request: QuarantineRequest, root: TrustRoot) -> QuarantineOutcome:
    """Quarantine what is proven local, halt on anything else, re-plan the rest.

    Raises:
        ForarbeiderRefusedError: ``invalid_timestamp`` when the check time is not UTC.
    """
    run = _Run(request, root, utc(request.checked_at))
    ledger = _loaded(request.ledger)
    if ledger is None:
        return _halted(None, [_failure("quarantine_ledger_malformed", "repository")])
    prepared = prepare_plan(request.plan, root)
    if not isinstance(prepared, Prepared):
        failures = [_failure(code, _PLAN_FAILURES.get(code, "unknown")) for code in prepared]
        return _halted(ledger, failures)
    base = build_plan(prepared.context, prepared.rows, request.plan.artifacts)
    attributed = attribute_plan(base, prepared.rows, request.plan.artifacts, request.reported)
    verdict = verify_exceptions(request.exceptions, request.policy, run.at, root)
    halts = [*attributed.halts, *verdict.halts, *_clock(ledger, run.at)]
    if finding_conflicts(attributed.local):
        halts.append(_failure("quarantine_finding_conflict", "unknown"))
    if halts:
        return _halted(ledger, halts, (base, prepared), verdict.rejected)
    merged = merge(ledger, attributed.local, run.at, base.plan_id)
    settled = settle(merged, verdict.verified, run.at)
    return _replanned(run, settled, (base, prepared), verdict.rejected)


def _loaded(raw: bytes | None) -> QuarantineLedger | None:
    if raw is None:
        return EMPTY_LEDGER
    try:
        return parse_ledger(raw)
    except ForarbeiderRefusedError:
        return None


def _clock(ledger: QuarantineLedger, at: datetime) -> list[GlobalFailure]:
    latest = latest_event(ledger)
    if latest is not None and at < latest:
        return [_failure("quarantine_clock_regressed", "repository")]
    return []


def _failure(code: str, category: GlobalCategory) -> GlobalFailure:
    return GlobalFailure(code=code, category=category)


def _halted(
    ledger: QuarantineLedger | None,
    halts: Iterable[GlobalFailure],
    built: tuple[ReleasePlan, Prepared] | None = None,
    rejected: tuple[str, ...] = (),
) -> QuarantineOutcome:
    ordered = tuple(sorted(set(halts), key=lambda item: (item.document_id or "", item.code)))
    plan = PlanEvidence(status="invalid", source=None, reasons=("plan_halted_global_failure",))
    metrics = metrics_of(built[0], built[1].rows, ledger) if built and ledger else None
    return QuarantineOutcome("halted", ordered, ledger, NO_QUARANTINE, plan, metrics, rejected)


def _replanned(
    run: _Run,
    ledger: QuarantineLedger,
    built: tuple[ReleasePlan, Prepared],
    rejected: tuple[str, ...],
) -> QuarantineOutcome:
    base, prepared = built
    found = quarantine_set(ledger)
    evidence = QuarantineEvidence(
        status="verified",
        source="engine_quarantine",
        reasons=(),
        quarantine=found,
        quarantine_sha256=digest_of(found),
        base_plan_id=base.plan_id,
        inventory_sha256=prepared.context.inventory_sha256,
        limits_sha256=prepared.context.limits_sha256,
        trust_root_sha256=run.root.sha256,
    )
    sealed = issue_provenance(evidence, "quarantine_assessment")
    plan = plan_release(run.request.plan._replace(quarantine=sealed), run.root)
    metrics = metrics_of(plan.plan, prepared.rows, ledger) if plan.plan else None
    return QuarantineOutcome("replanned", (), ledger, sealed, plan, metrics, rejected)


# --- owner exceptions ------------------------------------------------------------------------


def verify_exceptions(
    material: ExceptionMaterial, policy: bytes | None, at: datetime, root: TrustRoot
) -> ExceptionVerdict:
    """Every offered exception, verified; a signature failure or unverifiable revocation halts."""
    if not material.records:
        return ExceptionVerdict((), (), ())
    revocations = [verify_owner_record(root, r, _REVOCATION, at) for r in material.revocations]
    if any(check.status != "verified" for check in revocations):
        return ExceptionVerdict((), (), (_failure("quarantine_revocation_not_verified", "trust"),))
    if policy is None:
        return ExceptionVerdict((), ("exception_policy_unavailable",), ())
    revoked = _revoked(revocations)
    checks = [(verify_owner_record(root, r, _EXCEPTION, at), r) for r in material.records]
    if any(check.status != "verified" for check, _ in checks):
        halt = _failure("quarantine_exception_untrusted", "trust")
        return ExceptionVerdict((), _reasons(c for c, _ in checks), (halt,))
    return _judged(checks, _Judge(parse_policy(policy), at, revoked))


class _Judge(NamedTuple):
    policy: PublicationPolicy
    at: datetime
    revoked: frozenset[str]


def _revoked(revocations: Iterable[OwnerCheck]) -> frozenset[str]:
    found: set[str] = set()
    for check in revocations:
        if isinstance(check.record, ApprovalRevocation):
            found.update(check.record.revokes)
    return frozenset(found)


def _reasons(checks: Iterable[OwnerCheck]) -> tuple[str, ...]:
    return tuple(sorted({reason for check in checks for reason in check.reasons}))


def _judged(checks: list[tuple[OwnerCheck, SignedRecord]], judge: _Judge) -> ExceptionVerdict:
    accepted: list[VerifiedException] = []
    rejected: set[str] = set()
    for check, _ in checks:
        record = check.record
        if not isinstance(record, QuarantineException) or check.record_sha256 is None:
            rejected.add("exception_unverifiable")
            continue
        reasons = _own_reasons(record, judge)
        if reasons:
            rejected.update(reasons)
            continue
        accepted.append(VerifiedException(record, check.record_sha256))
    ordered = tuple(sorted(accepted, key=lambda item: item.sha256))
    return ExceptionVerdict(ordered, tuple(sorted(rejected)), ())


def _own_reasons(record: QuarantineException, judge: _Judge) -> list[str]:
    reasons = _scope_reasons(record.scope, judge.policy)
    if judge.at < record.valid_from:
        reasons.append("exception_not_yet_valid")
    if judge.at >= record.valid_until:
        reasons.append("exception_expired")
    if record.exception_id in judge.revoked:
        reasons.append("exception_revoked")
    return reasons
