"""Find candidate URLs to observe, without stepping around the capture gates.

Discovery answers one question — *what is there to look at?* — and answers it
from documents the authority publishes for exactly that purpose: sitemaps,
sitemap indexes, RSS and Atom feeds. Reading a listing page written for humans
is a per-CMS adapter and is deliberately not here.

Two properties matter more than the parsing:

**Discovery does not fetch. It asks the fetcher to.** Every discovery document
goes through :class:`~lovspor.observatory.fetch.Fetcher`, so the activation
gate, live ``robots.txt`` and the per-source rate limit apply to a sitemap the
same way they apply to a regulation, and the sitemap lands in the append-only
log as an observation of its own. That is not bookkeeping: what a municipality
listed on a given day is exactly the kind of evidence the observatory exists
to preserve (ADR-0010 §3), and it is what makes a later "this page appeared
between these two observations" claim checkable.

**A candidate is a proposal, never a capture.** Discovery returns URLs; it
never fetches one. Deciding to observe a candidate is a separate step, and
keeping it separate is what stops a sitemap listing 40,000 URLs from turning
one discovery run into a mass download.

Nothing is dropped silently. A link on another host, an unusable scheme, a
document past the depth or budget bound — each is returned as a
:class:`SkippedLink` with a reason, because a URL that vanishes without a
trace is indistinguishable from a parser that failed to see it.
"""

import gzip
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from io import BytesIO
from urllib.parse import urljoin, urlsplit

from lxml import etree
from pydantic import BaseModel, ConfigDict, Field

from lovspor.errors import ParseError, SourceNotActivatedError, TombstonedArtifactError
from lovspor.observatory.fetch import Fetcher
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation
from lovspor.observatory.registry import SourceRecord, capture_host
from lovspor.parsing.xml_normalizer import safe_parser

# Discovery methods, recorded on every observation this module causes. They
# name *how the URL was found*, which is a different question from how the
# bytes arrived (the provenance channel), and ADR-0010 §2 requires both.
DISCOVERY_ENTRY_POINT = "registered_entry_point"
DISCOVERY_SITEMAP = "sitemap"
DISCOVERY_SITEMAP_INDEX = "sitemap_index"
DISCOVERY_FEED = "feed"

DEFAULT_MAX_DOCUMENTS = 50
DEFAULT_MAX_DEPTH = 3
# The sitemap protocol caps an uncompressed sitemap at 50 MB. The cap here is
# on the *decompressed* size because that is what a gzip bomb inflates: a few
# hundred bytes on the wire pass the fetcher's byte cap untouched.
DEFAULT_MAX_DECOMPRESSED_BYTES = 50 * 1024 * 1024

_GZIP_MAGIC = b"\x1f\x8b"
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ATOM_ALTERNATE = frozenset({"", "alternate"})


class DiscoveredLink(BaseModel):
    """One link read out of a discovery document.

    ``is_nested_document`` separates a sitemap index entry — another document
    to read — from a page to propose. Both come out of the same parse, and
    conflating them would either crawl pages during discovery or ignore every
    sitemap behind an index.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1)
    discovery_method: str = Field(min_length=1)
    is_nested_document: bool
    site_reported_lastmod: str | None = None


class Candidate(BaseModel):
    """A URL worth observing, and where the proposal came from.

    ``site_reported_lastmod`` is the site's own claim, kept verbatim and
    unparsed. It is a scheduling hint, never an observation: nothing here
    checked it, and turning "2026-08-01" into a timestamp would dress an
    unverified string up as a recorded fact.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1)
    discovery_method: str = Field(min_length=1)
    found_in: str = Field(min_length=1)
    site_reported_lastmod: str | None = None


class SkippedLink(BaseModel):
    """A link discovery declined to follow, and why.

    ``found_in`` is ``None`` for a configured entry point: it was not found in
    any document, someone put it in the configuration.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    found_in: str | None = None


class DiscoveryResult(BaseModel):
    """What one discovery run proposed, declined, and actually read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str = Field(min_length=1)
    candidates: tuple[Candidate, ...] = ()
    skipped: tuple[SkippedLink, ...] = ()
    documents_read: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscoverySettings:
    """Bounds on one discovery run.

    All three are bounds, not tuning: an unbounded walk over someone else's
    sitemap index is the politeness failure ADR-0010 §4 exists to prevent, and
    a run that cannot end cannot be reviewed.
    """

    max_documents: int = DEFAULT_MAX_DOCUMENTS
    max_depth: int = DEFAULT_MAX_DEPTH
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES


def _local_name(element: etree._Element) -> str:
    """The tag without its namespace, or ``""`` for a non-element node.

    Matching on the local name is deliberate: municipal CMS exports routinely
    omit the sitemap namespace or declare the wrong one, and a namespace-strict
    reader would report those files as empty rather than as sitemaps.
    """
    if not isinstance(element.tag, str):
        return ""
    return etree.QName(element).localname


