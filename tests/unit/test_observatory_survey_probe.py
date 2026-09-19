"""Probing one host for what it offers a crawler (issue #349).

Transport only: the meaning of a probe's result is
:mod:`lovspor.observatory.survey`'s and is tested there. What matters here is
which requests leave the machine, in what order, and which ones must not.

The probe runs before registration, so it has no recorded access-policy check
to take a user agent or a rate limit from. It therefore carries the same ones
every registered source was cleared with, which is the conservative direction:
a recon pass must not be more aggressive than the capture it is scouting for.
"""

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.observatory.survey_probe import (
    SURVEY_USER_AGENT,
    ProbeSettings,
    SiteProbe,
)

DOMAIN = "example.invalid"
ROBOTS = "https://example.invalid/robots.txt"
SITEMAP = "https://example.invalid/sitemap.xml"
FRONT = "https://example.invalid/"

SITEMAP_XML = (
    b'<?xml version="1.0"?>'
    b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<url><loc>https://example.invalid/forskrift</loc></url></urlset>"
)


def _probe(client: httpx.Client) -> SiteProbe:
    """A probe that never really sleeps, so the delay is asserted, not waited."""
    slept: list[float] = []
    probe = SiteProbe(client, ProbeSettings(sleep=slept.append))
    probe.slept = slept  # type: ignore[attr-defined]
    return probe


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client()


