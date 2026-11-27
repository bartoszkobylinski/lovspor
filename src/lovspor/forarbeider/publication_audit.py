"""AR6c: the independent, read-only auditor of remote ``main``'s publication history (#738).

**What it is.** A bounded diagnostic slice of the §9.7 independent auditor
(AR8): given the remote, the trust root and the expected settings, it walks
remote ``main`` backwards from the head it observes, by first parent, to a
boundary the caller names, and judges every commit in between. It trusts no
runner receipt, no workspace, no clone and no classification a runner made:
each commit is judged afresh with the AR6b verifiers
(:func:`~lovspor.forarbeider.publication_attestation.derive_facts` for the
commit, its manifest and the manifest's bot signature;
:func:`~lovspor.forarbeider.publication_attestation.read_attestation` for the
attestation), which call the one SSH signature check of
:mod:`~lovspor.forarbeider.policy_trust`. Nothing here verifies a signature or
a manifest by itself.

**The trust contract** (:class:`AuditRequest`, recorded in the report's scope):
the repository, branch and path come from the expected settings; the trust
root is named by its SHA-256; the boundary is a commit the caller supplies,
optionally with the canonical bytes of a prior report that ended there. The
boundary is **never trusted**: the audit proves only that it is an ancestor of
the observed head, and checks a prior report for consistency (same scope, a
``verified_window``, ending at the boundary, made no later than this audit).
A prior report is unsigned, so it is evidence of nothing beyond that. Every
audit is ``unanchored``: no independent pinned checkpoint exists until AR7, so
no report claims full-history coverage or resistance to a rollback.

**Per commit:** a merge, or a root, ends the walk; a commit carrying no
release manifest and no manifest signature is ``foreign``; one that carries
either is a release, ``release_invalid`` when any fact fails (signature class,
parent and base, file set, message, manifest, manifest signature). A valid
release's attestation is ``attested``, ``missing`` (no tag: never attested),
``invalid`` (a tag that fails), or ``unverifiable`` (the store could not be
read). Across the window, two releases of one manifest, one batch id or one
plan sequence are duplicate facts; two attestations claiming one transaction
conflict (a claim, compared, never verified).

**Never clean:** a remote that cannot be read, a commit that cannot be read, a
merge, the traversal limit, a head that moved between the first and the last
read, a boundary not reached, no boundary, an empty window, or a prior report
that does not hold. Anomalies win over gaps in the verdict; both are listed.

**Read-only by construction.** :class:`AuditSource` holds only the three read
callables (``main``'s head, a commit, an attestation tag); the auditor has no
handle that could publish, create or move a tag, write a receipt, set the
latch or settle a budget.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from hashlib import sha256
from typing import Final, NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.publication_attestation import (
    REACH_LIMIT,
    Expected,
    derive_facts,
    public_source,
    read_attestation,
)
from lovspor.forarbeider.publication_attestation_models import RemoteFacts
from lovspor.forarbeider.publication_attestation_store import Stored
from lovspor.forarbeider.publication_audit_models import (
    AttestationState,
    AuditBoundary,
    AuditCounts,
    AuditedCommit,
    AuditReport,
    AuditScope,
    BoundarySource,
    BoundaryStatus,
    Verdict,
)
from lovspor.forarbeider.publication_execute import Simulation
from lovspor.forarbeider.publication_models import RELEASE_FILE, RELEASE_SIGNATURE_FILE
from lovspor.forarbeider.publication_remote import RemoteCommit
from lovspor.forarbeider.version_models import canonical_text

#: The most commits one audit examines before it calls its coverage incomplete.
AUDIT_LIMIT: Final = REACH_LIMIT
_STORE_UNREADABLE: Final = "attestation_store_unreadable"
_COMMIT_ID: Final = re.compile(r"[0-9a-f]{40}")


class AuditSource(NamedTuple):
    """The remote, read-only: ``main``'s head, one commit, one attestation tag."""

    head: Callable[[], str | None]
    commit: Callable[[str], RemoteCommit | None]
    attestation: Callable[[str, datetime], Stored | None]


