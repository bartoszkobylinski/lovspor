"""Observable cache format, refusal diagnostics and filesystem contracts."""

import json
import struct
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from lovspor.embeddings.cache_format import decode_entry, encode_entry
from lovspor.embeddings.cache_store import entry_location, fsync_dir
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheStorageError
from tests.unit.test_embedding_cache_format import (
    ESI,
    INPUT_HASH,
    NOW,
    _doc_entry,
    _doc_key,
    _policy,
    _reseal,
    _unit_entry,
    _unit_key,
    _with_meta,
)
from tests.unit.test_embedding_cache_store import _entry, _open, _unit


def _message(data: bytes, reason: str, unit: bool = False) -> None:
    key = _unit_key() if unit else _doc_key()
    with pytest.raises(EmbeddingCacheError) as caught:
        decode_entry(data, key, _policy())
    label = f"{'unit' if unit else 'document'} esi={ESI[:8]} input_hash={INPUT_HASH[:12]}"
    if unit:
        label += " unit 1/2"
    assert str(caught.value) == f"embedding cache entry refused ({label}): {reason}"


def test_labels_identify_document_and_unit() -> None:
    assert _doc_key().label() == f"document esi={ESI[:8]} input_hash={INPUT_HASH[:12]}"
    assert _unit_key().label() == f"unit esi={ESI[:8]} input_hash={INPUT_HASH[:12]} unit 1/2"


def test_wire_metadata_is_canonical() -> None:
    data = encode_entry(_doc_entry())
    length = struct.unpack(">I", data[4:8])[0]
    raw = data[8 : 8 + length]
    meta = json.loads(raw)
    assert raw == json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_wire_timestamp_is_utc_in_other_local_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as env:
        env.setenv("TZ", "EST5EDT")
        time.tzset()
        try:
            data = encode_entry(_doc_entry())
            length = struct.unpack(">I", data[4:8])[0]
            assert json.loads(data[8 : 8 + length])["created_at"] == "2026-11-06T12:00:00+00:00"
        finally:
            env.undo()
            time.tzset()


@pytest.mark.parametrize("unit", [False, True])
def test_encoding_scale_mismatch_explains_contract(unit: bool) -> None:
    entry = _unit_entry() if unit else _doc_entry()
    with pytest.raises(ValueError) as caught:
        encode_entry(replace(entry, scale=1.0 if unit else None))
    assert str(caught.value) == "a document entry has a scale and a unit entry has none"


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("torn", "torn entry, 0 bytes"),
        ("magic", "bad magic"),
        ("digest", "digest mismatch (torn or modified)"),
        ("length", "meta length past the end"),
        ("json", "meta is not JSON"),
        ("object", "meta is not an object"),
        ("empty_payload", "payload is 0 bytes"),
        ("fields", "unexpected meta fields ['extra']"),
        ("int8", "int8 value -128 is never written by quantize_int8"),
        ("scale", "scale is not a finite positive float"),
        ("timestamp", "created_at is not a timezone-aware timestamp"),
        ("future", "created_at is in the future"),
        ("expired", "expired, older than 30 days; run `lovspor embedding-cache prune`"),
    ],
)
def test_document_refusals_explain_corruption(kind: str, reason: str) -> None:
    data = encode_entry(_doc_entry())
    if kind == "torn":
        data = b""
    elif kind == "magic":
        data = _reseal(b"NOPE" + data[4:-32])
    elif kind == "digest":
        data = data[:-1] + bytes([data[-1] ^ 1])
    elif kind == "length":
        data = _reseal(data[:4] + struct.pack(">I", len(data) + 1) + data[8:-32])
    elif kind in ("json", "object"):
        raw = b"!" if kind == "json" else b"[]"
        data = _reseal(data[:4] + struct.pack(">I", len(raw)) + raw)
    elif kind == "empty_payload":
        length = struct.unpack(">I", data[4:8])[0]
        data = _reseal(data[: 8 + length])
    elif kind == "fields":
        data = _with_meta(data, extra=1)
    elif kind == "int8":
        data = _reseal(data[:-33] + b"\x80")
    elif kind == "scale":
        data = _with_meta(data, scale=0.0)
    elif kind == "timestamp":
        data = _with_meta(data, created_at="yesterday")
    elif kind == "future":
        data = _with_meta(data, created_at=(NOW + timedelta(hours=1)).isoformat())
    elif kind == "expired":
        data = _with_meta(data, created_at=(NOW - timedelta(days=31)).isoformat())
    _message(data, reason)


