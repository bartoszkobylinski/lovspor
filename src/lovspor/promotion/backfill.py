"""Backfill: every observed version of one document, in order (ADR-0016 2, Option C2; S6).

The versions come from the primary URL's observations since the first
capture (:mod:`lovspor.promotion.versions`). Each is written only when a human
approved **its** text — the standing decision on one of its blobs at that URL
is an ``approve`` bound to its content hash and the running extractor, given
at or after the version was first observed (an approval cannot vouch for a
text it predates). Anything else is a hold, by reason:

* ``extraction:<reason>`` — the version's bytes are held by the extractor;
* ``rejected`` / ``held_by_reviewer`` — the standing decision on one of its
  blobs says so;
* ``approval_stale`` — an approval exists, for another text, another
  extractor, or given before this version appeared (A→B→A: v3 needs its own);
* ``not_approved`` — nobody decided;
* ``identity:<reason>`` / ``identity_changed`` — the version cannot be named,
  or is named as another document;
* ``after_earlier_hold`` — an earlier version is held. Versions are numbered
  by observation order, so writing past a hold would renumber every later
  version the day the hold is resolved; the backfill stops at the first one.

**One version per run.** A run writes the next version the corpus does not
hold and prints the commit to make; the next run, after that commit, writes
the one after. That is how one commit per version, in order, comes about
without this code ever committing — and each commit is dated when it is made,
never backdated to ``ObservedAt`` (ADR-0016 2).

**Deterministic.** The observations a run reads are those up to the latest
approval among the versions it may write (``through``); a later capture of
the same text changes nothing until the observation refresh. The files are
the S3 writer's, with every version's interval re-read from the log through
the same cut-off, and the corpus must already agree with the log about the
versions it holds — a divergence is refused, never patched.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation
from lovspor.promotion.archive import ArchivedArtifact, Fetch, read_blob
from lovspor.promotion.corpus import LOCAL_DIR, CorpusCheckout
from lovspor.promotion.decisions import ArtifactKey, Decision, HumanDecision, utc_text
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.intervals import with_intervals
from lovspor.promotion.models import Authority
from lovspor.promotion.plan import Held, Prepared, prepare, require_approval
from lovspor.promotion.versions import DerivedVersion, PrimaryHistory, read_primary
from lovspor.promotion.writer import ObservationsFile, WriteSet, observations_text, write_set

NOT_APPROVED = "not_approved"
APPROVAL_STALE = "approval_stale"
AFTER_EARLIER_HOLD = "after_earlier_hold"
IDENTITY_CHANGED = "identity_changed"
_REFUSALS = {Decision.REJECT: "rejected", Decision.HOLD: "held_by_reviewer"}


class VersionHold(BaseModel):
    """A version the backfill may not write, and why (ADR-0016 4h)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    reason: str
    detail: str
    held: Held | None = None
    key: ArtifactKey | None = None


class ApprovedVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: DerivedVersion
    decision: HumanDecision


class BackfillPlan(BaseModel):
    """The versions observed at the primary URL: an approved prefix, then the holds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    history: PrimaryHistory
    approved: tuple[ApprovedVersion, ...]
    holds: tuple[VersionHold, ...]

    @property
    def through(self) -> datetime | None:
        """The cut-off: the latest approval among the versions that may be written."""
        return max((a.decision.decided_at for a in self.approved), default=None)

    def holds_by_reason(self) -> dict[str, int]:
        return dict(sorted(Counter(hold.reason for hold in self.holds).items()))


@dataclass(frozen=True)
class Inputs:
    """What a backfill reads: the log, the authority's fetches, the authority, the corpus."""

    log: ObservationLog
    fetches: tuple[Fetch, ...]
    authority: Authority
    corpus: CorpusCheckout


class NextVersion(BaseModel):
    """The next version to write: its placement, its files, and the approval it rests on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prepared: Prepared
    writes: WriteSet
    decision: HumanDecision
    key: ArtifactKey


def plan_backfill(history: PrimaryHistory, decisions: Sequence[HumanDecision]) -> BackfillPlan:
    """Each version approved or held, in order; everything after the first hold is held."""
    approved: list[ApprovedVersion] = []
    holds: list[VersionHold] = []
    for version in history.versions:
        if holds:
            detail = f"v{holds[0].version} is held ({holds[0].reason})"
            holds.append(
                VersionHold(version=version.version, reason=AFTER_EARLIER_HOLD, detail=detail)
            )
            continue
        outcome = _approval(version, decisions)
        if isinstance(outcome, VersionHold):
            holds.append(outcome)
        else:
            approved.append(ApprovedVersion(version=version, decision=outcome))
    return BackfillPlan(history=history, approved=tuple(approved), holds=tuple(holds))


def _approval(
    version: DerivedVersion, decisions: Sequence[HumanDecision]
) -> HumanDecision | VersionHold:
    held = version.held
    if held is not None:
        reason = f"extraction:{held.held_reason}"
        return VersionHold(version=version.version, reason=reason, detail=held.held_detail or "")
    standing = _standing(version, decisions)
    refusals = [d for d in standing if d.decision in _REFUSALS]
    if refusals:
        return _refused(version, refusals[0])
    fitting = [d for d in standing if _fits(d, version)]
    if fitting:
        return max(fitting, key=lambda d: d.decided_at)
    if standing:
        detail = _stale(max(standing, key=lambda d: d.decided_at), version)
        return VersionHold(version=version.version, reason=APPROVAL_STALE, detail=detail)
    detail = f"no decision on {version.source_sha256s[0]}; run `lovspor promote approve`"
    return VersionHold(version=version.version, reason=NOT_APPROVED, detail=detail)


def _stale(decision: HumanDecision, version: DerivedVersion) -> str:
    """Why a standing approval does not cover ``version``; approve it again to cover it."""
    if decision.content_hash != version.content_hash:
        return f"{decision.artifact.sha256} was approved for another text"
    if decision.extractor_version != EXTRACTOR_VERSION:
        return (
            f"approved under extractor {decision.extractor_version}; this engine extracts "
            f"with {EXTRACTOR_VERSION}"
        )
    return f"approved at {utc_text(decision.decided_at)}, before this version was first observed"


def _refused(version: DerivedVersion, decision: HumanDecision) -> VersionHold:
    when = utc_text(decision.decided_at)
    detail = f"{decision.decision.value} of {decision.artifact.sha256} at {when}"
    return VersionHold(version=version.version, reason=_REFUSALS[decision.decision], detail=detail)


def _standing(
    version: DerivedVersion, decisions: Sequence[HumanDecision]
) -> tuple[HumanDecision, ...]:
    """The last decision on each of the version's blobs at its URL."""
    blobs = set(version.source_sha256s)
    last: dict[str, HumanDecision] = {}
    for decision in decisions:
        artifact = decision.artifact
        if artifact.source_url == version.primary_url and artifact.sha256 in blobs:
            last[artifact.sha256] = decision
    return tuple(last[sha] for sha in sorted(last))


