"""Byte format of one durable embedding-cache entry (#802, design §4 and §5.3).

An entry holds the vectors a provider returned for one document, or for one
provider request of a long document, together with the identity they were
paid for::

    magic "LSEC" | uint32-BE meta length | canonical JSON meta | payload | SHA-256

The key is the embedding space identity (ADR-0005) and the ADR-0006 input
hash of the whole document; a unit entry adds its index, the unit count and
``hash_embedding_inputs`` over its own slice. No other hash function is
involved, so the cache cannot drift from the identity the manifest stamps.

A document entry stores exactly what the sidecar is made of — int8 rows and
the document's one quantization scale — so a sidecar assembled from it is
byte-identical to the one the paying run would have written. A unit entry
must store float32, because the scale depends on rows not yet fetched.

Decoding validates everything against the key the caller derived from the
*current* document and embedder. A failure raises
:class:`~lovspor.errors.EmbeddingCacheError`; it is never a miss.
"""

import hashlib
import json
import math
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lovspor.errors import EmbeddingCacheError, EmbeddingCacheExpiredError

SCHEMA = 1
MAGIC = b"LSEC"
_LENGTH = struct.Struct(">I")
_DIGEST_SIZE = 32
_FIXED_SIZE = len(MAGIC) + _LENGTH.size + _DIGEST_SIZE
_CLOCK_SKEW = timedelta(minutes=5)
_INT8_MIN_WRITTEN = -127
_DTYPES: dict[str, np.dtype[Any]] = {"int8": np.dtype(np.int8), "float32": np.dtype("<f4")}
_META_FIELDS = frozenset(
    {"schema", "kind", "esi", "input_hash", "dim", "rows", "dtype", "scale", "unit", "created_at"},
)
_ESI_PATTERN = r"^[0-9a-f]{32}$"
_HASH_PATTERN = r"^[0-9a-f]{64}$"


class UnitKey(BaseModel):
    """One provider request of a document that needs more than one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0)
    count: int = Field(ge=2)
    input_hash: str = Field(pattern=_HASH_PATTERN)

    @model_validator(mode="after")
    def _index_in_range(self) -> Self:
        if self.index >= self.count:
            raise ValueError(f"unit index {self.index} out of range for {self.count} units")
        return self


class EntryKey(BaseModel):
    """Everything an entry must match to be served."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    esi: str = Field(pattern=_ESI_PATTERN)
    input_hash: str = Field(pattern=_HASH_PATTERN)
    dim: int = Field(gt=0)
    rows: int = Field(gt=0)
    unit: UnitKey | None = None

    @property
    def kind(self) -> str:
        return "document" if self.unit is None else "unit"

    @property
    def dtype(self) -> str:
        return "int8" if self.unit is None else "float32"

    def label(self) -> str:
        """Short, content-free name for messages: kind, space and hash prefixes."""
        suffix = "" if self.unit is None else f" unit {self.unit.index + 1}/{self.unit.count}"
        return f"{self.kind} esi={self.esi[:8]} input_hash={self.input_hash[:12]}{suffix}"


@dataclass(frozen=True)
class ReadPolicy:
    """Retention and the clock it is measured with."""

    max_age: timedelta
    clock: Callable[[], datetime]


@dataclass(frozen=True)
class CachedVectors:
    """Vectors bound to the key they were paid for."""

    key: EntryKey
    vectors: np.ndarray
    scale: float | None
    created_at: datetime


