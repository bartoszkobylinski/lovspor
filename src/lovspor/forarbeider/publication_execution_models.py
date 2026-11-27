"""AR4b-1 contracts: publication authorization evidence and attempt records (#738; §9.3 step 5).

**Authorization evidence** (``publication-authorization/1``) is what the
engine found when it checked one exact publication attempt, immediately
before the mutation. It binds:

- the attempt id, the transaction, plan, batch and release manifest;
- every proposed file with its SHA-256, and their digest together;
- the publication path and the expected base commit, and the remote ``main``
  read at that moment;
- the policy, the trust root, the publisher and its key;
- the STOP/RESUME state, the quarantine set, the limits and the budget
  reservation;
- the check time and every check's result.

Each check is ``passed``, ``failed`` or ``unavailable``. **Unavailable is
never passed.** The decision follows from the checks alone; no caller field
can set it:

- ``denied``: any check failed, or the mode is production. Production needs
  every check passed, and the AR5 anchors and the AR7 settings check
  (:data:`AR5_TO_AR7_CHECKS`) have no production implementation, so production
  is always denied today (AR6a's manifest signature is verified in both modes);
- ``simulation_only``: nothing failed and every unavailable check is in
  :data:`WAIVABLE_IN_SIMULATION`, each listed in ``waived``. It permits
  execution against a test adapter only (a stub GraphQL endpoint, a local bare
  remote); ``production_authorized`` is ``False`` by type.

The evidence carries the authorizing verifier's seal: one built or edited
outside it is refused.

**Attempt records** are append-only and hash-linked, one folder per
transaction: ``denied``, ``prepared_to_send`` (persisted *before* any
external mutation), then exactly one of ``published``, ``refused`` (provably
nothing published) or ``outcome_unknown``. A ``prepared_to_send`` with
nothing after it, or an ``outcome_unknown``, means recovery is required:
nothing is ever sent again blindly.
"""

from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.publication_models import CommitId, ProposedFile
from lovspor.forarbeider.release_contract_models import BudgetUsage
from lovspor.forarbeider.release_provenance import Provenance
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank
from lovspor.forarbeider.version_release_models import PolicyReference

AUTHORIZATION_VERSION: Final = "publication-authorization/1"
#: How long an authorization stays fresh for its attempt.
FRESH_SECONDS: Final = 120

CheckName = Literal[
    "proposal",
    "plan",
    "quarantine",
    "trust_root",
    "policy",
    "publisher",
    "limits",
    "stop_control",
    "stop_latch",
    "budget",
    "remote_head",
    "path_identity",
    "bot_signature",
    "ar1_gate_verdict",
    "fc7b_pending_blockers",
    "ar5_control_anchor",
    "ar5_ledger_anchor",
    "ar5_quarantine_anchor",
    "ar6_release_manifest_signature",
    "ar7_repository_settings",
]
#: The checks AR5-AR7 make. The AR5 anchors are implemented for simulation and
#: are no longer waivable; in production they stay unavailable until AR7. The
#: AR6a manifest signature is verified in both modes and never waived.
AR5_TO_AR7_CHECKS: Final[tuple[CheckName, ...]] = (
    "ar5_control_anchor",
    "ar5_ledger_anchor",
    "ar5_quarantine_anchor",
    "ar6_release_manifest_signature",
    "ar7_repository_settings",
)
#: Checks a simulation may waive, each listed; anything else unavailable denies.
WAIVABLE_IN_SIMULATION: Final[frozenset[str]] = frozenset(
    {*AR5_TO_AR7_CHECKS, "ar1_gate_verdict", "fc7b_pending_blockers"}
    - {
        "ar5_control_anchor",
        "ar5_ledger_anchor",
        "ar5_quarantine_anchor",
        "ar6_release_manifest_signature",
    }
)
CheckStatus = Literal["passed", "failed", "unavailable"]
Mode = Literal["production", "simulation"]


class AuthorizationCheck(Frozen):
    name: CheckName
    status: CheckStatus
    reasons: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def _explained(self) -> Self:
        if (self.status == "passed") == bool(self.reasons):
            raise ValueError("a check has reasons exactly when it did not pass")
        return self


class AppIdentity(Frozen):
    """The GitHub App a path-A publication must be made by, pinned in the settings."""

    app_id: int = Field(ge=1)
    slug: NonBlank
    installation_id: int = Field(ge=1)


class PublicationSettings(Frozen):
    """The expected configuration; AR7 verifies it against GitHub, nothing does here."""

    path: Literal["A", "B"]
    repository: Literal["bartoszkobylinski/lovverk-forarbeider"] = (
        "bartoszkobylinski/lovverk-forarbeider"
    )
    branch: Literal["main"] = "main"
    app: AppIdentity | None
    bot_principal: NonBlank
    source: Literal["unverified_ar7_pending"] = "unverified_ar7_pending"

    @model_validator(mode="after")
    def _one_path(self) -> Self:
        if (self.path == "A") != (self.app is not None):
            raise ValueError("path A names its App; path B names none")
        return self


