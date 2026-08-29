"""Capture one URL politely, and record what happened (ADR-0010 §2, §4, §7).

This is the fetch step of the observatory and nothing else: no discovery, no
adapters, no scheduling. It takes a URL someone already decided to observe,
and its whole job is to reach that URL without damaging the relationship with
the authority serving it, then write down what came back.

Three gates stand in front of every request, in order:

1. **Activation** — :func:`~lovspor.observatory.registry.authorise_capture`
   decides whether this URL may be fetched at all. A URL that fails is a
   configuration error and raises, because nothing observed it and nothing
   should have tried.
2. **robots.txt, read live** — the registry's ``robots_allows`` records what a
   human concluded on the day of the check; a path added to ``robots.txt``
   afterwards is invisible to it. So robots is fetched and consulted per host,
   and a disallowed URL is *recorded* rather than raised: ADR-0010's validation
   asks that crawl logs demonstrate robots compliance, and a refusal that
   leaves no trace demonstrates nothing.
3. **Rate limit** — the per-source ``rate_limit_seconds`` from the recorded
   access-policy check, enforced per host by waiting. ADR-0010 calls per-source
   limits load-bearing rather than cosmetic; this is where that is true or not.

A redirect is followed only while its target stays inside the cleared domain.
The ban exists so a redirect cannot carry a fetch onto a host that never
passed gate 1 — including one ADR-0010 §4 forbids — and a hop from ``www.X``
to ``X`` never leaves it. Refusing that one blocked 5 of the first 36 sources
of the fleet bootstrap, which is why it is allowed (issue #159). A target
outside the domain is recorded with its ``Location`` header and not followed,
so the caller can submit it as its own capture and let it pass the gates on
its own merits.

Every hop is recorded, including the ones that were followed. A followed
redirect is filed as a ``FetchFailure`` because that hop returned no content,
which is what the type's docstring says and not what its name suggests — the
log therefore reports far more "failures" than documents it failed to get
(issue #188).

Every outcome ends up in the append-only log, success or not: a fetch that
timed out is evidence about the source, and Design Principle §15 keeps it
visible instead of leaving a gap that reads like an absence of interest.
"""

import hashlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from lovspor.errors import SourceNotActivatedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, FetchFailure, RetrievalProvenance
from lovspor.observatory.registry import (
    SourceRegistry,
    authorise_capture,
    capture_host,
    host_within_domain,
)

