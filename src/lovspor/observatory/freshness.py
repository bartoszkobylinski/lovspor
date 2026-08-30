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

**A URL that has never yielded content has a record of its own, and issue #204
is what happens when nothing reads it.** Both rules above key on a *sighting*,
so a URL that only ever failed was proposed, fetched, failed and proposed
again, on every pass, forever: 962 URLs accounted for 39,672 outbound requests
that came back with nothing, one municipality's own 404 page asked for 154
times. Every one of those also occupied a rate-limit slot a capturable
document was queued behind, which is why this is not merely untidy.

So a failure is now evidence too — but only the kind that describes the URL
rather than the moment (:func:`is_url_property`). A timeout or a 503 says
nothing about the page and still counts for nothing here. A 404, a redirect we
will not follow, a body over the cap: those will land the same way tomorrow,
and the URL is left alone for :func:`failure_backoff` before being asked
again. Never dropped — a 404 can become a 200 the day a page is published, and
refusing to look again would be #151's silent zero wearing a different hat.
The site's own claim overrides the wait outright: a ``lastmod`` later than the
failure is the source telling us the URL is not what it was.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, time, timedelta
from typing import NamedTuple

from lovspor.observatory.discovery import Candidate
from lovspor.observatory.model import ArtifactObservation, FetchFailure, ObservationRecord

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
    page for as long as its ``lastmod`` stayed put. That exclusion is why
    :class:`FailureHold` exists rather than a second entry in this map — a
    failure is evidence about a different question, and answering the sighting
    question with it is exactly the confusion this comment used to invite.

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


#: Failure outcomes that are a property of the URL and of policy settled before
#: the request, rather than of the moment it was made. Named rather than
#: derived: what makes ``robots_disallowed`` repeatable is that we decided it,
#: and what makes ``redirect_not_followed`` repeatable is that the target sits
#: outside a domain a human cleared — neither is legible from a status code,
#: and ``redirect_not_followed`` carries a 301 or a 302, which read as
#: perfectly ordinary responses.
_URL_PROPERTY_OUTCOMES = frozenset(
    {
        "redirect_limit_exceeded",
        "redirect_not_followed",
        "response_exceeded_max_bytes",
        "robots_disallowed",
    }
)

#: Client errors that are the server saying "not now" rather than "not this
#: URL" — a request timeout, a retry sent too early, a rate-limit refusal.
#: Treating these as properties of the URL would let a source that throttled us
#: once hold its own pages back for two days.
_MOMENTARY_CLIENT_ERRORS = frozenset({408, 425, 429})

_HTTP_OUTCOME_PREFIX = "http_"
_CLIENT_ERROR_STATUS = 400
_SERVER_ERROR_STATUS = 500


def is_url_property(outcome: str) -> bool:
    """Whether this failure describes the URL rather than the moment.

    The category is the point, not the status code. It was defined the other
    way round in issue #204 — around ``http_404`` and its neighbours — because
    the archive at the time was dominated by them. Once #210 and #212 had
    removed two unrelated re-fetch loops, a clean fleet sample said otherwise:
    ``redirect_not_followed`` was the largest deterministic bucket by a factor
    of four, 100 URLs asked 268 times in 41 minutes, and it has no status code
    of its own to be recognised by.

    Anything unrecognised is a property of the moment. That is the safe
    default and the same asymmetry the rest of this module runs on: an outcome
    a future fetcher invents will be re-asked until somebody classifies it,
    which costs requests, rather than held back, which costs observations.
    """
    if outcome in _URL_PROPERTY_OUTCOMES:
        return True
    status = _status_in(outcome)
    if status is None:
        return False
    if status in _MOMENTARY_CLIENT_ERRORS:
        return False
    return _CLIENT_ERROR_STATUS <= status < _SERVER_ERROR_STATUS