def encode_entry(entry: CachedVectors) -> bytes:
    """Serialize ``entry``; refuses vectors that do not match their own key."""
    _check_encodable(entry)
    meta = json.dumps(_meta_of(entry), sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = np.ascontiguousarray(entry.vectors, dtype=_DTYPES[entry.key.dtype]).tobytes()
    body = MAGIC + _LENGTH.pack(len(meta)) + meta + payload
    return body + hashlib.sha256(body).digest()


def decode_entry(data: bytes, expected: EntryKey, policy: ReadPolicy) -> CachedVectors:
    """Parse and validate an entry against ``expected``; refuse on any mismatch."""
    meta, payload = _split(data, expected)
    _check_identity(meta, expected)
    vectors = _vectors(payload, expected)
    scale = _scale(meta, expected)
    created_at = _created_at(meta, expected, policy)
    return CachedVectors(key=expected, vectors=vectors, scale=scale, created_at=created_at)


def describe_entry(data: bytes, name: str) -> EntryKey:
    """The key an entry claims, once its digest and meta parse; for audits.

    Serving never trusts this: :func:`decode_entry` compares the entry with
    the key the caller derived. ``verify`` uses it to check a stored file
    against the name it is stored under.
    """
    meta, _payload = _split(data, name)
    try:
        unit = None if meta.get("unit") is None else UnitKey.model_validate(meta["unit"])
        fields = {field: meta[field] for field in ("esi", "input_hash", "dim", "rows")}
        return EntryKey.model_validate({**fields, "unit": unit})
    except (KeyError, TypeError, ValueError) as exc:
        raise _refuse(name, "meta does not describe a valid key") from exc


def _check_encodable(entry: CachedVectors) -> None:
    key = entry.key
    if entry.vectors.shape != (key.rows, key.dim):
        raise ValueError(f"vector shape {entry.vectors.shape} does not match {key.label()}")
    if entry.vectors.dtype != _DTYPES[key.dtype]:
        raise ValueError(f"{key.kind} entry needs {key.dtype} vectors, got {entry.vectors.dtype}")
    if (key.unit is None) != (entry.scale is not None):
        raise ValueError("a document entry has a scale and a unit entry has none")


def _meta_of(entry: CachedVectors) -> dict[str, Any]:
    key = entry.key
    return {
        "schema": SCHEMA,
        "kind": key.kind,
        "esi": key.esi,
        "input_hash": key.input_hash,
        "dim": key.dim,
        "rows": key.rows,
        "dtype": key.dtype,
        "scale": entry.scale,
        "unit": None if key.unit is None else key.unit.model_dump(),
        "created_at": entry.created_at.astimezone(UTC).isoformat(timespec="seconds"),
    }


def _refuse(expected: EntryKey | str, reason: str) -> EmbeddingCacheError:
    label = expected if isinstance(expected, str) else expected.label()
    return EmbeddingCacheError(f"embedding cache entry refused ({label}): {reason}")


def _split(data: bytes, expected: EntryKey | str) -> tuple[dict[str, Any], bytes]:
    if len(data) < _FIXED_SIZE:
        raise _refuse(expected, f"torn entry, {len(data)} bytes")
    if data[: len(MAGIC)] != MAGIC:
        raise _refuse(expected, "bad magic")
    body, digest = data[:-_DIGEST_SIZE], data[-_DIGEST_SIZE:]
    if hashlib.sha256(body).digest() != digest:
        raise _refuse(expected, "digest mismatch (torn or modified)")
    (length,) = _LENGTH.unpack_from(body, len(MAGIC))
    start = len(MAGIC) + _LENGTH.size
    if start + length > len(body):
        raise _refuse(expected, "meta length past the end")
    try:
        meta = json.loads(body[start : start + length].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _refuse(expected, "meta is not JSON") from exc
    if not isinstance(meta, dict):
        raise _refuse(expected, "meta is not an object")
    return meta, body[start + length :]


def _check_identity(meta: dict[str, Any], expected: EntryKey) -> None:
    if set(meta) != _META_FIELDS:
        raise _refuse(expected, f"unexpected meta fields {sorted(set(meta) ^ _META_FIELDS)}")
    wanted: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": expected.kind,
        "esi": expected.esi,
        "input_hash": expected.input_hash,
        "dim": expected.dim,
        "rows": expected.rows,
        "dtype": expected.dtype,
        "unit": None if expected.unit is None else expected.unit.model_dump(),
    }
    for field, value in wanted.items():
        if meta[field] != value or type(meta[field]) is not type(value):
            raise _refuse(expected, f"{field} does not match")


def _vectors(payload: bytes, expected: EntryKey) -> np.ndarray:
    dtype = _DTYPES[expected.dtype]
    if len(payload) != expected.rows * expected.dim * dtype.itemsize:
        raise _refuse(expected, f"payload is {len(payload)} bytes")
    vectors = np.frombuffer(payload, dtype=dtype).reshape(expected.rows, expected.dim)
    if expected.unit is not None and not np.isfinite(vectors).all():
        raise _refuse(expected, "a vector value is not finite")
    if expected.unit is None and int(vectors.min()) < _INT8_MIN_WRITTEN:
        raise _refuse(expected, "int8 value -128 is never written by quantize_int8")
    return vectors.copy()


def _scale(meta: dict[str, Any], expected: EntryKey) -> float | None:
    scale = meta["scale"]
    if expected.unit is not None:
        if scale is not None:
            raise _refuse(expected, "a unit entry carries a scale")
        return None
    if type(scale) is not float or not math.isfinite(scale) or scale <= 0:
        raise _refuse(expected, "scale is not a finite positive float")
    return scale


def _created_at(meta: dict[str, Any], expected: EntryKey, policy: ReadPolicy) -> datetime:
    raw = meta["created_at"]
    try:
        created = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        created = None
    if created is None or created.tzinfo is None:
        raise _refuse(expected, "created_at is not a timezone-aware timestamp")
    now = policy.clock()
    if created > now + _CLOCK_SKEW:
        raise _refuse(expected, "created_at is in the future")
    if now - created > policy.max_age:
        raise EmbeddingCacheExpiredError(
            f"embedding cache entry refused ({expected.label()}): expired, "
            f"older than {policy.max_age.days} days; run `lovspor embedding-cache prune`",
        )
    return created
