"""One source's capture pass: fetch what discovery proposed and has changed.

Split out of ``commands`` so the pass can grow a step without the command
module growing with it. ``capture`` and every lane of a sweep run exactly
this pass, so whatever it declines, it declines identically in both.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NamedTuple

import typer

from lovspor.errors import AmbiguousSourceError, StaleSourceError
from lovspor.observatory.discovery import Candidate
from lovspor.observatory.fetch import Fetcher
from lovspor.observatory.freshness import CaptureState, worth_capturing
from lovspor.observatory.model import ArtifactObservation


class CaptureCounts(NamedTuple):
    """What one source's pass did, and whether the limit cut it short.

    ``capped`` is the point of the type: the three counters cannot express the
    difference between a sitemap that ran out and a pass that was stopped, and
    that difference is what makes a truncated source read as finished (#172).

    ``deferred`` is counted apart from ``unchanged`` because the two are
    different answers. A URL is unchanged when the site says so; it is deferred
    when it has refused us the same way and we are waiting before asking again
    (#204). Folding them together would hide a source whose candidate list has
    quietly become a list of dead ends.
    """

    captured: int
    failed: int
    unchanged: int
    capped: bool
    #: The pass stopped because a candidate's host is claimed by more than one
    #: activated source. Its own field rather than a kind of `capped`: both
    #: leave the source's archive incomplete, but a limit was our choice and
    #: this is a register that cannot name a publisher (#215).
    contested: bool = False
    deferred: int = 0
    #: Redirect hops followed on the way to the records above. Reported rather
    #: than left implicit: they are 75% of everything the log files as a
    #: failure (#188), and the pass that stopped counting them as failures
    #: must not be the pass that stopped mentioning them at all.
    redirects: int = 0
    #: The pass stopped because the register stopped filing a candidate under
    #: the row this run bound itself to. Apart from `contested` because the
    #: repair differs: that one needs a human to say which authority publishes
    #: a host, this one is already repaired and needs only a re-run (#221).
    stale: bool = False


@dataclass
class _Tally:
    """The running counts of one pass, before it knows how it ends."""

    captured: int = 0
    failed: int = 0
    unchanged: int = 0
    deferred: int = 0
    redirects: int = 0

    def counts(
        self, *, capped: bool = False, contested: bool = False, stale: bool = False
    ) -> CaptureCounts:
        return CaptureCounts(
            self.captured,
            self.failed,
            self.unchanged,
            capped,
            contested,
            self.deferred,
            self.redirects,
            stale,
        )


def capture_candidates(
    fetcher: Fetcher, candidates: tuple[Candidate, ...], state: CaptureState, limit: int
) -> CaptureCounts:
    """Fetch what has changed, in order, and report each outcome as it happens.

    A run over a municipal site is hours of politely-spaced requests, so the
    per-URL line is not noise: it is the only way an operator can tell a slow
    run from a stuck one.
    """
    tally = _Tally()
    # One instant for the whole pass: a clock read per candidate would let two
    # candidates observed at the same moment fall on opposite sides of the
    # re-check window, for no reason a reader could reconstruct later.
    now = datetime.now(UTC)
    for candidate in candidates:
        if not worth_capturing(candidate, state, now):
            if candidate.url in state.observed:
                tally.unchanged += 1
            else:
                tally.deferred += 1
            continue
        if limit and tally.captured + tally.failed >= limit:
            typer.echo(f"stopping at --limit {limit}")
            return tally.counts(capped=True)
        refused = _fetch(fetcher, candidate, tally)
        if refused is not None:
            return refused
    return tally.counts()


def _fetch(fetcher: Fetcher, candidate: Candidate, tally: _Tally) -> CaptureCounts | None:
    """Fetch one candidate into the tally, or the counts of a pass it ended."""
    try:
        record = fetcher.capture(candidate.url, candidate.discovery_method)
    except (AmbiguousSourceError, StaleSourceError) as exc:
        # A refusal about the register, not about the page. Either it cannot
        # name one authority for this host — discovery cleared the source's
        # own host, but a candidate may sit on a subdomain a second source
        # also claims (#215) — or it no longer names the one this run bound
        # itself to (#221). Reaching either as a traceback would end the
        # pass with an empty stderr and the records already appended
        # unexplained (#208's shape).
        typer.echo(f"Refused: {exc}", err=True)
        stale = isinstance(exc, StaleSourceError)
        return tally.counts(contested=not stale, stale=stale)
    tally.redirects += len(record.provenance.redirect_chain)
    if isinstance(record, ArtifactObservation):
        tally.captured += 1
        typer.echo(f"  {record.http_status}  {candidate.url}")
    else:
        tally.failed += 1
        typer.echo(f"  {record.outcome}  {candidate.url}")
    return None


def capture_summary(counts: CaptureCounts) -> str:
    """The line a whole pass is read from, and the one the fleet greps.

    ``deferred`` is appended rather than inserted: the prefix is what an
    operator's scripts match on to tell a finished source from a running one,
    and a new counter must not move it.
    """
    return (
        f"captured: {counts.captured} | failed: {counts.failed} "
        f"| unchanged since last seen: {counts.unchanged} "
        f"| deferred after repeated failure: {counts.deferred} "
        f"| redirect hops: {counts.redirects}"
    )
