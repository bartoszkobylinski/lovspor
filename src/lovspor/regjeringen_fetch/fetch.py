"""Fetch an owner-named list of regjeringen.no Prop. L / LS pages, politely (#738).

ADR-0018 *Legal and access boundary* and Amendment 1 point 4 set the rules this
module enforces:

- **Only listed URLs, only the grammar.** A Prop. L / LS landing page or one
  ``?ch=N`` chapter page on ``www.regjeringen.no`` over HTTPS. Anything else
  refuses the whole list before a request is made. Nothing is discovered.
- **robots.txt read live, once per run.** An unreadable one stops the run; a
  disallowed URL is recorded and never requested. Its digest goes into the run
  record so the standing policy notices when the rules change.
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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from lovspor.errors import RegjeringenFetchError
from lovspor.observatory.fetch import CaptureSettings, RateLimiter
from lovspor.observatory.robots import RobotsPolicy
from lovspor.observatory.robots_live import published_policy
from lovspor.regjeringen_fetch.models import FetchOutcome, FetchRecord, FetchRun, run_file_name

USER_AGENT = "lovspor-forarbeider/1 (+https://lovspor.no; kontakt@lovspor.no)"
HOST = "www.regjeringen.no"
ROBOTS_URL = f"https://{HOST}/robots.txt"
MAX_URLS = 200
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
_URL = re.compile(
    r"https://www\.regjeringen\.no/no/dokumenter/prop\.-[1-9]\d{0,3}-ls?-\d{8}"
    r"/id[1-9]\d{0,11}/(\?ch=[1-9]\d{0,4})?"
)
_STOP_STATUSES = frozenset({403, 429})
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


@dataclass(frozen=True)
class _Robots:
    status: int
    sha256: str
    policy: RobotsPolicy


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


def run_fetch(
    client: httpx.Client, urls: tuple[str, ...], out: Path, settings: FetchSettings
) -> FetchRun:
    """Fetch ``urls`` into ``out`` under the rules above and write the run record.

    Raises:
        RegjeringenFetchError: robots.txt could not be read; nothing was fetched.
    """
    session = _Session(client, out, settings, RateLimiter(_capture_settings(settings)))
    started_at = settings.clock.now()
    robots = _read_robots(session)
    session.delay = max(settings.floor_seconds, robots.policy.crawl_delay(USER_AGENT) or 0.0)
    run = FetchRun(
        user_agent=USER_AGENT,
        started_at=started_at,
        robots_url=ROBOTS_URL,
        robots_status=robots.status,
        robots_sha256=robots.sha256,
        delay_seconds=session.delay,
        records=_fetch_all(session, urls, robots.policy),
    )
    _write_private(out / run_file_name(started_at), run.model_dump_json(indent=2).encode())
    return run


def _capture_settings(settings: FetchSettings) -> CaptureSettings:
    clock = settings.clock
    return CaptureSettings(monotonic=clock.monotonic, sleep=clock.sleep)


def _read_robots(session: _Session) -> _Robots:
    session.limiter.wait(HOST, session.delay)
    try:
        response = session.client.get(ROBOTS_URL, headers=_headers(), timeout=_timeout(session))
    except httpx.HTTPError as error:
        raise RegjeringenFetchError("robots_unreadable", type(error).__name__) from error
    policy = published_policy(response)
    if policy is None:
        raise RegjeringenFetchError("robots_unreadable", str(response.status_code))
    digest = hashlib.sha256(response.content).hexdigest()
    return _Robots(response.status_code, digest, policy)


def _fetch_all(
    session: _Session, urls: tuple[str, ...], policy: RobotsPolicy
) -> tuple[FetchRecord, ...]:
    records: list[FetchRecord] = []
    stopped = False
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
