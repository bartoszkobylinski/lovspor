"""What a fetch outcome means (issues #188, #204).

Every fetch that returned no bytes is a :class:`FetchFailure`, and that is
true of a 301 as much as of a 404 — the hop carried no document. But one of
them lost the document and the other did not, and the record's ``kind`` cannot
tell them apart. On the archive as it stands, 313,727 of 416,532
``fetch_failure`` records are followed redirects: read by ``kind`` alone the
log reports a 50.4% failure rate, against a real 12.4%.

The engine never read it that way — :meth:`Fetcher.capture` returns only the
terminal result, so the round summary, the sweep totals and ``status`` have
always counted the honest number. What had no honest reading was the archive
itself, which is what anyone auditing it, and every derived layer built on it
later, will actually open. This module is that reading, as code rather than as
a paragraph somebody has to have read: one definition, applied by the engine
and available to any reader of the log, and it applies to every record already
written rather than splitting the archive at a commit boundary.

**The two questions have opposite safe defaults, and that is deliberate.**
:func:`lost_the_document` asks whether a document was missed, and an outcome
nobody has classified counts as a loss — an unrecognised failure must not
vanish from the failure rate. :func:`is_url_property` asks whether a URL may
be left alone for a while, and there an unrecognised outcome counts for
nothing — it costs a request rather than an observation window. Erring toward
the same direction in both would be wrong in one of them.
"""

import collections
from collections.abc import Callable
from dataclasses import dataclass, field

from lovspor.observatory.model import ArtifactObservation, FetchFailure, ObservationRecord

#: The one outcome that is not a failure at all: the hop redirected inside the
#: cleared domain and the fetcher went on to ask the target. The document
#: arrives — or fails — under its own record.
REDIRECT_FOLLOWED = "redirect_followed"

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


def is_redirect_hop(outcome: str) -> bool:
    """Whether this record is a followed redirect rather than a failed fetch.

    Stated as its own question because two rules turn on it and both would be
    wrong to answer by accident: a hop is not a lost document, and a hop must
    never start a backoff — the fetcher is about to ask the target, and holding
    the requested URL back over the hop it took would defer a page that is
    being fetched right now.
    """
    return outcome == REDIRECT_FOLLOWED


def lost_the_document(outcome: str) -> bool:
    """Whether this failure means the document was not obtained.

    Everything except a followed redirect. Total on purpose, and defaulting to
    loss: an outcome a future fetcher invents is counted against us until
    somebody classifies it, because a failure rate that quietly omits what it
    does not recognise is the number that gets quoted.
    """
    return not is_redirect_hop(outcome)


def is_url_property(outcome: str) -> bool:
    """Whether this failure describes the URL rather than the moment.

    The category is the point, not the status code. It was defined the other
    way round in issue #204 — around ``http_404`` and its neighbours — because
    the archive at the time was dominated by them. Once #210 and #212 had
    removed two unrelated re-fetch loops, a clean fleet sample said otherwise:
    ``redirect_not_followed`` was the largest deterministic bucket by a factor
    of four, 100 URLs asked 268 times in 41 minutes, and it has no status code
    of its own to be recognised by.

    Anything unrecognised is a property of the moment. That is the safe default
    here and the same asymmetry the freshness rule runs on: an outcome a future
    fetcher invents will be re-asked until somebody classifies it, which costs
    requests, rather than held back, which costs observations.
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


@dataclass
class ArchiveComposition:
    """What an observation log is made of, under the rule above.

    Mutable and folded into, because the log is larger than any answer taken
    from it and the whole point of :meth:`ObservationLog.scan_into` is to pay
    for the answer instead of for the archive (issue #199).
    """

    artifacts: int = 0
    #: Followed redirects: recorded as failures, and not failures.
    hops: int = 0
    #: Failures that actually lost a document.
    lost: int = 0
    tombstones: int = 0
    by_outcome: collections.Counter[str] = field(default_factory=collections.Counter)

    @property
    def records(self) -> int:
        return self.artifacts + self.hops + self.lost + self.tombstones

    @property
    def loss_rate(self) -> float:
        """Lost documents as a share of every record, or 0.0 for an empty log.

        Zero rather than undefined: the caller is a report, and a report that
        raises on an empty archive tells an operator less than one that says
        nothing was lost out of nothing.
        """
        return self.lost / self.records if self.records else 0.0

    @property
    def naive_failure_rate(self) -> float:
        """What the same log reports when ``kind`` alone is counted.

        Kept beside the real figure rather than left implicit: the gap between
        the two is the whole of issue #188, and a reader who has quoted the
        wrong number needs to see both to recognise which one they had.
        """
        return (self.hops + self.lost) / self.records if self.records else 0.0


def collect_composition(into: ArchiveComposition) -> Callable[[ObservationRecord], None]:
    """A collector for :meth:`ObservationLog.scan_into` that folds ``into``."""

    def collect(record: ObservationRecord) -> None:
        if isinstance(record, ArtifactObservation):
            into.artifacts += 1
        elif isinstance(record, FetchFailure):
            into.by_outcome[record.outcome] += 1
            if lost_the_document(record.outcome):
                into.lost += 1
            else:
                into.hops += 1
        else:
            into.tombstones += 1

    return collect