class AuditRequest(NamedTuple):
    """The audit's boundary: ``boundary`` and ``prior`` are the caller's, never trusted."""

    expected: Expected
    boundary: str | None
    prior: bytes | None = None
    limit: int = AUDIT_LIMIT


class _Walk(NamedTuple):
    commits: tuple[AuditedCommit, ...]
    reached: bool
    gaps: tuple[str, ...]


class _Frame(NamedTuple):
    """What the report states before the walk: scope, boundary and the boundary's own gaps."""

    scope: AuditScope
    commit: str | None
    source: BoundarySource
    prior_sha256: str | None
    gaps: tuple[str, ...]


def audit_source(environment: Simulation, expected: Expected) -> AuditSource:
    """The configured path's remote, read-only, through an adapter proved sandboxed first.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible`` or a sandbox refusal.
    """
    found = public_source(environment, expected)
    return AuditSource(found.main.head, found.main.commit, found.attestations.read)


def audit_remote(environment: Simulation, request: AuditRequest, at: datetime) -> AuditReport:
    """:func:`audit` of the configured remote; a remote that cannot be opened is ``incomplete``."""
    try:
        source = audit_source(environment, request.expected)
    except ForarbeiderRefusedError:
        frame = _frame(request, at)
        return _report(frame, (None, None), _Walk((), False, ("remote_unavailable",)), ())
    return audit(source, request, at)


def audit(source: AuditSource, request: AuditRequest, at: datetime) -> AuditReport:
    """The audit of remote ``main`` from its observed head to ``request.boundary``.

    ``at`` is the audit time: keys are judged at it (their validity windows and
    revocations; whether a key was valid when it signed is #1040).
    """
    frame = _frame(request, at)
    observed = _read(source.head)
    if observed is None:
        return _report(frame, (None, None), _Walk((), False, ("remote_unavailable",)), ())
    walk = _walk(source, request, observed, at)
    final = _read(source.head)
    moved = () if final == observed else (_moved(final),)
    walk = walk._replace(gaps=(*walk.gaps, *moved))
    return _report(frame, (observed, final), walk, _rewritten(frame, request, walk))


def _moved(final: str | None) -> str:
    return (
        "remote_main_unreadable_after_walk" if final is None else "remote_head_moved_during_audit"
    )


def _read[T](action: Callable[[], T]) -> T | None:
    """A remote read; any failure of the remote is no answer, never a verdict."""
    try:
        return action()
    except Exception:
        return None


# --- the boundary -----------------------------------------------------------------------------


def _scope(request: AuditRequest, at: datetime) -> AuditScope:
    root, settings = request.expected
    return AuditScope(
        path=settings.path,
        app_id=settings.app.app_id if settings.app is not None else None,
        bot_principal=settings.bot_principal,
        trust_root_sha256=root.sha256,
        traversal_limit=request.limit,
        audited_at=at,
    )


def _frame(request: AuditRequest, at: datetime) -> _Frame:
    scope = _scope(request, at)
    given = request.boundary
    if given is None:
        return _Frame(scope, None, "none", None, ("no_boundary_given",))
    commit = given if _COMMIT_ID.fullmatch(given) else None
    named = () if commit is not None else ("boundary_not_a_commit_id",)
    if request.prior is None:
        return _Frame(scope, commit, "caller_supplied", None, named)
    digest = sha256(request.prior).hexdigest()
    gaps = (*named, *_prior_gaps(request, scope))
    return _Frame(scope, commit, "prior_audit", digest, gaps)


def _comparable(scope: AuditScope) -> dict[str, object]:
    return scope.model_dump(exclude={"audited_at", "traversal_limit"})