DEFAULT_TIMEOUT_SECONDS = 30.0
# Municipal PDFs are the large case; a cap keeps one oversized response from
# filling the archive disk. Truncated bytes are never stored: a partial body
# with a hash of its own would be evidence of something never served.
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
CHANNEL_HTTP = "http"
ROBOTS_PATH = "/robots.txt"
DEFAULT_CONTENT_TYPE = "application/octet-stream"
_REDIRECT_STATUS = 300
_MAX_REDIRECT_HOPS = 3
_CLIENT_ERROR_STATUS = 400
_SERVER_ERROR_STATUS = 500
# Recorded response headers, by allowlist. A blanket copy would put Set-Cookie
# and other per-session material into an append-only log that is never
# rewritten — immutability makes over-collection permanent.
RECORDED_HEADERS = frozenset(
    {
        "content-type",
        "content-length",
        "content-disposition",
        "etag",
        "last-modified",
        "date",
        "location",
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _selected_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers if key.lower() in RECORDED_HEADERS}


@dataclass(frozen=True)
class CaptureSettings:
    """Knobs that are the fetcher's own, not the source's.

    The politeness parameters are deliberately absent: user agent and rate
    limit come from the source's recorded access-policy check, so no
    deployment can widen them behind the registry's back (ADR-0010 §4).

    ``now``, ``monotonic`` and ``sleep`` are injectable because time is an
    input here, not an ambient fact: ``observed_at`` is an axis a test must be
    able to pin, and a rate limiter testable only by waiting stops being tested.
    """

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_bytes: int = DEFAULT_MAX_BYTES
    now: Callable[[], datetime] = _utc_now
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class _Attempt:
    """The three fields every record of one capture attempt carries.

    ``url`` is this hop's URL, which is what a failure record is about — a
    ``robots_disallowed`` or a 404 happened *there*. ``requested_url`` is the
    URL the caller asked for, which is what an artifact is filed under: the
    archive is asked "what did this URL serve", and answering under the
    address a redirect happened to land on leaves that question unanswered
    forever (issue #211).
    """

    authority_id: str
    url: str
    provenance: RetrievalProvenance
    canonical_domain: str = ""
    hops_left: int = 0
    requested_url: str = ""

    @property
    def subject(self) -> str:
        """The URL an artifact from this attempt belongs to."""
        return self.requested_url or self.url


@dataclass(frozen=True)
class _Request:
    """What one `capture` call is asking for, carried across its hops.

    Bundled rather than passed through as four more arguments: the chain grows
    hop by hop, and threading it by hand is how the requested URL got lost in
    the first place.
    """

    requested_url: str
    discovery_method: str
    adapter: str
    redirect_chain: tuple[str, ...] = ()

    def after(self, target: str) -> "_Request":
        return _Request(
            self.requested_url, self.discovery_method, self.adapter, (*self.redirect_chain, target)
        )


class RateLimiter:
    """Per-host spacing, enforced by waiting before the next request."""

    def __init__(self, settings: CaptureSettings) -> None:
        self._settings = settings
        self._last: dict[str, float] = {}

    def wait(self, host: str, minimum_interval: float) -> float:
        """Block until ``host`` may be hit again; return how long that took."""
        previous = self._last.get(host)
        waited = 0.0
        if previous is not None:
            waited = max(0.0, minimum_interval - (self._settings.monotonic() - previous))
            if waited > 0:
                self._settings.sleep(waited)
        self._last[host] = self._settings.monotonic()
        return waited


class RobotsGate:
    """robots.txt for a host, fetched once and cached for the run.

    An unreachable ``robots.txt`` denies. That is the uncomfortable direction —
    it stops capture when a server is merely flaky — and it is the right one:
    the alternative is crawling a site whose rules could not be read, the
    politeness failure ADR-0010 names as a risk to the very relationships the
    observatory depends on. A 4xx (including the usual 404) allows: no
    robots.txt is how a site says it publishes no restrictions.

    Rules are matched on the **product token** — the part of the User-Agent
    before the first ``/`` — so a site writing ``User-agent: lovspor-observatory``
    binds this crawler even though the header also carries a version and a
    contact URL.
    """

    def __init__(self, client: httpx.Client, settings: CaptureSettings) -> None:
        self._client = client
        self._settings = settings
        self._parsers: dict[str, RobotFileParser | None] = {}

    def allows(self, url: str, user_agent: str) -> bool:
        parser = self._parser_for(url)
        return False if parser is None else parser.can_fetch(user_agent, url)

    def sitemaps(self, url: str) -> tuple[str, ...]:
        """The sitemaps the host declares in its own ``robots.txt``.

        Where discovery should start is a question the source already answers
        in public, in the same file we are obliged to read anyway. Taking the
        answer from there rather than from a hand-copied argument keeps the
        entry points current when the site moves them, and makes the crawl
        follow what the site publishes rather than what someone remembered.

        Empty when robots.txt declares none or could not be read — the caller
        decides what to do about that, since "no declared sitemap" is an
        ordinary state and not an error.
        """
        parser = self._parser_for(url)
        return () if parser is None else tuple(parser.site_maps() or ())

    def _parser_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, ROBOTS_PATH, "", ""))
        if robots_url not in self._parsers:
            self._parsers[robots_url] = self._load(robots_url)
        return self._parsers[robots_url]

    def _load(self, robots_url: str) -> RobotFileParser | None:
        try:
            response = self._client.get(robots_url, timeout=self._settings.timeout_seconds)
        except httpx.HTTPError:
            return None
        if response.status_code >= _SERVER_ERROR_STATUS:
            return None
        parser = RobotFileParser()
        # 4xx means no rules were published, which is an empty rule set — not
        # a document to parse. Feeding it the error page's body would let a
        # styled 404 accidentally read as directives.
        published = (
            [] if response.status_code >= _CLIENT_ERROR_STATUS else response.text.splitlines()
        )
        parser.parse(published)
        return parser


