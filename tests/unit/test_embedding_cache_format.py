"""Entry format of the durable embedding cache (#802, design §4 and §5.3).

Every read is validated against the identity the caller derived from the
current document and embedder. Any failure is a typed refusal, never a miss:
a refused entry must stop the run instead of being paid over.
"""

import hashlib
import json
import struct
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from lovspor.embeddings.cache_format import (
    CachedVectors,
    EntryKey,
    ReadPolicy,
    UnitKey,
    decode_entry,
    encode_entry,
)
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheExpiredError, LovsporError

ESI = "0123456789abcdef0123456789abcdef"
OTHER_ESI = "fedcba9876543210fedcba9876543210"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
INPUT_HASH = hash_embedding_inputs([EmbeddingInput("§ 1", "tekst")])
OTHER_HASH = hash_embedding_inputs([EmbeddingInput("§ 2", "tekst")])


def _policy(now: datetime = NOW, days: int = 30) -> ReadPolicy:
    return ReadPolicy(max_age=timedelta(days=days), clock=lambda: now)


def _doc_key(rows: int = 2, dim: int = 4) -> EntryKey:
    return EntryKey(esi=ESI, input_hash=INPUT_HASH, dim=dim, rows=rows)


def _unit_key(index: int = 0, count: int = 2, rows: int = 2) -> EntryKey:
    unit = UnitKey(index=index, count=count, input_hash=OTHER_HASH)
    return EntryKey(esi=ESI, input_hash=INPUT_HASH, dim=4, rows=rows, unit=unit)


def _doc_entry(created_at: datetime = NOW) -> CachedVectors:
    vectors = np.array([[1, -127, 0, 5], [127, 3, -2, 0]], dtype=np.int8)
    return CachedVectors(key=_doc_key(), vectors=vectors, scale=0.0078, created_at=created_at)


def _unit_entry() -> CachedVectors:
    vectors = np.array([[0.5, -0.5, 0.5, -0.5], [1.0, 0.0, 0.0, 0.0]], dtype=np.float32)
    return CachedVectors(key=_unit_key(), vectors=vectors, scale=None, created_at=NOW)


def _reseal(body: bytes) -> bytes:
    """Recompute the trailing digest, so a test reaches the check behind it."""
    return body + hashlib.sha256(body).digest()


def _with_meta(data: bytes, **changes: object) -> bytes:
    """Rewrite meta fields and reseal: a forged entry with a valid digest."""
    (length,) = struct.unpack(">I", data[4:8])
    meta = json.loads(data[8 : 8 + length])
    meta.update(changes)
    raw = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()
    return _reseal(data[:4] + struct.pack(">I", len(raw)) + raw + data[8 + length : -32])


def test_document_entry_round_trips_byte_for_byte() -> None:
    entry = _doc_entry()
    decoded = decode_entry(encode_entry(entry), _doc_key(), _policy())
    assert decoded.vectors.dtype == np.int8
    assert np.array_equal(decoded.vectors, entry.vectors)
    assert decoded.scale == entry.scale
    assert decoded.created_at == NOW
    assert encode_entry(decoded) == encode_entry(entry)


def test_unit_entry_round_trips_float32_exactly() -> None:
    entry = _unit_entry()
    decoded = decode_entry(encode_entry(entry), _unit_key(), _policy())
    assert decoded.vectors.dtype == np.float32
    assert decoded.vectors.tobytes() == entry.vectors.tobytes()
    assert decoded.scale is None


def test_encoding_is_deterministic() -> None:
    assert encode_entry(_doc_entry()) == encode_entry(_doc_entry())


def test_errors_are_lovspor_errors() -> None:
    assert issubclass(EmbeddingCacheError, LovsporError)
    assert issubclass(EmbeddingCacheExpiredError, EmbeddingCacheError)


@pytest.mark.parametrize("cut", [0, 7, 40, -1, -33])
def test_torn_entry_is_refused(cut: int) -> None:
    data = encode_entry(_doc_entry())
    with pytest.raises(EmbeddingCacheError):
        decode_entry(data[:cut], _doc_key(), _policy())


def test_flipped_payload_byte_is_refused() -> None:
    data = bytearray(encode_entry(_doc_entry()))
    data[-33] ^= 0x01
    with pytest.raises(EmbeddingCacheError, match="digest"):
        decode_entry(bytes(data), _doc_key(), _policy())


def test_wrong_magic_is_refused() -> None:
    data = encode_entry(_doc_entry())
    with pytest.raises(EmbeddingCacheError, match="magic"):
        decode_entry(_reseal(b"LSPE" + data[4:-32]), _doc_key(), _policy())


