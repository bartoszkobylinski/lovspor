"""Decide by URL path whether a discovered candidate is worth a capture (#348).

Discovery proposes; capture fetches. Between them sits a choice that was
deliberately deferred: which proposals to observe. On a whole-site crawl of
201 municipalities, 95% of distinct URLs name no regulation in their path,
and that share is crawl budget spent off-target. This module is the pure
half of that choice: an allow-list of path stems, a document rule, and one
predicate.

It is **not a classifier** (ADR-0010 defers deciding what is law). It scopes
budget by what a path *names*, never by what a document says. A regulation on
a path that names none — ``/tjenester/vann-og-avlop/`` — is a known, accepted
miss, not a bug to fix by opening the page.

Matching is a substring match on the percent-decoded, lowercased path. It
has to be a substring: ``forskrift`` is a whole path segment once in 142,069
crawled URLs and almost always the head of a longer slug. Substring matching
is also why the list stays short — ``delegeringsreglement`` is covered by
``reglement`` and ``politivedtekt`` by ``vedtekt``, so neither is listed.

The stems are the owner's decision of 2026-09-19 on issue #348, tuned offline
against the crawled corpus: the recommended twenty plus the three spellings
of public hearing (``hoyring``, ``hoering``, ``horing``), which go in together
so bokmål and nynorsk sites are held to the same rule.

That set rejected the URLs of known positives (#507). The classification
study of 2026-10-03 (665 labelled artifacts, and the R1.1 enacted-regulation
classifier over the whole archive) found 61 of 117 labelled enacted
regulations, 14 of 84 labelled adopted rule sets and 151 of 286 R1.1-positive
URLs on paths the set rejected — about 40 of ~153 distinct forskrifter. The
owner's decision of 2026-10-03 widens it two ways:

- **More stems**, each read off a rejected positive's URL and each naming a
  regulation or the page that holds one: school rules (``ordensregl``,
  ``regler-for``), fees (``gebyr``, ``betalingssats``), councillors' pay
  (``godtgjor``, ``for-folkevalgte``), school permission, routes, districts
  and SFO, leash orders (``bandtvang``), alcohol sale hours, and the
  nynorsk forms the first set lacked (``retningsliner`` beside
  ``retningslinjer``, ``ordensregler`` beside ``ordensreglar``).
- **Every linked document**: a path ending in one of
  :data:`DOCUMENT_SUFFIXES` is selected whatever else it names. Linked PDFs
  are the richest regulation source the study found — 85 of the ~153
  forskrifter also exist as a PDF or DOCX — and their file names
  (``Aksellastrestriksjoner Gran Kommune 2024.pdf``) anticipate no stem.

Measured on the 144,842 URLs the archive held on 2026-10-03, stems and suffix
together select 79 of the 117 labelled enacted regulations (56 before), 79 of
the 84 adopted rule sets (70) and 195 of the 286 R1.1 positives (135), at a
selected share of 11.0% of all URLs against 6.6% before. Nearly every URL
still rejected repeats a regulation from a CMS sidebar on a news page whose
own, canonical page is selected; ``docs/operations.md`` lists the rest.

The owner's decision of 2026-09-26 placed the step: it sits inside ADR-0010's
deferral of classification, and it is global, behind a flag, switched on after
one measured pass. So it is off unless :data:`ENV_CAPTURE_SELECTION` is ``1``,
and off still counts what it would keep — that count is the measurement.

Proposals from a registered listing page bypass the path rule (#348's
proposed shape): a listing is a page a reviewer declared, so what it links to
is not a guess.
"""

import os
from typing import NamedTuple
from urllib.parse import unquote, urlsplit

from lovspor.observatory.discovery import Candidate, DiscoveryResult

#: Set to 1 in the job's environment to fetch only selected candidates.
ENV_CAPTURE_SELECTION = "LOVSPOR_OBSERVATORY_CAPTURE_SELECTION"

#: Discovery's reason for a second proposal of a URL it already holds.
_DUPLICATE = "duplicate_candidate"

REGULATION_PATH_STEMS: tuple[str, ...] = (
    "forskrift",
    "reglement",
    "vedtekt",
    "kunngjor",
    "kunngjør",
    "kunngjering",
    "kunngjoering",
    "bestemmels",
    "lokal-lov",
    "regelverk",
    "retningslin",
    "regulativ",
    "lover-og-regler",
    "lover_og_regler",
    "kommunale-regler",
    "skoleregler",
    "skulereglar",
    "ordensregl",
    "foresegn",
    "lovverk",
    "hoyring",
    "hoering",
    "horing",
    # Owner decision 2026-10-03 (#507): read off rejected positives' URLs.
    "regler-for",
    "reglar-for",
    "gebyr",
    "betalingssats",
    "abonnementsvilk",
    "godtgjor",
    "godtgjør",
    "godtgjer",
    "arbeidsvilk",
    "for-folkevalgte",
    "for-folkevalde",
    "permisjon",
    "skolerute",
    "skulerute",
    "skolekrets",
    "skulekrins",
    "skolefritidsordning",
    "skulefritidsordning",
    "/sfo",
    "bandtvang",
    "båndtvang",
    "alkoholpolitisk",
    "skjenketid",
    "salgstid",
    "salstid",
)

#: A path ending in one of these is a linked document, selected whatever its
#: path names (owner decision 2026-10-03, #507).
DOCUMENT_SUFFIXES: tuple[str, ...] = (".pdf", ".docx", ".doc", ".odt")


def selects(url: str) -> bool:
    """True when the URL's path contains one of :data:`REGULATION_PATH_STEMS`
    or ends in one of :data:`DOCUMENT_SUFFIXES`.

    Only the path is read: a host, query or fragment naming a regulation says
    nothing about the page the path addresses.
    """
    path = unquote(urlsplit(url).path).lower()
    return path.endswith(DOCUMENT_SUFFIXES) or any(stem in path for stem in REGULATION_PATH_STEMS)


def selection_enabled() -> bool:
    """Whether this process fetches only selected candidates; one spelling, ``1``."""
    return os.environ.get(ENV_CAPTURE_SELECTION, "").strip() == "1"


class Selection(NamedTuple):
    """Which of discovery's proposals a pass will fetch, and how many it would."""

    chosen: tuple[Candidate, ...]
    proposed: int
    #: Proposals the rule keeps, counted whether or not it is switched on.
    matching: int
    enabled: bool

    @property
    def unselected(self) -> int:
        """Proposals left unfetched because selection declined them."""
        return self.proposed - len(self.chosen)


def choose(result: DiscoveryResult, listings: tuple[str, ...], enabled: bool) -> Selection:
    """Apply the path rule to ``result``'s candidates, keeping their order.

    ``listings`` are the source's registered listing pages. A candidate any
    of them proposed bypasses the rule — including one a sitemap proposed
    first, which discovery keeps and files the listing's proposal of as a
    duplicate.
    """
    listed = {s.url for s in result.skipped if s.reason == _DUPLICATE and s.found_in in listings}
    kept = tuple(
        c for c in result.candidates if c.found_in in listings or c.url in listed or selects(c.url)
    )
    chosen = kept if enabled else result.candidates
    return Selection(chosen, len(result.candidates), len(kept), enabled)
