"""Network capture of crosswalk evidence into a write-once cache (#610, rulings D1, D2, D5).

The only network stage. It reads two sources and nothing else: Lovtidend avd. I
archives from `https://api.lovdata.no/v1/publicData/` and Stortinget
`https://data.stortinget.no/eksport/{saker,publikasjoner,sak}`. regjeringen.no is
never fetched while its reuse terms are unruled (R4). Redirects are refused,
bodies are size-bounded and parsed with the safe XML parser, Stortinget calls
are paced under its 100-a-minute limit, and a refusal is recorded as that
refusal, never as an empty answer.

Every object is stored once under its digest; a second capture never rewrites a
first. Lovdata bytes stay in the gitignored operator cache, never in Git.
"""

import re
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from lovspor.crosswalk.cache import (
    MANIFEST,
    Entry,
    Manifest,
    cited_decisions,
    discovery_candidates,
    law_records,
    listing_keys,
    verified_archive,
    write_manifest,
)
from lovspor.crosswalk.canonical import UTF8
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.errors import LovsporError, NetworkError, StortingetStatusError
from lovspor.sources.lovdata import LovdataArchive, LovdataClient
from lovspor.stortinget_case_source import CaseSnapshot, archive_sak
from lovspor.stortinget_discovery import ListingKind, ListingSnapshot, fetch_case, fetch_listing

ARCHIVE_URL = "https://api.lovdata.no/v1/publicData/get/{}"
AVD1_ARCHIVE = re.compile(r"lovtidend-avd1-(?:2001-2025|\d{4})\.tar\.bz2")
ARCHIVE_BYTE_LIMIT = 1_000_000_000
REQUEST_GAP_SECONDS = 0.7  # Stortinget allows 100 calls a minute.
RATE_LIMIT_PAUSE_SECONDS = 61.0
ATTEMPTS = 3
_TOO_MANY = 429
_SERVER_ERROR = 500