@pytest.mark.parametrize("timestamp", [None, 123, [], {}])
def test_non_string_timestamp_is_typed_refusal(timestamp: object) -> None:
    _message(
        _with_meta(encode_entry(_doc_entry()), created_at=timestamp),
        "created_at is not a timezone-aware timestamp",
    )


@pytest.mark.parametrize("kind", ["scale", "finite"])
def test_unit_refusals_explain_corruption(kind: str) -> None:
    data = encode_entry(_unit_entry())
    if kind == "scale":
        data = _with_meta(data, scale=1.0)
        reason = "a unit entry carries a scale"
    else:
        data = _reseal(data[:-36] + struct.pack("<f", float("nan")))
        reason = "a vector value is not finite"
    _message(data, reason, unit=True)


def test_minimal_envelope_reaches_json_validation() -> None:
    _message(_reseal(b"LSEC" + struct.pack(">I", 0)), "meta is not JSON")


def test_layout_matches_documented_paths(tmp_path: Path) -> None:
    store = _open(tmp_path / "cache")
    store.put(_entry())
    store.put(_unit())
    key = _entry().key
    unit = _unit().key.unit
    assert unit is not None
    doc = store.root / "schema-1" / key.esi / "documents" / f"{key.input_hash}.lsec"
    chunk = (
        store.root / "schema-1" / key.esi / "units" / key.input_hash / f"0-2-{unit.input_hash}.lsec"
    )
    assert doc.read_bytes() == encode_entry(_entry())
    assert chunk.read_bytes() == encode_entry(_unit())


def test_root_refusals_are_actionable(tmp_path: Path) -> None:
    with pytest.raises(EmbeddingCacheError) as caught:
        _open(Path("relative"))
    assert str(caught.value) == "embedding cache root must be an absolute path"
    with pytest.raises(EmbeddingCacheError) as caught:
        _open(tmp_path / "cache", (tmp_path,))
    assert str(caught.value) == (
        "embedding cache root is inside a repository it must stay out of; "
        "set LOVSPOR_EMBEDDING_CACHE_DIR outside the lovverk and engine checkouts"
    )
    with pytest.raises(EmbeddingCacheStorageError) as caught:
        _open(tmp_path / "missing" / "cache")
    assert str(caught.value) == f"cannot create embedding cache root: {caught.value.__cause__}"


def test_permission_refusal_names_directory_and_entry(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(EmbeddingCacheError) as caught:
        _open(root)
    assert str(caught.value) == (
        "embedding cache refuses cache: permission must be owner-only and owned by the running user"
    )
    root.chmod(0o700)
    store = _open(root)
    store.put(_entry())
    target = next(root.rglob("*.lsec"))
    target.chmod(0o644)
    with pytest.raises(EmbeddingCacheError) as caught:
        store.get(_entry().key)
    assert str(caught.value) == (
        f"embedding cache refuses {target.name}: permission must be owner-only "
        "and owned by the running user"
    )


def test_vanished_race_entry_is_diagnosed(tmp_path: Path) -> None:
    store = _open(tmp_path / "cache")
    with pytest.raises(EmbeddingCacheStorageError) as caught:
        store._existing(_entry().key)
    assert str(caught.value) == f"entry vanished after a write race ({_entry().key.label()})"


def test_unreadable_entry_reports_os_error(tmp_path: Path) -> None:
    store = _open(tmp_path / "cache")
    store.put(_entry())
    target = next(store.root.rglob("*.lsec"))
    target.chmod(0)
    try:
        with pytest.raises(EmbeddingCacheStorageError) as caught:
            store.get(_entry().key)
        assert str(caught.value) == f"cannot read {target.name}: {caught.value.__cause__.strerror}"
    finally:
        target.chmod(0o600)


def test_fsync_refuses_files_and_symlinks(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.touch()
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    for path in (file, link):
        with pytest.raises(OSError):
            fsync_dir(path)


def test_layout_components_are_case_sensitive() -> None:
    key = _doc_key()
    assert entry_location(key) == (("schema-1", key.esi, "documents"), f"{key.input_hash}.lsec")
    key = _unit_key()
    assert key.unit is not None
    assert entry_location(key) == (
        ("schema-1", key.esi, "units", key.input_hash),
        f"0-2-{key.unit.input_hash}.lsec",
    )


def test_publication_is_private_immutable_and_cleans_staging(tmp_path: Path) -> None:
    """Exercise publication without substituting any store implementation."""
    store = _open(tmp_path / "cache")
    store.put(_entry(value=5))
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*.lsec")}
    returned = store.put(_entry(value=9))
    assert returned.vectors[0, 0] == 5
    after = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*.lsec")}
    assert after == before
    assert len(after) == 1
    for path in store.root.rglob("*"):
        assert ".tmp." not in path.name
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
