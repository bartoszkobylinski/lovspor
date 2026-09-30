"""Decide by URL path whether a discovered candidate is worth a capture (#348).

Discovery proposes; capture fetches. Between them sits a choice that was
deliberately deferred: which proposals to observe. On a whole-site crawl of
201 municipalities, 95% of distinct URLs name no regulation in their path,
and that share is crawl budget spent off-target. This module is the pure
half of that choice: an allow-list of path stems and one predicate.

It is **not a classifier** (ADR-0010 defers deciding what is law). It scopes
budget by what a path *names*, never by what a document says. A regulation on
a path that names none — ``/tjenester/vann-og-avlop/`` — is a known, accepted
miss, not a bug to fix by opening the page.

Matching is a substring match on the percent-decoded, lowercased path. It
has to be a substring: ``forskrift`` is a whole path segment once in 142,069
crawled URLs and almost always the head of a longer slug. Substring matching
is also why the list is short — ``delegeringsreglement`` is covered by
``reglement`` and ``politivedtekt`` by ``vedtekt``, so neither is listed.

The stems are the owner's decision of 2026-09-19 on issue #348, tuned offline
against the crawled corpus: the recommended twenty plus the three spellings
of public hearing (``hoyring``, ``hoering``, ``horing``), which go in together
so bokmål and nynorsk sites are held to the same rule.
"""

from urllib.parse import unquote, urlsplit

REGULATION_PATH_STEMS: tuple[str, ...] = (
    "forskrift",
    "reglement",
    "vedtekt",
    "kunngjor",
    "kunngjør",
    "kunngjering",
    "kunngjoering",
    "planbestemmels",
    "lokal-lov",
    "regelverk",
    "retningslinj",
    "regulativ",
    "lover-og-regler",
    "lover_og_regler",
    "kommunale-regler",
    "skoleregler",
    "skulereglar",
    "ordensreglar",
    "foresegn",
    "lovverk",
    "hoyring",
    "hoering",
    "horing",
)


def selects(url: str) -> bool:
    """True when the URL's path contains one of :data:`REGULATION_PATH_STEMS`.

    Only the path is read: a host, query or fragment naming a regulation says
    nothing about the page the path addresses.
    """
    path = unquote(urlsplit(url).path).lower()
    return any(stem in path for stem in REGULATION_PATH_STEMS)
