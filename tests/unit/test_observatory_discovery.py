"""Discovery: finding candidate URLs without stepping around the capture gates.

Only the HTTP transport is mocked (pytest-httpx). The fetcher, its gates, the
registry, the log and the blob store are the real thing — discovery's whole
claim is that it cannot fetch anything the fetcher would refuse, and a test
that stubbed the fetcher would be testing the stub.
"""

import gzip
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import ParseError
from lovspor.observatory.discovery import (
    DISCOVERY_ENTRY_POINT,
    DISCOVERY_FEED,
    DISCOVERY_SITEMAP,
    DISCOVERY_SITEMAP_INDEX,
    Discoverer,
    DiscoverySettings,
    parse_discovery_document,
)
from lovspor.observatory.fetch import CaptureSettings, Fetcher
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation
from lovspor.observatory.registry import (
    AccessPolicyCheck,
    SourceRecord,
    SourceRegistry,
    activate,
)
from lovspor.observatory.storage import ObservatoryRoot

BAERUM_ID = "3201"
BAERUM_DOMAIN = "baerum.kommune.no"
ROBOTS_URL = f"https://{BAERUM_DOMAIN}/robots.txt"
SITEMAP_URL = f"https://{BAERUM_DOMAIN}/sitemap.xml"
INDEX_URL = f"https://{BAERUM_DOMAIN}/sitemap-index.xml"
NESTED_URL = f"https://{BAERUM_DOMAIN}/sitemap-forskrifter.xml"
FEED_URL = f"https://{BAERUM_DOMAIN}/kunngjoringer.atom"
PAGE_URL = f"https://{BAERUM_DOMAIN}/forskrifter/renovasjon"
OTHER_PAGE_URL = f"https://{BAERUM_DOMAIN}/forskrifter/vann"
USER_AGENT = "lovspor-observatory/0.1 (+https://lovspor.no/observatory)"
OBSERVED_AT = datetime(2026, 8, 18, 10, 30, tzinfo=UTC)

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _urlset(*entries: str) -> bytes:
    body = "".join(entries)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="{SITEMAP_NS}">{body}</urlset>'
    ).encode()


def _url_entry(loc: str, lastmod: str | None = None) -> str:
    stamp = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    return f"<url><loc>{loc}</loc>{stamp}</url>"


def _sitemapindex(*locs: str) -> bytes:
    body = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<sitemapindex xmlns="{SITEMAP_NS}">{body}</sitemapindex>'
    ).encode()


def _atom(*links: str) -> bytes:
    body = "".join(f'<entry><link rel="alternate" href="{link}"/></entry>' for link in links)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'
    ).encode()


def _rss(*links: str) -> bytes:
    body = "".join(f"<item><link>{link}</link></item>" for link in links)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


def _policy() -> AccessPolicyCheck:
    return AccessPolicyCheck(
        checked_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        robots_txt_url=ROBOTS_URL,
        robots_allows=True,
        terms_reviewed=True,
        terms_permit_capture=True,
        rate_limit_seconds=2.0,
        user_agent=USER_AGENT,
        reviewed_by="bartosz",
    )


def _source() -> SourceRecord:
    return activate(
        SourceRecord(
            authority_type="kommune",
            authority_id=BAERUM_ID,
            name="Bærum",
            canonical_domain=BAERUM_DOMAIN,
        ),
        _policy(),
    )


@pytest.fixture
def log(tmp_path: Path) -> ObservationLog:
    return ObservationLog(ObservatoryRoot(tmp_path / "observatory", forbidden=[]))


def _discoverer(
    log: ObservationLog, settings: DiscoverySettings | None = None
) -> tuple[Discoverer, SourceRecord]:
    source = _source()
    fetcher = Fetcher(
        SourceRegistry(sources={BAERUM_ID: source}),
        log,
        httpx.Client(),
        CaptureSettings(now=lambda: OBSERVED_AT, sleep=lambda _: None),
    )
    return Discoverer(fetcher, log, settings), source


def _allow_robots(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=ROBOTS_URL, text="User-agent: *\nAllow: /\n")