def _status_in(outcome: str) -> int | None:
    """The status an ``http_404``-shaped outcome names, or None for the rest.

    ``transport_error: ConnectError`` and ``timeout`` are named failures with
    no status at all, and must not be read as one.
    """
    if not outcome.startswith(_HTTP_OUTCOME_PREFIX):
        return None
    try:
        return int(outcome.removeprefix(_HTTP_OUTCOME_PREFIX))
    except ValueError:
        return None


class FailureHold(NamedTuple):
    """A URL's unbroken run of one URL-property failure.

    The run ends when the URL yields content, or when it fails a *different*
    way: a page that went from 404 to a redirect changed, and a count that
    survived the change would describe two behaviours as one.
    """

    outcome: str
    consecutive: int
    last_failed_at: datetime

    def then(self, record: FetchFailure) -> "FailureHold":
        """This hold extended by one more failure of the same kind, or a new one."""
        if record.outcome != self.outcome:
            return FailureHold(record.outcome, 1, record.observed_at)
        return FailureHold(
            self.outcome,
            self.consecutive + 1,
            max(self.last_failed_at, record.observed_at),
        )


class CaptureState(NamedTuple):
    """What the log already knows about the URLs a pass is about to consider.

    Two maps rather than two arguments because they are folded in one reading
    of the log and consulted by one decision. Splitting them across the call
    chain is how a caller ends up passing the sightings and forgetting the
    failures, which reads exactly like the bug this pairing fixes.
    """

    #: When each URL was last seen *with content*.
    observed: dict[str, datetime]
    #: Where each URL that has never yielded content stands in its run of
    #: URL-property failures. A URL in both maps is governed by ``observed``.
    holds: dict[str, FailureHold]

    @classmethod
    def empty(cls) -> "CaptureState":
        return cls({}, {})


def collect_capture_state(
    state: CaptureState, authority_id: str | None = None
) -> Callable[[ObservationRecord], None]:
    """A collector for :meth:`ObservationLog.scan_into` that folds both maps.

    One collector rather than two so the log is read once. It is already the
    most expensive thing a capture does before it fetches anything (issue
    #201), and a second full pass to learn about failures would double that
    cost to answer half a question.
    """
    observe = collect_latest_observations(state.observed, authority_id)

    def collect(record: ObservationRecord) -> None:
        observe(record)
        if isinstance(record, ArtifactObservation):
            _clear_hold(state.holds, record, authority_id)
        elif isinstance(record, FetchFailure):
            _extend_hold(state.holds, record, authority_id)

    return collect


def capture_state(
    records: Iterable[ObservationRecord], authority_id: str | None = None
) -> CaptureState:
    """The pull-based form of :func:`collect_capture_state`."""
    state = CaptureState.empty()
    collect = collect_capture_state(state, authority_id)
    for record in records:
        collect(record)
    return state


def _clear_hold(
    holds: dict[str, FailureHold], record: ArtifactObservation, authority_id: str | None
) -> None:
    """Content arrived, so the URL's run of refusals is over.

    Dropped rather than zeroed: a URL that serves content is governed by its
    sighting from here on, and a hold left behind at count zero would be a
    fact about the past that no longer decides anything.
    """
    if authority_id is None or record.authority_id == authority_id:
        holds.pop(record.url, None)


def _extend_hold(
    holds: dict[str, FailureHold], record: FetchFailure, authority_id: str | None
) -> None:
    """Fold one failure into the URL's run, or ignore it as momentary."""
    if authority_id is not None and record.authority_id != authority_id:
        return
    if not is_url_property(record.outcome):
        return
    held = holds.get(record.url)
    holds[record.url] = (
        FailureHold(record.outcome, 1, record.observed_at) if held is None else held.then(record)
    )


#: How long a URL the site says nothing readable about is taken on trust once
#: it has been observed. The same figure as the observation SLA, deliberately
#: rather than by import: that one says how often a *source* is looked at, this
#: says how long one undated *page* is left alone, and they are two claims that
#: happen to agree today. Argue them down separately (issue #209).
UNDATED_RECHECK = timedelta(hours=24)

