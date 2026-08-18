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

    def test_an_oversized_response_is_recorded_and_never_stored(
        self, log: ObservationLog, httpx_mock: HTTPXMock
    ) -> None:
        """Partial bytes with a hash of their own would be evidence of
        something the source never served."""
        _allow_robots(httpx_mock)
        httpx_mock.add_response(url=PAGE_URL, content=b"x" * 5000)

        result = _fetcher(log).capture(PAGE_URL, "sitemap", "baerum_html")

        assert isinstance(result, FetchFailure)
        assert result.outcome == "response_exceeded_max_bytes"
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
