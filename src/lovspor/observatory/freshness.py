"""Whether a candidate is worth fetching again (ADR-0010 §7).

A full pass over a municipal site is hours of politely-spaced requests, so
repeating it wholesale for a handful of changed pages wastes the source's
capacity as much as ours. Sitemaps carry a ``lastmod``, which makes a cheaper
question possible: has anything changed since we last saw this URL?

The comparison is deliberately one-sided. ``lastmod`` is the site's own
unverified claim and this module never treats it as fact — it only ever uses
it to *decline* work, never to assert that a page is current. A URL never
observed is always fetched, and so is one whose recorded change is later than
our last sighting. Re-fetching costs one request; skipping wrongly costs an
observation window that cannot be recovered, and ADR-0010 exists because that
asymmetry is the whole point.

**A URL the site says nothing about is the exception, and it had to become
one.** Until issue #209 every such candidate was fetched on every pass,
because there was no claim to compare against and "fetch when unsure" was
applied to a URL already observed minutes earlier. Discovery proposes plenty
of them — a link found inside a page carries no ``lastmod`` even when the
sitemap it came from stamps every entry — so a crawl could not terminate:
79% of all captures in the first bootstrap were re-captures, one PDF 1,675
times, and eight lanes ran 44 hours without finishing a single municipality.

For those, and only those, this module now consults its own record instead of
a claim that does not exist: a URL seen inside :data:`UNDATED_RECHECK` is left
alone. That is a genuine weakening — an undated page changing twice a day is
caught a day late — accepted because the alternative is not "catch it sooner"
but "re-download the whole site forever and never reach the pages behind it".
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, time, timedelta

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


#: How long a URL the site says nothing readable about is taken on trust once
#: it has been observed. The same figure as the observation SLA, deliberately
#: rather than by import: that one says how often a *source* is looked at, this
#: says how long one undated *page* is left alone, and they are two claims that
#: happen to agree today. Argue them down separately (issue #209).
UNDATED_RECHECK = timedelta(hours=24)


def worth_capturing(candidate: Candidate, observed: Mapping[str, datetime], now: datetime) -> bool:
    """Whether this candidate should be fetched now.

    Declined in two cases, and both need the URL to have been observed already.
    The site says it last changed at a moment we already hold a later sighting
    from — the certain case, unchanged since this module was written. Or the
    site says nothing readable and we saw it less than :data:`UNDATED_RECHECK`
    ago, which is a judgement about our own record rather than about the site's
    claim, and is there because a candidate with no ``lastmod`` was otherwise
    re-fetched on every pass forever (issue #209).

    An observation stamped ahead of ``now`` fetches. It cannot be used to
    compute an age, and a clock that disagrees with the archive must not be
    able to hold a page back.
    """
    last_seen = observed.get(candidate.url)
    if last_seen is None:
        return True
    changed_at = _site_claim(candidate)
    if changed_at is not None:
        return last_seen <= changed_at
    age = now - last_seen
    return age < timedelta() or age >= UNDATED_RECHECK


def _site_claim(candidate: Candidate) -> datetime | None:
    """When the site says this page last changed, or None if it did not say.

    An absent ``lastmod`` and one that cannot be read are the same answer: the
    site told us nothing we can compare against. Collapsing them here keeps the
    two from drifting apart, which is how an unparseable stamp ended up on the
    fetch-always path while an absent one was about to gain a window.
    """
    if candidate.site_reported_lastmod is None:
        return None
    return parse_site_lastmod(candidate.site_reported_lastmod)