#: How long a URL is left alone after the first failure that describes it.
FAILED_RECHECK = timedelta(hours=24)

#: The longest it is ever left alone, however long its run of failures. Held to
#: two observation windows on purpose. A longer ceiling would save more
#: requests in steady state — a dead URL still costs one a day, across every
#: source — but it buys them with the one thing this archive cannot recover,
#: and the waste #204 measured was per *round*, minutes apart, which the first
#: window already removes. Widening it is a separate argument, to be had
#: against measurements of the steady state rather than of a bootstrap.
FAILED_RECHECK_CEILING = timedelta(hours=48)

#: Enough doublings to reach any plausible ceiling; the cap exists so that a
#: URL with thousands of failures behind it cannot overflow the arithmetic.
_MAX_DOUBLINGS = 8


def failure_backoff(consecutive: int) -> timedelta:
    """How long to leave a URL alone after ``consecutive`` failures of one kind.

    Doubling, because a URL that has refused the same way ten times running is
    a worse bet than one that refused once — but bounded, because neither is
    ever a certainty and the ceiling is what keeps a page published after a
    long absence findable.
    """
    doublings = min(max(consecutive - 1, 0), _MAX_DOUBLINGS)
    return min(FAILED_RECHECK * (1 << doublings), FAILED_RECHECK_CEILING)


def worth_capturing(candidate: Candidate, state: CaptureState, now: datetime) -> bool:
    """Whether this candidate should be fetched now.

    A URL we have content for is judged on that sighting; one we do not is
    judged on its run of failures. The order matters and only reads one way: a
    page that has served content is a page whose freshness question is about
    change, not about whether it exists.
    """
    last_seen = state.observed.get(candidate.url)
    if last_seen is not None:
        return _worth_after_sighting(candidate, last_seen, now)
    return _worth_after_failure(candidate, state.holds.get(candidate.url), now)


def _worth_after_sighting(candidate: Candidate, last_seen: datetime, now: datetime) -> bool:
    """Declined in two cases, and both need the URL to have been observed.

    The site says it last changed at a moment we already hold a later sighting
    from — the certain case, unchanged since this module was written. Or the
    site says nothing readable and we saw it less than :data:`UNDATED_RECHECK`
    ago, which is a judgement about our own record rather than about the site's
    claim, and is there because a candidate with no ``lastmod`` was otherwise
    re-fetched on every pass forever (issue #209).

    A sighting stamped ahead of ``now`` fetches, whichever comparison it would
    have fed. Either the clock is wrong or the record is, and neither makes it
    usable evidence that a page was seen after the site changed it — so the
    rule is hoisted above both branches rather than guarding only the age
    arithmetic it happens to break.
    """
    if last_seen > now:
        return True
    changed_at = _site_claim(candidate)
    if changed_at is not None:
        return last_seen <= changed_at
    return now - last_seen >= UNDATED_RECHECK


def _worth_after_failure(candidate: Candidate, held: FailureHold | None, now: datetime) -> bool:
    """A URL that has never yielded content, judged on how it has been failing.

    The site's claim outranks the wait: a ``lastmod`` later than the failure is
    the source saying this URL is not what it was when we asked, which is the
    strongest reason there is to ask again. Without that override the backoff
    would be a bet on the past over a statement about the present, and a page
    published the day after its 404 would wait out the whole window.

    A failure stamped ahead of the clock fetches, for the reason a sighting
    does: an age cannot be computed from it, and a disagreeing clock must not
    be able to hold a page back.
    """
    if held is None or held.last_failed_at > now:
        return True
    changed_at = _site_claim(candidate)
    if changed_at is not None and held.last_failed_at <= changed_at:
        return True
    return now - held.last_failed_at >= failure_backoff(held.consecutive)


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
