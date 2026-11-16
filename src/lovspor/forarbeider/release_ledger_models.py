"""AR1b records: the budget ledger's accounting entries (#738; §9.9 AR1, AR5).

A ``LedgerEntry`` is one immutable, hash-linked accounting record:

- ``reserve``: plans a release's batch against its day's budgets before any
  publication call; the amounts count against the budget at once;
- ``commit``: settles that reservation after publication, with amounts never
  above the reservation; the release is then spent and can never be reserved
  again;
- ``cancel``: releases an open reservation; the release cannot be reserved
  again either, so a retry cannot reopen a budget it gave back;
- ``reconcile``: states a day's settled totals as observed independently (for
  AR5, from the published history); they must equal the ledger's own.

Every entry binds its ledger id, its sequence, the SHA-256 of the exact bytes
of the entry before it, its day, the release id and batch id it accounts for
(none for ``reconcile``), its resource amounts, the SHA-256 of the signed
limits it was checked against and the time it was recorded.

A retry of an operation already in the ledger is idempotent: the planner
answers ``already_applied`` and appends nothing. A conflicting operation is
refused. **A local hash chain proves neither append-only storage nor
concurrency safety**: a writer can rewrite or truncate the whole chain, and two
writers can fork it. That is why ledger evidence stays ``unanchored`` and the
gates never pass it until AR5 provides :data:`AR5_LEDGER_REQUIREMENTS`.
"""

from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.release_contract_models import (
    LEDGER_REQUIREMENTS as AR5_LEDGER_REQUIREMENTS,
)
from lovspor.forarbeider.release_contract_models import BudgetUsage
from lovspor.forarbeider.version_models import Digest, Frozen

__all__ = ["AR5_LEDGER_REQUIREMENTS", "LedgerEntry", "LedgerOperation", "LedgerPlan"]

Operation = Literal["reserve", "commit", "cancel", "reconcile"]
_Id = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")


class LedgerOperation(Frozen):
    """One accounting operation a runner asks to record; not yet an entry."""

    operation: Operation
    ledger_id: str = _Id
    day: date
    release_id: Digest | None
    batch_id: str | None = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    amounts: BudgetUsage
    recorded_at: Instant

    @model_validator(mode="after")
    def _bound(self) -> Self:
        unbound = self.release_id is None and self.batch_id is None
        bound = self.release_id is not None and self.batch_id is not None
        if self.operation == "reconcile" and not unbound:
            raise ValueError("a reconciliation covers a whole day, no release")
        if self.operation != "reconcile" and not bound:
            raise ValueError("an accounting operation names its release and batch")
        if self.recorded_at.date() < self.day:
            raise ValueError("an operation is not recorded before its day")
        return self


class LedgerEntry(LedgerOperation):
    """One immutable, hash-linked ledger record."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_budget_ledger_entry"] = "forarbeider_budget_ledger_entry"
    sequence: int = Field(ge=1)
    previous_sha256: Digest | None
    limits_sha256: Digest

    @model_validator(mode="after")
    def _linked(self) -> Self:
        if (self.sequence == 1) != (self.previous_sha256 is None):
            raise ValueError("only the first entry has no previous entry")
        return self


class LedgerPlan(Frozen):
    """The planner's answer: an entry to append, nothing (already applied) or a refusal."""

    outcome: Literal["append", "already_applied", "refused"]
    reasons: tuple[str, ...]
    entry: LedgerEntry | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.outcome == "append") != (self.entry is not None):
            raise ValueError("only an append carries an entry")
        if (self.outcome == "refused") != bool(self.reasons):
            raise ValueError("a refusal, and only a refusal, has reasons")
        return self