def test_meta_length_past_the_end_is_refused() -> None:
    data = encode_entry(_doc_entry())
    forged = _reseal(data[:4] + struct.pack(">I", 10**6) + data[8:-32])
    with pytest.raises(EmbeddingCacheError):
        decode_entry(forged, _doc_key(), _policy())


def test_meta_that_is_not_json_is_refused() -> None:
    data = encode_entry(_doc_entry())
    (length,) = struct.unpack(">I", data[4:8])
    forged = _reseal(data[:8] + b"\xff" * length + data[8 + length : -32])
    with pytest.raises(EmbeddingCacheError, match="meta"):
        decode_entry(forged, _doc_key(), _policy())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", 2),
        ("kind", "unit"),
        ("esi", OTHER_ESI),
        ("input_hash", OTHER_HASH),
        ("dim", 8),
        ("rows", 3),
        ("dtype", "float32"),
    ],
)
def test_swapped_identity_field_is_refused(field: str, value: object) -> None:
    forged = _with_meta(encode_entry(_doc_entry()), **{field: value})
    with pytest.raises(EmbeddingCacheError, match=field):
        decode_entry(forged, _doc_key(), _policy())


def test_entry_read_under_another_key_is_refused() -> None:
    """A file renamed under another document's key does not validate."""
    data = encode_entry(_doc_entry())
    other = EntryKey(esi=ESI, input_hash=OTHER_HASH, dim=4, rows=2)
    with pytest.raises(EmbeddingCacheError, match="input_hash"):
        decode_entry(data, other, _policy())


def test_entry_of_another_space_is_refused() -> None:
    data = encode_entry(_doc_entry())
    other = EntryKey(esi=OTHER_ESI, input_hash=INPUT_HASH, dim=4, rows=2)
    with pytest.raises(EmbeddingCacheError, match="esi"):
        decode_entry(data, other, _policy())


def test_unknown_meta_field_is_refused() -> None:
    forged = _with_meta(encode_entry(_doc_entry()), extra="x")
    with pytest.raises(EmbeddingCacheError, match="fields"):
        decode_entry(forged, _doc_key(), _policy())


@pytest.mark.parametrize(
    "unit",
    [
        {"index": 1, "count": 2, "input_hash": OTHER_HASH},
        {"index": 0, "count": 3, "input_hash": OTHER_HASH},
        {"index": 0, "count": 2, "input_hash": INPUT_HASH},
    ],
)
def test_unit_entry_of_another_unit_is_refused(unit: dict[str, object]) -> None:
    forged = _with_meta(encode_entry(_unit_entry()), unit=unit)
    with pytest.raises(EmbeddingCacheError, match="unit"):
        decode_entry(forged, _unit_key(), _policy())


def test_unit_entry_asked_for_as_document_is_refused() -> None:
    data = encode_entry(_unit_entry())
    with pytest.raises(EmbeddingCacheError):
        decode_entry(data, _doc_key(), _policy())


def test_short_payload_is_refused_even_with_a_valid_digest() -> None:
    data = encode_entry(_doc_entry())
    with pytest.raises(EmbeddingCacheError, match="payload"):
        decode_entry(_reseal(data[:-33]), _doc_key(), _policy())


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_non_finite_unit_vector_is_refused(bad: float) -> None:
    data = encode_entry(_unit_entry())
    payload_end = len(data) - 32
    forged = _reseal(data[: payload_end - 4] + struct.pack("<f", bad))
    with pytest.raises(EmbeddingCacheError, match="finite"):
        decode_entry(forged, _unit_key(), _policy())


@pytest.mark.parametrize("scale", [0.0, -1.0, float("nan"), float("inf"), None])
def test_bad_document_scale_is_refused(scale: float | None) -> None:
    forged = _with_meta(encode_entry(_doc_entry()), scale=scale)
    with pytest.raises(EmbeddingCacheError, match="scale"):
        decode_entry(forged, _doc_key(), _policy())


def test_document_row_at_minus_128_is_refused() -> None:
    """``quantize_int8`` clips to ±127; -128 is not something it writes."""
    data = encode_entry(_doc_entry())
    payload_end = len(data) - 32
    forged = _reseal(data[: payload_end - 1] + b"\x80")
    with pytest.raises(EmbeddingCacheError, match="int8"):
        decode_entry(forged, _doc_key(), _policy())


def test_expired_entry_is_refused_not_ignored() -> None:
    data = encode_entry(_doc_entry(created_at=NOW - timedelta(days=31)))
    with pytest.raises(EmbeddingCacheExpiredError):
        decode_entry(data, _doc_key(), _policy())


def test_entry_inside_retention_is_served() -> None:
    data = encode_entry(_doc_entry(created_at=NOW - timedelta(days=29)))
    assert decode_entry(data, _doc_key(), _policy()).created_at == NOW - timedelta(days=29)