def _fits(decision: HumanDecision, version: DerivedVersion) -> bool:
    return (
        decision.decision is Decision.APPROVE
        and decision.content_hash == version.content_hash
        and decision.extractor_version == EXTRACTOR_VERSION
        and decision.decided_at >= version.observed_at_first
    )


def next_version(inputs: Inputs, plan: BackfillPlan) -> NextVersion | VersionHold | None:
    """The next version the corpus does not hold, a hold on it, or ``None`` when none is due."""
    if plan.through is None:
        return None
    history = read_primary(inputs.log, inputs.fetches, plan.history.primary_url, plan.through)
    first = _prepared(inputs, history, history.versions[0])
    if isinstance(first, VersionHold):
        return first
    in_corpus = _in_corpus(inputs.corpus, first, history)
    if in_corpus >= len(plan.approved):
        return None
    version = history.versions[in_corpus]
    prepared = _prepared(inputs, history, version)
    if isinstance(prepared, VersionHold):
        return prepared
    if prepared.identity.doc_id != first.identity.doc_id:
        detail = f"its text is named {prepared.identity.doc_id}, not {first.identity.doc_id}"
        return VersionHold(version=version.version, reason=IDENTITY_CHANGED, detail=detail)
    return _next(inputs, history, prepared, plan.approved[in_corpus])


def _prepared(
    inputs: Inputs, history: PrimaryHistory, version: DerivedVersion
) -> Prepared | VersionHold:
    artifact = _artifact(inputs, history, version)
    prepared = prepare(artifact, inputs.authority, inputs.corpus)
    if isinstance(prepared, Held):
        return VersionHold(
            version=version.version,
            reason=f"{prepared.stage}:{prepared.reason}",
            detail=prepared.detail,
            held=prepared,
            key=artifact.key,
        )
    return prepared


def _artifact(inputs: Inputs, history: PrimaryHistory, version: DerivedVersion) -> ArchivedArtifact:
    """The version as the S3 writer reads an artifact: its first blob and its whole run."""
    sha256 = version.source_sha256s[0]
    content_type = next(
        f.content_type
        for f in inputs.fetches
        if isinstance(f, ArtifactObservation)
        and f.sha256 == sha256
        and f.url == version.primary_url
    )
    return ArchivedArtifact(
        key=ArtifactKey(
            authority_id=inputs.authority.id, sha256=sha256, source_url=version.primary_url
        ),
        content_type=content_type,
        payload=read_blob(inputs.log, sha256),
        observed_at_first=version.observed_at_first,
        observed_at_last=version.observed_at_last,
        observations=version.observations,
        corroborating_urls=version.corroborating_urls,
        source_status=history.source_status,
    )


def _in_corpus(corpus: CorpusCheckout, first: Prepared, history: PrimaryHistory) -> int:
    """How many versions the corpus holds, refusing a corpus the log does not reproduce."""
    record = corpus.local_manifest().documents.get(first.identity.doc_id)
    if record is None:
        return 0
    path = corpus.inside(f"{record.authority_id}/observations/{record.slug}.json")
    try:
        existing = ObservationsFile.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        msg = f"{path} does not read; the versions the corpus holds cannot be checked"
        raise PromotionRefusedError(msg) from exc
    with_intervals(existing, history)
    if len(existing.versions) != record.version:
        msg = f"{path} lists {len(existing.versions)} versions, the manifest {record.version}"
        raise PromotionRefusedError(msg)
    return record.version


def _next(
    inputs: Inputs, history: PrimaryHistory, prepared: Prepared, approved: ApprovedVersion
) -> NextVersion:
    number = approved.version.version
    if prepared.unchanged or prepared.version != number:
        msg = f"the corpus places this text as v{prepared.version}, the log as v{number}"
        raise PromotionRefusedError(msg)
    decision = require_approval(approved.decision, prepared)
    artifact = _artifact(inputs, history, history.versions[number - 1])
    writes = write_set(prepared, artifact, decision, inputs.corpus)
    path = f"{LOCAL_DIR}/{prepared.identity.authority.id}/observations/{prepared.slug}.json"
    observed = ObservationsFile.model_validate_json(writes.files[path])
    files = {**writes.files, path: observations_text(with_intervals(observed, history))}
    return NextVersion(
        prepared=prepared,
        writes=writes.model_copy(update={"files": files}),
        decision=decision,
        key=artifact.key,
    )
