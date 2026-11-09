"""The bodies of ``lovspor ops forarbeider-release policy`` (#738, contract Amendment 1).

Sign, revoke and show the standing release policy. Records go to the same
owner-only workspace as every release record, named by digest and never
replaced. Nothing here reads a chapter or writes a corpus. Printed: digests,
kinds, codes and moments. Never evidence paths, evidence text or handles.

Sign prints every bound digest and the digest of the exact record it will
store before it asks, and the typed phrase carries that record digest, so a
changed byte in any bound file changes what the owner must type. It refuses
while an overlapping policy is unrevoked: at most one is ever effective.
"""

import re
from datetime import datetime
from pathlib import Path

import typer
from pydantic import BaseModel, ConfigDict

from lovspor.forarbeider.commands import aware_moment
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_models import (
    PolicyEvidence,
    PolicyEvidenceKind,
    PolicyFault,
    PolicyObservation,
    PolicyRevocation,
    RegisteredPolicy,
    StandingPolicy,
    overlaps,
    policy_faults,
    suppresses,
)
from lovspor.forarbeider.release_commands import (
    ReleaseScope,
    actor,
    digest_option,
    forbidden_trees,
    interval,
    open_release_workspace,
    require_a_person,
    stored_record,
    validated,
)
from lovspor.forarbeider.release_models import digest, record_text
from lovspor.forarbeider.release_store import (
    MAX_EVIDENCE_BYTES,
    read_file,
    read_named,
    refused_as,
    write_record,
)
from lovspor.forarbeider.staging import private_directory_exists

_EVIDENCE = re.compile(r"(?P<kind>[a-z_]+)=(?P<path>/.+)")
_RECORD_NAME = re.compile(r"[0-9a-f]{64}\.json")
_SCOPE = "flagged chapters still need their own review"


