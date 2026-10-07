"""The versions of one document, read from its primary URL's observations (ADR-0016 1d, 2; S6).

A **version** is a run of consecutive observations at the primary source URL
whose extracted text has one ``content_hash`` (Option B2), ordered by first
observation. Content that comes back after another (A→B→A) is a new version,
never a return to the first: the runs are versions 1, 2 and 3.

What does not take part in the comparison, and why:

* a **fetch failure** (a 404, a timeout) is no content. It neither ends a run
  nor starts one; it is the ``source_status`` and nothing more — never a
  repeal (ADR-0016 2);
* an observation of a **tombstoned blob** (ADR-0010 §7) cannot be re-verified,
  so its content is unknown. It is excluded from the comparison and listed,
  with its time, so the gap is visible rather than bridged silently.

An observation whose bytes the extractor **holds** (an empty page, a garbled
PDF, personal data) is a run of its own, keyed by its bytes, carrying the
hold. It is a version candidate that may not be written; a caller that
writes versions in order must stop at it, because skipping it would renumber
every version after it the day the hold is resolved.

Reading goes through the observatory's own readers only: the corrected view
of the log (ADR-0015) via :func:`~lovspor.promotion.archive.authority_fetches`,
the tombstone fold, and the blob store with its integrity checks. A blob that
is missing without a tombstone is a damaged archive and refuses the read.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation
from lovspor.promotion.archive import Fetch, SourceStatus, read_blob, source_status
from lovspor.promotion.decisions import ObservationUsed, utc_text
from lovspor.promotion.extract import extract_regulation
from lovspor.promotion.identity import content_hash
from lovspor.promotion.models import HeldExtraction


class ExclusionReason(StrEnum):
    TOMBSTONED = "tombstoned"


class ExcludedObservation(BaseModel):
    """An observation left out of the content comparison, with the reason."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: str
    url: str
    sha256: str
    reason: ExclusionReason


class Sighting(BaseModel):
    """One capture at the primary URL: the hash of its text, or the hold on its bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: AwareDatetime
    sha256: str
    content_hash: str | None
    held_reason: str | None = None
    held_detail: str | None = None

    @property
    def run_key(self) -> str:
        return self.content_hash if self.content_hash is not None else f"held:{self.sha256}"


class DerivedVersion(BaseModel):
    """One run of one content at the primary URL; ``content_hash`` is ``None`` when held."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    primary_url: str
    sightings: tuple[Sighting, ...]
    corroborating_urls: tuple[str, ...] = ()

    @property
    def content_hash(self) -> str | None:
        return self.sightings[0].content_hash

    @property
    def held(self) -> Sighting | None:
        first = self.sightings[0]
        return first if first.content_hash is None else None

    @property
    def observed_at_first(self) -> datetime:
        return self.sightings[0].observed_at

    @property
    def observed_at_last(self) -> datetime:
        return self.sightings[-1].observed_at

    @property
    def source_sha256s(self) -> tuple[str, ...]:
        """The blobs of this run, first-seen first: ``[0]`` is the one rendered."""
        return tuple(dict.fromkeys(s.sha256 for s in self.sightings))

    @property
    def observations(self) -> tuple[ObservationUsed, ...]:
        return tuple(
            ObservationUsed(
                observed_at=utc_text(s.observed_at), url=self.primary_url, sha256=s.sha256
            )
            for s in self.sightings
        )

    def through(self, cutoff: datetime) -> DerivedVersion | None:
        """This run as it stood at ``cutoff``; ``None`` when it was not yet observed."""
        kept = tuple(s for s in self.sightings if s.observed_at <= cutoff)
        return self.model_copy(update={"sightings": kept}) if kept else None


class PrimaryHistory(BaseModel):
    """Every version observed at one primary URL, what was excluded, and the last outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary_url: str
    versions: tuple[DerivedVersion, ...]
    excluded: tuple[ExcludedObservation, ...]
    source_status: SourceStatus


def derive_versions(primary_url: str, sightings: Iterable[Sighting]) -> tuple[DerivedVersion, ...]:
    """Consecutive sightings of one content, oldest first, numbered from 1."""
    ordered = sorted(sightings, key=lambda s: (s.observed_at, s.sha256))
    runs: list[list[Sighting]] = []
    for sighting in ordered:
        if runs and runs[-1][-1].run_key == sighting.run_key:
            runs[-1].append(sighting)
        else:
            runs.append([sighting])
    return tuple(
        DerivedVersion(version=number, primary_url=primary_url, sightings=tuple(run))
        for number, run in enumerate(runs, start=1)
    )


def read_primary(
    log: ObservationLog, fetches: tuple[Fetch, ...], primary_url: str, through: datetime | None
) -> PrimaryHistory:
    """The versions at ``primary_url`` from the observations up to ``through`` (all if ``None``)."""
    seen = [f for f in fetches if through is None or f.observed_at <= through]
    if not any(f.url == primary_url for f in seen):
        msg = f"{primary_url} has no observation under this authority"
        raise PromotionRefusedError(msg)
    captures = [f for f in seen if isinstance(f, ArtifactObservation) and f.url == primary_url]
    tombstoned = log.tombstoned_hashes()
    kept = [f for f in captures if f.sha256 not in tombstoned]
    hashes = _texts(log, kept)
    versions = derive_versions(primary_url, (_sighting(f, hashes[f.sha256]) for f in kept))
    return PrimaryHistory(
        primary_url=primary_url,
        versions=tuple(_with_copies(v, seen) for v in versions),
        excluded=_excluded(captures, tombstoned),
        source_status=source_status(seen, primary_url),
    )


_Text = tuple[str | None, HeldExtraction | None]


def _texts(log: ObservationLog, captures: list[ArtifactObservation]) -> dict[str, _Text]:
    """Each distinct blob read and extracted once: its content hash, or its hold."""
    texts: dict[str, _Text] = {}
    for capture in captures:
        if capture.sha256 not in texts:
            texts[capture.sha256] = _text(read_blob(log, capture.sha256), capture.content_type)
    return texts


def _text(payload: bytes, content_type: str) -> _Text:
    extracted = extract_regulation(payload, content_type)
    if isinstance(extracted, HeldExtraction):
        return None, extracted
    return content_hash(extracted.regulation.full_text), None


def _sighting(capture: ArtifactObservation, text: _Text) -> Sighting:
    digest, held = text
    return Sighting(
        observed_at=capture.observed_at,
        sha256=capture.sha256,
        content_hash=digest,
        held_reason=held.reason.value if held is not None else None,
        held_detail=held.detail if held is not None else None,
    )


def _with_copies(version: DerivedVersion, seen: list[Fetch]) -> DerivedVersion:
    """Other URLs that served one of this run's very blobs — byte-identical copies only."""
    blobs = set(version.source_sha256s)
    urls = {f.url for f in seen if isinstance(f, ArtifactObservation) and f.sha256 in blobs}
    copies = tuple(sorted(urls - {version.primary_url}))
    return version.model_copy(update={"corroborating_urls": copies})


def _excluded(
    captures: list[ArtifactObservation], tombstoned: frozenset[str]
) -> tuple[ExcludedObservation, ...]:
    gone = sorted((f for f in captures if f.sha256 in tombstoned), key=lambda f: f.observed_at)
    return tuple(
        ExcludedObservation(
            observed_at=utc_text(f.observed_at),
            url=f.url,
            sha256=f.sha256,
            reason=ExclusionReason.TOMBSTONED,
        )
        for f in gone
    )