def _descendants(parent: etree._Element, name: str) -> Iterator[etree._Element]:
    return (element for element in parent.iter() if _local_name(element) == name)


def _text(parent: etree._Element, name: str) -> str | None:
    """The stripped text of the first ``name`` descendant, or None if empty.

    An unresolved entity reference — the XXE payload shape — leaves an element
    whose text is ``None``, so it reads as absent here and the link is dropped.
    """
    for element in _descendants(parent, name):
        text = (element.text or "").strip()
        return text or None
    return None


def _read_urlset(root: etree._Element, document_url: str) -> Iterator[DiscoveredLink]:
    for element in _descendants(root, "url"):
        location = _text(element, "loc")
        if location is None:
            continue
        yield DiscoveredLink(
            url=urljoin(document_url, location),
            discovery_method=DISCOVERY_SITEMAP,
            is_nested_document=False,
            site_reported_lastmod=_text(element, "lastmod"),
        )


def _read_sitemapindex(root: etree._Element, document_url: str) -> Iterator[DiscoveredLink]:
    for element in _descendants(root, "sitemap"):
        location = _text(element, "loc")
        if location is None:
            continue
        yield DiscoveredLink(
            url=urljoin(document_url, location),
            discovery_method=DISCOVERY_SITEMAP_INDEX,
            is_nested_document=True,
            site_reported_lastmod=_text(element, "lastmod"),
        )


def _alternate_href(entry: etree._Element) -> str | None:
    """The Atom entry's alternate link — the entry's own page, not an enclosure."""
    for element in _descendants(entry, "link"):
        if element.get("rel", "").lower() in _ATOM_ALTERNATE:
            return (element.get("href") or "").strip() or None
    return None


def _read_atom(root: etree._Element, document_url: str) -> Iterator[DiscoveredLink]:
    for entry in _descendants(root, "entry"):
        href = _alternate_href(entry)
        if href is None:
            continue
        yield DiscoveredLink(
            url=urljoin(document_url, href),
            discovery_method=DISCOVERY_FEED,
            is_nested_document=False,
            site_reported_lastmod=_text(entry, "updated"),
        )


def _read_rss(root: etree._Element, document_url: str) -> Iterator[DiscoveredLink]:
    for item in _descendants(root, "item"):
        location = _text(item, "link")
        if location is None:
            continue
        yield DiscoveredLink(
            url=urljoin(document_url, location),
            discovery_method=DISCOVERY_FEED,
            is_nested_document=False,
            site_reported_lastmod=_text(item, "pubDate"),
        )


_READERS = {
    "urlset": _read_urlset,
    "sitemapindex": _read_sitemapindex,
    "feed": _read_atom,
    "rss": _read_rss,
}


def _decompressed(payload: bytes, limit: int) -> bytes:
    """Gunzip ``payload`` if it is gzipped, refusing an oversized expansion.

    Sitemaps are commonly served as ``.xml.gz``, and the fetcher's byte cap
    applies to the compressed bytes only — which is precisely what makes a
    decompression bomb slip past it. One byte over the limit is read on
    purpose: it is how the limit is detected without buffering the rest.
    """
    if not payload.startswith(_GZIP_MAGIC):
        return payload
    try:
        with gzip.GzipFile(fileobj=BytesIO(payload)) as stream:
            data = stream.read(limit + 1)
    except (OSError, EOFError) as exc:
        raise ParseError(f"unreadable gzipped discovery document: {exc}") from exc
    if len(data) > limit:
        raise ParseError(f"decompressed discovery document exceeds {limit} bytes")
    return data


def parse_discovery_document(
    payload: bytes, document_url: str, max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES
) -> tuple[DiscoveredLink, ...]:
    """Read the links out of a sitemap, sitemap index, Atom or RSS document.

    Relative locations are resolved against ``document_url``: the sitemap
    protocol requires absolute URLs and Atom does not, and real exports get it
    wrong in both directions.

    Raises:
        ParseError: the bytes are not well-formed XML, decompress past the
            limit, or the root element is not a discovery document this reader
            recognises — an HTML error page served under a sitemap URL is the
            common case.
    """
    try:
        tree = etree.parse(BytesIO(_decompressed(payload, max_decompressed_bytes)), safe_parser())
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"{document_url}: malformed discovery document: {exc}") from exc
    root = tree.getroot()
    reader = _READERS.get(_local_name(root))
    if reader is None:
        raise ParseError(f"{document_url}: unrecognised discovery document <{_local_name(root)}>")
    return tuple(reader(root, document_url))


@dataclass(frozen=True)
class _Pending:
    """A discovery document waiting to be read."""

    url: str
    discovery_method: str
    depth: int
    found_in: str | None


