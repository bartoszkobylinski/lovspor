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
  ``extraction:lovdata_copy``, ``identity:no_identity``), or two different
  texts of the batch mint one id (``batch:same_id_in_batch``: which URL is
  primary is a recorded human decision, ADR-0016 1d, never picked here);
  ``batch:needs_canonical_source`` is a defensive hold for a group of pages
  that reached :func:`resolve_group` without a chosen page, unreachable from
  :func:`group_candidates`, which always chooses one (#566);
* ``refused`` — the request cannot be carried out (not observed, tombstoned,
  withdrawn …), counted under ``request:refused`` with the reason kept.

Candidates that mint one id from one extracted text (same ``content_hash``)
are folded into **one** candidate listing every page in ``sources``, so a
regulation on 90 pages is one candidate and at most one sample item, not 90.
It is assessed, reviewed and written from its canonical page, chosen by a
deterministic rule from the URLs (:func:`choose_canonical`, #566).

Holds and refusals are counted by reason, per candidate, never dropped (4h).

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

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.fields import TrimmedNonBlankStr
from lovspor.observatory.log import ObservationLog
from lovspor.promotion.archive import Fetch, read_artifact
from lovspor.promotion.classifier import ClassifiedArtifact, ClassifierOutput
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import (
    ArtifactKey,
    Carried,
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
NEEDS_CANONICAL_SOURCE = "batch:needs_canonical_source"
REFUSED = "request:refused"
MIN_TITLE_WORD = 4


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
    sources: tuple[ArtifactKey, ...] = ()

    @property
    def promotable(self) -> bool:
        return self.outcome in {"ready", "unchanged"}


class CandidateGroup(BaseModel):
    """Candidates that mint one id from one extracted text: one regulation on N pages (#566).

    Members are listed by source URL, then hash — a listing order, not a choice.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    members: tuple[BatchItem, ...] = Field(min_length=2)

    @field_validator("members")
    @classmethod
    def _one_regulation(cls, members: tuple[BatchItem, ...]) -> tuple[BatchItem, ...]:
        """A group is one id from one text; anything else is a real collision."""
        if len({(m.doc_id, m.content_hash) for m in members}) != 1 or members[0].doc_id is None:
            raise ValueError("a group's pages must mint one id from one extracted text")
        return members

    @property
    def doc_id(self) -> str:
        return str(self.members[0].doc_id)

    @property
    def content_hash(self) -> str | None:
        return self.members[0].content_hash

    @property
    def sources(self) -> tuple[ArtifactKey, ...]:
        return tuple(m.key for m in self.members)


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
    items = group_candidates(prepared)
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
    decision = inputs.decisions.latest_decision(candidate.key)
    review = _review(decision, prepared, inputs.decisions.carried())
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


def _review(decision: HumanDecision | None, prepared: Prepared, carried: Carried) -> Review:
    if decision is None:
        return Review.UNREVIEWED
    if decision.decision is Decision.REJECT:
        return Review.REJECTED
    if decision.decision is Decision.HOLD:
        return Review.HELD_BY_REVIEWER
    try:
        require_approval(decision, prepared, carried)
    except PromotionRefusedError:
        return Review.STALE
    return Review.APPROVED


_Text = tuple[str, str]


def group_candidates(items: tuple[BatchItem, ...]) -> tuple[BatchItem, ...]:
    """Fold each id-and-text into one candidate, then hold every id that two texts mint.

    A folded candidate stands where its first member stood in the batch.
    """
    members = _members(items)
    texts = Counter(doc_id for doc_id, _ in members)
    folded: list[BatchItem] = []
    for item in items:
        text = _text(item)
        if text is None:
            folded.append(item)
        elif text in members:
            folded.append(_fold(members.pop(text), texts[text[0]]))
    return tuple(folded)


def choose_canonical(group: CandidateGroup) -> ArtifactKey:
    """The page a group is promoted from (#566, owner decision 2026-10-08, option (a)).

    Deterministic, and read only from what the members carry: each page's URL
    and sha256 and the regulation title extracted from the text. A member does
    not carry the page's own HTML title, so "the page is about the regulation"
    is read from the URL alone. In order:

    1. **About the regulation**: the most title words in the page's own slug,
       the last segment of its percent-decoded URL path. A title word is in
       the slug when a slug word contains it, so a Norwegian compound or
       inflection counts (``skolen`` in ``grunnskolen``, ``forskrift`` in
       ``forskrifter``); the reverse, a slug word inside a longer title word,
       does not. Title words have four
       letters or more, so ``om``, ``og``, ``i`` and ``for`` do not count, and
       words naming the site itself (its host labels, e.g. the kommune's name
       in ``www.kongsvinger.kommune.no``) say nothing about the page and do not
       count either. Both sides fold as slugs spell Norwegian: case, ``æ ø å``
       as ``a o a``, ``ae oe aa`` as ``a o a``, accents dropped.
    2. **Shortest path**: fewest path segments, then the shortest decoded path.
    3. **Tie-break**: the URL, then the sha256.

    Every page stays in provenance: :func:`resolve_group` keeps them all in
    ``sources``, whichever page is chosen.
    """
    title = {word for word in _words(group.members[0].title or "") if len(word) >= MIN_TITLE_WORD}
    return min((m.key for m in group.members), key=lambda key: _canonical_rank(key, title))


def resolve_group(group: CandidateGroup, canonical: ArtifactKey | None) -> BatchItem:
    """The group as one candidate from its chosen page, every page kept in ``sources``.

    ``None`` holds the group as :data:`NEEDS_CANONICAL_SOURCE`: a defensive
    path, since :func:`group_candidates` always passes :func:`choose_canonical`'s page.
    """
    sources: dict[str, object] = {"sources": group.sources}
    if canonical is None:
        return group.members[0].model_copy(update=sources | _needs_canonical(group))
    chosen = next((m for m in group.members if m.key == canonical), None)
    if chosen is None:
        msg = f"{canonical.source_url} is not a page of the group that mints {group.doc_id}"
        raise PromotionRefusedError(msg)
    return chosen.model_copy(update=sources)


_FOLDED_LETTERS = str.maketrans({"æ": "a", "ø": "o", "å": "a"})
_FOLDED_DIGRAPHS = (("ae", "a"), ("oe", "o"), ("aa", "a"))


def _canonical_rank(key: ArtifactKey, title: set[str]) -> tuple[int, int, int, str, str]:
    url = urlsplit(key.source_url)
    path = unquote(url.path)
    segments = [segment for segment in path.split("/") if segment]
    slug = _words(segments[-1]) if segments else frozenset()
    named = title - _words(url.hostname or "")
    about = sum(any(word in part for part in slug) for word in named)
    return -about, len(segments), len(path), key.source_url, key.sha256


def _words(text: str) -> frozenset[str]:
    decomposed = unicodedata.normalize("NFKD", text.casefold().translate(_FOLDED_LETTERS))
    folded = "".join(c for c in decomposed if not unicodedata.combining(c))
    for digraph, letter in _FOLDED_DIGRAPHS:
        folded = folded.replace(digraph, letter)
    return frozenset(re.findall(r"[^\W_]+", folded))


def _text(item: BatchItem) -> _Text | None:
    if not item.promotable or item.doc_id is None or item.content_hash is None:
        return None
    return item.doc_id, item.content_hash


def _members(items: tuple[BatchItem, ...]) -> dict[_Text, tuple[BatchItem, ...]]:
    grouped: dict[_Text, list[BatchItem]] = {}
    for item in items:
        text = _text(item)
        if text is not None:
            grouped.setdefault(text, []).append(item)
    return {text: tuple(sorted(group, key=_by_source)) for text, group in grouped.items()}


def _by_source(item: BatchItem) -> tuple[str, str]:
    return item.key.source_url, item.key.sha256


def _fold(pages: tuple[BatchItem, ...], texts: int) -> BatchItem:
    first = pages[0]
    sources: dict[str, object] = {"sources": tuple(p.key for p in pages)} if len(pages) > 1 else {}
    if texts > 1:
        return first.model_copy(update=sources | _same_id(first.doc_id, texts))
    if len(pages) == 1:
        return first
    group = CandidateGroup(members=pages)
    return resolve_group(group, choose_canonical(group))


def _needs_canonical(group: CandidateGroup) -> dict[str, object]:
    detail = (
        f"{len(group.members)} pages carry this one text and no canonical page was chosen "
        "(#566) — every page stays in provenance"
    )
    return {"outcome": "held", "hold": NEEDS_CANONICAL_SOURCE, "detail": detail}


def _same_id(doc_id: str | None, count: int) -> dict[str, object]:
    detail = (
        f"{count} candidates of this batch mint {doc_id}; which URL is primary is a "
        "recorded human decision (ADR-0016 1d) — promote the primary one alone"
    )
    return {"outcome": "held", "hold": SAME_ID_IN_BATCH, "detail": detail}
