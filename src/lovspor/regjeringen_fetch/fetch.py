"""Fetch an owner-named list of regjeringen.no Prop. L / LS pages, politely (#738).

ADR-0018 *Legal and access boundary* and Amendment 1 points 6 and 8 set the rules this
module enforces:

- **Only listed URLs, only the grammar.** A Prop. L / LS landing page or one
  ``?ch=N`` chapter page on ``www.regjeringen.no`` over HTTPS. Anything else
  refuses the whole list before a request is made. Nothing is discovered.
- **robots.txt read live, once per run** (Amendment 1 point 8). No automatic
  redirect: a 3xx is followed by hand for at most five hops, each to ``https``
  on ``www.regjeringen.no``, and a redirect body is never parsed. A 403 or 429
  stops the run, as does an unreadable answer; any other 4xx means no rules are
  published. A disallowed URL is recorded and never requested. The exact bytes
  served are stored under their digest, so a standing policy can bind them and
  notice when the rules change.
- **Website terms, when named.** ``terms_url`` is read once in the same run,
  under the same rules and spacing, following no redirect, and stored the same
  way. Neither page grants reuse rights; they are the current source proof.
- **Spaced requests.** At least ``floor_seconds`` between requests, longer if
  robots.txt asks for a longer ``Crawl-delay``.
- **Stop on refusal.** A 403 or 429 ends the run; later URLs are recorded as
  not attempted.
- **No redirect is followed**, and a body over ``max_bytes`` is never stored.

Pages land owner-only under their SHA-256, outside every Git checkout. The
offline ``forarbeider-metadata`` / ``forarbeider-chapter`` commands read them.
"""

import hashlib
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from lovspor.errors import RegjeringenFetchError
from lovspor.observatory.fetch import CaptureSettings, RateLimiter
from lovspor.observatory.robots import RobotsPolicy
from lovspor.regjeringen_fetch.models import (
    FetchOutcome,
    FetchRecord,
    FetchRun,
    Hop,
    SourceEvidence,
    SourceKind,
    SourceState,
    evidence_file_name,
    run_file_name,
)

USER_AGENT = "lovspor-forarbeider/1 (+https://lovspor.no; kontakt@lovspor.no)"
HOST = "www.regjeringen.no"
ROBOTS_URL = f"https://{HOST}/robots.txt"
MAX_URLS = 200
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
_URL = re.compile(
    r"https://www\.regjeringen\.no/no/dokumenter/prop\.-[1-9]\d{0,3}-ls?-\d{8}"
    r"/id[1-9]\d{0,11}/(\?ch=[1-9]\d{0,4})?"
)
_TERMS_PATH = re.compile(r"/|(/[A-Za-z0-9._~-]+)+/?")
_STOP_STATUSES = frozenset({403, 429})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
ROBOTS_MAX_BYTES = 512 * 1024
MAX_ROBOTS_HOPS = 5
_PRIVATE_BITS = 0o077


def _local_now() -> datetime:
    return datetime.now().astimezone()


@dataclass(frozen=True)
class FetchClock:
    """Time as an input, so spacing and ``retrieved_at`` are testable."""

    now: Callable[[], datetime] = _local_now
    monotonic: Callable[[], float] = field(default_factory=lambda: CaptureSettings().monotonic)
    sleep: Callable[[float], None] = field(default_factory=lambda: CaptureSettings().sleep)


@dataclass(frozen=True)
class FetchSettings:
    """The fetcher's own limits. The floor is a minimum, never a target."""

    max_bytes: int = DEFAULT_MAX_BYTES
    floor_seconds: float = 5.0
    timeout_seconds: float = 30.0
    clock: FetchClock = field(default_factory=FetchClock)
    terms_url: str | None = None


@dataclass(frozen=True)
class _Robots:
    evidence: SourceEvidence
    policy: RobotsPolicy


@dataclass(frozen=True)
class _Reply:
    """One response, read once: the body only for a 200 within the cap."""

    status: int
    location: str | None
    body: bytes | None


