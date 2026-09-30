"""Tests for lovspor.observatory.robots_live — robots.txt read for activation.

Issue #449, owner decision 2026-09-29: ``activate-source`` refuses a rate
below the Crawl-delay of the live robots.txt, and the read follows up to five
redirects (RFC 9309 §2.3.1.2), each checked against the cleared domain.
"""

from datetime import UTC, datetime
from itertools import pairwise

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import RateBelowCrawlDelayError, RobotsUnreadableError
from lovspor.observatory.registry import AccessPolicyCheck
from lovspor.observatory.robots_live import (
    MAX_REDIRECT_HOPS,
    read_robots,
    refuse_rate_below_crawl_delay,
)

DOMAIN = "stad.kommune.no"
ROBOTS_URL = f"https://www.{DOMAIN}/robots.txt"
APEX_ROBOTS_URL = f"https://{DOMAIN}/robots.txt"
UA = "lovspor-observatory/0.1 (+https://lovspor.no/observatory)"
KONGSVINGER = (
    "User-agent: *\n"
    "Sitemap: https://www.kongsvinger.kommune.no/sitemap.xml\n"
    "User-agent: MSNBot\n"
    "Crawl-delay: 30\n"
    "User-agent: Bingbot\n"
    "Crawl-delay: 30\n"
)


def _check(rate_limit_seconds: float) -> AccessPolicyCheck:
    return AccessPolicyCheck(
        checked_at=datetime(2026, 9, 29, 12, tzinfo=UTC),
        robots_txt_url=ROBOTS_URL,
        robots_allows=True,
        terms_reviewed=True,
        terms_permit_capture=True,
        rate_limit_seconds=rate_limit_seconds,
        user_agent=UA,
        reviewed_by="Reviewer",
    )


def _redirect(httpx_mock: HTTPXMock, url: str, location: str, status: int = 301) -> None:
    httpx_mock.add_response(url=url, status_code=status, headers={"Location": location})


def _hop(n: int) -> str:
    return f"https://www.{DOMAIN}/hop{n}/robots.txt"


def _chain(httpx_mock: HTTPXMock, redirects: int) -> None:
    """``ROBOTS_URL`` redirecting ``redirects`` times inside the domain, then a file."""
    urls = [ROBOTS_URL, *(_hop(n) for n in range(1, redirects + 1))]
    for here, there in pairwise(urls):
        _redirect(httpx_mock, here, there)
    httpx_mock.add_response(url=urls[-1], text="User-agent: *\nCrawl-delay: 3\n")