def _prior_gaps(request: AuditRequest, scope: AuditScope) -> list[str]:
    """Why a prior report cannot bound this audit, even as an untrusted boundary."""
    raw = request.prior or b""
    try:
        found = AuditReport.model_validate_json(raw)
    except ValidationError:
        return ["boundary_report_unreadable"]
    checks = (
        ("boundary_report_not_canonical", canonical_text(found).encode() != raw),
        ("boundary_report_other_scope", _comparable(found.scope) != _comparable(scope)),
        ("boundary_report_not_verified", found.verdict != "verified_window"),
        ("boundary_not_the_reports_head", found.observed_head != request.boundary),
        ("boundary_report_after_audit_time", found.scope.audited_at > scope.audited_at),
    )
    return [gap for gap, failed in checks if failed]


def _rewritten(frame: _Frame, request: AuditRequest, walk: _Walk) -> tuple[str, ...]:
    """A consistent prior report whose head the walk passed by, all the way to the root."""
    ended_at_root = bool(walk.commits) and walk.commits[-1].classification == "root"
    if frame.source == "prior_audit" and not frame.gaps and ended_at_root and not walk.reached:
        return ("history_rewritten_since_prior_audit",)
    return ()


# --- the walk ---------------------------------------------------------------------------------


def _walk(source: AuditSource, request: AuditRequest, observed: str, at: datetime) -> _Walk:
    found: list[AuditedCommit] = []
    oid = observed
    while oid != request.boundary:
        if len(found) == request.limit:
            return _Walk(tuple(found), False, ("traversal_limit_reached",))
        commit = _read(lambda oid=oid: source.commit(oid))  # type: ignore[misc]
        if commit is None:
            return _Walk(tuple(found), False, (f"commit_unreadable:{oid}",))
        found.append(_examine(source, commit, request.expected, at))
        if len(commit.parents) != 1:
            return _Walk(tuple(found), False, ())
        oid = commit.parents[0]
    return _Walk(tuple(found), True, ())


def _is_release(commit: RemoteCommit) -> bool:
    return any(
        RELEASE_FILE.fullmatch(path) or RELEASE_SIGNATURE_FILE.fullmatch(path)
        for path in commit.files
    )


def _examine(
    source: AuditSource, commit: RemoteCommit, expected: Expected, at: datetime
) -> AuditedCommit:
    shape = {"commit": commit.oid, "parents": commit.parents, "attestation": "not_applicable"}
    if len(commit.parents) != 1:
        kind = "merge" if commit.parents else "root"
        return AuditedCommit.model_validate(shape | {"classification": kind})
    if not _is_release(commit):
        reasons = tuple(f"commit_signature:{r}" for r in commit.signature_reasons)
        return AuditedCommit.model_validate(
            shape | {"classification": "foreign", "reasons": reasons}
        )
    facts, reasons = derive_facts(commit, expected, at)
    if facts is None:
        invalid = {"classification": "release_invalid", "reasons": reasons}
        return AuditedCommit.model_validate(shape | invalid)
    return _attested(source, facts, expected, at)


def _attested(
    source: AuditSource, facts: RemoteFacts, expected: Expected, at: datetime
) -> AuditedCommit:
    made = {
        "commit": facts.commit,
        "parents": (facts.parent,),
        "classification": "release",
        "release_manifest_sha256": facts.release_manifest_sha256,
        "plan_id": facts.plan_id,
        "batch_id": facts.batch_id,
        "batch_sequence": facts.batch_sequence,
    }
    stored = _stored(source, facts.commit, at)
    if stored is None:
        return _with(made, "missing", ("attestation_missing",))
    if _STORE_UNREADABLE in stored.storage_reasons:
        return _with(made, "unverifiable", stored.storage_reasons)
    found, problems = read_attestation(stored, facts, expected.settings, (expected.root, at))
    if found is None:
        return _with(made, "invalid", problems)
    claimed = {"claimed_transaction_id": found.claims.transaction_id}
    return AuditedCommit.model_validate(made | claimed | {"attestation": "attested"})


def _stored(source: AuditSource, commit: str, at: datetime) -> Stored | None:
    try:
        return source.attestation(commit, at)
    except Exception:
        return Stored(None, None, (_STORE_UNREADABLE,))


def _with(
    made: dict[str, object], state: AttestationState, reasons: tuple[str, ...]
) -> AuditedCommit:
    return AuditedCommit.model_validate(made | {"attestation": state, "reasons": reasons})