@dataclass
class _Read:
    """One source-policy page being read: its hops so far and the latest request time."""

    kind: SourceKind
    requested_url: str
    retrieved_at: datetime
    hops: list[Hop] = field(default_factory=list)

    def evidence(
        self, state: SourceState, status: int | None, body: bytes | None = None
    ) -> SourceEvidence:
        return SourceEvidence(
            kind=self.kind,
            requested_url=self.requested_url,
            hops=tuple(self.hops),
            state=state,
            status=status,
            retrieved_at=self.retrieved_at,
            sha256=None if body is None else hashlib.sha256(body).hexdigest(),
            size=None if body is None else len(body),
        )


@dataclass
class _Session:
    """One run's moving parts, so helpers stay under four parameters."""

    client: httpx.Client
    out: Path
    settings: FetchSettings
    limiter: RateLimiter
    delay: float = 0.0


def read_url_list(path: Path, limit: int) -> tuple[str, ...]:
    """The listed URLs in order, without blanks, ``#`` comments or repeats.

    Raises:
        RegjeringenFetchError: a URL is outside the grammar, or the list is empty or too long.
    """
    urls: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line in urls:
            continue
        if not _URL.fullmatch(line):
            raise RegjeringenFetchError("url_not_allowed", line)
        urls.append(line)
    if not urls:
        raise RegjeringenFetchError("no_urls", str(path))
    if len(urls) > min(limit, MAX_URLS):
        raise RegjeringenFetchError("too_many_urls", f"{len(urls)} > {min(limit, MAX_URLS)}")
    return tuple(urls)


def prepare_out(out: Path) -> Path:
    """An owner-only directory outside every Git checkout, created if missing.

    Raises:
        RegjeringenFetchError: the path is relative, inside a checkout, or readable by others.
    """
    if not out.is_absolute():
        raise RegjeringenFetchError("out_not_absolute", str(out))
    resolved = out.resolve()
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise RegjeringenFetchError("out_inside_git", str(out))
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    if resolved.stat().st_mode & _PRIVATE_BITS:
        raise RegjeringenFetchError("out_not_private", str(out))
    return resolved


def check_terms_url(url: str) -> str:
    """``url`` if it names one page on ``https://www.regjeringen.no`` without a query.

    Raises:
        RegjeringenFetchError: any other scheme, host, query, fragment or dot segment.
    """
    parts = urlsplit(url)
    segments = parts.path.split("/")
    if (
        (parts.scheme, parts.netloc) != ("https", HOST)
        or "?" in url
        or "#" in url
        or not _TERMS_PATH.fullmatch(parts.path)
        or "." in segments
        or ".." in segments
    ):
        raise RegjeringenFetchError("terms_url_not_allowed", url)
    return url


def run_fetch(
    client: httpx.Client, urls: tuple[str, ...], out: Path, settings: FetchSettings
) -> FetchRun:
    """Fetch ``urls`` into ``out`` under the rules above and write the run record.

    Raises:
        RegjeringenFetchError: robots.txt could not be read; nothing was fetched.
    """
    if settings.terms_url is not None:
        check_terms_url(settings.terms_url)
    session = _Session(client, out, settings, RateLimiter(_capture_settings(settings)))
    started_at = settings.clock.now()
    robots = _read_robots(session)
    session.delay = max(settings.floor_seconds, robots.policy.crawl_delay(USER_AGENT) or 0.0)
    terms = _read_terms(session, robots.policy)
    refused = terms is not None and terms.state is SourceState.REFUSED
    run = FetchRun(
        user_agent=USER_AGENT,
        started_at=started_at,
        robots=robots.evidence,
        terms=terms,
        delay_seconds=session.delay,
        records=_fetch_all(session, urls, robots.policy, refused),
    )
    _write_private(out / run_file_name(started_at), run.model_dump_json(indent=2).encode())
    return run


def _capture_settings(settings: FetchSettings) -> CaptureSettings:
    clock = settings.clock
    return CaptureSettings(monotonic=clock.monotonic, sleep=clock.sleep)


