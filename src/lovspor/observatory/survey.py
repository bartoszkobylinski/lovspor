"""What a municipal site offers a crawler, read before the site is registered.

Registration needs evidence, and gathering it is the one step the observatory
never had a supported command for. The 2026-08-20 sweep over all 358
municipalities — the source of "190 of 358 serve one at the conventional path"
in :mod:`lovspor.observatory.commands` — ran as a script and persisted nothing,
so its population cannot be re-derived (issue #349). This module is the part of
that work that decides what a probe *means*; the probing itself and the record
it writes are the caller's.

**Nothing here fetches.** It is given what came back and reports a shape, which
is what lets the same rules be exercised against hand-written fixtures under
ADR-0010 §5 rather than against someone's live server.

Six entries, kept apart on purpose:

``robots_unreadable``
    ``robots.txt`` could not be read. :mod:`lovspor.observatory.fetch` treats
    that as a denial and so does this — an unreadable policy is not an absent
    one, and the difference decides whether a human has to look.
``robots_disallowed``
    The site publishes a policy and it refuses the root. Recorded as a refusal,
    never as a missing index: one is the site's decision, the other our finding.
``declared_sitemap``
    ``robots.txt`` names a sitemap. The cheapest possible entry, so it wins over
    anything else the page may also reveal.
``conventional_sitemap``
    Nothing declared, but ``/sitemap.xml`` is served anyway — the majority case
    for Norwegian municipalities, and invisible to a robots-only reader.
``browser_assembled``
    No sitemap, and the front page carries the API markers issue #194 found on
    all twelve of the sitemap-less municipalities it examined. These are not
    dead ends; their index exists and a sitemap reader cannot see it.
``no_machine_index``
    No sitemap and no marker. The only entry that genuinely means "a human must
    look", which is why the five above must not drain into it.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

#: Strings whose presence in a front page is evidence of the browser-assembled
#: CMS issue #194 found on 12 of 12 sitemap-less municipalities. They are
#: reported verbatim rather than resolved to a vendor name: the markers are
#: observable, the vendor behind them is an inference, and #194 could only call
#: it "ACOS-ish" from exactly this evidence. Sorted, so a record's marker list
#: is stable regardless of the order they appear in the page.
API_MARKERS: tuple[str, ...] = ("/api/presentation/", "/kunde/grensesnitt/")

Entry = Literal[
    "robots_unreadable",
    "robots_disallowed",
    "declared_sitemap",
    "conventional_sitemap",
    "browser_assembled",
    "no_machine_index",
]


class RobotsReadout(BaseModel):
    """What one host's ``robots.txt`` said, as the prober found it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    readable: bool
    allows_root: bool
    declared_sitemaps: tuple[str, ...] = ()


class SiteShape(BaseModel):
    """One host's offer to a crawler, as a survey row.

    Carries the evidence beside the conclusion: a row that says
    ``robots_disallowed`` still reports the sitemap the site declares, because
    the refusal and the declaration are two separate facts about it and folding
    them loses the one that will matter when the policy changes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str
    entry: Entry
    robots_readable: bool
    robots_allows_root: bool
    declared_sitemaps: tuple[str, ...]
    front_page_markers: tuple[str, ...]


def front_page_markers(payload: bytes) -> tuple[str, ...]:
    """The :data:`API_MARKERS` present in this page.

    Decodes leniently: a pass over hundreds of hosts must not stop because one
    server mislabels its encoding, and a marker is an ASCII path either way.
    """
    text = payload.decode("utf-8", errors="ignore")
    return tuple(marker for marker in API_MARKERS if marker in text)


def _entry_of(robots: RobotsReadout, conventional_sitemap: bool, markers: tuple[str, ...]) -> Entry:
    """Which entry this host offers, cheapest usable route first."""
    if not robots.readable:
        return "robots_unreadable"
    if not robots.allows_root:
        return "robots_disallowed"
    if robots.declared_sitemaps:
        return "declared_sitemap"
    if conventional_sitemap:
        return "conventional_sitemap"
    if markers:
        return "browser_assembled"
    return "no_machine_index"


def read_site_shape(
    *,
    domain: str,
    robots: RobotsReadout,
    conventional_sitemap: bool,
    front_page: bytes,
) -> SiteShape:
    """Read one host's probe results into a survey row."""
    markers = front_page_markers(front_page)
    return SiteShape(
        domain=domain,
        entry=_entry_of(robots, conventional_sitemap, markers),
        robots_readable=robots.readable,
        robots_allows_root=robots.allows_root,
        declared_sitemaps=robots.declared_sitemaps,
        front_page_markers=markers,
    )