class TestReadRobots:
    def test_a_served_file_is_parsed(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=ROBOTS_URL, text=KONGSVINGER)

        with httpx.Client() as client:
            policy = read_robots(client, ROBOTS_URL, UA, DOMAIN)

        assert policy.crawl_delay(UA) == 30.0

    def test_the_read_names_the_crawler_the_source_was_cleared_for(
        self, httpx_mock: HTTPXMock
    ) -> None:
        _chain(httpx_mock, 2)

        with httpx.Client() as client:
            read_robots(client, ROBOTS_URL, UA, DOMAIN)

        assert [r.headers["User-Agent"] for r in httpx_mock.get_requests()] == [UA] * 3

    def test_a_missing_file_publishes_no_delay(self, httpx_mock: HTTPXMock) -> None:
        """RFC 9309 §2.3.1.3 and the capture gate: 4xx is an empty rule set."""
        httpx_mock.add_response(url=ROBOTS_URL, status_code=404, text="Crawl-delay: 99")

        with httpx.Client() as client:
            policy = read_robots(client, ROBOTS_URL, UA, DOMAIN)

        assert policy.crawl_delay(UA) is None

    def test_a_server_error_is_unreadable(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=ROBOTS_URL, status_code=503)

        with httpx.Client() as client, pytest.raises(RobotsUnreadableError, match="503"):
            read_robots(client, ROBOTS_URL, UA, DOMAIN)

    def test_a_transport_error_is_unreadable(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ConnectTimeout("timed out"), url=ROBOTS_URL)

        with httpx.Client() as client, pytest.raises(RobotsUnreadableError, match=ROBOTS_URL):
            read_robots(client, ROBOTS_URL, UA, DOMAIN)

    def test_a_redirect_to_the_apex_of_the_cleared_domain_is_followed(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """Stad 4649: ``www`` redirects to the apex, which never leaves the domain."""
        _redirect(httpx_mock, ROBOTS_URL, APEX_ROBOTS_URL)
        httpx_mock.add_response(url=APEX_ROBOTS_URL, text="User-agent: *\nCrawl-delay: 10\n")

        with httpx.Client() as client:
            policy = read_robots(client, ROBOTS_URL, UA, DOMAIN)

        assert policy.crawl_delay(UA) == 10.0

    def test_a_relative_location_resolves_against_the_hop(self, httpx_mock: HTTPXMock) -> None:
        _redirect(httpx_mock, ROBOTS_URL, "/nytt/robots.txt", status=308)
        httpx_mock.add_response(
            url=f"https://www.{DOMAIN}/nytt/robots.txt", text="User-agent: *\nCrawl-delay: 2\n"
        )

        with httpx.Client() as client:
            assert read_robots(client, ROBOTS_URL, UA, DOMAIN).crawl_delay(UA) == 2.0

    def test_five_redirects_are_followed(self, httpx_mock: HTTPXMock) -> None:
        _chain(httpx_mock, MAX_REDIRECT_HOPS)

        with httpx.Client() as client:
            policy = read_robots(client, ROBOTS_URL, UA, DOMAIN)

        assert policy.crawl_delay(UA) == 3.0
        assert len(httpx_mock.get_requests()) == MAX_REDIRECT_HOPS + 1

    def test_a_sixth_redirect_is_refused_without_following_it(self, httpx_mock: HTTPXMock) -> None:
        urls = [ROBOTS_URL, *(_hop(n) for n in range(1, 7))]
        for here, there in pairwise(urls):
            _redirect(httpx_mock, here, there)
        httpx_mock.add_response(url=urls[-1], text="User-agent: *\n", is_optional=True)

        with httpx.Client() as client, pytest.raises(RobotsUnreadableError, match="more than 5"):
            read_robots(client, ROBOTS_URL, UA, DOMAIN)

        assert len(httpx_mock.get_requests()) == 6

    @pytest.mark.parametrize(
        "location",
        [
            "https://stad.no/robots.txt",
            "https://notstad.kommune.no/robots.txt",
            "https://stad.kommune.no.evil.example/robots.txt",
            "//stad.no/robots.txt",
        ],
    )
    def test_a_hop_off_the_cleared_domain_is_refused(
        self, httpx_mock: HTTPXMock, location: str
    ) -> None:
        _redirect(httpx_mock, ROBOTS_URL, location)

        with httpx.Client() as client, pytest.raises(RobotsUnreadableError, match="outside"):
            read_robots(client, ROBOTS_URL, UA, DOMAIN)

        assert len(httpx_mock.get_requests()) == 1

    def test_a_later_hop_off_the_domain_is_refused_too(self, httpx_mock: HTTPXMock) -> None:
        _redirect(httpx_mock, ROBOTS_URL, APEX_ROBOTS_URL)
        _redirect(httpx_mock, APEX_ROBOTS_URL, "https://cdn.example.invalid/robots.txt")

        with httpx.Client() as client, pytest.raises(RobotsUnreadableError, match="outside"):
            read_robots(client, ROBOTS_URL, UA, DOMAIN)

    def test_a_redirect_without_a_location_is_unreadable(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=ROBOTS_URL, status_code=302)

        with httpx.Client() as client, pytest.raises(RobotsUnreadableError, match="Location"):
            read_robots(client, ROBOTS_URL, UA, DOMAIN)

    def test_a_300_is_a_redirect_like_any_other_3xx(self, httpx_mock: HTTPXMock) -> None:
        """RFC 9309 §2.3.1.2 speaks of the 3xx class; 300 is its lower bound."""
        _redirect(httpx_mock, ROBOTS_URL, APEX_ROBOTS_URL, status=300)
        httpx_mock.add_response(url=APEX_ROBOTS_URL, text="User-agent: *\nCrawl-delay: 4\n")

        with httpx.Client() as client:
            assert read_robots(client, ROBOTS_URL, UA, DOMAIN).crawl_delay(UA) == 4.0

    def test_a_400_publishes_no_rules_rather_than_reading_as_a_redirect(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """400 opens the 4xx class, which the capture gate reads as no rules."""
        httpx_mock.add_response(url=ROBOTS_URL, status_code=400, text="Crawl-delay: 99")

        with httpx.Client() as client:
            policy = read_robots(client, ROBOTS_URL, UA, DOMAIN)

        assert policy.crawl_delay(UA) is None

    def test_an_unreadable_hop_is_named_in_the_refusal(self, httpx_mock: HTTPXMock) -> None:
        """The operator is told which URL failed: after a redirect, the hop."""
        _redirect(httpx_mock, ROBOTS_URL, APEX_ROBOTS_URL)
        httpx_mock.add_response(url=APEX_ROBOTS_URL, status_code=503)

        with httpx.Client() as client, pytest.raises(RobotsUnreadableError) as refused:
            read_robots(client, ROBOTS_URL, UA, DOMAIN)

        assert str(refused.value) == f"{APEX_ROBOTS_URL} answered HTTP 503"

    def test_a_client_that_follows_redirects_still_has_each_hop_checked(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """The domain test holds whatever client the caller hands in: the read
        never lets httpx follow a hop the test has not seen."""
        _redirect(httpx_mock, ROBOTS_URL, "https://cdn.example.invalid/robots.txt")

        with (
            httpx.Client(follow_redirects=True) as client,
            pytest.raises(RobotsUnreadableError, match="outside the cleared domain"),
        ):
            read_robots(client, ROBOTS_URL, UA, DOMAIN)

        assert len(httpx_mock.get_requests()) == 1


class TestWhatTheEquivalentsRegisterAssumes:
    """`mutation-equivalents.toml` waives ``_get``'s ``follow_redirects=None``
    mutant and ``_next_hop``'s ``"LOCATION"`` mutant on the strength of httpx,
    not of Python (issue #132)."""

    def test_httpx_response_header_lookup_ignores_case(self) -> None:
        response = httpx.Response(301, headers={"Location": APEX_ROBOTS_URL})

        assert response.headers.get("LOCATION") == response.headers.get("location")
        assert response.headers.get("LOCATION") == APEX_ROBOTS_URL

    def test_httpx_reads_follow_redirects_none_as_do_not_follow(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """``None`` is not httpx's client-default sentinel, and it is falsy:
        even a client configured to follow returns the redirect itself."""
        _redirect(httpx_mock, ROBOTS_URL, APEX_ROBOTS_URL)

        with httpx.Client(follow_redirects=True) as client:
            response = client.get(ROBOTS_URL, follow_redirects=None)  # type: ignore[arg-type]

        assert response.status_code == 301
        assert len(httpx_mock.get_requests()) == 1


class TestRefuseRateBelowCrawlDelay:
    def test_kongsvinger_at_seven_seconds_is_refused(self, httpx_mock: HTTPXMock) -> None:
        """The #416 dry-run state of 3401: 7 s against the owner's 30 s reading."""
        httpx_mock.add_response(url=ROBOTS_URL, text=KONGSVINGER)

        with (
            httpx.Client() as client,
            pytest.raises(RateBelowCrawlDelayError, match=r"7\.0.*30\.0") as refused,
        ):
            refuse_rate_below_crawl_delay(client, _check(7.0), DOMAIN)

        assert ROBOTS_URL in str(refused.value)

    def test_a_rate_equal_to_the_delay_passes(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=ROBOTS_URL, text=KONGSVINGER)

        with httpx.Client() as client:
            refuse_rate_below_crawl_delay(client, _check(30.0), DOMAIN)

    def test_a_file_without_a_delay_sets_no_floor(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=ROBOTS_URL, text="User-agent: *\nDisallow: /privat/\n")

        with httpx.Client() as client:
            refuse_rate_below_crawl_delay(client, _check(0.5), DOMAIN)

    def test_the_delay_is_read_after_the_redirect(self, httpx_mock: HTTPXMock) -> None:
        _redirect(httpx_mock, ROBOTS_URL, APEX_ROBOTS_URL)
        httpx_mock.add_response(url=APEX_ROBOTS_URL, text="User-agent: *\nCrawl-delay: 10\n")

        with httpx.Client() as client, pytest.raises(RateBelowCrawlDelayError):
            refuse_rate_below_crawl_delay(client, _check(9.9), DOMAIN)

    def test_an_unreadable_file_refuses_activation(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=ROBOTS_URL, status_code=500)

        with httpx.Client() as client, pytest.raises(RobotsUnreadableError):
            refuse_rate_below_crawl_delay(client, _check(60.0), DOMAIN)