def _read_robots(session: _Session) -> _Robots:
    """robots.txt, following at most MAX_ROBOTS_HOPS same-host redirects by hand.

    Raises:
        RegjeringenFetchError: ``robots_refused`` on 403/429, ``robots_unreadable``
            on anything that is neither a readable 200 nor another 4xx.
    """
    read = _Read(SourceKind.ROBOTS_TXT, ROBOTS_URL, session.settings.clock.now())
    reply = _robots_hop(session, read, ROBOTS_URL)
    while reply.status in _REDIRECT_STATUSES and reply.location is not None:
        if len(read.hops) > MAX_ROBOTS_HOPS:
            raise RegjeringenFetchError("robots_unreadable", "too_many_redirects")
        reply = _robots_hop(session, read, _same_host(read.hops[-1].url, reply.location))
    return _robots_answer(session, read, reply)


def _robots_hop(session: _Session, read: _Read, url: str) -> _Reply:
    session.limiter.wait(HOST, session.settings.floor_seconds)
    read.retrieved_at = session.settings.clock.now()
    try:
        reply = _exchange(session, url, ROBOTS_MAX_BYTES)
    except httpx.HTTPError as error:
        raise RegjeringenFetchError("robots_unreadable", type(error).__name__) from error
    read.hops.append(Hop(url=url, status=reply.status, location=reply.location))
    return reply


def _same_host(base: str, location: str) -> str:
    target = urljoin(base, location)
    parts = urlsplit(target)
    if (parts.scheme, parts.netloc) != ("https", HOST):
        raise RegjeringenFetchError("robots_unreadable", "redirect_off_host")
    return target


def _robots_answer(session: _Session, read: _Read, reply: _Reply) -> _Robots:
    status = reply.status
    if status in _STOP_STATUSES:
        raise RegjeringenFetchError("robots_refused", str(status))
    if httpx.codes.is_client_error(status):
        return _Robots(read.evidence(SourceState.ABSENT, status), RobotsPolicy.parse([]))
    if status != httpx.codes.OK:
        raise RegjeringenFetchError("robots_unreadable", str(status))
    if reply.body is None:
        raise RegjeringenFetchError("robots_unreadable", "oversized")
    try:
        lines = reply.body.decode().splitlines()
    except UnicodeDecodeError as error:
        raise RegjeringenFetchError("robots_unreadable", "undecodable") from error
    evidence = read.evidence(SourceState.PUBLISHED, status, reply.body)
    _store_evidence(session.out, evidence, reply.body)
    return _Robots(evidence, RobotsPolicy.parse(lines))


def _read_terms(session: _Session, policy: RobotsPolicy) -> SourceEvidence | None:
    url = session.settings.terms_url
    if url is None:
        return None
    read = _Read(SourceKind.WEBSITE_TERMS, url, session.settings.clock.now())
    if not policy.allows(USER_AGENT, url):
        return read.evidence(SourceState.UNREADABLE, None)
    session.limiter.wait(HOST, session.delay)
    read.retrieved_at = session.settings.clock.now()
    try:
        reply = _exchange(session, url, session.settings.max_bytes)
    except httpx.HTTPError:
        return read.evidence(SourceState.UNREADABLE, None)
    read.hops.append(Hop(url=url, status=reply.status, location=reply.location))
    return _terms_answer(session, read, reply)


def _terms_answer(session: _Session, read: _Read, reply: _Reply) -> SourceEvidence:
    status = reply.status
    if status in _STOP_STATUSES:
        return read.evidence(SourceState.REFUSED, status)
    if httpx.codes.is_client_error(status):
        return read.evidence(SourceState.ABSENT, status)
    if status != httpx.codes.OK or reply.body is None:
        return read.evidence(SourceState.UNREADABLE, status)
    evidence = read.evidence(SourceState.PUBLISHED, status, reply.body)
    _store_evidence(session.out, evidence, reply.body)
    return evidence


def _exchange(session: _Session, url: str, max_bytes: int) -> _Reply:
    with session.client.stream(
        "GET", url, headers=_headers(), timeout=_timeout(session), follow_redirects=False
    ) as response:
        status = response.status_code
        body = _bounded_body(response, max_bytes) if status == httpx.codes.OK else None
        return _Reply(status, response.headers.get("Location"), body)


