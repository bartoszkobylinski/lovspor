"""Ask one host what it offers a crawler, before anything registers it.

Three requests at most — ``robots.txt``, the conventional ``/sitemap.xml``, the
front page — and fewer when the answer is already decided. What the results
*mean* is :mod:`lovspor.observatory.survey`'s question; this module only goes
and gets them.

**This is not a hole in the activation gate.** ADR-0010 §4 gates *capture*, and
:class:`~lovspor.observatory.fetch.Fetcher` enforces it. Reading a site's own
published policy cannot be gated on having cleared that policy — the engine
already fetches ``robots.txt`` outside the gate for exactly that reason
(:class:`~lovspor.observatory.fetch.RobotsGate`). What this module adds is two
further requests, both of which robots.txt is consulted about first, and neither
of which is stored as observed material: a probe answers "could this be
registered", not "what does this source say", and the observation log stays a
log of activated sources only.

The probe has no recorded access-policy check to take its politeness from, so it
carries what all 201 cleared sources carry: the same user agent, and the same
7-second spacing. A recon pass must never be more aggressive than the capture it
is scouting for.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from lovspor.errors import ParseError
from lovspor.observatory.discovery import parse_discovery_document
from lovspor.observatory.fetch import DEFAULT_MAX_BYTES, CaptureSettings, RobotsGate
from lovspor.observatory.survey import RobotsReadout, SiteShape, read_site_shape

#: Identical to the agent every registered source was cleared with, so a
#: municipality reading its logs sees one crawler rather than two.
SURVEY_USER_AGENT = "lovspor-observatory/0.1 (+https://lovspor.no/observatory)"

#: The spacing recorded on all 201 activated sources' access-policy checks.
DEFAULT_DELAY_SECONDS = 7.0

#: A marker hunt needs the head of a page, not all of it.
DEFAULT_FRONT_PAGE_BYTES = 512 * 1024

SITEMAP_PATH = "sitemap.xml"


@dataclass(frozen=True)
class ProbeSettings:
    """Knobs the probe owns, because no registry row exists to own them yet."""

    user_agent: str = SURVEY_USER_AGENT
    timeout_seconds: float = 10.0
    delay_seconds: float = DEFAULT_DELAY_SECONDS
    sleep: Callable[[float], None] = field(default=time.sleep)


class SiteProbe:
    """One host per :meth:`read`, at most three requests, nothing stored."""

    def __init__(self, client: httpx.Client, settings: ProbeSettings | None = None) -> None:
        self._client = client
        self._settings = settings or ProbeSettings()
        self._owes_delay = False

    def read(self, domain: str, max_bytes: int = DEFAULT_FRONT_PAGE_BYTES) -> SiteShape:
        """What ``domain`` offers, as a survey row.

        Stops at the first answer that settles it: a policy that cannot be read
        or that refuses the root makes the remaining requests both pointless and
        impolite.
        """
        base = f"https://{domain}/"
        self._owes_delay = False
        gate = RobotsGate(
            self._client, CaptureSettings(timeout_seconds=self._settings.timeout_seconds)
        )
        robots = self._robots(gate, base)
        if not (robots.readable and robots.allows_root):
            return read_site_shape(
                domain=domain, robots=robots, conventional_sitemap=False, front_page=b""
            )
        return read_site_shape(
            domain=domain,
            robots=robots,
            conventional_sitemap=self._serves_discovery_document(gate, base, robots),
            front_page=self._body(base, max_bytes),
        )

    def _robots(self, gate: RobotsGate, base: str) -> RobotsReadout:
        self._wait()
        return RobotsReadout(
            readable=gate.readable(base),
            allows_root=gate.allows(base, self._settings.user_agent),
            declared_sitemaps=gate.sitemaps(base),
        )

    def _serves_discovery_document(
        self, gate: RobotsGate, base: str, robots: RobotsReadout
    ) -> bool:
        """Whether ``/sitemap.xml`` serves something discovery could read.

        Skipped when the site declares a sitemap: that answer already wins, and
        a request whose result cannot change the outcome is one a recon pass has
        no business making.

        A 200 is not the test. Sites routinely answer any path with a styled
        page, and reading such a page as an index is how a crawler ends up
        following a 404's navigation. The parser discovery itself uses decides.
        """
        if robots.declared_sitemaps:
            return False
        url = f"{base}{SITEMAP_PATH}"
        if not gate.allows(url, self._settings.user_agent):
            return False
        payload = self._body(url, DEFAULT_MAX_BYTES)
        try:
            parse_discovery_document(payload, url)
        except ParseError:
            return False
        return True

    def _body(self, url: str, max_bytes: int) -> bytes:
        """The head of one document, or empty when the host did not deliver it.

        Unreachable is recorded as empty rather than raised: a pass over
        hundreds of hosts cannot end because one of them refused a connection,
        and an empty body reads downstream as "no marker found", which is the
        truth about what was seen.
        """
        self._wait()
        try:
            with self._client.stream(
                "GET",
                url,
                headers={"User-Agent": self._settings.user_agent},
                timeout=self._settings.timeout_seconds,
            ) as response:
                if response.status_code != httpx.codes.OK:
                    return b""
                return _capped(response, max_bytes)
        except httpx.HTTPError:
            return b""

    def _wait(self) -> None:
        """Space out requests to this host; the first one owes nothing."""
        if self._owes_delay:
            self._settings.sleep(self._settings.delay_seconds)
        self._owes_delay = True


def _capped(response: httpx.Response, max_bytes: int) -> bytes:
    """Read a response up to ``max_bytes`` without ever holding more."""
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) >= max_bytes:
            return bytes(body[:max_bytes])
    return bytes(body)