class AuthorizationEvidence(Frozen):
    """What the engine found for one attempt, just before sending; sealed by its verifier."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_authorization"] = (
        "forarbeider_publication_authorization"
    )
    authorization_version: Literal["publication-authorization/1"] = AUTHORIZATION_VERSION
    mode: Mode
    attempt_id: Digest
    transaction_id: Digest
    plan_id: Digest
    batch_id: NonBlank
    release_manifest_path: NonBlank
    release_manifest_sha256: Digest
    files: tuple[ProposedFile, ...]
    proposal_sha256: Digest
    path: Literal["A", "B"]
    base_commit: CommitId
    remote_head: str | None
    local_commit: CommitId | None
    policy: PolicyReference | None
    trust_root_sha256: Digest
    publisher_principal: NonBlank
    publisher_fingerprint: str | None
    control_state: Literal["stopped", "not_stopped"]
    control_head_sha256: Digest | None
    #: AR5b: the stop latch's newest transition when authorized; ``None`` for no transition.
    stop_latch_head_sha256: Digest | None = None
    #: AR6a: the detached manifest signature the evidence was issued on; ``None`` for none.
    release_manifest_signature_sha256: Digest | None = None
    quarantine_sha256: Digest | None
    limits_sha256: Digest | None
    budget_state: str
    budget_reserved: BudgetUsage | None
    checked_at: Instant
    checks: tuple[AuthorizationCheck, ...]
    decision: Literal["denied", "simulation_only"]
    denied_by: tuple[NonBlank, ...]
    waived: tuple[NonBlank, ...]
    production_authorized: Literal[False] = False
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def _decided_by_the_checks(self) -> Self:
        if (self.decision, self.denied_by, self.waived) != decide(self.checks, self.mode):
            raise ValueError("the decision follows from the checks and the mode alone")
        return self


def decide(
    checks: tuple[AuthorizationCheck, ...], mode: Mode
) -> tuple[Literal["denied", "simulation_only"], tuple[str, ...], tuple[str, ...]]:
    """The decision, what denied it and what a simulation waived; unavailable never passes."""
    failed = sorted(c.name for c in checks if c.status == "failed")
    unavailable = sorted(c.name for c in checks if c.status == "unavailable")
    if mode == "production":
        blocking = sorted({*failed, *unavailable})
        return "denied", tuple(blocking or ["production_execution_disabled"]), ()
    blocking = sorted({*failed, *(n for n in unavailable if n not in WAIVABLE_IN_SIMULATION)})
    if blocking or not checks:
        return "denied", tuple(blocking or ["no_checks"]), ()
    return "simulation_only", (), tuple(unavailable)


AttemptState = Literal["denied", "prepared_to_send", "published", "refused", "outcome_unknown"]


class AttemptRecord(Frozen):
    """One append-only step of one transaction's publication attempts."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_attempt"] = "forarbeider_publication_attempt"
    transaction_id: Digest
    release_manifest_sha256: Digest
    base_commit: CommitId
    path: Literal["A", "B"]
    attempt_id: Digest
    sequence: int = Field(ge=1)
    state: AttemptState
    recorded_at: Instant
    authorization_sha256: Digest | None
    commit: CommitId | None
    reasons: tuple[NonBlank, ...] = ()
    previous_sha256: Digest | None

    @model_validator(mode="after")
    def _linked(self) -> Self:
        if (self.sequence == 1) != (self.previous_sha256 is None):
            raise ValueError("only the first record has no previous record")
        if self.state == "published" and self.commit is None:
            raise ValueError("a publication names its commit")
        return self


class StopLatch(Frozen):
    """The AR4b-1 single-file latch; AR5b reads it as set for good and writes it no more."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_stop_latch"] = (
        "forarbeider_publication_stop_latch"
    )
    reasons: tuple[NonBlank, ...] = Field(min_length=1)
    set_at: Instant
    transaction_id: Digest


class LatchEvent(Frozen):
    """One append-only transition of the stop latch (AR5b): ``set``, then ``cleared``, then...

    A ``set`` names why (a STOP, a revocation, an unknown outcome, a halt). A
    ``cleared`` names the owner-signed RESUME it rests on and the verified
    resolution of the cause; the ``set`` before it stays, as evidence.
    """

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_stop_latch_event"] = (
        "forarbeider_publication_stop_latch_event"
    )
    sequence: int = Field(ge=1)
    previous_sha256: Digest | None
    kind: Literal["set", "cleared"]
    at: Instant
    transaction_id: Digest
    reasons: tuple[NonBlank, ...] = Field(min_length=1)
    resume_control_sha256: Digest | None = None

    @model_validator(mode="after")
    def _linked(self) -> Self:
        if (self.sequence == 1) != (self.previous_sha256 is None):
            raise ValueError("only the first event has no previous event")
        if (self.kind == "cleared") != (self.resume_control_sha256 is not None):
            raise ValueError("exactly a clearing names the RESUME it rests on")
        return self