def _store_evidence(out: Path, evidence: SourceEvidence, body: bytes) -> None:
    """Write once under the digest; an existing file must already hold these bytes."""
    path = out / str(evidence_file_name(evidence))
    if path.exists():
        verified_source_bytes(out, evidence)
        return
    _write_private(path, body)


def verified_source_bytes(out: Path, evidence: SourceEvidence) -> bytes:
    """The stored bytes of published evidence, re-hashed against their record.

    Raises:
        RegjeringenFetchError: the evidence is not published, or the stored file
            is missing, not a private regular file, or not the recorded bytes.
    """
    name = evidence_file_name(evidence)
    if name is None:
        raise RegjeringenFetchError("evidence_not_published", evidence.state)
    path = out / name
    try:
        mode = path.lstat().st_mode
        body = path.read_bytes()
    except OSError as error:
        raise RegjeringenFetchError("evidence_missing", name) from error
    if not stat.S_ISREG(mode) or mode & _PRIVATE_BITS:
        raise RegjeringenFetchError("evidence_not_private", name)
    if hashlib.sha256(body).hexdigest() != evidence.sha256 or len(body) != evidence.size:
        raise RegjeringenFetchError("evidence_mismatch", name)
    return body


def _fetch_all(
    session: _Session, urls: tuple[str, ...], policy: RobotsPolicy, stopped: bool
) -> tuple[FetchRecord, ...]:
    records: list[FetchRecord] = []
    for url in urls:
        if stopped:
            records.append(_record(session, url, FetchOutcome.NOT_ATTEMPTED))
        elif not policy.allows(USER_AGENT, url):
            records.append(_record(session, url, FetchOutcome.ROBOTS_DISALLOWED))
        else:
            records.append(_fetch_one(session, url))
            stopped = records[-1].outcome is FetchOutcome.STOPPED_BY_SERVER
    return tuple(records)


def _fetch_one(session: _Session, url: str) -> FetchRecord:
    session.limiter.wait(urlsplit(url).netloc, session.delay)
    retrieved_at = session.settings.clock.now()
    try:
        with session.client.stream(
            "GET", url, headers=_headers(), timeout=_timeout(session), follow_redirects=False
        ) as response:
            return _answer(session, url, response, retrieved_at)
    except httpx.HTTPError:
        return FetchRecord(url=url, outcome=FetchOutcome.TRANSPORT_ERROR, retrieved_at=retrieved_at)


def _answer(
    session: _Session, url: str, response: httpx.Response, retrieved_at: datetime
) -> FetchRecord:
    status = response.status_code
    base = FetchRecord(
        url=url, outcome=FetchOutcome.HTTP_STATUS, retrieved_at=retrieved_at, status=status
    )
    if response.is_redirect:
        location = response.headers.get("Location")
        update = {"outcome": FetchOutcome.REDIRECT_NOT_FOLLOWED, "location": location}
        return base.model_copy(update=update)
    if status in _STOP_STATUSES:
        return base.model_copy(update={"outcome": FetchOutcome.STOPPED_BY_SERVER})
    if status != httpx.codes.OK:
        return base
    body = _bounded_body(response, session.settings.max_bytes)
    if body is None:
        return base.model_copy(update={"outcome": FetchOutcome.TOO_LARGE})
    digest = hashlib.sha256(body).hexdigest()
    _store_page(session.out / f"{digest}.html", body)
    stored = {"outcome": FetchOutcome.FETCHED, "sha256": digest, "size": len(body)}
    return base.model_copy(update=stored)


def _bounded_body(response: httpx.Response, max_bytes: int) -> bytes | None:
    """The body, or ``None`` once it passes ``max_bytes``: a cut body is never stored."""
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _store_page(path: Path, body: bytes) -> None:
    """Write once: the name is the digest, so an existing file already holds these bytes."""
    if not path.exists():
        _write_private(path, body)


def _write_private(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)


def _record(session: _Session, url: str, outcome: FetchOutcome) -> FetchRecord:
    return FetchRecord(url=url, outcome=outcome, retrieved_at=session.settings.clock.now())


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


def _timeout(session: _Session) -> float:
    return session.settings.timeout_seconds