class TestParseSitemap:
    def test_a_urlset_yields_page_links(self) -> None:
        payload = _urlset(_url_entry(PAGE_URL, "2026-08-01"), _url_entry(OTHER_PAGE_URL))

        links = parse_discovery_document(payload, SITEMAP_URL)

        assert [(link.url, link.is_nested_document) for link in links] == [
            (PAGE_URL, False),
            (OTHER_PAGE_URL, False),
        ]
        assert links[0].site_reported_lastmod == "2026-08-01"
        assert links[1].site_reported_lastmod is None

    def test_a_sitemap_index_yields_nested_documents(self) -> None:
        links = parse_discovery_document(_sitemapindex(NESTED_URL), INDEX_URL)

        assert [(link.url, link.is_nested_document) for link in links] == [(NESTED_URL, True)]

    def test_a_sitemap_without_a_namespace_is_still_read(self) -> None:
        """Municipal CMS exports omit or misdeclare the namespace; the element
        names are the signal, not the declaration."""
        payload = f"<urlset><url><loc>{PAGE_URL}</loc></url></urlset>".encode()

        assert [link.url for link in parse_discovery_document(payload, SITEMAP_URL)] == [PAGE_URL]

    def test_a_relative_loc_is_resolved_against_the_document(self) -> None:
        payload = _urlset(_url_entry("/forskrifter/renovasjon"))

        assert [link.url for link in parse_discovery_document(payload, SITEMAP_URL)] == [PAGE_URL]

    def test_an_empty_loc_is_dropped(self) -> None:
        payload = _urlset("<url><loc></loc></url>", _url_entry(PAGE_URL))

        assert [link.url for link in parse_discovery_document(payload, SITEMAP_URL)] == [PAGE_URL]

    def test_malformed_xml_is_a_parse_error(self) -> None:
        with pytest.raises(ParseError):
            parse_discovery_document(b"<urlset><url>", SITEMAP_URL)

    def test_an_unrecognised_root_element_is_a_parse_error(self) -> None:
        with pytest.raises(ParseError, match="unrecognised"):
            parse_discovery_document(b"<html><body>hello</body></html>", SITEMAP_URL)

    def test_an_external_entity_is_never_resolved(self, tmp_path: Path) -> None:
        """XXE: the entity is never expanded, so the file's contents cannot
        become a URL this crawler then goes and fetches."""
        secret = tmp_path / "secret.txt"
        secret.write_text("https://evil.example/leaked", encoding="utf-8")
        payload = (
            f'<?xml version="1.0"?>'
            f'<!DOCTYPE urlset [<!ENTITY xxe SYSTEM "file://{secret}">]>'
            f"<urlset><url><loc>&xxe;</loc></url></urlset>"
        ).encode()

        assert parse_discovery_document(payload, SITEMAP_URL) == ()


class TestParseCompressedSitemap:
    def test_a_gzipped_sitemap_is_read(self) -> None:
        payload = gzip.compress(_urlset(_url_entry(PAGE_URL)))

        assert [link.url for link in parse_discovery_document(payload, SITEMAP_URL)] == [PAGE_URL]

    def test_a_decompression_bomb_is_refused(self) -> None:
        """A large expansion from a few hundred bytes is the whole attack, and
        it sails through the fetcher's cap on the compressed bytes."""
        bomb = gzip.compress(b"<urlset>" + b"<!-- x -->" * 100_000 + b"</urlset>")

        assert len(bomb) < 4096
        with pytest.raises(ParseError, match="decompressed"):
            parse_discovery_document(bomb, SITEMAP_URL, max_decompressed_bytes=64 * 1024)


class TestParseFeed:
    def test_atom_entries_yield_their_alternate_link(self) -> None:
        links = parse_discovery_document(_atom(PAGE_URL), FEED_URL)

        assert [(link.url, link.is_nested_document) for link in links] == [(PAGE_URL, False)]

    def test_an_atom_link_may_be_relative(self) -> None:
        links = parse_discovery_document(_atom("/forskrifter/renovasjon"), FEED_URL)

        assert [link.url for link in links] == [PAGE_URL]

    def test_rss_items_yield_their_link_text(self) -> None:
        links = parse_discovery_document(_rss(PAGE_URL), FEED_URL)

        assert [link.url for link in links] == [PAGE_URL]


