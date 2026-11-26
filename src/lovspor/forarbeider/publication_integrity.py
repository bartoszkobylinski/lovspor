"""AR4b-2: the integrity and the authorization of one remote commit (#738; §9.3a 2a, 2b).

**Integrity** asks whether the commit is, byte for byte, the transaction's
publication: one parent, the base; the signature class the path requires
(path A: GitHub verified it, for the pinned App; path B: the bot key the trust
root lists, in the release namespace, at the commit's stated time); the
transaction's message; exactly the proposed files with their SHA-256; and one
release manifest, named by its digest, that binds this transaction's plan,
batch, path and policy and lists every other changed file. The manifest's own
bot signature is AR6: it stays ``unavailable``, waived in simulation only.

**Authorization** asks a different question: did the policy hold at the
publication event? Its evidence is (i) the runner's ``prepared_to_send``
record, (ii) the remote's record of the ref update and (iii) the owner-signed
times of revocations and stops. The owner's records are evaluated *as of* a
moment by keeping only those signed by then:

- a key revoked in the trust root carries no time: ``authorization_unknown``;
- the policy did not hold at the attempt: ``unauthorized``;
- (i) or (ii) missing: ``authorization_unknown``;
- the policy held at the event and at both ends of the clock uncertainty:
  ``authorized``; at none of them: ``unauthorized``; otherwise ``unknown``.

The AR1 verdict, FC7b's pending blockers and the AR5/AR7 anchors stay
unavailable and are listed as waived; nothing unavailable is ever passed. The
classification is a judgement from evidence, never a proof (§9.3a).
"""

from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Final, NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.policy_authorization import (
    AuthorizationRequest,
    SignedRecord,
    verify_authorization,
)
from lovspor.forarbeider.policy_authorization_models import ApprovalRevocation
from lovspor.forarbeider.policy_trust import TrustRoot
from lovspor.forarbeider.publication_authorize import AuthorizationInputs
from lovspor.forarbeider.publication_execution_models import AttemptRecord
from lovspor.forarbeider.publication_models import RELEASE_FILE, ReleaseManifestFile
from lovspor.forarbeider.publication_path_a import body, headline
from lovspor.forarbeider.publication_recovery_models import (
    CLOCK_UNCERTAINTY_SECONDS,
    Authorization,
    IntegrityCheck,
    IntegrityName,
)
from lovspor.forarbeider.publication_remote import RemoteCommit
from lovspor.forarbeider.release_control import ControlRequest, verify_control
from lovspor.forarbeider.release_control_models import ReleaseControl
from lovspor.forarbeider.release_plan_models import ReleasePlan
from lovspor.forarbeider.release_provenance import authentic
from lovspor.forarbeider.version_release_models import PolicyReference, digest_of

#: Evidence AR4b-2 cannot have; listed on every receipt, never passed.
AUTHORIZATION_UNAVAILABLE: Final = (
    "ar1_gate_verdict",
    "ar5_control_anchor",
    "ar5_ledger_anchor",
    "ar7_repository_settings",
    "fc7b_pending_blockers",
)
_UNCERTAINTY = timedelta(seconds=CLOCK_UNCERTAINTY_SECONDS)


class Verdict(NamedTuple):
    authorization: Authorization
    reasons: tuple[str, ...]
    attempt: AttemptRecord | None


# --- integrity ---------------------------------------------------------------------------------


def _check(name: IntegrityName, reasons: list[str]) -> IntegrityCheck:
    return IntegrityCheck(
        name=name, status="failed" if reasons else "passed", reasons=tuple(reasons)
    )


def sealed_plan(inputs: AuthorizationInputs) -> ReleasePlan | None:
    evidence = inputs.plan
    plan = evidence.plan
    sealed = evidence.status == "verified" and authentic(evidence, "batch_plan")
    if plan is None or not sealed or evidence.plan_sha256 != digest_of(plan):
        return None
    return plan


def ours(commit: RemoteCommit, inputs: AuthorizationInputs) -> bool:
    """Whether the commit names this transaction: its headline and body are the transaction's."""
    transaction = inputs.transaction
    return (commit.headline, commit.body) == (headline(transaction), body(transaction))


def _files(commit: RemoteCommit, inputs: AuthorizationInputs) -> list[str]:
    wanted = {item.path: item.sha256 for item in inputs.transaction.content.files}
    if set(commit.files) != set(wanted):
        return ["files_not_the_proposal"]
    found = {path: data for path, data in commit.files.items() if data is not None}
    if any(sha256(found.get(p, b"")).hexdigest() != digest for p, digest in wanted.items()):
        return ["file_bytes_not_the_proposal"]
    return []


def _manifest_file(commit: RemoteCommit, inputs: AuthorizationInputs) -> ReleaseManifestFile | str:
    content = inputs.transaction.content
    releases = [path for path in commit.files if RELEASE_FILE.fullmatch(path)]
    raw = commit.files.get(content.release_manifest_path)
    if releases != [content.release_manifest_path] or raw is None:
        return "manifest_not_the_one_release_file"
    if sha256(raw).hexdigest() != content.release_manifest_sha256:
        return "manifest_digest_mismatch"
    try:
        return ReleaseManifestFile.model_validate_json(raw)
    except ValidationError:
        return "manifest_unreadable"


