"""``get_observation_history``: a local document on its observation axis (ADR-0016 2, 5; S7).

The observation axis is the instants the authority's website was read at,
as ``observations/<slug>.json`` holds them (slice S6): per promoted version
the closed interval ``[observed_at_first, observed_at_last]`` of its
observations at the primary URL. An interval says these bytes were
retrievable at the observed instants and nothing more.

An instant ``observed_at`` placed against those intervals has exactly one of
four typed outcomes, mirroring ADR-0011 point 6:

* :class:`Contained` — inside a version's interval, bounds included;
* :class:`BetweenObservations` — in the gap between two versions' intervals:
  both neighbours are named and neither is asserted for the instant;
* :class:`BeforeFirstObservation` — before the document was first observed,
  carrying that first observation and the archive's floor;
* :class:`AfterLastObservation` — after the last observation. S6 writes the
  last interval closed (``observed_at_last`` is the last instant seen, moved
  only by the weekly ``observe:`` refresh), so a later instant is not
  contained: the version last seen is named, never asserted past it.

``observed_at`` is the observation axis only. It never selects a corpus
state: that is the transaction axis, ``recorded_at``, git commit time. No
code path here reads git to place an instant, and no clock is consulted.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from lovspor.errors import LocalScopeError, ObservedAtError
from lovspor.local_corpus import LocalDataset, Observation, is_local_address
from lovspor.promotion.archive import SourceStatus
from lovspor.promotion.decisions import utc_text
from lovspor.promotion.versions import ExcludedObservation
from lovspor.promotion.writer import VersionObservations

OBSERVATION_FLOOR = "2026-08-19"
"""The observatory's first capture: no local document was observed before it."""

_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class ObservedVersion(BaseModel):
    """One promoted version and the interval it was observed in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    content_hash: str
    observed_at_first: str
    observed_at_last: str
    observation_count: int
    primary_url: str
    corroborating_urls: tuple[str, ...]

    @classmethod
    def of(cls, entry: VersionObservations) -> ObservedVersion:
        return cls.model_validate(entry.model_dump(include=set(cls.model_fields)))


class Contained(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["contained"] = "contained"
    observed_at: str
    version: ObservedVersion
    text: str | None = None


class BetweenObservations(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["between_observations"] = "between_observations"
    observed_at: str
    before: ObservedVersion
    after: ObservedVersion
    notice: str = (
        "Nothing was observed between these two observations; neither version is "
        "asserted at observed_at."
    )


class BeforeFirstObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["before_first_observation"] = "before_first_observation"
    observed_at: str
    observed_at_first: str
    observation_floor: str = OBSERVATION_FLOOR
    notice: str = (
        "The document was not yet observed at observed_at; this says nothing about "
        "whether it existed then."
    )


class AfterLastObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["after_last_observation"] = "after_last_observation"
    observed_at: str
    last: ObservedVersion
    observed_at_last: str
    notice: str = (
        "Nothing was observed after observed_at_last; the version last seen is not "
        "asserted at observed_at."
    )


ObservationOutcome = Contained | BetweenObservations | BeforeFirstObservation | AfterLastObservation


class ObservationHistory(BaseModel):
    """The ``get_observation_history`` answer; ``at`` is ``None`` without ``observed_at``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document: str
    doc_id: str
    dataset: Literal["lokale-forskrifter"] = "lokale-forskrifter"
    versions: tuple[ObservedVersion, ...]
    source_status: SourceStatus
    excluded: tuple[ExcludedObservation, ...]
    observation: Observation
    at: ObservationOutcome | None


def parse_observed_at(value: str) -> datetime:
    """An instant with an offset, in UTC; anything else is refused, never guessed."""
    if not _INSTANT.fullmatch(value):
        why = (
            "; a calendar date would hide which side of a version change it means"
            if _DATE.fullmatch(value)
            else ""
        )
        msg = (
            "observed_at must be an instant with an offset, e.g. 2026-09-01T12:00:00Z, "
            f"got {value!r}{why}"
        )
        raise ObservedAtError(msg)
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError as exc:
        msg = f"observed_at must be an instant with an offset, got {value!r}: {exc}"
        raise ObservedAtError(msg) from exc


def locate(versions: Sequence[ObservedVersion], at: datetime) -> ObservationOutcome:
    """Where ``at`` falls against ``versions`` (oldest first, at least one)."""
    stamp = utc_text(at)
    previous: ObservedVersion | None = None
    for version in versions:
        if at < _instant(version.observed_at_first):
            if previous is None:
                first = version.observed_at_first
                return BeforeFirstObservation(observed_at=stamp, observed_at_first=first)
            return BetweenObservations(observed_at=stamp, before=previous, after=version)
        if at <= _instant(version.observed_at_last):
            return Contained(observed_at=stamp, version=version)
        previous = version
    last = versions[-1]
    return AfterLastObservation(
        observed_at=stamp, last=last, observed_at_last=last.observed_at_last
    )


def observation_history(
    local: LocalDataset, document: str, observed_at: str | None, include_text: bool
) -> dict[str, Any]:
    """The versions of one local ``document``, and where ``observed_at`` falls."""
    _check_request(document, observed_at, include_text)
    instant = None if observed_at is None else parse_observed_at(observed_at)
    served = local.document(document)
    observed = local.observations(served)
    versions = tuple(ObservedVersion.of(entry) for entry in observed.versions)
    at = None if instant is None else locate(versions, instant)
    if include_text and isinstance(at, Contained):
        text = local.version_text(served, at.version.version, at.version.content_hash)
        at = at.model_copy(update={"text": text})
    return ObservationHistory(
        document=served.address,
        doc_id=served.doc_id,
        versions=versions,
        source_status=observed.source_status,
        excluded=observed.excluded,
        observation=served.observation,
        at=at,
    ).model_dump(mode="json")


def _check_request(document: str, observed_at: str | None, include_text: bool) -> None:
    if include_text and observed_at is None:
        msg = "include_text needs observed_at: the text is that of the version observed then"
        raise ObservedAtError(msg)
    if not is_local_address(document):
        msg = (
            f"{document!r} is not a local regulation; observation history exists only for "
            "<authority_id>/<slug> or lf-/lk- addresses. A central law's history is "
            "get_law_history"
        )
        raise LocalScopeError(msg)


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text)
