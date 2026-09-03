"""Derived freshness index beside the observation log (issue #201).

The capture-state fold — ``url -> latest observed_at`` plus the failure
holds — is recomputed from the whole log on every round, so a round's cost
grows with the archive forever. This sidecar persists the fold together
with the byte offset of the log it was built from, so the next round folds
only the tail.

ADR-0010 §7 discipline: the index is ``derived = f(observation_snapshot,
derivation_version)`` — an accelerant for ``worth_capturing``'s decision,
never authoritative, never evidence, never read by ``verify``. The log is
unconditionally primary. It is consulted only when its anchor proves the
skipped prefix is the same bytes it was built from: the recorded offset
must not exceed the log, and the SHA-256 of the whole prefix up to the
offset must match. Any doubt — missing file, unparseable content, another
derivation version, a shrunken log, a mismatched digest — costs one full
re-fold and a fresh index, never a wrong answer. The invariant issue #201
names: divergence may only ever cost a redundant fetch, never a skipped
one — and with a proven prefix plus a damage-checked tail, the indexed
fold IS the full fold.

The proof is the WHOLE prefix, not a window: a windowed anchor would let
a same-size rewrite (or bit rot) deeper in the file serve a fold of bytes
the log no longer holds — the silent-zero class, and a narrowing of the
damage-refusal guarantee the full read gives. The price is honest: each
open re-reads the prefix as raw bytes to hash it, so the linear component
drops from parse cost to raw-IO cost (roughly an order of magnitude)
rather than to zero. Any byte change anywhere forces the full re-fold,
whose parse surfaces damage exactly as before.

The tail read reuses :meth:`ObservationLog.scan_into`, so torn or
malformed tail lines surface in the returned ``LogScan`` exactly as they
do on a full read — the caller's damaged-log refusal is unchanged, and a
scan that did not complete never advances the index.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lovspor.atomic_io import atomic_write_text
from lovspor.observatory.freshness import CaptureState, FailureHold, collect_capture_state
from lovspor.observatory.log import LogScan, ObservationLog
from lovspor.observatory.model import require_utc

FRESHNESS_INDEX_FILENAME = "freshness-index.json"

INDEX_DERIVATION_VERSION = 1
"""Behaviour version of the capture-state fold this index caches.