class TestWhatLeavesTheMachine:
    def test_robots_is_read_first_and_nothing_else_is_touched_when_it_refuses(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """A disallowed root ends the probe: the refusal is the finding."""
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nDisallow: /\n")

        shape = _probe(client).read(DOMAIN)

        assert shape.entry == "robots_disallowed"
        assert [str(r.url) for r in httpx_mock.get_requests()] == [ROBOTS]

    def test_an_unreadable_robots_stops_the_probe_too(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=ROBOTS, status_code=503)

        shape = _probe(client).read(DOMAIN)

        assert shape.entry == "robots_unreadable"
        assert [str(r.url) for r in httpx_mock.get_requests()] == [ROBOTS]

    def test_a_permitted_host_is_asked_three_things_at_most(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, content=SITEMAP_XML)
        httpx_mock.add_response(url=FRONT, content=b"<html><body>Kommune</body></html>")

        shape = _probe(client).read(DOMAIN)

        assert [str(r.url) for r in httpx_mock.get_requests()] == [ROBOTS, SITEMAP, FRONT]
        assert shape.entry == "conventional_sitemap"

    def test_every_request_this_module_makes_carries_the_declared_user_agent(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """A recon pass that will not say who it is has no business running."""
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, status_code=404)
        httpx_mock.add_response(url=FRONT, content=b"<html></html>")

        _probe(client).read(DOMAIN)

        ours = [r for r in httpx_mock.get_requests() if str(r.url) != ROBOTS]
        assert {r.headers.get("user-agent") for r in ours} == {SURVEY_USER_AGENT}
        assert "lovspor.no/observatory" in SURVEY_USER_AGENT

    def test_the_robots_fetch_is_still_anonymous_which_is_issue_350(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """Characterisation, not approval.

        ``RobotsGate._load`` sends no ``User-Agent`` (fetch.py:232), so the one
        request that reads a municipality's crawl policy does not name the
        crawler acting on it. That is #350 and predates this module — every
        observatory robots fetch has always gone out this way. Pinned here so a
        fix turns this test red on purpose instead of passing unnoticed.
        """
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nDisallow: /\n")

        _probe(client).read(DOMAIN)

        robots_request = next(r for r in httpx_mock.get_requests() if str(r.url) == ROBOTS)
        assert SURVEY_USER_AGENT not in robots_request.headers.get("user-agent", "")


class TestWhatCountsAsASitemapAtTheConventionalPath:
    def test_a_document_discovery_can_read_counts(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, content=SITEMAP_XML)
        httpx_mock.add_response(url=FRONT, content=b"<html></html>")

        assert _probe(client).read(DOMAIN).entry == "conventional_sitemap"

    def test_a_soft_404_serving_html_under_the_sitemap_url_does_not(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """200 is not the test. Many sites answer any path with a styled page."""
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, content=b"<html><body>Ikke funnet</body></html>")
        httpx_mock.add_response(url=FRONT, content=b"<html></html>")

        assert _probe(client).read(DOMAIN).entry == "no_machine_index"

    def test_a_404_is_an_ordinary_answer_and_the_probe_continues_to_the_front_page(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, status_code=404)
        httpx_mock.add_response(
            url=FRONT, content=b'<html><script src="/api/presentation/x"></script></html>'
        )

        shape = _probe(client).read(DOMAIN)

        assert shape.entry == "browser_assembled"
        assert shape.front_page_markers == ("/api/presentation/",)

    def test_a_disallowed_sitemap_path_is_not_requested_even_though_the_root_is_allowed(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """Robots is consulted per path, not once for the host.

        The rules are written ``Disallow`` first on purpose. Under an
        ``Allow: /`` above it, Python 3.12's parser permits the path anyway —
        first match wins there and longest match only from 3.13 — which is
        issue #351 and not this module's to decide. Ordered this way the
        fixture asserts what it means to assert on every interpreter.
        """
        httpx_mock.add_response(
            url=ROBOTS, text="User-agent: *\nDisallow: /sitemap.xml\nAllow: /\n"
        )
        httpx_mock.add_response(url=FRONT, content=b"<html></html>")

        shape = _probe(client).read(DOMAIN)

        assert shape.entry == "no_machine_index"
        assert [str(r.url) for r in httpx_mock.get_requests()] == [ROBOTS, FRONT]

    def test_a_sitemap_the_site_declares_is_not_re_fetched_at_the_conventional_path(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """Declared wins, so the conventional probe is a request not worth making."""
        httpx_mock.add_response(
            url=ROBOTS,
            text="User-agent: *\nAllow: /\nSitemap: https://example.invalid/sitemap_index.xml\n",
        )
        httpx_mock.add_response(url=FRONT, content=b"<html></html>")

        shape = _probe(client).read(DOMAIN)

        assert shape.entry == "declared_sitemap"
        assert [str(r.url) for r in httpx_mock.get_requests()] == [ROBOTS, FRONT]


class TestWhenTheHostMisbehaves:
    def test_a_transport_error_on_robots_reads_as_unreadable_not_as_a_crash(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """358 hosts in one pass: one refusing a TCP connection cannot end it."""
        httpx_mock.add_exception(httpx.ConnectError("no route"), url=ROBOTS)

        assert _probe(client).read(DOMAIN).entry == "robots_unreadable"

    def test_a_front_page_that_times_out_leaves_the_sitemap_finding_intact(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, content=SITEMAP_XML)
        httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=FRONT)

        shape = _probe(client).read(DOMAIN)

        assert shape.entry == "conventional_sitemap"
        assert shape.front_page_markers == ()

    def test_a_front_page_that_times_out_with_no_sitemap_needs_a_human(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """Unreachable is not "assembled in the browser" — the two must not merge."""
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, status_code=404)
        httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=FRONT)

        assert _probe(client).read(DOMAIN).entry == "no_machine_index"

    def test_a_front_page_past_the_byte_ceiling_is_read_only_up_to_it(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """A marker hunt does not need a 50 MB page, and must not hold one."""
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, status_code=404)
        httpx_mock.add_response(url=FRONT, content=b"x" * 400 + b"/api/presentation/")

        shape = _probe(client).read(DOMAIN, max_bytes=100)

        assert shape.entry == "no_machine_index"
        assert shape.front_page_markers == ()


class TestPoliteness:
    def test_the_recorded_rate_limit_is_waited_out_between_requests_to_one_host(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """7.0 s is what all 201 registered sources were cleared with."""
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, content=SITEMAP_XML)
        httpx_mock.add_response(url=FRONT, content=b"<html></html>")

        probe = _probe(client)
        probe.read(DOMAIN)

        assert probe.slept == [7.0, 7.0]  # type: ignore[attr-defined]

    def test_the_default_delay_matches_the_cleared_limit(self) -> None:
        assert ProbeSettings().delay_seconds == 7.0