class TestDiscovery:
    def test_a_sitemap_entry_point_yields_candidates(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(
            url=SITEMAP_URL, content=_urlset(_url_entry(PAGE_URL, "2026-08-01"))
        )
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [SITEMAP_URL])

        assert result.authority_id == BAERUM_ID
        assert [c.url for c in result.candidates] == [PAGE_URL]
        assert result.candidates[0].discovery_method == DISCOVERY_SITEMAP
        assert result.candidates[0].found_in == SITEMAP_URL
        assert result.candidates[0].site_reported_lastmod == "2026-08-01"
        assert result.skipped == ()

    def test_discovery_never_fetches_a_candidate(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """Discovery proposes; capturing a candidate is a later, separate decision."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(_url_entry(PAGE_URL)))
        discoverer, source = _discoverer(log)

        discoverer.discover(source, [SITEMAP_URL])

        assert PAGE_URL not in [str(request.url) for request in httpx_mock.get_requests()]

    def test_the_discovery_document_itself_is_recorded_as_an_observation(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """A sitemap is evidence too: it is what the site listed at that time."""
        _allow_robots(httpx_mock)
        payload = _urlset(_url_entry(PAGE_URL))
        httpx_mock.add_response(url=SITEMAP_URL, content=payload)
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [SITEMAP_URL])

        records = list(log.records())
        assert [type(record) for record in records] == [ArtifactObservation]
        observation = records[0]
        assert isinstance(observation, ArtifactObservation)
        assert observation.url == SITEMAP_URL
        assert observation.provenance.discovery_method == DISCOVERY_ENTRY_POINT
        assert log.read_blob(observation.sha256) == payload
        assert result.documents_read == (SITEMAP_URL,)

    def test_a_sitemap_index_is_followed_and_its_children_recorded_as_such(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=INDEX_URL, content=_sitemapindex(NESTED_URL))
        httpx_mock.add_response(url=NESTED_URL, content=_urlset(_url_entry(PAGE_URL)))
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [INDEX_URL])

        assert [c.url for c in result.candidates] == [PAGE_URL]
        assert result.documents_read == (INDEX_URL, NESTED_URL)
        methods = [
            record.provenance.discovery_method
            for record in log.records()
            if isinstance(record, ArtifactObservation)
        ]
        assert methods == [DISCOVERY_ENTRY_POINT, DISCOVERY_SITEMAP_INDEX]

    def test_a_feed_entry_point_marks_its_candidates_as_feed_found(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=FEED_URL, content=_atom(PAGE_URL))
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [FEED_URL])

        assert [c.discovery_method for c in result.candidates] == [DISCOVERY_FEED]

    def test_an_off_source_candidate_is_skipped_visibly(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """Not silently: a sitemap listing another host is a fact about the
        source, and a dropped URL nobody can see is indistinguishable from a
        parser that missed it."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(
            url=SITEMAP_URL,
            content=_urlset(_url_entry("https://oslo.kommune.no/forskrift"), _url_entry(PAGE_URL)),
        )
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [SITEMAP_URL])

        assert [c.url for c in result.candidates] == [PAGE_URL]
        assert [(s.url, s.reason) for s in result.skipped] == [
            ("https://oslo.kommune.no/forskrift", "off_source_host")
        ]
        assert "oslo.kommune.no" not in "".join(str(r.url) for r in httpx_mock.get_requests())

    def test_an_off_source_nested_sitemap_is_never_fetched(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(
            url=INDEX_URL, content=_sitemapindex("https://oslo.kommune.no/sitemap.xml")
        )
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [INDEX_URL])

        assert result.candidates == ()
        assert [s.reason for s in result.skipped] == ["off_source_host"]
        assert result.documents_read == (INDEX_URL,)

    def test_a_non_http_scheme_is_skipped(self, log: ObservationLog, httpx_mock: HTTPXMock) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(
            url=SITEMAP_URL, content=_urlset(_url_entry("mailto:post@baerum.kommune.no"))
        )
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [SITEMAP_URL])

        assert result.candidates == ()
        assert [s.reason for s in result.skipped] == ["unsupported_scheme"]

    def test_an_off_source_entry_point_is_skipped_without_a_request(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, ["https://oslo.kommune.no/sitemap.xml"])

        assert result.documents_read == ()
        assert [s.reason for s in result.skipped] == ["off_source_host"]
        assert httpx_mock.get_requests() == []

    def test_a_cycle_terminates(self, log: ObservationLog, httpx_mock: HTTPXMock) -> None:
        """A sitemap index listing itself is a real CMS export bug, and an
        unguarded crawler answers it by hammering the source forever."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=INDEX_URL, content=_sitemapindex(INDEX_URL), is_reusable=True)
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [INDEX_URL])

        assert result.documents_read == (INDEX_URL,)
        assert [s.reason for s in result.skipped] == ["already_read"]

    def test_depth_is_bounded(self, log: ObservationLog, httpx_mock: HTTPXMock) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=INDEX_URL, content=_sitemapindex(NESTED_URL))
        httpx_mock.add_response(url=NESTED_URL, content=_sitemapindex(SITEMAP_URL))
        discoverer, source = _discoverer(log, DiscoverySettings(max_depth=1))

        result = discoverer.discover(source, [INDEX_URL])

        assert result.documents_read == (INDEX_URL, NESTED_URL)
        assert [(s.url, s.reason) for s in result.skipped] == [(SITEMAP_URL, "max_depth_exceeded")]

    def test_the_document_budget_is_bounded(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=INDEX_URL, content=_sitemapindex(NESTED_URL, SITEMAP_URL))
        httpx_mock.add_response(url=NESTED_URL, content=_urlset(_url_entry(PAGE_URL)))
        discoverer, source = _discoverer(log, DiscoverySettings(max_documents=2))

        result = discoverer.discover(source, [INDEX_URL])

        assert result.documents_read == (INDEX_URL, NESTED_URL)
        assert [(s.url, s.reason) for s in result.skipped] == [
            (SITEMAP_URL, "document_budget_exhausted")
        ]

    def test_a_failed_discovery_fetch_does_not_end_the_run(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=INDEX_URL, content=_sitemapindex(NESTED_URL, SITEMAP_URL))
        httpx_mock.add_response(url=NESTED_URL, status_code=404)
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(_url_entry(PAGE_URL)))
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [INDEX_URL])

        assert [c.url for c in result.candidates] == [PAGE_URL]
        assert [(s.url, s.reason) for s in result.skipped] == [
            (NESTED_URL, "fetch_failed: http_404")
        ]
        assert result.documents_read == (INDEX_URL, SITEMAP_URL)

    def test_an_unparseable_discovery_document_is_skipped_not_raised(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """A CMS serving an HTML error page under a sitemap URL is ordinary;
        it stops that document, not the run."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=INDEX_URL, content=_sitemapindex(NESTED_URL, SITEMAP_URL))
        httpx_mock.add_response(url=NESTED_URL, content=b"<html><body>oops</body></html>")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(_url_entry(PAGE_URL)))
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [INDEX_URL])

        assert [c.url for c in result.candidates] == [PAGE_URL]
        assert [s.reason for s in result.skipped] == ["unparseable_document"]
        assert result.documents_read == (INDEX_URL, NESTED_URL, SITEMAP_URL)

    def test_a_duplicate_candidate_is_reported_once(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=INDEX_URL, content=_sitemapindex(NESTED_URL, SITEMAP_URL))
        httpx_mock.add_response(url=NESTED_URL, content=_urlset(_url_entry(PAGE_URL)))
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(_url_entry(PAGE_URL)))
        discoverer, source = _discoverer(log)

        result = discoverer.discover(source, [INDEX_URL])

        assert [c.url for c in result.candidates] == [PAGE_URL]
        assert [(s.url, s.reason) for s in result.skipped] == [(PAGE_URL, "duplicate_candidate")]

    def test_the_same_input_discovers_the_same_thing_twice(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """Downstream is a pure function of the snapshot (ADR-0010 §7), so
        discovery output must not depend on iteration order."""
        _allow_robots(httpx_mock)
        payload = _urlset(_url_entry(OTHER_PAGE_URL), _url_entry(PAGE_URL))
        httpx_mock.add_response(url=SITEMAP_URL, content=payload, is_reusable=True)
        discoverer, source = _discoverer(log)

        first = discoverer.discover(source, [SITEMAP_URL])
        second = discoverer.discover(source, [SITEMAP_URL])

        assert first == second
        assert [c.url for c in first.candidates] == [OTHER_PAGE_URL, PAGE_URL]
