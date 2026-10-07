"""One artifact read out of the observatory archive, for promotion (ADR-0016 S3).

Read-only, and only through the observatory's own readers: the corrected view
of the observation log (ADR-0015, so a re-attributed record counts for the
authority it was re-filed to) and the blob store. Nothing here writes to the
archive, and nothing from it is copied into the engine repository (ADR-0010 §5).

An artifact is named by its authority and either the SHA-256 of its bytes or
the URL that served them. The pair (URL, bytes) must be unique under the
authority; when it is not, the request is refused with the candidates listed
rather than one of them picked.

The observations a promotion reads are those **up to a cut-off** — the
reviewer's decision time. Observations of the same bytes appended after the
approval therefore change nothing: a rerun reads the same snapshot and writes
the same files (ADR-0016 4e), and refreshing the intervals is the weekly
bookkeeping of a later slice.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, FetchFailure, ObservationRecord
from lovspor.promotion.decisions import ArtifactKey, ObservationUsed, utc_text

_SHA256 = re.compile(r"[0-9a-f]{64}")

#: A record of one fetch, with bytes or without.
Fetch = ArtifactObservation | FetchFailure


class SourceStatus(BaseModel):
    """The last thing the archive saw at the primary URL, up to the cut-off."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: str
    http_status: int | None
    observed_at: str


class ArchivedArtifact(BaseModel):
    """The bytes of one artifact and what the archive observed of them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: ArtifactKey
    content_type: str
    payload: bytes
    observed_at_first: datetime
    observed_at_last: datetime
    observations: tuple[ObservationUsed, ...]
    corroborating_urls: tuple[str, ...]
    source_status: SourceStatus


def authority_fetches(log: ObservationLog, authority_id: str) -> tuple[Fetch, ...]:
    """Every fetch filed under ``authority_id`` in the corrected log, in append order."""
    found: list[Fetch] = []

    def collect(record: ObservationRecord) -> None:
        if isinstance(record, Fetch) and record.authority_id == authority_id:
            found.append(record)

    scan = log.scan_corrected_into(collect)
    if not scan.complete:
        msg = "the observation log is damaged; run `lovspor observatory verify` first"
        raise PromotionRefusedError(msg)
    return tuple(found)


def locate(fetches: tuple[Fetch, ...], authority_id: str, artifact: str) -> ArtifactKey:
    """The one (URL, bytes) pair ``artifact`` names — a SHA-256 or a source URL."""
    by_hash = _SHA256.fullmatch(artifact) is not None
    pairs = sorted(
        {
            (f.url, f.sha256)
            for f in fetches
            if isinstance(f, ArtifactObservation) and artifact == (f.sha256 if by_hash else f.url)
        }
    )
    if len(pairs) != 1:
        listed = "; ".join(f"{sha} at {url}" for url, sha in pairs) or "none"
        msg = f"{artifact} names {len(pairs)} artifact(s) under {authority_id}: {listed}"
        raise PromotionRefusedError(msg)
    url, sha256 = pairs[0]
    return ArtifactKey(authority_id=authority_id, sha256=sha256, source_url=url)


def read_artifact(
    log: ObservationLog, fetches: tuple[Fetch, ...], key: ArtifactKey, through: datetime | None
) -> ArchivedArtifact:
    """The artifact's bytes and its observations up to ``through`` (all when ``None``)."""
    seen = [f for f in fetches if through is None or f.observed_at <= through]
    own = _own(seen, key)
    if not own:
        msg = f"{key.sha256} was not observed at {key.source_url} by the decision time"
        raise PromotionRefusedError(msg)
    return ArchivedArtifact(
        key=key,
        content_type=own[0].content_type,
        payload=read_blob(log, key.sha256),
        observed_at_first=own[0].observed_at,
        observed_at_last=own[-1].observed_at,
        observations=tuple(_used(f) for f in own),
        corroborating_urls=_corroborating(seen, key),
        source_status=source_status(seen, key.source_url),
    )


def _own(seen: list[Fetch], key: ArtifactKey) -> list[ArtifactObservation]:
    """The observations of exactly these bytes at exactly this URL, oldest first."""
    own = [
        f
        for f in seen
        if isinstance(f, ArtifactObservation) and f.sha256 == key.sha256 and f.url == key.source_url
    ]
    return sorted(own, key=lambda f: f.observed_at)


def _is_artifact(fetch: Fetch, sha256: str) -> bool:
    return isinstance(fetch, ArtifactObservation) and fetch.sha256 == sha256


def _used(fetch: ArtifactObservation) -> ObservationUsed:
    return ObservationUsed(
        observed_at=utc_text(fetch.observed_at), url=fetch.url, sha256=fetch.sha256
    )


def _corroborating(seen: list[Fetch], key: ArtifactKey) -> tuple[str, ...]:
    """Other URLs that served the very same bytes — byte-identical copies only.

    A copy in another form (the PDF of an HTML page) carries the regulation
    too, but telling so needs its text compared; that is the backfill slice.
    """
    urls = {f.url for f in seen if _is_artifact(f, key.sha256)}
    return tuple(sorted(urls - {key.source_url}))


def source_status(seen: list[Fetch], url: str) -> SourceStatus:
    """The last fetch of ``url`` among ``seen``: retrieved, or the failure's outcome."""
    last = sorted((f for f in seen if f.url == url), key=lambda f: f.observed_at)[-1]
    outcome = "retrieved" if isinstance(last, ArtifactObservation) else last.outcome
    return SourceStatus(
        outcome=outcome, http_status=last.http_status, observed_at=utc_text(last.observed_at)
    )


def read_blob(log: ObservationLog, sha256: str) -> bytes:
    """The archived bytes of ``sha256``, refused when tombstoned, missing or altered."""
    if sha256 in log.tombstoned_hashes():
        msg = f"{sha256} is tombstoned in the archive; it cannot be re-verified (ADR-0010 §7)"
        raise PromotionRefusedError(msg)
    try:
        payload = log.read_blob(sha256)
    except FileNotFoundError as exc:
        msg = f"blob {sha256} is not on disk; run `lovspor observatory verify`"
        raise PromotionRefusedError(msg) from exc
    if hashlib.sha256(payload).hexdigest() != sha256:
        msg = f"blob {sha256} no longer hashes to its name; run `lovspor observatory verify`"
        raise PromotionRefusedError(msg)
    return payload
