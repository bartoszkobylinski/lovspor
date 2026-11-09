"""The current source proof a standing policy is compared against (#738, Amendment 1 point 8).

A policy binds the robots.txt and terms bytes the owner read. Whether those
are still what regjeringen.no publishes is a separate question, answered only
by a ``forarbeider-fetch`` run: its record names the pages it read, and the
bytes it stored must re-hash to that record. This module reads such a run and
says, page by page, why it does not match a policy. It makes no request.

Evidence that is intact on disk is not current: a policy whose local files all
hash to their signed digests still clears nothing without a matching proof.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from lovspor.errors import RegjeringenFetchError
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_models import PolicyEvidenceKind, StandingPolicy
from lovspor.forarbeider.release_commands import forbidden_trees
from lovspor.forarbeider.release_store import MAX_EVIDENCE_BYTES, read_file, refused_as
from lovspor.regjeringen_fetch.fetch import verified_source_bytes
from lovspor.regjeringen_fetch.models import FetchRun, SourceEvidence, SourceState

SOURCE_PROOF_MAX_AGE = timedelta(hours=24)


class SourceFault(StrEnum):
    """Why the current source proof does not let a policy clear. Any one denies."""

    ROBOTS_CHANGED = "robots_changed"
    ROBOTS_NOT_PUBLISHED = "robots_not_published"
    SOURCE_PROOF_MISSING = "source_proof_missing"
    SOURCE_PROOF_STALE = "source_proof_stale"
    TERMS_CHANGED = "terms_changed"
    TERMS_NOT_PUBLISHED = "terms_not_published"


class CurrentSource(BaseModel):
    """robots.txt and terms as one fetch run read them, their stored bytes verified."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_started_at: AwareDatetime
    robots: SourceEvidence
    terms: SourceEvidence | None


def read_source_proof(run_file: Path) -> CurrentSource:
    """A fetch run record and the stored pages beside it, every digest re-checked.

    Raises:
        ForarbeiderRefusedError: the record is unsafe, unreadable or malformed, or a
            stored page is missing or no longer the bytes its record names.
    """
    with refused_as("source_proof"):
        payload = read_file(run_file, forbidden_trees(), MAX_EVIDENCE_BYTES)
    if payload is None:
        raise ForarbeiderRefusedError(RefusalReason.INPUT_UNREADABLE, "source_proof")
    try:
        run = FetchRun.model_validate_json(payload)
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, "source_proof") from error
    for page in (run.robots, run.terms):
        if page is not None and page.state is SourceState.PUBLISHED:
            _verified(run_file.parent, page)
    return CurrentSource(run_started_at=run.started_at, robots=run.robots, terms=run.terms)


def _verified(out: Path, page: SourceEvidence) -> None:
    try:
        verified_source_bytes(out, page)
    except RegjeringenFetchError as error:
        detail = f"source_proof {error.reason}"
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_CORRUPT, detail) from error


def source_faults(
    policy: StandingPolicy, source: CurrentSource | None, at: datetime
) -> tuple[SourceFault, ...]:
    """Every reason ``source`` does not let ``policy`` clear at ``at``; empty when it does."""
    if source is None:
        return (SourceFault.SOURCE_PROOF_MISSING,)
    signed = {item.kind: item.sha256 for item in policy.evidence}
    faults = _freshness(source, at)
    robots = (SourceFault.ROBOTS_NOT_PUBLISHED, SourceFault.ROBOTS_CHANGED)
    terms = (SourceFault.TERMS_NOT_PUBLISHED, SourceFault.TERMS_CHANGED)
    faults |= _page(signed.get(PolicyEvidenceKind.ROBOTS_TXT), source.robots, robots)
    faults |= _page(signed.get(PolicyEvidenceKind.TERMS_PAGE), source.terms, terms)
    return tuple(sorted(faults))


def _freshness(source: CurrentSource, at: datetime) -> set[SourceFault]:
    moments = [page.retrieved_at for page in (source.robots, source.terms) if page is not None]
    if max(moments) > at or at - min(moments) > SOURCE_PROOF_MAX_AGE:
        return {SourceFault.SOURCE_PROOF_STALE}
    return set()


def _page(
    signed: str | None, seen: SourceEvidence | None, faults: tuple[SourceFault, SourceFault]
) -> set[SourceFault]:
    unpublished, changed = faults
    if seen is None or seen.state is not SourceState.PUBLISHED:
        return {unpublished}
    return set() if seen.sha256 == signed else {changed}
