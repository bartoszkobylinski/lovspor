"""The last-seen view of linked Lovdata local-regulation ids (issue #694).

The first-seen ledger (:mod:`lovspor.observatory.lf_ledger`) says when a
page first linked an id and never retracts, so a repealed local regulation
looks like one in force. This module is the other half, derived the same way
and persisted nowhere: for every (authority, kind, id) the latest capture
that linked it, and whether every page that ever linked it has since been
read again without the link. An id in that state is a *candidate* repeal for
a human to review, nothing more — "no longer linked" is an observation about
a kommune's website, "repealed" is a legal fact this module cannot know.

Evidence that a link went away is only a later 2xx HTML capture of the same
page whose blob reads. A fetch failure, an error page, a non-HTML answer, or
a capture whose blob is gone (tombstoned, or damage ``verify`` reports) says
nothing about the page's links, so it never moves an id to ``unlinked``; it
counts the page as failing so the reader sees how current the evidence is.
Verdicts are taken from the captures' ``ObservedAt``, not the log order, so
a re-filed or late-appended older capture cannot read as the latest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lovspor.errors import LogIntegrityError
from lovspor.observatory.lf_ledger import LedgerKey, _is_html_capture
from lovspor.observatory.lf_refs import LF_ID_PATTERN, LovdataKind, extract_lovdata_refs
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, FetchFailure, ObservationRecord
from lovspor.observatory.outcomes import lost_the_document

LinkStatus = Literal["linked", "unlinked"]
_PageKey = tuple[str, str]
_Ref = tuple[str, str]


class LastSeen(BaseModel):
    """One linked id: when it was first and last seen, and whether it still is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str = Field(min_length=1)
    kind: LovdataKind
    lf_id: str = Field(pattern=LF_ID_PATTERN)
    first_seen: datetime
    last_seen_linked: datetime
    #: The page of the latest capture that linked the id.
    last_url: str = Field(min_length=1)
    status: LinkStatus
    #: ``unlinked`` only: when the last page still linking it was read without it.
    unlinked_since: datetime | None
    #: The latest readable capture of any page that ever linked the id.
    evidence_at: datetime
    #: Pages that ever linked the id, and how many of them failed after their last readable capture.
    pages: int = Field(ge=1)
    pages_failing: int = Field(ge=0)

    @property
    def key(self) -> LedgerKey:
        return (self.authority_id, self.kind, self.lf_id)


class LastSeenReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[LastSeen, ...]
    html_records: int = Field(ge=0)
    blobs_read: int = Field(ge=0)
    blobs_missing: int = Field(ge=0)


@dataclass(frozen=True)
class _Capture:
    observed_at: datetime
    refs: frozenset[_Ref]


@dataclass
class _Page:
    """What one (authority, url) was read as, and when it last could not be."""

    captures: list[_Capture] = field(default_factory=list)
    failed_at: datetime | None = None

    def failed(self, at: datetime) -> None:
        if self.failed_at is None or at > self.failed_at:
            self.failed_at = at

    @property
    def latest_readable(self) -> datetime:
        return max(capture.observed_at for capture in self.captures)

    @property
    def failing(self) -> bool:
        return self.failed_at is not None and self.failed_at > self.latest_readable


@dataclass
class _Fold:
    log: ObservationLog
    pages: dict[_PageKey, _Page] = field(default_factory=dict)
    refs_by_blob: dict[str, frozenset[_Ref] | None] = field(default_factory=dict)
    html_records: int = 0
    blobs_read: int = 0
    blobs_missing: int = 0

    def __call__(self, record: ObservationRecord) -> None:
        if isinstance(record, FetchFailure):
            if lost_the_document(record.outcome):
                self._page(record).failed(record.observed_at)
            return
        if not isinstance(record, ArtifactObservation):
            return
        if not _is_html_capture(record):
            self._page(record).failed(record.observed_at)
            return
        self.html_records += 1
        refs = self._refs(record.sha256)
        if refs is None:
            self._page(record).failed(record.observed_at)
            return
        self._page(record).captures.append(_Capture(record.observed_at, refs))

    def _page(self, record: ArtifactObservation | FetchFailure) -> _Page:
        return self.pages.setdefault((record.authority_id, record.url), _Page())

    def _refs(self, sha256: str) -> frozenset[_Ref] | None:
        if sha256 in self.refs_by_blob:
            return self.refs_by_blob[sha256]
        try:
            refs = extract_lovdata_refs(self.log.read_blob(sha256))
            self.blobs_read += 1
            found: frozenset[_Ref] | None = frozenset((ref.kind, ref.lf_id) for ref in refs)
        except FileNotFoundError:
            self.blobs_missing += 1
            found = None
        self.refs_by_blob[sha256] = found
        return found


@dataclass
class _Sightings:
    """One id's history on one page: when it was linked and when it was dropped."""

    url: str
    page: _Page
    linked_at: list[datetime] = field(default_factory=list)

    @property
    def last_linked(self) -> datetime:
        return max(self.linked_at)

    @property
    def dropped_at(self) -> datetime | None:
        later = [c.observed_at for c in self.page.captures if c.observed_at > self.last_linked]
        return min(later) if later else None


def _sightings(fold: _Fold) -> dict[LedgerKey, list[_Sightings]]:
    found: dict[LedgerKey, dict[_PageKey, _Sightings]] = {}
    for (authority_id, url), page in fold.pages.items():
        for capture in page.captures:
            for kind, lf_id in capture.refs:
                on_pages = found.setdefault((authority_id, kind, lf_id), {})
                sighting = on_pages.setdefault((authority_id, url), _Sightings(url, page))
                sighting.linked_at.append(capture.observed_at)
    return {key: list(on_pages.values()) for key, on_pages in found.items()}


def _entry(key: LedgerKey, sightings: list[_Sightings]) -> LastSeen:
    authority_id, kind, lf_id = key
    latest = max(sightings, key=lambda s: (s.last_linked, s.url))
    drops = [s.dropped_at for s in sightings]
    unlinked = all(drop is not None for drop in drops)
    return LastSeen(
        authority_id=authority_id,
        kind=kind,  # type: ignore[arg-type]
        lf_id=lf_id,
        first_seen=min(min(s.linked_at) for s in sightings),
        last_seen_linked=latest.last_linked,
        last_url=latest.url,
        status="unlinked" if unlinked else "linked",
        unlinked_since=max(drop for drop in drops if drop is not None) if unlinked else None,
        evidence_at=max(s.page.latest_readable for s in sightings),
        pages=len(sightings),
        pages_failing=sum(1 for s in sightings if s.page.failing),
    )


def last_seen_report(log: ObservationLog) -> LastSeenReport:
    """Fold the whole corrected log into the last-seen view, in key order.

    Raises:
        LogIntegrityError: the log does not read clean; a view built on a
            damaged log would miss sightings and call linked ids unlinked.
    """
    fold = _Fold(log)
    scan = log.scan_corrected_into(fold)
    if not scan.complete:
        raise LogIntegrityError(
            f"{log.log_path}: unreadable record after byte {scan.clean_through}; "
            "the last-seen view is not built from a damaged log (run `observatory verify`)",
        )
    sightings = _sightings(fold)
    return LastSeenReport(
        entries=tuple(_entry(key, sightings[key]) for key in sorted(sightings)),
        html_records=fold.html_records,
        blobs_read=fold.blobs_read,
        blobs_missing=fold.blobs_missing,
    )