# --- the report -------------------------------------------------------------------------------


_ANOMALY: Final = {
    ("release_invalid", "not_applicable"): "release_invalid",
    ("foreign", "not_applicable"): "foreign_commit",
    ("merge", "not_applicable"): "merge_commit",
    ("release", "missing"): "attestation_missing",
    ("release", "invalid"): "attestation_invalid",
}


def _commit_findings(commits: Iterable[AuditedCommit]) -> tuple[list[str], list[str]]:
    anomalies: list[str] = []
    gaps: list[str] = []
    for item in commits:
        name = _ANOMALY.get((item.classification, item.attestation))
        if name is not None:
            anomalies.append(f"{name}:{item.commit}")
        if item.classification == "merge":
            gaps.append(f"traversal_stopped_at_merge:{item.commit}")
        if item.attestation == "unverifiable":
            gaps.append(f"attestation_unverifiable:{item.commit}")
    return anomalies, gaps


def _repeated(name: str, values: Iterable[str | None]) -> list[str]:
    counted = Counter(value for value in values if value is not None)
    return [f"{name}:{value}" for value, seen in sorted(counted.items()) if seen > 1]


def _duplicates(commits: tuple[AuditedCommit, ...]) -> list[str]:
    """Release facts two releases of the window share, and transactions two attestations claim."""
    found = [c for c in commits if c.classification == "release"]
    facts = "duplicate_release_facts"
    return [
        *_repeated(f"{facts}:manifest", (c.release_manifest_sha256 for c in found)),
        *_repeated(f"{facts}:batch_id", (c.batch_id for c in found)),
        *_repeated(f"{facts}:batch_sequence", (f"{c.plan_id}:{c.batch_sequence}" for c in found)),
        *_repeated("conflicting_claims:transaction", (c.claimed_transaction_id for c in found)),
    ]


def _boundary_gaps(frame: _Frame, walk: _Walk) -> list[str]:
    if frame.source == "none":
        return list(frame.gaps)
    gaps = list(frame.gaps)
    if not walk.reached:
        gaps.append("boundary_not_reached")
    elif not walk.commits:
        gaps.append("window_empty")
    return gaps


def _counts(commits: tuple[AuditedCommit, ...]) -> AuditCounts:
    kinds = Counter(c.classification for c in commits)
    states = Counter(c.attestation for c in commits if c.classification == "release")
    return AuditCounts(
        examined=len(commits),
        releases=kinds["release"],
        attested=states["attested"],
        missing_attestations=states["missing"],
        invalid_attestations=states["invalid"],
        unverifiable_attestations=states["unverifiable"],
        invalid_releases=kinds["release_invalid"],
        foreign=kinds["foreign"],
        merges=kinds["merge"],
    )


def _verdict(anomalies: list[str], gaps: list[str]) -> Verdict:
    if anomalies:
        return "anomalies_found"
    return "incomplete" if gaps else "verified_window"


def _boundary(frame: _Frame, walk: _Walk) -> AuditBoundary:
    status: BoundaryStatus = "reached" if walk.reached else "not_reached"
    if frame.source == "none":
        status = "not_given"
    return AuditBoundary(
        commit=frame.commit,
        source=frame.source,
        prior_report_sha256=frame.prior_sha256,
        status=status,
    )


def _report(
    frame: _Frame, heads: tuple[str | None, str | None], walk: _Walk, rewritten: tuple[str, ...]
) -> AuditReport:
    anomalies, commit_gaps = _commit_findings(walk.commits)
    anomalies += [*_duplicates(walk.commits), *rewritten]
    gaps = [*commit_gaps, *walk.gaps, *_boundary_gaps(frame, walk)]
    return AuditReport(
        scope=frame.scope,
        boundary=_boundary(frame, walk),
        observed_head=heads[0],
        final_head=heads[1],
        commits=walk.commits,
        counts=_counts(walk.commits),
        anomalies=tuple(anomalies),
        gaps=tuple(gaps),
        verdict=_verdict(anomalies, gaps),
    )
