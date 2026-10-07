"""A batch of classifier candidates, assessed for promotion (ADR-0016 4g, 4h, slice S8).

A batch is the enacted-regulation candidates the classifier names for one
authority — all of them, or a listed subset — under a **batch spec**: a JSON
document the operator writes, like the decision document, so the policy a
batch ran under can be re-read later. The spec states the batch id (the
sample's seed), the authority, the classifier output and its version, and
the **sample rate, which has no default** (see :mod:`lovspor.promotion.sample`).

Each candidate runs the same preparation as ``promote preview`` — extraction,
the personal-data gate, identity, placement — and ends in one outcome:

* ``ready`` — a new version is prepared; it is in the sample population;
* ``unchanged`` — the corpus already holds this version; also in the
  population, so the sample stays the same while a batch is written item by
  item;
* ``held`` — a property of the source stops it (``<stage>:<reason>``, e.g.
  ``extraction:lovdata_copy``, ``identity:no_identity``), or two candidates
  of the batch mint one id (``batch:same_id_in_batch``: which URL is primary
  is a recorded human decision, ADR-0016 1d, never picked here);
* ``refused`` — the request cannot be carried out (not observed, tombstoned,
  withdrawn …), counted under ``request:refused`` with the reason kept.

Holds and refusals are counted by reason, never dropped (4h).

The gate (4g): a deterministic sample of the population is drawn; the batch
**passes** only when every sampled item has a standing human approval of
exactly the prepared text in the decision log. **One rejected sampled item
blocks the whole batch**, and so does a sampled item not yet reviewed. Even
then only items with their own standing approval are written: promoting an
unreviewed item on the classifier's word alone (4d ``decided_by:
classifier``) waits for the owner's spot-check policy (Open Decision 2). This
module reads only; it records and writes nothing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.fields import TrimmedNonBlankStr
from lovspor.observatory.log import ObservationLog
from lovspor.promotion.archive import Fetch, read_artifact
from lovspor.promotion.classifier import ClassifiedArtifact, ClassifierOutput
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import (
    ArtifactKey,
    ClassifierEvidence,
    Decision,
    DecisionLog,
    HumanDecision,
)
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.models import Authority, PersonalDataHit
from lovspor.promotion.plan import Held, Prepared, prepare, require_approval
from lovspor.promotion.render import LOCAL_RENDERER_VERSION
from lovspor.promotion.sample import draw_sample, parse_sample_rate

SAME_ID_IN_BATCH = "batch:same_id_in_batch"
REFUSED = "request:refused"


def _rate(value: object) -> Decimal:
    if not isinstance(value, str):
        msg = 'sample_rate is a string such as "1" (100 %) or "0.05"; it has no default'
        raise ValueError(msg)
    try:
        return parse_sample_rate(value)
    except PromotionRefusedError as exc:
        raise ValueError(str(exc)) from exc


SampleRate = Annotated[Decimal, BeforeValidator(_rate)]


class BatchSpec(BaseModel):
    """What the operator asks of one batch; every field required unless stated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    authority_id: str = Field(pattern=r"^(?:\d{2}|\d{4})$")
    klass_version: TrimmedNonBlankStr
    classifier_output: Path
    classifier_version: TrimmedNonBlankStr
    sample_rate: SampleRate
    artifacts: tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], ...] = ()

    @field_validator("classifier_output")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            msg = f"classifier_output must be an absolute path, got {value}"
            raise ValueError(msg)
        return value


class Review(StrEnum):
    """Where the decision log stands on one prepared item."""

    APPROVED = "approved"
    REJECTED = "rejected"
    HELD_BY_REVIEWER = "held_by_reviewer"
    STALE = "approval_for_another_text"
    UNREVIEWED = "unreviewed"


class BatchItem(BaseModel):
    """One candidate of the batch and how far it got."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: ArtifactKey
    classifier: ClassifierEvidence
    outcome: Literal["ready", "unchanged", "held", "refused"]
    hold: str | None = None
    detail: str = ""
    personal_data: tuple[PersonalDataHit, ...] = ()
    doc_id: str | None = None
    title: str | None = None
    version: int | None = None
    markdown_path: str | None = None
    content_hash: str | None = None
    review: Review = Review.UNREVIEWED
    sampled: bool = False

    @property
    def promotable(self) -> bool:
        return self.outcome in {"ready", "unchanged"}


class Gate(BaseModel):
    """The batch's verdict: the sampled items that block it, by why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Literal["pass", "blocked", "empty"]
    rejected: tuple[ArtifactKey, ...] = ()
    awaiting_review: tuple[ArtifactKey, ...] = ()


