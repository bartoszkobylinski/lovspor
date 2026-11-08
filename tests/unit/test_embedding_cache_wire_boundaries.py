"""Cache design §5.3: exact payload sizes and retention by elapsed time."""

import struct
from dataclasses import replace
from datetime import timedelta, timezone

import numpy as np
import pytest

from lovspor.embeddings.cache_format import decode_entry, encode_entry
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheExpiredError
from tests.unit.test_embedding_cache_format import (
    NOW,
    _doc_entry,
    _policy,
    _reseal,
    _unit_entry,
    _with_meta,
)


@pytest.mark.parametrize("unit", [False, True], ids=["document", "unit"])
@pytest.mark.parametrize("extra", [b"\x00", b"\x00\x00\x00\x00"])
def test_extra_payload_is_refused_even_with_valid_digest(unit: bool, extra: bytes) -> None:
    """§5.3 requires exactly rows × dim × itemsize bytes, without trailing data."""  # noqa: RUF002
    entry = _unit_entry() if unit else _doc_entry()
    data = encode_entry(entry)
    forged = _reseal(data[:-32] + extra)
    with pytest.raises(EmbeddingCacheError, match="payload"):
        decode_entry(forged, entry.key, _policy())


@pytest.mark.parametrize("offset_hours", [-12, 14])
@pytest.mark.parametrize("expired", [False, True], ids=["inside-retention", "expired"])
def test_retention_uses_timestamp_instant_across_offsets(offset_hours: int, expired: bool) -> None:
    """§5.3 accepts aware timestamps; their UTC offset must not change age."""
    entry = _doc_entry()
    age = timedelta(days=30, seconds=1 if expired else -1)
    created = (NOW - age).astimezone(timezone(timedelta(hours=offset_hours)))
    data = _with_meta(encode_entry(entry), created_at=created.isoformat())
    if expired:
        with pytest.raises(EmbeddingCacheExpiredError):
            decode_entry(data, entry.key, _policy())
    else:
        decoded = decode_entry(data, entry.key, _policy())
        assert decoded.created_at == NOW - age
        assert decoded.vectors.tobytes() == entry.vectors.tobytes()
        assert decoded.scale == entry.scale


@pytest.mark.parametrize("offset_hours", [-12, 14])
def test_future_timestamp_is_refused_independently_of_offset(offset_hours: int) -> None:
    """§5.3's five-minute skew allowance compares instants, not local clock times."""
    entry = _unit_entry()
    created = (NOW + timedelta(minutes=5, seconds=1)).astimezone(
        timezone(timedelta(hours=offset_hours))
    )
    data = _with_meta(encode_entry(entry), created_at=created.isoformat())
    with pytest.raises(EmbeddingCacheError, match="future"):
        decode_entry(data, entry.key, _policy())


def test_unit_checkpoint_preserves_finite_float32_bits_in_little_endian_row_order() -> None:
    """Design §4: unit payloads retain float32 values before document quantization."""
    # Positive/negative zero, subnormals, smallest normals and largest finite
    # values. Numeric equality alone would miss losing the sign of zero.
    payload = struct.pack(
        "<8I",
        0x00000000,
        0x80000000,
        0x00000001,
        0x80000001,
        0x00800000,
        0x80800000,
        0x7F7FFFFF,
        0xFF7FFFFF,
    )
    vectors = np.frombuffer(payload, dtype="<f4").reshape(2, 4)
    assert np.isfinite(vectors).all()
    entry = replace(_unit_entry(), vectors=vectors)

    wire = encode_entry(entry)
    (meta_length,) = struct.unpack(">I", wire[4:8])
    assert wire[8 + meta_length : -32] == payload

    decoded = decode_entry(wire, entry.key, _policy())
    assert decoded.vectors.shape == (2, 4)
    assert decoded.vectors.dtype == np.dtype("<f4")
    assert decoded.vectors.tobytes() == payload
    assert decoded.scale is None
    assert encode_entry(decoded) == wire
