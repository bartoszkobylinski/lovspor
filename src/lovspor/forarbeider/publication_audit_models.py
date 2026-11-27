"""AR6c: the report of the independent publication history audit (#738; §9.7 auditor, AR8).

One audit reads remote ``main`` backwards from the head it observes to a
boundary the caller names, and judges every commit in between from the remote
and the trust root alone (:mod:`~lovspor.forarbeider.publication_audit`). The
report is canonical JSON with no time of its own making, so the same remote,
the same inputs and the same audit time give the same bytes.

**What the report never claims.**

- The boundary is **never trusted**: a caller supplies it, alone or with a
  prior report that is itself unsigned. The audit proves only that the
  boundary is an ancestor of the observed head; every audit is ``unanchored``
  until AR7 supplies an independent checkpoint.
- ``verified_window`` means only that every commit between the observed head
  and the boundary is a release whose facts and attestation verify, that the
  walk reached the boundary, and that the head did not move while it was read.
  It proves neither the history before the boundary, nor resistance to a
  rollback, nor the authorization at any publication, nor the quarantine
  source's identity (#1037), nor that the keys were valid when they signed
  (#1040), nor that the attestation tags are protected (#1046).
- Runner claims in an attestation (the transaction, the authorization) stay
  claims: the report names the claimed transaction only to find two
  attestations claiming the same one.

It holds commit ids, digests and reason codes; never a document body, a source
excerpt or anything from the runner's private records beyond those claims.
"""

from typing import Final, Literal

from pydantic import Field

from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.publication_models import REPOSITORY, CommitId
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank

AUDIT_VERSION: Final = "publication-history-audit/1"
Verdict = Literal["verified_window", "anomalies_found", "incomplete"]
CommitClass = Literal["release", "release_invalid", "foreign", "merge", "root"]
AttestationState = Literal["attested", "missing", "invalid", "unverifiable", "not_applicable"]
BoundarySource = Literal["none", "caller_supplied", "prior_audit"]
BoundaryStatus = Literal["reached", "not_reached", "not_given"]

#: What no audit of this slice verifies, on every report whatever its verdict.
NOT_VERIFIED: Final = (
    "ar7_independent_checkpoint",
    "attestation_tag_protection_1046",
    "attestation_tags_of_commits_off_main",
    "authorization_at_publication",
    "budget_settlement",
    "complete_history_before_the_boundary",
    "historical_key_validity_1040",
    "policy_owner_signature",
    "quarantine_source_identity_1037",
    "rollback_resistance",
)


class AuditScope(Frozen):
    """What was audited, against what, and how far; the same for a prior report to count."""

    repository: Literal["bartoszkobylinski/lovverk-forarbeider"] = REPOSITORY
    branch: Literal["main"] = "main"
    path: Literal["A", "B"]
    app_id: int | None = Field(ge=1)
    bot_principal: NonBlank
    trust_root_sha256: Digest
    anchoring: Literal["unanchored"] = "unanchored"
    traversal_limit: int = Field(ge=1)
    audited_at: Instant


class AuditBoundary(Frozen):
    """Where the walk stops; supplied by the caller, so ``trusted`` is always false."""

    commit: CommitId | None
    source: BoundarySource
    trusted: Literal[False] = False
    prior_report_sha256: Digest | None
    status: BoundaryStatus


class AuditedCommit(Frozen):
    """One commit of the window, newest first, as the remote and the trust root judge it."""

    commit: CommitId
    parents: tuple[CommitId, ...]
    classification: CommitClass
    attestation: AttestationState
    release_manifest_sha256: Digest | None = None
    plan_id: Digest | None = None
    batch_id: NonBlank | None = None
    batch_sequence: int | None = Field(default=None, ge=1)
    #: A runner claim in a verified attestation; never a verified fact.
    claimed_transaction_id: Digest | None = None
    reasons: tuple[NonBlank, ...] = ()


class AuditCounts(Frozen):
    examined: int = Field(ge=0)
    releases: int = Field(ge=0)
    attested: int = Field(ge=0)
    missing_attestations: int = Field(ge=0)
    invalid_attestations: int = Field(ge=0)
    unverifiable_attestations: int = Field(ge=0)
    invalid_releases: int = Field(ge=0)
    foreign: int = Field(ge=0)
    merges: int = Field(ge=0)


class AuditReport(Frozen):
    """The audit of one window of remote ``main``; canonical bytes, deterministic."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_history_audit"] = (
        "forarbeider_publication_history_audit"
    )
    audit_version: Literal["publication-history-audit/1"] = AUDIT_VERSION
    mode: Literal["simulation"] = "simulation"
    scope: AuditScope
    boundary: AuditBoundary
    observed_head: CommitId | None
    final_head: CommitId | None
    commits: tuple[AuditedCommit, ...]
    counts: AuditCounts
    anomalies: tuple[NonBlank, ...]
    gaps: tuple[NonBlank, ...]
    verdict: Verdict
    verdict_scope: Literal["this_window_only"] = "this_window_only"
    not_verified: tuple[NonBlank, ...] = NOT_VERIFIED
