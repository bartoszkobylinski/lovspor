"""AR2 records: a deterministic plan of bounded release batches (#738; §5.3, §9.2, §9.3).

A ``ReleasePlan`` accounts for **every** document of a verified inventory and
every document offered outside it, each with one disposition:

- ``planned``: in exactly one batch;
- ``blocked``: its evidence holds but something stops it (an FC7 or FC7b
  blocker, an incomplete source or publication, another policy, a document too
  large for any batch);
- ``excluded``: its evidence does not hold (an integrity blocker, an altered or
  missing artifact);
- ``missing``: in the inventory, but no evidence was offered for it;
- ``outside_inventory``: offered, but not in the inventory;
- ``quarantined``: an active AR3 quarantine or a permanent owner exclusion
  keeps it out, whatever else holds (``quarantine:*`` and
  ``owner_exclusion:*`` reasons, next to every other reason it has).

Nothing is dropped silently, and nothing but ``planned`` is ever in a batch.

**What planning leaves to the gates.** FC8b gives every selection a few
blockers that do not depend on the document: the unanchored lineage head and
the unresolved owner decisions D10 and D11, as FC8b and FC7b state them
(:data:`RELEASE_WIDE_BLOCKERS`). A document whose *only* blockers are these is
planned. The plan names them (``pending_release_blockers``), and the AR1 gates
still fail on every one of them for every batch: a plan is never an
authorization, and ``release_eligible`` and ``published`` are ``False``. Any
other blocker keeps a document out of every batch.

**Sizes.** A document's files and bytes are counted from the exact artifact
bytes its FC8b selection rests on (raw sources and the bound FC6, FC5, FC7,
FC7b and lineage records), each distinct digest once: ``size_basis`` is
``bound_artifacts``. AR4 renders the proposed publication bytes and must
recount from them.

**Exact bindings.** Every document with evidence names the digest of its FC8b
selection *and* of its FC8b binding: the lineage head and the FC6, FC5, FC7
and FC7b records of the very observation the plan was built from. Two
observations can share a selection and a version yet rest on different
records, so a batch must match both, document by document.

**Identity.** ``plan_id`` is the digest of the basis, the documents and the
batches. A batch id is derived from the basis and its documents' versions,
selections, bindings and artifacts, so the same inputs give the same ids and any change of policy,
limits, inventory or version gives new ones: a stale plan never matches.
"""

from collections import Counter
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.release_limits_models import (
    MAX_BATCH_BYTES,
    MAX_BATCH_DOCUMENTS,
    MAX_BATCH_FILES,
)
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank
from lovspor.forarbeider.version_release_models import PolicyReference, digest_of

PLANNER_VERSION: Final = "release-plan/1"
#: Blockers FC8b gives every selection whatever the document; the AR1 gates judge them.
RELEASE_WIDE_BLOCKERS: Final = frozenset(
    {
        ("trust", "head_unanchored"),
        ("governance", "d10_legal_assessment_unresolved"),
        ("governance", "d11_automated_publication_not_accepted"),
        ("authorization", "fc7b:d10_legal_assessment_not_verifiable"),
        ("authorization", "fc7b:d11_automated_publication_not_accepted"),
    }
)
PENDING_RELEASE_BLOCKERS: Final = tuple(
    f"{category}:{code}" for category, code in sorted(RELEASE_WIDE_BLOCKERS)
)

Disposition = Literal[
    "planned", "blocked", "excluded", "missing", "outside_inventory", "quarantined"
]
_BatchId = Field(pattern=r"^batch-[0-9a-f]{56}$")


def _sorted_once(values: tuple[str, ...], what: str) -> None:
    if list(values) != sorted(set(values)):
        raise ValueError(f"{what} are sorted, once each")


class BatchCaps(Frozen):
    """The bounds every batch keeps: the lower of §9.3's hard caps and the signed limits."""

    documents: int = Field(ge=1, le=MAX_BATCH_DOCUMENTS)
    files: int = Field(ge=1, le=MAX_BATCH_FILES)
    bytes: int = Field(ge=1, le=MAX_BATCH_BYTES)


class PlanBasis(Frozen):
    """Everything a plan was made from, each by digest."""

    planner_version: Literal["release-plan/1"] = PLANNER_VERSION
    inventory_id: NonBlank
    inventory_version: int = Field(ge=1)
    inventory_sha256: Digest
    policy: PolicyReference
    limits_sha256: Digest
    caps: BatchCaps
    selection_release_id: Digest
    selection_bindings_sha256: Digest
    size_basis: Literal["bound_artifacts"] = "bound_artifacts"
    pending_release_blockers: tuple[NonBlank, ...] = PENDING_RELEASE_BLOCKERS
    #: The digest of the AR3 quarantine set the plan honours; ``None``: not assessed.
    quarantine_sha256: Digest | None = None


