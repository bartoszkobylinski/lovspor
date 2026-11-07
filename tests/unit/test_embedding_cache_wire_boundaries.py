"""Cache design §5.3: exact payload sizes and retention by elapsed time."""

from datetime import timedelta, timezone

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