class BatchAssessment(BaseModel):
    """Everything the report shows and the write step reads, for one batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec: BatchSpec
    classifier_output_sha256: str
    extractor_version: int
    renderer_version: int
    items: tuple[BatchItem, ...]
    gate: Gate

    @property
    def holds_by_reason(self) -> dict[str, int]:
        counted = Counter(i.hold for i in self.items if i.hold is not None)
        return {reason: counted[reason] for reason in sorted(counted)}

    @property
    def to_write(self) -> tuple[BatchItem, ...]:
        """Ready items with their own standing approval — none unless the gate passed."""
        if self.gate.verdict != "pass":
            return ()
        return tuple(i for i in self.items if i.outcome == "ready" and i.review is Review.APPROVED)


@dataclass(frozen=True)
class BatchInputs:
    """What assessing a batch reads: the archive, the corpus and the decision log."""

    log: ObservationLog
    fetches: tuple[Fetch, ...]
    corpus: CorpusCheckout
    authority: Authority
    decisions: DecisionLog


def assess_batch(spec: BatchSpec, output: ClassifierOutput, inputs: BatchInputs) -> BatchAssessment:
    """Prepare every candidate, hold same-id pairs, draw the sample and decide the gate."""
    candidates = output.candidates(spec.authority_id, spec.artifacts)
    prepared = tuple(assess_item(c, inputs, output.classifier_version) for c in candidates)
    items = _hold_same_ids(prepared)
    population = [i.key for i in items if i.promotable]
    sampled = set(draw_sample(population, spec.batch_id, spec.sample_rate))
    items = tuple(i.model_copy(update={"sampled": i.key in sampled}) for i in items)
    return BatchAssessment(
        spec=spec,
        classifier_output_sha256=output.source_sha256,
        extractor_version=EXTRACTOR_VERSION,
        renderer_version=LOCAL_RENDERER_VERSION,
        items=items,
        gate=decide_gate(items),
    )


def assess_item(candidate: ClassifiedArtifact, inputs: BatchInputs, version: str) -> BatchItem:
    """Run the preview preparation on one candidate; nothing is written or recorded."""
    item = BatchItem(
        key=candidate.key, classifier=candidate.evidence(version), outcome="refused", hold=REFUSED
    )
    try:
        artifact = read_artifact(inputs.log, inputs.fetches, candidate.key, None)
        prepared = prepare(artifact, inputs.authority, inputs.corpus)
    except PromotionRefusedError as exc:
        return item.model_copy(update={"detail": str(exc)})
    if isinstance(prepared, Held):
        return item.model_copy(update=_held(prepared))
    review = _review(inputs.decisions.latest_decision(candidate.key), prepared)
    return item.model_copy(update=_placed(prepared) | {"review": review})


def decide_gate(items: tuple[BatchItem, ...]) -> Gate:
    """Pass only when every sampled item is approved; a rejected one blocks the batch."""
    sampled = [i for i in items if i.sampled]
    if not sampled:
        return Gate(verdict="empty")
    rejected = tuple(i.key for i in sampled if i.review is Review.REJECTED)
    awaiting = tuple(i.key for i in sampled if i.review not in {Review.APPROVED, Review.REJECTED})
    if rejected or awaiting:
        return Gate(verdict="blocked", rejected=rejected, awaiting_review=awaiting)
    return Gate(verdict="pass")


def _held(held: Held) -> dict[str, object]:
    return {
        "outcome": "held",
        "hold": f"{held.stage}:{held.reason}",
        "detail": held.detail,
        "personal_data": held.personal_data,
    }


def _placed(prepared: Prepared) -> dict[str, object]:
    return {
        "outcome": "unchanged" if prepared.unchanged else "ready",
        "hold": None,
        "doc_id": prepared.identity.doc_id,
        "title": prepared.extracted.fields.title,
        "version": prepared.version,
        "markdown_path": prepared.markdown_path,
        "content_hash": prepared.identity.content_hash,
    }


def _review(decision: HumanDecision | None, prepared: Prepared) -> Review:
    if decision is None:
        return Review.UNREVIEWED
    if decision.decision is Decision.REJECT:
        return Review.REJECTED
    if decision.decision is Decision.HOLD:
        return Review.HELD_BY_REVIEWER
    try:
        require_approval(decision, prepared)
    except PromotionRefusedError:
        return Review.STALE
    return Review.APPROVED


def _hold_same_ids(items: tuple[BatchItem, ...]) -> tuple[BatchItem, ...]:
    """Hold every promotable item whose id another item of the batch also mints."""
    minted = Counter(i.doc_id for i in items if i.promotable)
    return tuple(
        i.model_copy(update=_same_id(i.doc_id, minted[i.doc_id]))
        if i.promotable and minted[i.doc_id] > 1
        else i
        for i in items
    )


def _same_id(doc_id: str | None, count: int) -> dict[str, object]:
    detail = (
        f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
        "recorded human decision (ADR-0016 1d) — promote the primary one alone"
    )
    return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
