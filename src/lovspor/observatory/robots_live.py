"""robots.txt read live at activation, and the Crawl-delay floor it sets (issue #449).

``docs/operations.md`` has long required ``rate_limit_seconds`` to be at least
the source's own ``Crawl-delay``, but nothing checked it: the rule rested on a
reviewer reading the file by eye, and Kongsvinger (3401) showed that reading
can disagree with a spec-literal parse. The owner's decision of 2026-09-29 is
that ``activate-source`` refuses a rate below the delay parsed from the live
file, with :meth:`RobotsPolicy.crawl_delay` choosing the group.

The read follows redirects, up to the five hops RFC 9309 §2.3.1.2 asks a
crawler to follow — Stad (4649) serves its file from the apex after a redirect
from ``www``. Each hop must stay inside the cleared domain, by the one test
the capture gate uses (:func:`host_within_domain`): a file served from a host
nobody cleared is not this source's policy. Five hops exceeded, a hop off the
domain, a transport error and a 5xx all refuse activation; a 4xx is a file
that publishes no rules, as the capture gate reads it, and so sets no floor.
"""

from urllib.parse import urljoin, urlsplit

import httpx

from lovspor.errors import RateBelowCrawlDelayError, RobotsUnreadableError
from lovspor.observatory.registry import AccessPolicyCheck, host_within_domain
from lovspor.observatory.robots import RobotsPolicy

MAX_REDIRECT_HOPS = 5
_REDIRECT_STATUS = 300
_CLIENT_ERROR_STATUS = 400
_SERVER_ERROR_STATUS = 500


def published_policy(response: httpx.Response) -> RobotsPolicy | None:
    """The policy a non-redirect response publishes, or ``None`` if unreadable.

    Shared with the capture gate so both read a status code the same way:
    5xx is unreadable; 4xx means no rules were published, an empty rule set
    rather than a document — a styled 404 page must not read as directives.
    """
    if response.status_code >= _SERVER_ERROR_STATUS:
        return None
    if response.status_code >= _CLIENT_ERROR_STATUS:
        return RobotsPolicy.parse([])
    return RobotsPolicy.parse(response.text.splitlines())


def read_robots(client: httpx.Client, url: str, user_agent: str, domain: str) -> RobotsPolicy:
    """The robots.txt at ``url``, following redirects that stay inside ``domain``.

    Raises:
        RobotsUnreadableError: unreachable, 5xx, a redirect off ``domain`` or
            without a ``Location``, or more than :data:`MAX_REDIRECT_HOPS`.
    """
    for _ in range(MAX_REDIRECT_HOPS + 1):
        response = _get(client, url, user_agent)
        if not _REDIRECT_STATUS <= response.status_code < _CLIENT_ERROR_STATUS:
            return _readable(response, url)
        url = _next_hop(url, response, domain)
    raise RobotsUnreadableError(
        f"robots.txt redirected more than {MAX_REDIRECT_HOPS} times (last target {url})"
    )


def refuse_rate_below_crawl_delay(
    client: httpx.Client, check: AccessPolicyCheck, domain: str
) -> None:
    """Refuse a check whose rate is faster than the live file's ``Crawl-delay``.

    Raises:
        RobotsUnreadableError: the file could not be read inside ``domain``.
        RateBelowCrawlDelayError: ``rate_limit_seconds`` is below the delay.
    """
    policy = read_robots(client, check.robots_txt_url, check.user_agent, domain)
    delay = policy.crawl_delay(check.user_agent)
    if delay is not None and check.rate_limit_seconds < delay:
        raise RateBelowCrawlDelayError(
            f"rate_limit_seconds {check.rate_limit_seconds} is below the Crawl-delay "
            f"{delay} that {check.robots_txt_url} declares for this crawler; record a "
            f"rate of at least {delay} and run activate-source again"
        )


def _get(client: httpx.Client, url: str, user_agent: str) -> httpx.Response:
    # Named like every other request (issue #350), and never auto-following:
    # each hop has to pass the domain test before it is asked for.
    try:
        return client.get(url, headers={"User-Agent": user_agent}, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise RobotsUnreadableError(f"cannot read {url}: {exc}") from exc


def _readable(response: httpx.Response, url: str) -> RobotsPolicy:
    policy = published_policy(response)
    if policy is None:
        raise RobotsUnreadableError(f"{url} answered HTTP {response.status_code}")
    return policy


def _next_hop(url: str, response: httpx.Response, domain: str) -> str:
    location: str | None = response.headers.get("location")
    if not location:
        raise RobotsUnreadableError(
            f"{url} redirected (HTTP {response.status_code}) without Location"
        )
    target: str = urljoin(url, location)
    host = urlsplit(target).hostname
    if host is None or not host_within_domain(host, domain):
        raise RobotsUnreadableError(
            f"{url} redirects to {target}, outside the cleared domain {domain}"
        )
    return target