@dataclass
class _Walk:
    """One discovery run in progress.

    ``candidates`` is a dict rather than a list so a URL listed in two sitemaps
    is proposed once, in the order it was first seen — discovery output feeds a
    pipeline ADR-0010 §7 requires to be a pure function of its input.
    """

    source: SourceRecord
    queue: deque[_Pending] = field(default_factory=deque)
    seen_documents: set[str] = field(default_factory=set)
    candidates: dict[str, Candidate] = field(default_factory=dict)
    skipped: list[SkippedLink] = field(default_factory=list)
    documents_read: list[str] = field(default_factory=list)

    def skip(self, url: str, reason: str, found_in: str | None) -> None:
        self.skipped.append(SkippedLink(url=url, reason=reason, found_in=found_in))


class Discoverer:
    """Walk an activated source's discovery documents and propose candidates."""

    def __init__(
        self, fetcher: Fetcher, log: ObservationLog, settings: DiscoverySettings | None = None
    ) -> None:
        self._fetcher = fetcher
        self._log = log
        self._settings = settings or DiscoverySettings()

    def discover(self, source: SourceRecord, entry_points: Iterable[str]) -> DiscoveryResult:
        """Read ``entry_points`` and whatever they index, and report the links.

        Raises:
            SourceNotActivatedError: ``source`` is not activated for capture.
                Its own URLs are refused by the fetcher's gate, which is the
                configuration error it should be — nothing was observed.
        """
        walk = _Walk(source=source)
        for url in entry_points:
            self._enqueue(walk, _Pending(url, DISCOVERY_ENTRY_POINT, 0, None))
        while walk.queue:
            self._visit(walk, walk.queue.popleft())
        return DiscoveryResult(
            authority_id=source.authority_id,
            candidates=tuple(walk.candidates.values()),
            skipped=tuple(walk.skipped),
            documents_read=tuple(walk.documents_read),
        )

    def _visit(self, walk: _Walk, pending: _Pending) -> None:
        if len(walk.documents_read) >= self._settings.max_documents:
            walk.skip(pending.url, "document_budget_exhausted", pending.found_in)
            return
        record = self._fetcher.capture(pending.url, pending.discovery_method)
        if not isinstance(record, ArtifactObservation):
            walk.skip(pending.url, f"fetch_failed: {record.outcome}", pending.found_in)
            return
        walk.documents_read.append(pending.url)
        links = self._links(walk, record, pending)
        for link in links:
            self._accept(walk, link, pending)

    def _links(
        self, walk: _Walk, record: ArtifactObservation, pending: _Pending
    ) -> tuple[DiscoveredLink, ...]:
        """The links in a document just read, or none if it could not be read.

        A CMS serving an HTML error page under a sitemap URL stops that
        document, not the run: the other entry points still have something to
        say about the source.
        """
        try:
            payload = self._log.read_blob(record.sha256)
            return parse_discovery_document(
                payload, pending.url, self._settings.max_decompressed_bytes
            )
        except ParseError:
            walk.skip(pending.url, "unparseable_document", pending.found_in)
        except TombstonedArtifactError:
            walk.skip(pending.url, "blob_tombstoned", pending.found_in)
        return ()

    def _accept(self, walk: _Walk, link: DiscoveredLink, parent: _Pending) -> None:
        if link.is_nested_document:
            self._enqueue(
                walk,
                _Pending(link.url, link.discovery_method, parent.depth + 1, parent.url),
            )
            return
        reason = _off_source(walk, link.url) or _duplicate(walk, link.url)
        if reason is not None:
            walk.skip(link.url, reason, parent.url)
            return
        walk.candidates[link.url] = Candidate(
            url=link.url,
            discovery_method=link.discovery_method,
            found_in=parent.url,
            site_reported_lastmod=link.site_reported_lastmod,
        )

    def _enqueue(self, walk: _Walk, pending: _Pending) -> None:
        """Queue a document to read, or record why it will not be read.

        Both bounds are checked here rather than at read time so a document
        that will never be fetched is never counted against the budget, and an
        index cycle is refused before it reaches the source.
        """
        reason = _off_source(walk, pending.url) or self._out_of_bounds(walk, pending)
        if reason is not None:
            walk.skip(pending.url, reason, pending.found_in)
            return
        walk.seen_documents.add(pending.url)
        walk.queue.append(pending)

    def _out_of_bounds(self, walk: _Walk, pending: _Pending) -> str | None:
        if pending.depth > self._settings.max_depth:
            return "max_depth_exceeded"
        if pending.url in walk.seen_documents:
            return "already_read"
        return None


def _off_source(walk: _Walk, url: str) -> str | None:
    """Why ``url`` is not this source's to fetch, or None if it is.

    The host check is the registry's, not a second implementation of it: a
    discovery walk that accepted a host the activation gate would refuse would
    only move the refusal one step later, after the request was already made.
    """
    if urlsplit(url).scheme not in _ALLOWED_SCHEMES:
        return "unsupported_scheme"
    try:
        host = capture_host(url)
    except SourceNotActivatedError:
        return "off_source_host"
    return None if walk.source.covers(host) else "off_source_host"


def _duplicate(walk: _Walk, url: str) -> str | None:
    return "duplicate_candidate" if url in walk.candidates else None
