"""Observation intervals of promoted versions, re-read from the log (ADR-0016 2, 3; S6).

``observations/<slug>.json`` holds, per promoted version, the instants its
content was observed at the primary URL: first, last, how many times, from
which blobs, and which other URLs served the same bytes. Those are facts of
the log, so they are re-derived from it, never carried forward by hand:
:func:`with_intervals` takes a document's file and the versions read from the
log (:mod:`lovspor.promotion.versions`) and returns the file with every
version's interval as the log has it, the last outcome at the URL, and the
observations excluded from the comparison (a tombstoned blob, listed).

Only the interval moves. The version list, each version's content hash and
its first observation (the front matter carries it), and the promotion audit
are what the corpus already committed; when the log does not reproduce them
— promoted version *n* is not the log's version *n* of the same text at the
same URL, first seen at the same instant — the file is refused, never
patched. That is also what keeps an extractor bump out of a refresh: new text
hashes are a migration (ADR-0016 4e), not new intervals.
"""

from __future__ import annotations

from lovspor.errors import PromotionRefusedError
from lovspor.promotion.decisions import utc_text
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.versions import DerivedVersion, PrimaryHistory
from lovspor.promotion.writer import ObservationsFile, VersionObservations


def with_intervals(existing: ObservationsFile, history: PrimaryHistory) -> ObservationsFile:
    """``existing`` with every promoted version's interval as ``history`` reads it."""
    derived = {version.version: version for version in history.versions}
    entries = tuple(_refreshed(entry, derived.get(entry.version)) for entry in existing.versions)
    return existing.model_copy(
        update={
            "versions": entries,
            "source_status": history.source_status,
            "excluded": history.excluded,
        }
    )


def _refreshed(entry: VersionObservations, version: DerivedVersion | None) -> VersionObservations:
    problem = _divergence(entry, version)
    if problem is not None or version is None:
        msg = f"promoted version {entry.version} does not match the log: {problem}"
        raise PromotionRefusedError(msg)
    return entry.model_copy(
        update={
            "observed_at_last": utc_text(version.observed_at_last),
            "observation_count": len(version.sightings),
            "source_sha256s": version.source_sha256s,
            "corroborating_urls": version.corroborating_urls,
        }
    )


def _divergence(entry: VersionObservations, version: DerivedVersion | None) -> str | None:
    """Why ``version`` is not the promoted ``entry``, or ``None`` when it is."""
    if entry.promotion.extractor_version != EXTRACTOR_VERSION:
        return (
            f"it was extracted with extractor {entry.promotion.extractor_version}, this engine "
            f"runs {EXTRACTOR_VERSION}; that is a migration, not a refresh"
        )
    if version is None:
        return "the log has no such version at this URL"
    if version.primary_url != entry.primary_url:
        return f"its primary URL is {entry.primary_url}, the log's version is at another"
    if version.content_hash != entry.content_hash:
        return "the log's version of that number has another text"
    if utc_text(version.observed_at_first) != entry.observed_at_first:
        return (
            f"it was first observed at {entry.observed_at_first}, "
            f"the log's version at {utc_text(version.observed_at_first)}"
        )
    return None