class Pacer:
    """Single-threaded spacing of Stortinget requests, with a progress log.

    The clock and the sleep are injectable so tests can measure the pacing
    without waiting for it.
    """

    def __init__(
        self,
        log: Path,
        gap: float = REQUEST_GAP_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._gap, self._log = gap, log
        self._last: float | None = None
        self._clock, self.sleep = clock, sleep

    def wait(self) -> None:
        if self._last is not None:
            delay = self._last + self._gap - self._clock()
            if delay > 0:
                self.sleep(delay)
        self._last = self._clock()

    def note(self, line: str) -> None:
        with self._log.open("a", encoding=UTF8) as stream:
            stream.write(f"{datetime.now(UTC).isoformat()} {line}\n")


def _retryable(exc: LovsporError) -> float | None:
    """The pause before one more try, or None when the refusal is final."""
    if isinstance(exc, StortingetStatusError):
        if exc.status_code == _TOO_MANY:
            return RATE_LIMIT_PAUSE_SECONDS
        return REQUEST_GAP_SECONDS * 4 if exc.status_code >= _SERVER_ERROR else None
    return REQUEST_GAP_SECONDS * 4 if isinstance(exc, NetworkError) else None


def attempt[T](pacer: Pacer, request: Callable[[], T]) -> T:
    """At most ATTEMPTS paced tries; a 429, a 5xx or a transport failure may be retried."""
    for number in range(1, ATTEMPTS + 1):
        pacer.wait()
        try:
            return request()
        except LovsporError as exc:
            pause = _retryable(exc)
            if pause is None or number == ATTEMPTS:
                raise
            pacer.note(f"retry after {type(exc).__name__}: {exc}; pausing {pause}s")
            pacer.sleep(pause)
    raise AssertionError("unreachable")


def store_once(directory: Path, name: str, body: bytes) -> Path:
    """Write-once: an existing object must already hold exactly these bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    try:
        with path.open("xb") as output:
            output.write(body)
    except FileExistsError:
        if path.read_bytes() != body:
            raise CrosswalkError(f"cached object differs from its digest name: {path}") from None
    return path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _failure(role: Entry, exc: LovsporError) -> Entry:
    status = exc.status_code if isinstance(exc, StortingetStatusError) else None
    error = f"{type(exc).__name__}: {exc}"
    return {**role, "error": error, "http_status": status, "retrieved_at": _now()}


def _response_record(snapshot: ListingSnapshot | CaseSnapshot, path: Path, cache: Path) -> Entry:
    return {
        "source_url": snapshot.source_url,
        "retrieved_at": snapshot.observed_at.isoformat(),
        "content_type": snapshot.content_type,
        "sha256": snapshot.source_sha256,
        "size_bytes": len(snapshot.body),
        "path": str(path.relative_to(cache)),
        "error": None,
        "http_status": 200,
    }


def capture_listing(client: httpx.Client, cache: Path, pacer: Pacer, key: tuple[str, str]) -> Entry:
    kind, session = key
    role = {"stage": "discovery", "kind": kind, "key": session}
    try:
        snapshot = attempt(pacer, lambda: fetch_listing(client, _kind(kind), session))
    except LovsporError as exc:
        pacer.note(f"FAIL {kind} {session}: {exc}")
        return _failure(role, exc)
    pacer.note(f"ok {kind} {session} {snapshot.source_sha256[:12]}")
    name = f"{kind}-{session}-{snapshot.source_sha256}.xml"
    path = store_once(cache / "stortinget/listings", name, snapshot.body)
    return {**role, **_response_record(snapshot, path, cache)}


def _kind(kind: str) -> ListingKind:
    if kind not in ("saker", "publikasjoner"):
        raise CrosswalkError(f"not a Stortinget listing: {kind!r}")
    return "saker" if kind == "saker" else "publikasjoner"


def capture_sak(client: httpx.Client, cache: Path, pacer: Pacer, key: tuple[str, int]) -> Entry:
    stage, sak_id = key
    role = {"stage": stage, "kind": "sak", "key": sak_id}
    try:
        snapshot = attempt(pacer, lambda: fetch_case(client, sak_id))
    except LovsporError as exc:
        pacer.note(f"FAIL {stage} sak {sak_id}: {exc}")
        return _failure(role, exc)
    pacer.note(f"ok {stage} sak {sak_id} {snapshot.source_sha256[:12]}")
    try:
        path = archive_sak(snapshot, cache / "stortinget" / stage)
    except ValueError as exc:
        raise CrosswalkError(f"sak {sak_id} could not be stored once: {exc}") from exc
    return {**role, **_response_record(snapshot, path, cache)}


def avd1_archives(catalogue: Iterable[LovdataArchive]) -> list[LovdataArchive]:
    """Every Lovtidend avd. I archive the catalogue lists; a new year is captured, not trusted."""
    found = [entry for entry in catalogue if AVD1_ARCHIVE.fullmatch(entry.filename)]
    for entry in found:
        if not 0 < entry.size_bytes <= ARCHIVE_BYTE_LIMIT:
            raise CrosswalkError(f"{entry.filename}: catalogue size {entry.size_bytes} refused")
    if not found:
        raise CrosswalkError("the publicData catalogue lists no Lovtidend avd. I archive")
    return sorted(found, key=lambda entry: entry.filename)


def _archive_record(entry: LovdataArchive, retrieved_at: str, path: Path, cache: Path) -> Entry:
    return {
        "filename": entry.filename,
        "source_url": ARCHIVE_URL.format(entry.filename),
        "retrieved_at": retrieved_at,
        "last_modified": entry.last_modified.isoformat(),
        "catalogue_size_bytes": entry.size_bytes,
        "path": str(path.relative_to(cache)),
    }


def capture_archive(client: LovdataClient, entry: LovdataArchive, cache: Path) -> Entry:
    """Download into staging, then keep it once under its digest; never overwrite a pin."""
    retrieved_at = _now()
    staging = cache / "lovdata" / ".staging"
    result = client.download(entry, staging)
    target = cache / "lovdata" / result.sha256 / entry.filename
    if target.exists():
        result.path.unlink()
        pin = {"path": str(target.relative_to(cache)), "sha256": result.sha256}
        verified_archive(cache, pin)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(result.path, target)
    record = _archive_record(entry, retrieved_at, target, cache)
    return {**record, "sha256": result.sha256, "size_bytes": result.size_bytes}


def capture_lovdata(client: LovdataClient, cache: Path) -> list[Entry]:
    return [
        capture_archive(client, entry, cache) for entry in avd1_archives(client.list_datasets())
    ]


_LAW_ID = re.compile(r"LOV-\d{4}-\d{2}-\d{2}-[1-9]\d*")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Sources:
    """The clients a capture uses; each defaults to the real, redirect-refusing one."""

    lovdata: LovdataClient | None = None
    stortinget: httpx.Client | None = None
    pacer: Pacer | None = None


REAL_SOURCES = Sources()


def checked_scope(law_ids: Iterable[str], lovverk_commit: str | None) -> list[str]:
    ordered = sorted(set(law_ids))
    bad = [law_id for law_id in ordered if not _LAW_ID.fullmatch(law_id)]
    if bad or not ordered:
        raise CrosswalkError(f"not a list of Lovdata law ids: {bad[:3] or 'empty'}")
    if lovverk_commit is None or not _GIT_SHA.fullmatch(lovverk_commit):
        raise CrosswalkError("lovverk_commit must be a 40-hex commit id")
    return ordered


def _new_manifest(cache: Path, law_ids: list[str], lovverk_commit: str) -> Manifest:
    if (cache / MANIFEST).exists():
        raise CrosswalkError(f"{cache} already holds a capture; use a new directory")
    cache.mkdir(parents=True, exist_ok=True)
    scope = {"law_ids": law_ids, "lovverk_commit": lovverk_commit}
    return {"started_at": _now(), "scope": scope, "lovdata": [], "stortinget": []}


def capture_stortinget(client: httpx.Client, cache: Path, manifest: Manifest, pacer: Pacer) -> None:
    """Listings of every cited decision's sessions, then every case a hint proposes."""
    laws = law_records(cache, manifest["lovdata"], manifest["scope"]["law_ids"])
    decisions = cited_decisions(laws)
    for key in listing_keys(decisions):
        manifest["stortinget"].append(capture_listing(client, cache, pacer, key))
        write_manifest(cache, manifest)
    for sak_id in discovery_candidates(cache, manifest, decisions):
        manifest["stortinget"].append(capture_sak(client, cache, pacer, ("discovery", sak_id)))
        write_manifest(cache, manifest)


def capture(
    cache: Path, law_ids: Iterable[str], lovverk_commit: str, sources: Sources = REAL_SOURCES
) -> Manifest:
    """One complete capture into a new cache directory."""
    manifest = _new_manifest(cache, checked_scope(law_ids, lovverk_commit), lovverk_commit)
    pacer = sources.pacer or Pacer(cache / "progress.log")
    with sources.lovdata or LovdataClient() as lovdata:
        manifest["lovdata"] = capture_lovdata(lovdata, cache)
    write_manifest(cache, manifest)
    client = sources.stortinget or httpx.Client(timeout=60, follow_redirects=False)
    with client as stortinget:
        capture_stortinget(stortinget, cache, manifest, pacer)
    manifest["finished_at"] = _now()
    write_manifest(cache, manifest)
    return manifest
