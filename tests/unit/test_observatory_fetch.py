"""Capture step: authorisation, robots, politeness, and what gets recorded.

Only the HTTP transport is mocked (pytest-httpx). The registry, the log, the
blob store and every gate are the real thing — a test that stubbed the gates
would be testing the stubs, and the gates are the whole point of this module.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import SourceNotActivatedError
from lovspor.observatory import fetch as fetch_module
from lovspor.observatory.fetch import (
    CaptureSettings,
    Fetcher,
    RateLimiter,
    RobotsGate,
)
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, FetchFailure
from lovspor.observatory.registry import (
    AccessPolicyCheck,
    SourceRecord,
    SourceRegistry,
    activate,
)
from lovspor.observatory.storage import ObservatoryRoot

BAERUM_ID = "3201"
BAERUM_DOMAIN = "baerum.kommune.no"
PAGE_URL = f"https://{BAERUM_DOMAIN}/forskrifter/renovasjon"
ROBOTS_URL = f"https://{BAERUM_DOMAIN}/robots.txt"
OSLO_ID = "0301"
OSLO_DOMAIN = "oslo.kommune.no"
OSLO_URL = f"https://{OSLO_DOMAIN}/forskrift"
OSLO_ROBOTS_URL = f"https://{OSLO_DOMAIN}/robots.txt"
USER_AGENT = "lovspor-observatory/0.1 (+https://lovspor.no/observatory)"
OBSERVED_AT = datetime(2026, 8, 18, 10, 30, tzinfo=UTC)
PAYLOAD = "Forskrift om renovasjon, Bærum kommune. Æ Ø Å".encode()


def _policy(rate_limit_seconds: float = 2.0) -> AccessPolicyCheck:
    return AccessPolicyCheck(
        checked_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        robots_txt_url=ROBOTS_URL,
        robots_allows=True,
        terms_reviewed=True,
        terms_permit_capture=True,
        rate_limit_seconds=rate_limit_seconds,
        user_agent=USER_AGENT,
        reviewed_by="bartosz",
    )


def _registry(rate_limit_seconds: float = 2.0) -> SourceRegistry:
    source = SourceRecord(
        authority_type="kommune",
        authority_id=BAERUM_ID,
        name="Bærum",
        canonical_domain=BAERUM_DOMAIN,
    )
    return SourceRegistry(sources={BAERUM_ID: activate(source, _policy(rate_limit_seconds))})


def _two_host_registry(rate_limit_seconds: float = 2.0) -> SourceRegistry:
    baerum = SourceRecord(
        authority_type="kommune",
        authority_id=BAERUM_ID,
        name="Bærum",
        canonical_domain=BAERUM_DOMAIN,
    )
    oslo = SourceRecord(
        authority_type="kommune",
        authority_id=OSLO_ID,
        name="Oslo",
        canonical_domain=OSLO_DOMAIN,
    )
    return SourceRegistry(
        sources={
            BAERUM_ID: activate(baerum, _policy(rate_limit_seconds)),
            OSLO_ID: activate(oslo, _policy(rate_limit_seconds)),
        }
    )


class _Clock:
    """A monotonic clock that only moves when something sleeps on it."""

    def __init__(self) -> None:
        self.value = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.value += seconds


def _settings(clock: _Clock | None = None, max_bytes: int = 1024) -> CaptureSettings:
    clock = clock or _Clock()
    return CaptureSettings(
        max_bytes=max_bytes,
        now=lambda: OBSERVED_AT,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


@pytest.fixture
def log(tmp_path: Path) -> ObservationLog:
    return ObservationLog(ObservatoryRoot(tmp_path / "observatory", forbidden=[]))


def _fetcher(
    log: ObservationLog, settings: CaptureSettings | None = None, **kwargs: float
) -> Fetcher:
    return Fetcher(_registry(**kwargs), log, httpx.Client(), settings or _settings())


def _allow_robots(httpx_mock: HTTPXMock, body: str = "User-agent: *\nAllow: /\n") -> None:
    httpx_mock.add_response(url=ROBOTS_URL, text=body)


class TestActivationGate:
    def test_an_unregistered_host_is_refused_before_any_request(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """No request, and no record: nothing observed the source."""
        with pytest.raises(SourceNotActivatedError):
            _fetcher(log).capture("https://oslo.kommune.no/forskrift", "sitemap")

        assert list(log.records()) == []
        assert httpx_mock.get_requests() == []

    def test_lovdata_stays_refused_even_via_a_registered_authority(
        self, log: ObservationLog
    ) -> None:
        """ADR-0010 §4 denies the host centrally, not per source."""
        with pytest.raises(SourceNotActivatedError, match="globally denied"):
            _fetcher(log).capture(
                "https://lovdata.no/dokument/LTII/forskrift/2020-01-01-1", "manual"
            )

        assert list(log.records()) == []


class TestRobotsGate:
    def test_a_disallowed_path_is_recorded_not_fetched(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The refusal is evidence of compliance; ADR-0010 asks the crawl log
        to demonstrate it, and a silent skip demonstrates nothing."""
        _allow_robots(httpx_mock, "User-agent: *\nDisallow: /forskrifter/\n")

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "robots_disallowed"
        assert result.http_status is None
        assert [r.url for r in httpx_mock.get_requests()] == [ROBOTS_URL]
        assert list(log.records()) == [result]

    def test_robots_is_fetched_once_per_host(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD, is_reusable=True)
        fetcher = _fetcher(log)

        fetcher.capture(PAGE_URL, "sitemap")
        fetcher.capture(PAGE_URL, "sitemap")

        assert [r.url for r in httpx_mock.get_requests()].count(ROBOTS_URL) == 1

    def test_a_missing_robots_file_allows_capture(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """404 is how a site says it publishes no restrictions."""
        httpx_mock.add_response(url=ROBOTS_URL, status_code=404, text="<h1>Not found</h1>")
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)

        assert isinstance(_fetcher(log).capture(PAGE_URL, "sitemap"), ArtifactObservation)

    def test_an_unreadable_robots_file_denies_capture(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """Fail closed: crawling a site whose rules could not be read is the
        politeness failure the ADR warns about."""
        httpx_mock.add_response(url=ROBOTS_URL, status_code=503)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "robots_disallowed"

    def test_a_robots_transport_error_denies_capture(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_exception(httpx.ConnectError("dns"), url=ROBOTS_URL)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "robots_disallowed"

    def test_an_unreachable_robots_file_is_still_cached_and_not_refetched(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The cache stores the denial itself, not just an allow: a second
        capture against a host whose robots.txt is unreachable must not retry
        that request, or a struggling server gets hit once per capture
        instead of once per run."""
        httpx_mock.add_response(url=ROBOTS_URL, status_code=503)
        fetcher = _fetcher(log)

        first = fetcher.capture(PAGE_URL, "sitemap")
        second = fetcher.capture(PAGE_URL, "sitemap")

        assert isinstance(first, FetchFailure)
        assert isinstance(second, FetchFailure)
        assert [r.url for r in httpx_mock.get_requests()].count(ROBOTS_URL) == 1

    def test_a_rule_naming_this_crawler_binds_it_over_the_wildcard(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """robots.txt is matched on the product token — the part of the User-Agent
        before the first "/" — which is what site owners write in their rules."""
        httpx_mock.add_response(
            url=ROBOTS_URL,
            text="User-agent: lovspor-observatory\nDisallow: /forskrifter/\n\n"
            "User-agent: *\nAllow: /\n",
        )
        gate = RobotsGate(httpx.Client(), _settings())

        assert gate.allows(PAGE_URL, USER_AGENT) is False
        assert gate.allows(PAGE_URL, "some-other-bot/1.0") is True

    def test_capture_checks_robots_against_the_registered_user_agent(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The registered user agent, not some other one, is what robots.txt
        rules are matched against: checking the wrong agent (or none at all)
        would let a rule written for this crawler go unenforced."""
        httpx_mock.add_response(
            url=ROBOTS_URL,
            text="User-agent: *\nDisallow: /forskrifter/\n\n"
            "User-agent: lovspor-observatory\nAllow: /\n",
        )
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, ArtifactObservation)


class TestPoliteness:
    def test_the_second_fetch_waits_the_registered_interval(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD, is_reusable=True)
        clock = _Clock()
        fetcher = _fetcher(log, _settings(clock), rate_limit_seconds=2.0)

        fetcher.capture(PAGE_URL, "sitemap")
        fetcher.capture(PAGE_URL, "sitemap")

        assert clock.slept == [2.0]

    def test_the_interval_comes_from_the_registry_not_the_caller(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD, is_reusable=True)
        clock = _Clock()
        fetcher = _fetcher(log, _settings(clock), rate_limit_seconds=7.5)

        fetcher.capture(PAGE_URL, "sitemap")
        fetcher.capture(PAGE_URL, "sitemap")

        assert clock.slept == [7.5]

    def test_time_already_spent_counts_against_the_interval(self) -> None:
        clock = _Clock()
        limiter = RateLimiter(_settings(clock))

        limiter.wait("example.invalid", 5.0)
        clock.value += 3.0
        waited = limiter.wait("example.invalid", 5.0)

        assert waited == 2.0
        assert clock.slept == [2.0]

    def test_a_first_fetch_never_waits(self) -> None:
        clock = _Clock()

        assert RateLimiter(_settings(clock)).wait("example.invalid", 30.0) == 0.0
        assert clock.slept == []

    def test_hosts_are_spaced_independently(self) -> None:
        clock = _Clock()
        limiter = RateLimiter(_settings(clock))

        limiter.wait("a.invalid", 5.0)

        assert limiter.wait("b.invalid", 5.0) == 0.0


class TestRecordedObservation:
    def test_a_successful_capture_stores_the_bytes_and_records_them(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(
            url=PAGE_URL,
            content=PAYLOAD,
            headers={"Content-Type": "text/html; charset=utf-8", "ETag": '"abc"'},
        )

        record = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(record, ArtifactObservation)
        assert record.authority_id == BAERUM_ID
        assert record.observed_at == OBSERVED_AT
        assert record.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
        assert record.content_type == "text/html; charset=utf-8"
        assert record.http_status == 200
        assert log.read_blob(record.sha256) == PAYLOAD
        assert list(log.records()) == [record]

    def test_the_politeness_in_force_is_part_of_the_record(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """ADR-0010 §4: an audit asking whether a source was fetched politely
        is answered by the record, not by the config that happened to run."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)

        record = _fetcher(log, rate_limit_seconds=3.5).capture(PAGE_URL, "sitemap", "baerum_html")

        assert record.provenance.rate_limit_seconds == 3.5
        assert record.provenance.user_agent == USER_AGENT
        assert record.provenance.adapter == "baerum_html"
        assert record.provenance.channel == "http"
        assert record.provenance.discovery_method == "sitemap"

    def test_the_registered_user_agent_is_sent(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)

        _fetcher(log).capture(PAGE_URL, "sitemap")

        page_request = next(r for r in httpx_mock.get_requests() if str(r.url) == PAGE_URL)
        assert page_request.headers["User-Agent"] == USER_AGENT

    def test_the_user_agent_header_name_keeps_its_canonical_casing(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The header name itself, not just its value, is part of what is
        sent on the wire."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)

        _fetcher(log).capture(PAGE_URL, "sitemap")

        page_request = next(r for r in httpx_mock.get_requests() if str(r.url) == PAGE_URL)
        assert (b"User-Agent", USER_AGENT.encode()) in page_request.headers.raw

    def test_the_page_is_fetched_with_get(self, log: ObservationLog, httpx_mock: HTTPXMock) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, method="GET", content=PAYLOAD)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, ArtifactObservation)

    def test_only_allowlisted_headers_are_recorded(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The log is append-only, so over-collection cannot be walked back."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(
            url=PAGE_URL,
            content=PAYLOAD,
            headers={
                "Content-Type": "text/html",
                "Set-Cookie": "session=secret",
                "Server": "nginx",
            },
        )

        record = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert set(record.http_headers) <= {"content-type", "content-length", "date"}
        assert "set-cookie" not in record.http_headers

    def test_a_missing_content_type_falls_back_rather_than_guessing(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD, headers={})

        record = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(record, ArtifactObservation)
        assert record.content_type in {"application/octet-stream", "text/plain; charset=utf-8"}

    def test_identical_bytes_observed_twice_cost_one_blob_and_two_records(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD, is_reusable=True)
        fetcher = _fetcher(log)

        fetcher.capture(PAGE_URL, "sitemap")
        fetcher.capture(PAGE_URL, "sitemap")

        assert len(list(log.records())) == 2
        assert len(log.stored_hashes()) == 1


class TestFailuresAreObservations:
    def test_an_http_error_is_recorded_with_its_status(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, status_code=503, text="down")

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "http_503"
        assert result.http_status == 503
        assert list(log.records()) == [result]
        assert log.stored_hashes() == frozenset()

    def test_a_timeout_is_recorded_as_an_observation(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=PAGE_URL)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "timeout"
        assert result.http_status is None

    def test_a_transport_error_names_its_class(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_exception(httpx.ConnectError("no route"), url=PAGE_URL)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "transport_error: ConnectError"

    def test_a_redirect_is_recorded_with_its_target_and_not_followed(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """Following it would fetch a URL that never passed the activation
        gate — including, in the worst case, a globally denied host."""
        elsewhere = "https://www.example.invalid/moved"
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, status_code=301, headers={"Location": elsewhere})

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "redirect_not_followed"
        assert result.http_headers["location"] == elsewhere
        assert [str(r.url) for r in httpx_mock.get_requests()] == [ROBOTS_URL, PAGE_URL]

    def test_a_redirect_inside_the_authorised_domain_is_followed(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """Issue #158: www→apex is the commonest hosting shape in the country
        and it never leaves the canonical domain the reviewer cleared. The ban
        exists to stop a redirect carrying us OFF the authorised host, not to
        stop movement inside it — 5 of the first 36 municipalities of the fleet
        bootstrap were refused for this alone."""
        apex = f"https://{BAERUM_DOMAIN}/sitemap.xml"
        www = f"https://www.{BAERUM_DOMAIN}/sitemap.xml"
        httpx_mock.add_response(
            url=f"https://www.{BAERUM_DOMAIN}/robots.txt", text="User-agent: *\nAllow: /\n"
        )
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=www, status_code=301, headers={"Location": apex})
        httpx_mock.add_response(url=apex, content=PAYLOAD, headers={"Content-Type": "text/xml"})

        result = _fetcher(log).capture(www, "sitemap")

        assert isinstance(result, ArtifactObservation)
        assert result.url == apex
        assert result.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
        hops = [r for r in log.records() if getattr(r, "outcome", None) == "redirect_followed"]
        assert [r.url for r in hops] == [www]
        assert hops[0].http_headers["location"] == apex

    def test_a_followed_redirect_passes_every_gate_again(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The target is a new request, so robots decides about it on its own
        terms: a redirect must not smuggle a fetch past a rule that covers
        where it lands."""
        apex = f"https://{BAERUM_DOMAIN}/private/sitemap.xml"
        www = f"https://www.{BAERUM_DOMAIN}/sitemap.xml"
        # Python's RobotFileParser honours rule ORDER, not longest match, so
        # the specific rule has to precede the blanket Allow.
        robots = "User-agent: *\nDisallow: /private/\nAllow: /\n"
        httpx_mock.add_response(url=f"https://www.{BAERUM_DOMAIN}/robots.txt", text=robots)
        httpx_mock.add_response(url=ROBOTS_URL, text=robots)
        httpx_mock.add_response(url=www, status_code=301, headers={"Location": apex})

        result = _fetcher(log).capture(www, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "robots_disallowed"
        assert result.url == apex
        assert apex not in [str(r.url) for r in httpx_mock.get_requests()]

    def test_a_redirect_chain_is_bounded(self, log: ObservationLog, httpx_mock: HTTPXMock) -> None:
        """A loop inside the domain must end as a recorded outcome, not as a
        crawler spinning against someone else's server."""
        first = f"https://{BAERUM_DOMAIN}/a"
        second = f"https://{BAERUM_DOMAIN}/b"
        _allow_robots(httpx_mock)
        httpx_mock.add_response(
            url=first, status_code=302, headers={"Location": second}, is_reusable=True
        )
        httpx_mock.add_response(
            url=second, status_code=302, headers={"Location": first}, is_reusable=True
        )

        result = _fetcher(log).capture(first, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "redirect_limit_exceeded"
        followed = [
            record
            for record in log.records()
            if getattr(record, "outcome", None) == "redirect_followed"
        ]
        assert len(followed) == 3
        capture_requests = [
            request
            for request in httpx_mock.get_requests()
            if "/robots.txt" not in str(request.url)
        ]
        assert len(capture_requests) == 4

    def test_a_redirect_target_without_a_host_is_not_followed(
        self, log: ObservationLog, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Location whose host cannot be captured is recorded using the
        stable refusal outcome rather than being followed."""
        target = f"https://{BAERUM_DOMAIN}/unusable-target"
        real_capture_host = fetch_module.capture_host

        def reject_unusable_target(url: str) -> str:
            if url == target:
                raise SourceNotActivatedError("target has no usable host")
            return real_capture_host(url)

        monkeypatch.setattr(fetch_module, "capture_host", reject_unusable_target)
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, status_code=302, headers={"Location": target})

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "redirect_not_followed"
        assert result.http_headers["location"] == target
        assert [str(request.url) for request in httpx_mock.get_requests()] == [
            ROBOTS_URL,
            PAGE_URL,
        ]

    def test_a_relative_location_resolves_against_the_current_url(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        target = f"https://{BAERUM_DOMAIN}/sitemaps/index.xml"
        _allow_robots(httpx_mock)
        httpx_mock.add_response(
            url=PAGE_URL, status_code=301, headers={"Location": "/sitemaps/index.xml"}
        )
        httpx_mock.add_response(url=target, content=PAYLOAD)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, ArtifactObservation)
        assert result.url == target

    def test_a_same_host_redirect_is_rate_limited_as_a_second_request(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """Following a redirect does not waive the source's spacing rule:
        each HTTP request is independently subject to the per-host limit."""
        target = f"https://{BAERUM_DOMAIN}/moved"
        clock = _Clock()
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, status_code=302, headers={"Location": target})
        httpx_mock.add_response(url=target, content=PAYLOAD)

        result = _fetcher(log, _settings(clock)).capture(PAGE_URL, "sitemap")

        assert isinstance(result, ArtifactObservation)
        assert result.url == target
        assert clock.slept == [2.0]

    def test_an_oversized_response_is_recorded_and_never_stored(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """Partial bytes with a hash of their own would be evidence of
        something the source never served. The status and headers that came
        back before the cap was hit are still real observations and belong
        in the failure record, not blanked out."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(
            url=PAGE_URL, content=b"x" * 5000, headers={"Content-Type": "text/plain"}
        )

        result = _fetcher(log).capture(PAGE_URL, "sitemap", "baerum_html")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "response_exceeded_max_bytes"
        assert result.http_status == 200
        assert result.http_headers.get("content-type") == "text/plain"
        assert log.stored_hashes() == frozenset()

    def test_a_response_exactly_at_the_cap_is_kept(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=b"x" * 1024)

        assert isinstance(_fetcher(log).capture(PAGE_URL, "sitemap"), ArtifactObservation)

    def test_an_elapsed_interval_does_not_sleep_at_all(self) -> None:
        clock = _Clock()
        limiter = RateLimiter(_settings(clock))

        limiter.wait("example.invalid", 5.0)
        clock.value += 9.0

        assert limiter.wait("example.invalid", 5.0) == 0.0
        assert clock.slept == []


class TestStatusOutcomeBoundaries:
    """`_read`/`_outcome_for` branch on exact status thresholds (300, 400);
    a fencepost error there would misfile a whole class of outcomes."""

    def test_a_status_just_below_the_redirect_threshold_succeeds(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, status_code=299, content=PAYLOAD)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, ArtifactObservation)
        assert result.http_status == 299

    def test_a_status_at_the_redirect_threshold_is_not_followed(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, status_code=300)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "redirect_not_followed"
        assert result.http_status == 300

    def test_a_status_just_below_client_error_is_still_a_redirect_outcome(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, status_code=399)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "redirect_not_followed"

    def test_a_status_at_the_client_error_threshold_is_named_http_400(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, status_code=400)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "http_400"


class TestRobotsStatusBoundaries:
    """RobotsGate treats 4xx as "no rules published" and 5xx as unreachable
    (deny); the split sits at exactly 500, so the fencepost matters."""

    def test_a_robots_status_just_below_server_error_is_no_rules_published(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=ROBOTS_URL, status_code=499, text="Disallow: /")
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, ArtifactObservation)

    def test_a_robots_status_at_the_server_error_threshold_denies(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=ROBOTS_URL, status_code=500)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "robots_disallowed"

    def test_a_robots_status_at_the_client_error_threshold_is_no_rules_published(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """At exactly 400 the body is a 4xx error page, not a rules document:
        reading it as one would let an arbitrary directive on an error page
        block a capture that should be allowed."""
        httpx_mock.add_response(
            url=ROBOTS_URL, status_code=400, text="User-agent: *\nDisallow: /\n"
        )
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, ArtifactObservation)


class TestBodyCapBoundary:
    def test_a_response_one_byte_over_the_cap_is_discarded(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=b"x" * 1025)

        result = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "response_exceeded_max_bytes"


class TestRateLimiterBoundary:
    def test_time_exactly_at_the_interval_does_not_sleep(self) -> None:
        clock = _Clock()
        limiter = RateLimiter(_settings(clock))

        limiter.wait("example.invalid", 5.0)
        clock.value += 5.0
        waited = limiter.wait("example.invalid", 5.0)

        assert waited == 0.0
        assert clock.slept == []

    def test_a_sub_one_second_remainder_still_triggers_a_wait(self) -> None:
        """A remainder between 0 and 1 second is still a positive wait: a
        fencepost of ``> 1`` instead of ``> 0`` would silently skip it."""
        clock = _Clock()
        limiter = RateLimiter(_settings(clock))

        limiter.wait("example.invalid", 5.0)
        clock.value += 4.5
        waited = limiter.wait("example.invalid", 5.0)

        assert waited == 0.5
        assert clock.slept == [0.5]


class TestRateLimiterHostKey:
    def test_two_different_hosts_are_not_cross_rate_limited(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The limiter keys on the real hostname; collapsing every host onto
        one shared key would make an unrelated source's rate limit bleed into
        this one's very first request."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=OSLO_ROBOTS_URL, text="User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)
        httpx_mock.add_response(url=OSLO_URL, content=PAYLOAD)
        clock = _Clock()
        fetcher = Fetcher(_two_host_registry(5.0), log, httpx.Client(), _settings(clock))

        fetcher.capture(PAGE_URL, "sitemap")
        fetcher.capture(OSLO_URL, "sitemap")

        assert clock.slept == []

    def test_two_different_paths_on_the_same_host_still_share_the_rate_limit(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """capture_host() is keyed on the host alone: two URLs that differ
        only in path must contend for the same bucket, or a crawler could
        dodge the registered interval by varying the path on every request."""
        other_path_url = f"https://{BAERUM_DOMAIN}/forskrifter/en-annen-side"
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)
        httpx_mock.add_response(url=other_path_url, content=PAYLOAD)
        clock = _Clock()
        fetcher = _fetcher(log, _settings(clock), rate_limit_seconds=3.0)

        fetcher.capture(PAGE_URL, "sitemap")
        fetcher.capture(other_path_url, "sitemap")

        assert clock.slept == [3.0]


class TestTimeoutIsHonored:
    """A missing or ``None`` timeout would leave the crawler hanging
    indefinitely against a stalled endpoint, so the configured value must
    reach both the robots.txt request and the page request."""

    def test_the_robots_request_uses_the_configured_timeout(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)
        settings = CaptureSettings(timeout_seconds=12.5, max_bytes=1024)

        Fetcher(_registry(), log, httpx.Client(), settings).capture(PAGE_URL, "sitemap")

        robots_request = next(r for r in httpx_mock.get_requests() if str(r.url) == ROBOTS_URL)
        assert robots_request.extensions["timeout"] == httpx.Timeout(12.5).as_dict()

    def test_the_page_request_uses_the_configured_timeout(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)
        settings = CaptureSettings(timeout_seconds=12.5, max_bytes=1024)

        Fetcher(_registry(), log, httpx.Client(), settings).capture(PAGE_URL, "sitemap")

        page_request = next(r for r in httpx_mock.get_requests() if str(r.url) == PAGE_URL)
        assert page_request.extensions["timeout"] == httpx.Timeout(12.5).as_dict()


class TestHeaderAllowlist:
    def test_every_allowlisted_header_is_recorded_when_present(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """A silently narrowed allowlist would fail this instead of surfacing
        only as a missing field an unrelated test happens not to check."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(
            url=PAGE_URL,
            content=PAYLOAD,
            headers={
                "Content-Type": "text/html",
                "Content-Length": str(len(PAYLOAD)),
                "Content-Disposition": "inline",
                "ETag": '"abc"',
                "Last-Modified": "Tue, 18 Aug 2026 10:00:00 GMT",
                "Date": "Tue, 18 Aug 2026 10:30:00 GMT",
            },
        )

        record = _fetcher(log).capture(PAGE_URL, "sitemap")

        assert isinstance(record, ArtifactObservation)
        assert record.http_headers["content-type"] == "text/html"
        assert record.http_headers["content-length"] == str(len(PAYLOAD))
        assert record.http_headers["content-disposition"] == "inline"
        assert record.http_headers["etag"] == '"abc"'
        assert record.http_headers["last-modified"] == "Tue, 18 Aug 2026 10:00:00 GMT"
        assert record.http_headers["date"] == "Tue, 18 Aug 2026 10:30:00 GMT"


class TestRobotsCachedByHostNotByPath:
    def test_robots_is_cached_across_different_paths_on_the_same_host(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The cache key is the host: a mutant keying it by full URL would
        still pass the identical-URL variant of this test."""
        other_page = f"https://{BAERUM_DOMAIN}/forskrifter/vann"
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)
        httpx_mock.add_response(url=other_page, content=PAYLOAD)
        fetcher = _fetcher(log)

        fetcher.capture(PAGE_URL, "sitemap")
        fetcher.capture(other_page, "sitemap")

        assert [str(r.url) for r in httpx_mock.get_requests()].count(ROBOTS_URL) == 1


class TestDefaults:
    def test_the_default_clock_is_utc_aware(self) -> None:
        """observed_at is an axis: a naive default would be rejected by the
        model at the moment of recording, which is far too late."""
        assert CaptureSettings().now().tzinfo is UTC

    def test_a_source_active_without_a_policy_is_refused_at_the_fetcher(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The model forbids this state; the fetcher does not take that on
        trust, because the next step after trusting it is traffic against
        someone else's server with no recorded politeness terms."""
        bypassed = SourceRecord.model_construct(
            authority_type="kommune",
            authority_id=BAERUM_ID,
            name="Bærum",
            canonical_domain=BAERUM_DOMAIN,
            access_policy=None,
            active=True,
        )
        registry = SourceRegistry.model_construct(version=1, sources={BAERUM_ID: bypassed})
        fetcher = Fetcher(registry, log, httpx.Client(), _settings())

        with pytest.raises(SourceNotActivatedError, match="without an access-policy check"):
            fetcher.capture(PAGE_URL, "sitemap")

        assert httpx_mock.get_requests() == []
        assert list(log.records()) == []

    def test_a_url_without_a_host_is_refused_rather_than_pooled(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The rate-limit key is the validated host, never a placeholder: a
        fallback string would quietly pool every hostless URL into one bucket."""
        with pytest.raises(SourceNotActivatedError, match="no host"):
            _fetcher(log).capture("file:///etc/passwd", "manual")

        assert httpx_mock.get_requests() == []
        assert list(log.records()) == []


class TestDeclaredSitemaps:
    """Where to start looking is a question the source answers in public."""

    def test_the_declared_sitemaps_are_returned(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        sitemap = f"https://{BAERUM_DOMAIN}/sitemap.xml"
        _allow_robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {sitemap}\n")

        assert _fetcher(log).declared_sitemaps(ROBOTS_URL) == (sitemap,)

    def test_no_declaration_is_an_ordinary_answer(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """Not every site publishes one, and that is not an error to raise."""
        _allow_robots(httpx_mock)

        assert _fetcher(log).declared_sitemaps(ROBOTS_URL) == ()

    def test_an_unreadable_robots_file_declares_nothing(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """The same failure that denies capture also yields no entry points —
        a site whose rules could not be read is not one to start crawling."""
        httpx_mock.add_exception(httpx.ConnectError("unreachable"), url=ROBOTS_URL)

        assert _fetcher(log).declared_sitemaps(ROBOTS_URL) == ()

    def test_reading_the_declaration_records_nothing(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """A declaration is not an observation: nothing was retrieved from a
        source endpoint on anyone's behalf."""
        _allow_robots(
            httpx_mock, f"User-agent: *\nAllow: /\nSitemap: https://{BAERUM_DOMAIN}/s.xml\n"
        )

        _fetcher(log).declared_sitemaps(ROBOTS_URL)

        assert list(log.records()) == []

    def test_every_declared_sitemap_is_returned_in_file_order(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """robots.txt is not limited to one ``Sitemap:`` line, and a source
        that lists several is declaring several starting points, not one."""
        first = f"https://{BAERUM_DOMAIN}/sitemap-forskrifter.xml"
        second = f"https://{BAERUM_DOMAIN}/sitemap-vedtak.xml"
        _allow_robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {first}\nSitemap: {second}\n")

        assert _fetcher(log).declared_sitemaps(ROBOTS_URL) == (first, second)

    def test_the_robots_fetch_is_shared_with_the_allow_check(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """``declared_sitemaps`` and ``capture`` read the same host's
        robots.txt through the same gate, so asking where to start must not
        cost a second request beyond the one the politeness check already
        makes."""
        sitemap = f"https://{BAERUM_DOMAIN}/sitemap.xml"
        _allow_robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {sitemap}\n")
        httpx_mock.add_response(url=PAGE_URL, content=PAYLOAD)
        fetcher = _fetcher(log)

        fetcher.declared_sitemaps(ROBOTS_URL)
        fetcher.capture(PAGE_URL, "sitemap")

        assert [r.url for r in httpx_mock.get_requests()].count(ROBOTS_URL) == 1
