"""Whether a candidate is worth fetching again (ADR-0010 §7).

A full pass over a municipal site is hours of politely-spaced requests, so
repeating it wholesale for a handful of changed pages wastes the source's
capacity as much as ours. Sitemaps carry a ``lastmod``, which makes a cheaper
question possible: has anything changed since we last saw this URL?

The comparison is deliberately one-sided. ``lastmod`` is the site's own
unverified claim and this module never treats it as fact — it only ever uses
it to *decline* work, never to assert that a page is current. Every ambiguity
resolves toward fetching: an unparseable stamp, a URL never observed, a
timestamp without a timezone. Re-fetching costs one request; skipping wrongly
costs an observation window that cannot be recovered, and ADR-0010 exists
because that asymmetry is the whole point.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, time

from lovspor.observatory.discovery import Candidate
from lovspor.observatory.model import ArtifactObservation, ObservationRecord

# "2026-08-18" — a W3C date with no time part, which is what municipal
# sitemaps overwhelmingly publish.
_DATE_ONLY_LENGTH = 10


def collect_latest_observations(
    latest: dict[str, datetime], authority_id: str | None = None
) -> Callable[[ObservationRecord], None]:
    """A collector for :meth:`ObservationLog.scan_into` that folds into ``latest``.

    Push-based rather than a pass over materialised records, because the log
    is orders of magnitude larger than this map and only the map is wanted:
    610,850 records reduced to 81,408 URLs when this was written (issue #199).

    Failures are excluded on purpose: a timeout says nothing about whether the
    page changed, and treating it as a sighting would let one bad night hide a
    page for as long as its ``lastmod`` stayed put.

    ``authority_id`` narrows the fold to one source's own observations, which
    is what a capture can act on: its candidates are URLs on that source's
    cleared domain, and the gate records them under that source. The narrowing
    is safe in one direction only, and that is the direction it errs — should
    two registered sources ever cover one host, an observation filed under the
    other id is simply not seen here, and an unseen observation re-fetches a
    page. Skipping one wrongly is the mistake this module exists to avoid.
    """

    def collect(record: ObservationRecord) -> None:
        if not isinstance(record, ArtifactObservation):
            return
        if authority_id is not None and record.authority_id != authority_id:
            return
        seen = latest.get(record.url)
        latest[record.url] = record.observed_at if seen is None else max(seen, record.observed_at)

    return collect


def latest_observations(records: Iterable[ObservationRecord]) -> dict[str, datetime]:
    """The most recent time each URL was observed *with content*.

    The pull-based form of :func:`collect_latest_observations`, for a caller
    that already holds the records it wants folded.
    """
    latest: dict[str, datetime] = {}
    collect = collect_latest_observations(latest)
    for record in records:
        collect(record)
    return latest


def parse_site_lastmod(value: str) -> datetime | None:
    """Read a sitemap ``lastmod``, erring later rather than earlier.

    A date with no time becomes the *end* of that day, and a timestamp with no
    timezone is read as UTC. Both push the moment later, which can only ever
    make this module fetch a page it might have skipped — the direction that
    costs a request instead of an observation.

    Returns None for anything it cannot read, which the caller treats as
    "fetch it".
    """
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.time() == time.min and len(value.strip()) <= _DATE_ONLY_LENGTH:
        parsed = datetime.combine(parsed.date(), time.max)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def worth_capturing(candidate: Candidate, observed: Mapping[str, datetime]) -> bool:
    """Whether this candidate should be fetched now.

    False only in the one case that is safe: the site says the page last
    changed at a moment we already have an observation from after. Anything
    less certain than that is fetched.
    """
    last_seen = observed.get(candidate.url)
    if last_seen is None or candidate.site_reported_lastmod is None:
        return True
    changed_at = parse_site_lastmod(candidate.site_reported_lastmod)
    return changed_at is None or last_seen <= changed_at