class PlannedDocument(Frozen):
    """One document's disposition, its selected version, its size and every reason."""

    document_id: NonBlank
    disposition: Disposition
    version_id: Digest | None
    selection_sha256: Digest | None
    binding_sha256: Digest | None
    artifact_sha256s: tuple[Digest, ...]
    files: int = Field(ge=0)
    bytes: int = Field(ge=0)
    reasons: tuple[NonBlank, ...]
    batch_id: str | None = Field(default=None, pattern=r"^batch-[0-9a-f]{56}$")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _sorted_once(self.reasons, "reasons")
        _sorted_once(self.artifact_sha256s, "artifact digests")
        planned = self.disposition == "planned"
        if planned == bool(self.reasons):
            raise ValueError("a document is planned exactly when nothing stops it")
        if planned != (self.batch_id is not None):
            raise ValueError("a document is in a batch exactly when it is planned")
        if self.files != len(self.artifact_sha256s):
            raise ValueError("files counts the distinct artifacts")
        if (self.selection_sha256 is None) != (self.binding_sha256 is None):
            raise ValueError("a selection is planned together with its exact binding")
        return self


class PlannedBatch(Frozen):
    """One bounded batch: its id, its place in the order, its documents and its size."""

    batch_id: str = _BatchId
    sequence: int = Field(ge=1)
    document_ids: tuple[NonBlank, ...] = Field(min_length=1)
    documents: int = Field(ge=1)
    files: int = Field(ge=0)
    bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _sorted_once(self.document_ids, "batch document ids")
        if self.documents != len(self.document_ids):
            raise ValueError("documents counts the batch's documents")
        return self


class PlanCoverage(Frozen):
    """How the plan accounts for the inventory, by disposition."""

    expected: int = Field(ge=0)
    planned: int = Field(ge=0)
    blocked: int = Field(ge=0)
    excluded: int = Field(ge=0)
    missing: int = Field(ge=0)
    outside_inventory: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    state: Literal["complete", "incomplete"]


class ReleasePlan(Frozen):
    """Every expected and offered document, its disposition and the bounded batches."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_release_plan"] = "forarbeider_release_plan"
    plan_id: Digest
    basis: PlanBasis
    documents: tuple[PlannedDocument, ...]
    batches: tuple[PlannedBatch, ...]
    coverage: PlanCoverage
    release_eligible: Literal[False] = False
    published: Literal[False] = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _sorted_once(tuple(item.document_id for item in self.documents), "documents")
        if self.plan_id != plan_digest(self.basis, self.documents, self.batches):
            raise ValueError("plan_id is the digest of the basis, documents and batches")
        if self.coverage != coverage_of(self.documents):
            raise ValueError("coverage follows from the documents")
        _check_batches(self)
        return self


class PlanVerification(Frozen):
    """Whether a stored plan is still the one the current evidence gives; never eligibility."""

    status: Literal["current", "stale", "invalid"]
    findings: tuple[NonBlank, ...]

    @model_validator(mode="after")
    def _status_follows_findings(self) -> Self:
        if (self.status == "current") != (not self.findings):
            raise ValueError("current exactly when nothing differs")
        return self


def plan_digest(
    basis: PlanBasis, documents: tuple[PlannedDocument, ...], batches: tuple[PlannedBatch, ...]
) -> str:
    return digest_of(basis, *documents, *batches)


def coverage_of(documents: tuple[PlannedDocument, ...]) -> PlanCoverage:
    counted = Counter(item.disposition for item in documents)
    expected = len(documents) - counted["outside_inventory"]
    incomplete = counted["missing"] or counted["outside_inventory"]
    return PlanCoverage(
        expected=expected,
        planned=counted["planned"],
        blocked=counted["blocked"],
        excluded=counted["excluded"],
        missing=counted["missing"],
        outside_inventory=counted["outside_inventory"],
        quarantined=counted["quarantined"],
        state="incomplete" if incomplete else "complete",
    )


def _check_batches(plan: ReleasePlan) -> None:
    """Every planned document in exactly its batch; each batch's size its documents' sum."""
    by_id = {item.document_id: item for item in plan.documents}
    if [item.sequence for item in plan.batches] != list(range(1, len(plan.batches) + 1)):
        raise ValueError("batches are numbered 1, 2, 3... in order")
    placed = [document for batch in plan.batches for document in batch.document_ids]
    planned = [item.document_id for item in plan.documents if item.disposition == "planned"]
    if sorted(placed) != planned:
        raise ValueError("every planned document is in exactly one batch, and only those")
    for batch in plan.batches:
        _check_batch(batch, [by_id[document] for document in batch.document_ids], plan.basis.caps)


def _check_batch(batch: PlannedBatch, members: list[PlannedDocument], caps: BatchCaps) -> None:
    if any(item.batch_id != batch.batch_id for item in members):
        raise ValueError("a batch's documents name the batch")
    size = (len(members), sum(m.files for m in members), sum(m.bytes for m in members))
    if size != (batch.documents, batch.files, batch.bytes):
        raise ValueError("a batch's size is its documents' sum")
    if not _within(size, caps):
        raise ValueError("a batch keeps its caps")


def _within(size: tuple[int, int, int], caps: BatchCaps) -> bool:
    return size[0] <= caps.documents and size[1] <= caps.files and size[2] <= caps.bytes