class PolicyGrant(BaseModel):
    """The evidence, the owner and the window a policy is signed for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: tuple[str, ...]
    decided: str
    valid: str


def _evidence_bytes(path: Path, kind: str) -> bytes | None:
    with refused_as(f"evidence {kind}"):
        return read_file(path, forbidden_trees(), MAX_EVIDENCE_BYTES)


def _artifact(text: str) -> PolicyEvidence:
    """``KIND=/absolute/path``; the digest and size are read from the file itself.

    Raises:
        ForarbeiderRefusedError: a malformed option, or a file missing, empty or unsafe.
    """
    match = _EVIDENCE.fullmatch(text)
    if match is None or match["kind"] not in set(PolicyEvidenceKind):
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, "evidence")
    payload = _evidence_bytes(Path(match["path"]), match["kind"])
    if not payload:
        raise ForarbeiderRefusedError(RefusalReason.INPUT_UNREADABLE, "evidence")
    return PolicyEvidence(
        kind=PolicyEvidenceKind(match["kind"]),
        path=match["path"],
        sha256=digest(payload),
        size_bytes=len(payload),
    )


def _policy(grant: PolicyGrant) -> StandingPolicy:
    """The policy as declared; ``release_invalid`` names the fields that are wrong.

    Raises:
        ForarbeiderRefusedError: a malformed option or an incomplete policy.
    """
    artifacts = sorted((_artifact(item) for item in grant.evidence), key=_order)
    owner, moment = actor(grant.decided)
    start, end = interval(grant.valid)
    fields = {
        "schema_version": 1,
        "record_type": "forarbeider_standing_policy",
        "source_provider": "regjeringen.no",
        "document_types": ("prop-l", "prop-ls"),
        "evidence": tuple(artifacts),
        "owner": owner,
        "decided_at": moment,
        "valid_from": start,
        "valid_until": end,
        **_FIXED_POLICY,
    }
    return validated(StandingPolicy, fields)


#: What the record says of itself. No acceptance is here: each is an owner file.
_FIXED_POLICY: dict[str, object] = {
    "clears": "unflagged_chapters",
    "legal_sufficiency_decided_by_code": False,
    "identity_authenticated": False,
}


def _order(artifact: PolicyEvidence) -> int:
    return list(PolicyEvidenceKind).index(artifact.kind)


def _types(policy: StandingPolicy) -> str:
    return ",".join(policy.document_types)


def _preview(policy: StandingPolicy, sha: str) -> str:
    window = f"{policy.valid_from.isoformat()}/{policy.valid_until.isoformat()}"
    lines = [f"policy {sha[:12]} source {policy.source_provider} types {_types(policy)}"]
    lines.append(f"valid {window}")
    lines += [f"evidence {item.kind.value} {item.sha256[:12]}" for item in policy.evidence]
    return "\n".join(lines)


def policy_sign_impl(scope: ReleaseScope, grant: PolicyGrant) -> str:
    """Record the standing policy, at a terminal, and return the line that reports it.

    Raises:
        ForarbeiderRefusedError: any check failed; nothing was written.
    """
    policy = _policy(grant)
    sha = digest(record_text(policy).encode())
    workspace = open_release_workspace(scope)
    _require_no_overlap(workspace, policy, sha)
    scope_text = f"{policy.source_provider} {_types(policy)} until {policy.valid_until.isoformat()}"
    typer.echo(_preview(policy, sha))
    require_a_person(f"sign standing policy {sha[:12]} {scope_text}", "policy")
    stored = write_record(workspace, "policies", policy)
    return (
        f"policy {stored} {scope_text}: "
        f"clears unflagged chapters through a batch only, not permission to publish "
        f"a flagged chapter ({_SCOPE})"
    )


def _require_no_overlap(workspace: Path, policy: StandingPolicy, sha: str) -> None:
    """Refuse while an overlapping policy is unrevoked, or was revoked after this decision.

    Raises:
        ForarbeiderRefusedError: ``policy_overlap``, ``release_already_revoked`` or
            ``release_out_of_order``, naming the stored policy in the way.
    """
    for entry in register(workspace):
        if not overlaps(entry.policy, policy):
            continue
        if entry.revocation is None:
            raise ForarbeiderRefusedError(RefusalReason.POLICY_OVERLAP, entry.sha256)
        if entry.sha256 == sha:
            raise ForarbeiderRefusedError(RefusalReason.RELEASE_ALREADY_REVOKED, sha)
        if suppresses(entry, policy):
            raise ForarbeiderRefusedError(RefusalReason.RELEASE_OUT_OF_ORDER, entry.sha256)


def policy_revoke_impl(scope: ReleaseScope, policy: str, decided: str) -> str:
    """Record that the owner withdrew the policy. A second revocation is refused.

    Raises:
        ForarbeiderRefusedError: an unknown policy, one already revoked, or a
            revocation dated before the policy was decided.
    """
    workspace = open_release_workspace(scope)
    sha = digest_option(policy, "policy")
    record = stored_record(workspace, "policies", sha, StandingPolicy)
    if _revocation(workspace, sha) is not None:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_ALREADY_REVOKED, sha)
    owner, moment = actor(decided)
    if moment < record.decided_at:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_OUT_OF_ORDER, sha)
    revocation = PolicyRevocation(
        schema_version=1,
        record_type="forarbeider_standing_policy_revocation",
        policy_sha256=sha,
        owner=owner,
        revoked_at=moment,
    )
    write_record(workspace, "policy_revocations", revocation, sha)
    return f"revoked policy {sha}"


def _revocation(workspace: Path, sha: str) -> PolicyRevocation | None:
    revocation = read_named(workspace, "policy_revocations", sha, PolicyRevocation)
    if revocation is not None and revocation.policy_sha256 != sha:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_CORRUPT, sha)
    return revocation


def register(workspace: Path) -> tuple[RegisteredPolicy, ...]:
    """Every stored policy with its revocation. A file not named by a digest is corrupt.

    Raises:
        ForarbeiderRefusedError: an edited or misnamed record, or an unsafe folder.
    """
    directory = workspace / "policies"
    with refused_as("policies"):
        if not private_directory_exists(directory):
            return ()
    names = sorted(path.name for path in directory.iterdir() if not path.name.startswith("."))
    if any(_RECORD_NAME.fullmatch(name) is None for name in names):
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_CORRUPT, "policies")
    return tuple(_registered(workspace, name.removesuffix(".json")) for name in names)


def _registered(workspace: Path, sha: str) -> RegisteredPolicy:
    policy = stored_record(workspace, "policies", sha, StandingPolicy)
    return RegisteredPolicy(sha256=sha, policy=policy, revocation=_revocation(workspace, sha))


def _actual(item: PolicyEvidence) -> str | None:
    payload = _evidence_bytes(Path(item.path), item.kind.value)
    return None if payload is None else digest(payload)


def policy_state(
    workspace: Path, sha: str, at: datetime
) -> tuple[StandingPolicy, tuple[PolicyFault, ...]]:
    """The stored policy and every fault it has at ``at``, its evidence and the register read now.

    Every stored policy is read too: an overlapping unrevoked one makes this
    ambiguous, and a later revocation of an overlapping one suppresses it.

    Raises:
        ForarbeiderRefusedError: the policy is not stored, or was edited.
    """
    entry = _registered(workspace, sha)
    evidence = {item.path: _actual(item) for item in entry.policy.evidence}
    observed = PolicyObservation(evidence=evidence, policies=register(workspace))
    return entry.policy, policy_faults(entry, observed, at)


def policy_show_impl(scope: ReleaseScope, policy: str, at: str) -> str:
    """The policy's scope, window, evidence digests and state at ``at``: never paths or handles.

    Raises:
        ForarbeiderRefusedError: an unknown or edited policy, or a malformed option.
    """
    workspace = open_release_workspace(scope)
    sha = digest_option(policy, "policy")
    with refused_as("at"):
        moment = aware_moment(at)
    record, faults = policy_state(workspace, sha, moment)
    window = f"{record.valid_from.isoformat()}/{record.valid_until.isoformat()}"
    lines = [f"policy {sha} {record.source_provider} {_types(record)} valid {window}"]
    lines += [f"evidence {item.kind.value} {item.sha256}" for item in record.evidence]
    state = f"faults {','.join(faults)}" if faults else "usable"
    return "\n".join([*lines, f"state {state}; identity_authenticated=false"])
