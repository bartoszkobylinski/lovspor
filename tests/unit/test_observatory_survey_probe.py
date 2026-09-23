"""Probing one host for what it offers a crawler (issue #349).

Transport only: the meaning of a probe's result is
:mod:`lovspor.observatory.survey`'s and is tested there. What matters here is
which requests leave the machine, in what order, and which ones must not.

The probe runs before registration, so it has no recorded access-policy check
to take a user agent or a rate limit from. It therefore carries the same ones
every registered source was cleared with, which is the conservative direction:
a recon pass must not be more aggressive than the capture it is scouting for.
"""

from unittest.mock import Mock, patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.observatory.survey_probe import (
    SURVEY_USER_AGENT,
    ProbeSettings,
    SiteProbe,
    _capped,
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
    def test_first_request_does_not_sleep(self, client: httpx.Client) -> None:
        probe = _probe(client)

        probe._wait()

        assert probe.slept == []  # type: ignore[attr-defined]

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

    def test_rules_written_for_this_crawler_by_name_are_the_ones_applied(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """The probe presents its own identity to the rule matcher, not nothing.

        A site that blocks `lovspor-observatory` while allowing everyone else is
        the case that separates the two: matching on the declared agent refuses,
        matching on nothing takes the `*` block and proceeds.
        """
        httpx_mock.add_response(
            url=ROBOTS,
            text="User-agent: lovspor-observatory\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
        )

        shape = _probe(client).read(DOMAIN)

        assert shape.entry == "robots_disallowed"
        assert [str(r.url) for r in httpx_mock.get_requests()] == [ROBOTS]

    def test_the_robots_fetch_names_the_survey_crawler(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """Issue #350, fixed: the request that reads a host's crawl policy
        carries the same User-Agent as every other request the probe makes."""
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nDisallow: /\n")

        _probe(client).read(DOMAIN)

        robots_request = next(r for r in httpx_mock.get_requests() if str(r.url) == ROBOTS)
        assert robots_request.headers["User-Agent"] == SURVEY_USER_AGENT


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

    def test_a_site_with_a_sitemap_still_has_its_markers_recorded(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """The front page is read even once a sitemap settles the entry.

        Pinning the design the Codex test author argued against on PR #353: it
        proposed skipping this request, since the entry is already decided. The
        entry is — but the markers are evidence about the host, not a tiebreaker.
        Issue #332 measured 54% of captured regulation pages carrying under 300
        characters, so a site can serve a sitemap *and* assemble its content in
        the browser, and a row that says both is what tells a planner the index
        may be a shell. Dropping the request would mean re-probing every host
        later to learn what this pass could have recorded.
        """
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, content=SITEMAP_XML)
        httpx_mock.add_response(
            url=FRONT, content=b'<html><script src="/api/presentation/x"></script></html>'
        )

        shape = _probe(client).read(DOMAIN)

        assert shape.entry == "conventional_sitemap"
        assert shape.front_page_markers == ("/api/presentation/",)

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
    def test_body_at_the_byte_ceiling_does_not_read_another_chunk(self) -> None:
        """Reaching the cap must stop consuming a potentially unbounded response."""
        response = Mock(spec=httpx.Response)

        def chunks() -> object:
            yield b"exact"
            raise AssertionError("response was consumed past the byte ceiling")

        response.iter_bytes.return_value = chunks()

        assert _capped(response, 5) == b"exact"

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

    def test_non_ok_and_transport_errors_return_an_empty_body(self, client: httpx.Client) -> None:
        response = Mock(status_code=httpx.codes.NOT_FOUND)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        stream = Mock(return_value=response)
        probe = SiteProbe(client, ProbeSettings(delay_seconds=0))
        probe._client = Mock(stream=stream)

        assert probe._body(FRONT, 100) == b""

        probe._client.stream.side_effect = httpx.ConnectError("no route")
        assert probe._body(FRONT, 100) == b""


class TestConfigurationPropagation:
    def test_timeout_is_passed_to_robots_and_document_requests(self, client: httpx.Client) -> None:
        settings = ProbeSettings(timeout_seconds=3.25, delay_seconds=0)
        probe = SiteProbe(client, settings)
        gate = Mock()
        gate.readable.return_value = False
        gate.allows.return_value = False
        gate.sitemaps.return_value = ()

        with patch("lovspor.observatory.survey_probe.RobotsGate", return_value=gate) as gate_type:
            probe.read(DOMAIN)

        assert gate_type.call_args.args[1].timeout_seconds == 3.25

    def test_user_agent_is_used_for_robots_permission_checks(self, client: httpx.Client) -> None:
        probe = SiteProbe(client, ProbeSettings(user_agent="survey-agent", delay_seconds=0))
        gate = Mock()
        gate.readable.return_value = True
        gate.allows.return_value = True
        gate.sitemaps.return_value = ()

        probe._robots(gate, FRONT)

        gate.allows.assert_called_once_with(FRONT, "survey-agent")

    def test_discovery_permission_and_parser_receive_the_requested_url(
        self, client: httpx.Client
    ) -> None:
        probe = SiteProbe(client, ProbeSettings(user_agent="survey-agent", delay_seconds=0))
        probe._body = Mock(return_value=SITEMAP_XML)  # type: ignore[method-assign]
        gate = Mock()
        gate.allows.return_value = True
        robots = Mock(declared_sitemaps=())

        with patch("lovspor.observatory.survey_probe.parse_discovery_document") as parse:
            assert probe._serves_discovery_document(gate, FRONT, robots) is True

        gate.allows.assert_called_once_with(SITEMAP, "survey-agent")
        parse.assert_called_once_with(SITEMAP_XML, SITEMAP)

    def test_document_request_carries_get_user_agent_and_timeout(
        self, client: httpx.Client
    ) -> None:
        response = Mock(status_code=httpx.codes.OK)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_bytes.return_value = [b"body"]
        stream = Mock(return_value=response)
        probe = SiteProbe(
            Mock(stream=stream),
            ProbeSettings(user_agent="survey-agent", timeout_seconds=3.25, delay_seconds=0),
        )

        assert probe._body(FRONT, 100) == b"body"
        args, kwargs = stream.call_args
        assert (args[0].upper(), args[1]) == ("GET", FRONT)
        assert httpx.Headers(kwargs["headers"])["user-agent"] == "survey-agent"
        assert kwargs["timeout"] == 3.25

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

    def test_a_new_host_does_not_inherit_the_previous_hosts_delay(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """Spacing is per host; beginning the next host owes no delay."""
        other = "other.invalid"
        for domain in (DOMAIN, other):
            httpx_mock.add_response(
                url=f"https://{domain}/robots.txt",
                text="User-agent: *\nDisallow: /\n",
            )
        probe = _probe(client)

        probe.read(DOMAIN)
        probe.read(other)

        assert probe.slept == []  # type: ignore[attr-defined]

    def test_the_configured_timeout_travels_with_every_request(
        self, client: httpx.Client, httpx_mock: HTTPXMock
    ) -> None:
        """A recon pass over hundreds of hosts cannot hang on one of them."""
        httpx_mock.add_response(url=ROBOTS, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP, status_code=404)
        httpx_mock.add_response(url=FRONT, content=b"<html></html>")

        SiteProbe(client, ProbeSettings(sleep=lambda _: None, timeout_seconds=4.5)).read(DOMAIN)

        timeouts = [request.extensions.get("timeout") for request in httpx_mock.get_requests()]
        expected = {"connect": 4.5, "read": 4.5, "write": 4.5, "pool": 4.5}
        assert all(timeout == expected for timeout in timeouts), timeouts

    def test_the_default_delay_matches_the_cleared_limit(self) -> None:
        assert ProbeSettings().delay_seconds == 7.0


class TestWhatTheEquivalentsRegisterAssumes:
    """`mutation-equivalents.toml` waives three `_body` mutants on the strength of
    httpx and of HTTP, not of Python (issue #132). A dependency bump that changes
    either fact must show up here as a red test, never as a stale waiver."""

    def test_httpx_upper_cases_the_request_method(self) -> None:
        assert httpx.Request("get", "https://example.invalid/").method == "GET"

    def test_httpx_header_names_are_case_insensitive(self) -> None:
        headers = httpx.Headers({"user-agent": "lovspor"})

        assert headers["User-Agent"] == "lovspor"
        assert headers["USER-AGENT"] == "lovspor"