class Fetcher:
    """Capture URLs for activated sources, recording every outcome."""

    def __init__(
        self,
        registry: SourceRegistry,
        log: ObservationLog,
        client: httpx.Client,
        settings: CaptureSettings | None = None,
    ) -> None:
        self._registry = registry
        self._log = log
        self._client = client
        self._settings = settings or CaptureSettings()
        self._limiter = RateLimiter(self._settings)
        self._robots = RobotsGate(client, self._settings)

    def declared_sitemaps(self, url: str) -> tuple[str, ...]:
        """The sitemaps the host serving ``url`` declares in its robots.txt.

        Exposed because where to start looking is a question the source
        answers in public, in the file this fetcher is obliged to read anyway.
        Nothing is fetched here beyond that file, and nothing is recorded: a
        declaration is not an observation.
        """
        return self._robots.sitemaps(url)

    def capture(
        self, url: str, discovery_method: str, adapter: str = CHANNEL_HTTP
    ) -> ArtifactObservation | FetchFailure:
        """Fetch ``url`` under its source's politeness terms and log the result.

        Raises:
            SourceNotActivatedError: no activated source covers this URL, or its
                host is globally denied. Nothing is fetched and nothing is
                recorded — no observation happened, and a record for a request
                never made would be fiction in an evidence log.
        """
        request = _Request(url, discovery_method, adapter)
        hops_left = _MAX_REDIRECT_HOPS
        while True:
            result = self._attempt(url, request, hops_left)
            if not (isinstance(result, FetchFailure) and result.outcome == "redirect_followed"):
                return result
            url = urljoin(url, result.http_headers["location"])
            request = request.after(url)
            hops_left -= 1

    def _attempt(
        self, url: str, request: _Request, hops_left: int
    ) -> ArtifactObservation | FetchFailure:
        """One hop: every gate, then the request. A redirect target is a new
        request and passes the gates again — a redirect must never smuggle a
        fetch past a rule that covers where it lands."""
        source = authorise_capture(self._registry, url)
        policy = source.access_policy
        if policy is None:
            raise SourceNotActivatedError(
                f"source {source.authority_id} is active without an access-policy check",
            )
        attempt = _Attempt(
            authority_id=source.authority_id,
            url=url,
            provenance=RetrievalProvenance(
                adapter=request.adapter,
                channel=CHANNEL_HTTP,
                discovery_method=request.discovery_method,
                user_agent=policy.user_agent,
                rate_limit_seconds=policy.rate_limit_seconds,
                redirect_chain=request.redirect_chain,
            ),
            canonical_domain=source.canonical_domain,
            hops_left=hops_left,
            requested_url=request.requested_url,
        )
        if not self._robots.allows(url, policy.user_agent):
            return self._failure(attempt, "robots_disallowed")
        self._limiter.wait(capture_host(url), policy.rate_limit_seconds)
        return self._request(attempt)

    def _request(self, attempt: _Attempt) -> ArtifactObservation | FetchFailure:
        headers = {"User-Agent": attempt.provenance.user_agent}
        try:
            with self._client.stream(
                "GET", attempt.url, headers=headers, timeout=self._settings.timeout_seconds
            ) as response:
                return self._read(attempt, response)
        except httpx.TimeoutException:
            return self._failure(attempt, "timeout")
        except httpx.HTTPError as exc:
            return self._failure(attempt, f"transport_error: {type(exc).__name__}")

    def _read(
        self, attempt: _Attempt, response: httpx.Response
    ) -> ArtifactObservation | FetchFailure:
        recorded = _selected_headers(response.headers.items())
        status = response.status_code
        if status >= _REDIRECT_STATUS:
            outcome = _outcome_for(status)
            if outcome == "redirect_not_followed":
                outcome = _redirect_outcome(attempt, recorded.get("location"))
            return self._failure(attempt, outcome, status, recorded)
        payload = self._body(response)
        if payload is None:
            return self._failure(attempt, "response_exceeded_max_bytes", status, recorded)
        record = ArtifactObservation(
            authority_id=attempt.authority_id,
            url=attempt.subject,
            observed_at=self._settings.now(),
            provenance=attempt.provenance,
            sha256=hashlib.sha256(payload).hexdigest(),
            content_type=recorded.get("content-type", DEFAULT_CONTENT_TYPE),
            http_status=status,
            http_headers=recorded,
        )
        self._log.append_artifact(record, payload)
        return record

    def _body(self, response: httpx.Response) -> bytes | None:
        """The response body, or None once it passes the cap."""
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > self._settings.max_bytes:
                return None
        return bytes(chunks)

    def _failure(
        self,
        attempt: _Attempt,
        outcome: str,
        status: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchFailure:
        record = FetchFailure(
            authority_id=attempt.authority_id,
            url=attempt.url,
            observed_at=self._settings.now(),
            provenance=attempt.provenance,
            outcome=outcome,
            http_status=status,
            http_headers=headers or {},
        )
        self._log.append(record)
        return record


def _redirect_outcome(attempt: _Attempt, location: str | None) -> str:
    """Name a redirect by whether it stays inside the cleared domain.

    The ban on following exists so a redirect cannot carry a fetch off the
    host the reviewer cleared (ADR-0010 §4). A hop from ``www.X`` to ``X`` —
    the commonest hosting shape among Norwegian municipalities — never leaves
    it, and refusing that one blocked 5 of the first 36 sources of the fleet
    bootstrap (issue #158). A target outside the domain, or one hop too many,
    still ends here.
    """
    if not location:
        return "redirect_not_followed"
    target = urljoin(attempt.url, location)
    try:
        host = capture_host(target)
    except SourceNotActivatedError:
        return "redirect_not_followed"
    if not host_within_domain(host, attempt.canonical_domain):
        return "redirect_not_followed"
    return "redirect_followed" if attempt.hops_left > 0 else "redirect_limit_exceeded"


def _outcome_for(status: int) -> str:
    """Name a non-2xx status as an outcome the log can be searched by."""
    return f"http_{status}" if status >= _CLIENT_ERROR_STATUS else "redirect_not_followed"