def _manifest(commit: RemoteCommit, inputs: AuthorizationInputs) -> list[str]:
    found = _manifest_file(commit, inputs)
    if isinstance(found, str):
        return [found]
    content = inputs.transaction.content
    plan = sealed_plan(inputs)
    policy: PolicyReference | None = plan.basis.policy if plan is not None else None
    reasons = []
    bound = (found.publication, found.plan_id, found.batch_id, found.batch_sequence)
    if bound != (content.publication, content.plan_id, content.batch_id, content.batch_sequence):
        reasons.append("manifest_not_this_transaction")
    if policy is None or found.policy != policy:
        reasons.append("manifest_policy_not_the_plans")
    listed = {item.path for item in found.files} | {content.release_manifest_path}
    if listed != set(commit.files):
        reasons.append("changed_files_not_listed_in_manifest")
    return reasons


def integrity_checks(
    commit: RemoteCommit, inputs: AuthorizationInputs
) -> tuple[IntegrityCheck, ...]:
    """§9.3a 2a, every check, each passed, failed or unavailable."""
    base = inputs.transaction.content.publication.base_commit
    message = [] if ours(commit, inputs) else ["message_not_the_transactions"]
    return (
        _check("ancestry", [] if commit.parents == (base,) else ["parent_not_the_base"]),
        _check("signature_class", list(commit.signature_reasons)),
        _check("message", message),
        _check("files", _files(commit, inputs)),
        _check("manifest", _manifest(commit, inputs)),
        IntegrityCheck(
            name="ar6_release_manifest_signature",
            status="unavailable",
            reasons=("not_signed_ar6_pending",),
        ),
    )


# --- authorization -----------------------------------------------------------------------------


def _signed_at(
    record: SignedRecord, model: type[ApprovalRevocation | ReleaseControl]
) -> datetime | None:
    try:
        return model.model_validate_json(record.record).signed_at
    except ValidationError:
        return None


def _known_by(
    records: tuple[SignedRecord, ...], model: type, at: datetime
) -> tuple[SignedRecord, ...]:
    """The records signed by ``at``; one whose time cannot be read is kept, to be refused."""
    kept = []
    for record in records:
        when = _signed_at(record, model)
        if when is None or when <= at:
            kept.append(record)
    return tuple(kept)


def _policy_held(inputs: AuthorizationInputs, root: TrustRoot, at: datetime) -> bool:
    offered: AuthorizationRequest = inputs.policy
    revocations = _known_by(offered.revocations, ApprovalRevocation, at)
    verdict = verify_authorization(replace(offered, checked_at=at, revocations=revocations), root)
    checks = (verdict.policy_approval, verdict.automated_publication, *verdict.revocations)
    plan = sealed_plan(inputs)
    ours_policy = PolicyReference(
        policy_id=verdict.policy_id,
        policy_version=verdict.policy_version,
        policy_sha256=verdict.policy_sha256,
    )
    same = plan is not None and plan.basis.policy == ours_policy
    return same and all(check.status == "verified" for check in checks)


def _not_stopped(inputs: AuthorizationInputs, root: TrustRoot, at: datetime) -> bool:
    plan = sealed_plan(inputs)
    policy_id = plan.basis.policy.policy_id if plan is not None else None
    history = _known_by(inputs.control, ReleaseControl, at)
    return verify_control(ControlRequest(history, policy_id, at), root).state == "not_stopped"


def held_at(inputs: AuthorizationInputs, root: TrustRoot, at: datetime) -> bool:
    """Whether the policy held at ``at``, by the owner's records signed by then."""
    return _policy_held(inputs, root, at) and _not_stopped(inputs, root, at)


def _attempt(records: list[AttemptRecord], inputs: AuthorizationInputs) -> AttemptRecord | None:
    content = inputs.transaction.content
    for record in records:
        same = (record.release_manifest_sha256, record.base_commit) == (
            content.release_manifest_sha256,
            content.publication.base_commit,
        )
        if record.state == "prepared_to_send" and same:
            return record
    return None


def _untimed_revocation(root: TrustRoot) -> bool:
    return any(entry.key_blob in root.revoked_blobs for entry in root.entries)


def _missing(attempt: AttemptRecord | None, event_at: datetime | None) -> tuple[str, ...]:
    absent = (
        ("attempt_record_missing", attempt is None),
        ("remote_event_missing", event_at is None),
    )
    return tuple(reason for reason, gone in absent if gone)


def authorization(
    inputs: AuthorizationInputs,
    root: TrustRoot,
    records: list[AttemptRecord],
    event_at: datetime | None,
) -> Verdict:
    """§9.3a 2b: authorized, unauthorized or unknown, with its reasons."""
    attempt = _attempt(records, inputs)
    if _untimed_revocation(root):
        return Verdict("authorization_unknown", ("untimed_key_revocation",), attempt)
    if attempt is not None and not held_at(inputs, root, attempt.recorded_at):
        return Verdict("unauthorized", ("policy_not_valid_at_attempt",), attempt)
    missing = _missing(attempt, event_at)
    if missing or event_at is None:
        return Verdict("authorization_unknown", missing, attempt)
    samples = [held_at(inputs, root, event_at + k * _UNCERTAINTY) for k in (-1, 0, 1)]
    if all(samples):
        return Verdict("authorized", (), attempt)
    if not any(samples):
        return Verdict("unauthorized", ("policy_not_valid_at_publication",), attempt)
    return Verdict("authorization_unknown", ("within_clock_uncertainty",), attempt)