def test_entry_from_the_future_is_refused() -> None:
    data = encode_entry(_doc_entry(created_at=NOW + timedelta(hours=1)))
    with pytest.raises(EmbeddingCacheError, match="future"):
        decode_entry(data, _doc_key(), _policy())


def test_small_clock_skew_is_tolerated() -> None:
    data = encode_entry(_doc_entry(created_at=NOW + timedelta(minutes=4)))
    assert decode_entry(data, _doc_key(), _policy()).scale == pytest.approx(0.0078)


def test_unparseable_timestamp_is_refused() -> None:
    forged = _with_meta(encode_entry(_doc_entry()), created_at="yesterday")
    with pytest.raises(EmbeddingCacheError, match="created_at"):
        decode_entry(forged, _doc_key(), _policy())


def test_naive_timestamp_is_refused() -> None:
    forged = _with_meta(encode_entry(_doc_entry()), created_at="2026-11-06T12:00:00")
    with pytest.raises(EmbeddingCacheError, match="created_at"):
        decode_entry(forged, _doc_key(), _policy())


@pytest.mark.parametrize(
    "changes",
    [
        {"esi": "UNKNOWN"},
        {"esi": "unknown"},
        {"input_hash": "../../etc/passwd"},
        {"dim": 0},
        {"rows": 0},
    ],
)
def test_malformed_key_cannot_be_built(changes: dict[str, object]) -> None:
    fields: dict[str, object] = {"esi": ESI, "input_hash": INPUT_HASH, "dim": 4, "rows": 1}
    fields.update(changes)
    with pytest.raises(ValueError):
        EntryKey.model_validate(fields)


@pytest.mark.parametrize(
    "unit",
    [
        {"index": 2, "count": 2, "input_hash": OTHER_HASH},
        {"index": -1, "count": 2, "input_hash": OTHER_HASH},
        {"index": 0, "count": 1, "input_hash": OTHER_HASH},
        {"index": 0, "count": 2, "input_hash": "nothex"},
    ],
)
def test_malformed_unit_key_cannot_be_built(unit: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        UnitKey.model_validate(unit)


def test_vectors_must_match_their_key_to_be_encoded() -> None:
    wrong_shape = CachedVectors(
        key=_doc_key(rows=3),
        vectors=np.zeros((2, 4), dtype=np.int8),
        scale=1.0,
        created_at=NOW,
    )
    with pytest.raises(ValueError, match="shape"):
        encode_entry(wrong_shape)


def test_document_entry_needs_int8_and_a_scale_to_be_encoded() -> None:
    floats = CachedVectors(
        key=_doc_key(), vectors=np.zeros((2, 4), dtype=np.float32), scale=1.0, created_at=NOW
    )
    with pytest.raises(ValueError, match="int8"):
        encode_entry(floats)


def test_refusal_message_carries_no_vector_values() -> None:
    forged = _with_meta(encode_entry(_doc_entry()), rows=3)
    with pytest.raises(EmbeddingCacheError) as caught:
        decode_entry(forged, _doc_key(), _policy())
    assert "-127" not in str(caught.value)
    assert INPUT_HASH[:12] in str(caught.value)


@pytest.mark.parametrize(
    ("created_at", "accepted"),
    [
        (NOW - timedelta(days=30), True),
        (NOW - timedelta(days=30, seconds=1), False),
        (NOW + timedelta(minutes=5), True),
        (NOW + timedelta(minutes=5, seconds=1), False),
    ],
)
def test_retention_and_clock_skew_boundaries(created_at: datetime, accepted: bool) -> None:
    """Design §5.3 permits five minutes skew and refuses ages over retention."""
    data = encode_entry(_doc_entry(created_at=created_at))
    if accepted:
        assert decode_entry(data, _doc_key(), _policy()).created_at == created_at
    else:
        error = EmbeddingCacheExpiredError if created_at < NOW else EmbeddingCacheError
        with pytest.raises(error):
            decode_entry(data, _doc_key(), _policy())


@pytest.mark.parametrize(("field", "value"), [("index", False), ("index", 0.0), ("count", 2.0)])
@pytest.mark.codex_proposal
@pytest.mark.xfail(
    strict=True, reason="Proposal: require exact JSON integer types in unit identity"
)
def test_unit_identity_requires_integer_metadata(field: str, value: object) -> None:
    """Proposal: extend the top-level type check to nested unit identity fields."""
    unit = _unit_key().unit
    assert unit is not None
    metadata = unit.model_dump()
    metadata[field] = value
    forged = _with_meta(encode_entry(_unit_entry()), unit=metadata)
    with pytest.raises(EmbeddingCacheError, match="unit"):
        decode_entry(forged, _unit_key(), _policy())