Bump on ANY change to what ``collect_capture_state`` folds — sighting
rules, hold transitions, record selection — the ``TEMPORAL_PARSER_VERSION``
precedent: an index written by other fold semantics must rebuild, never be
silently reused.
"""

_DIGEST_CHUNK = 1 << 20
"""Read size for hashing the prefix — bounded memory at any archive size."""


class StoredHold(BaseModel):
    """One :class:`~lovspor.observatory.freshness.FailureHold`, serialised."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: str
    consecutive: int = Field(ge=1)
    last_failed_at: datetime

    @field_validator("last_failed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return require_utc(value)


class FreshnessIndex(BaseModel):
    """The persisted fold plus the anchor that proves what it was built from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    derivation_version: int
    log_offset: int = Field(ge=0)
    prefix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: Binds the cached fold to the anchor it claims to derive from: the
    #: digest proves the LOG bytes, not the cached state, and a hand-altered
    #: state under a valid anchor would invent sightings that suppress
    #: fetches — the silent-zero class. Recomputed and compared on load;
    #: any mismatch is doubt, and doubt rebuilds (codex-tests, PR #233).
    state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed: dict[str, datetime]
    holds: dict[str, StoredHold]

    @field_validator("observed")
    @classmethod
    def _utc_observed(cls, value: dict[str, datetime]) -> dict[str, datetime]:
        return {url: require_utc(when) for url, when in value.items()}


def freshness_index_path(log: ObservationLog) -> Path:
    """The sidecar's place, beside the log under the same checked root."""
    return log.root / FRESHNESS_INDEX_FILENAME


def indexed_capture_state(log: ObservationLog) -> tuple[CaptureState, LogScan]:
    """The register-wide capture state, folding only what the index cannot prove.

    Returns the state and the scan whose ``complete`` the caller must check
    exactly as for a full read — a damaged log still refuses, and never
    advances the index. Only the unnarrowed (whole-register) fold is
    indexed: a per-authority fold is a different function of the log, and
    caching every narrowing would multiply the artifact for the cold path.
    """
    path = freshness_index_path(log)
    index = _load_index(path)
    if index is not None and _prefix_proven(log, index):
        state = _state_from_index(index)
        scan = log.scan_into(collect_capture_state(state), start=index.log_offset)
    else:
        state = CaptureState.empty()
        scan = log.scan_into(collect_capture_state(state))
    if scan.complete:
        _write_index(path, _index_from_state(log, state, scan.clean_through))
    return state, scan


def _load_index(path: Path) -> FreshnessIndex | None:
    """The stored index, or None for ANY doubt — a derived artifact is
    discarded, never repaired (deleting it costs one slow round)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        # Bytes straight into pydantic: undecodable content is exactly as
        # doubtful as unparseable JSON, and one gate answers both.
        index = FreshnessIndex.model_validate_json(raw)
    except ValidationError:
        return None
    if index.derivation_version != INDEX_DERIVATION_VERSION:
        return None
    if index.state_sha256 != _state_binding(index):
        return None
    return index


def _prefix_proven(log: ObservationLog, index: FreshnessIndex) -> bool:
    """True when the log still begins with the bytes the index was built from."""
    try:
        size = log.log_path.stat().st_size
    except OSError:
        return False
    if index.log_offset > size:
        return False
    return _prefix_digest(log.log_path, index.log_offset) == index.prefix_sha256


def _state_binding(index: FreshnessIndex) -> str:
    """The fold bound to its anchor — one digest over both, order-fixed."""
    canonical = json.dumps(
        {
            "derivation_version": index.derivation_version,
            "log_offset": index.log_offset,
            "prefix_sha256": index.prefix_sha256,
            "observed": {url: when.isoformat() for url, when in sorted(index.observed.items())},
            "holds": {
                url: hold.model_dump(mode="json") for url, hold in sorted(index.holds.items())
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prefix_digest(log_path: Path, offset: int) -> str:
    """SHA-256 of the log's first ``offset`` bytes, read in bounded chunks."""
    digest = hashlib.sha256()
    remaining = offset
    with log_path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(_DIGEST_CHUNK, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _state_from_index(index: FreshnessIndex) -> CaptureState:
    return CaptureState(
        observed=dict(index.observed),
        holds={
            url: FailureHold(hold.outcome, hold.consecutive, hold.last_failed_at)
            for url, hold in index.holds.items()
        },
    )


def _index_from_state(log: ObservationLog, state: CaptureState, offset: int) -> FreshnessIndex:
    unbound = FreshnessIndex(
        derivation_version=INDEX_DERIVATION_VERSION,
        log_offset=offset,
        prefix_sha256=_prefix_digest(log.log_path, offset) if offset else _empty_digest(),
        state_sha256=_empty_digest(),
        # Sorted so the document is byte-identical for a given log state —
        # the ADR-0010 §7 determinism the whole derivation family carries.
        observed=dict(sorted(state.observed.items())),
        holds={
            url: StoredHold(
                outcome=hold.outcome,
                consecutive=hold.consecutive,
                last_failed_at=hold.last_failed_at,
            )
            for url, hold in sorted(state.holds.items())
        },
    )
    return unbound.model_copy(update={"state_sha256": _state_binding(unbound)})


def _empty_digest() -> str:
    return hashlib.sha256(b"").hexdigest()


def _write_index(path: Path, index: FreshnessIndex) -> None:
    """Atomic replace: a reader never observes a half-written index, and two
    concurrent rebuilds each leave a whole, valid document (last one wins —
    both are correct derivations, one merely further along)."""
    atomic_write_text(path, index.model_dump_json() + "\n")
