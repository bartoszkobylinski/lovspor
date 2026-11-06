"""Observable diagnostics and boundary cases from the cache mutation artifact."""

import hashlib
import os
import struct
from datetime import timedelta
from pathlib import Path

import pytest
import typer

from lovspor.embeddings import cache_admin, cache_cli
from lovspor.embeddings.cache import CACHE_DIR_VARIABLE
from lovspor.embeddings.cache_format import decode_entry, describe_entry
from lovspor.embeddings.cache_store import LocalEntryStore, read_entry_bytes
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheStorageError
from tests.unit.test_embedding_cache_admin import ESI, NOW, POLICY, _doc, _unit


@pytest.fixture
def store(tmp_path: Path) -> LocalEntryStore:
    return LocalEntryStore.open(tmp_path / "cache", (), POLICY)


def _frame(meta: bytes, length: int | None = None) -> bytes:
    body = b"LSEC" + struct.pack(">I", len(meta) if length is None else length) + meta
    return body + hashlib.sha256(body).digest()


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", "torn entry, 0 bytes"),
        (b"x" * 40, "bad magic"),
        (b"LSEC" + b"x" * 36, "digest mismatch (torn or modified)"),
        (_frame(b"", 1), "meta length past the end"),
        (_frame(b""), "meta is not JSON"),
        (_frame(b"?"), "meta is not JSON"),
        (_frame(b"[]"), "meta is not an object"),
    ],
)
def test_decode_diagnostic_names_key_and_exact_reason(data: bytes, reason: str) -> None:
    key = _doc("a").key
    with pytest.raises(EmbeddingCacheError) as caught:
        decode_entry(data, key, POLICY)
    assert str(caught.value) == f"embedding cache entry refused ({key.label()}): {reason}"


def test_describe_invalid_key_names_file() -> None:
    with pytest.raises(EmbeddingCacheError) as caught:
        describe_entry(_frame(b"{}"), "broken.lsec")
    assert str(caught.value) == (
        "embedding cache entry refused (broken.lsec): meta does not describe a valid key"
    )


def test_read_errors_name_entry(tmp_path: Path) -> None:
    path = tmp_path / "entry.lsec"
    path.write_bytes(b"x")
    path.chmod(0o644)
    with pytest.raises(EmbeddingCacheError) as caught:
        read_entry_bytes(path)
    assert str(caught.value) == (
        "embedding cache refuses entry.lsec: permission must be owner-only "
        "and owned by the running user"
    )
    # ENAMETOOLONG is independent of whether the test process is privileged.
    path = tmp_path / ("a" * 300)
    with pytest.raises(EmbeddingCacheStorageError) as storage:
        read_entry_bytes(path)
    assert str(storage.value) == f"cannot read {path.name}: {storage.value.__cause__.strerror}"


@pytest.mark.parametrize("configured", [False, True])
def test_open_refusal_uses_stderr(monkeypatch, capsys, configured) -> None:
    if configured:
        monkeypatch.setenv(CACHE_DIR_VARIABLE, "relative")
        message = "embedding cache root must be an absolute path"
    else:
        monkeypatch.delenv(CACHE_DIR_VARIABLE, raising=False)
        message = f"{CACHE_DIR_VARIABLE} is not set; the embedding cache is off."
    with pytest.raises(typer.Exit) as caught:
        cache_cli.open_configured_store()
    assert caught.value.exit_code == (1 if configured else 2)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == message + "\n"


def test_clean_verify_succeeds(store, monkeypatch, capsys) -> None:
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    assert cache_cli.verify_impl() == 0
    assert capsys.readouterr().out == "valid=0 expired=0 refused=0\n"


@pytest.mark.parametrize("command", ["verify_impl", "prune_impl"])
def test_corruption_diagnostics_go_to_stderr(store, monkeypatch, capsys, command) -> None:
    store.put(_doc("a"))
    path = next(store.root.rglob("*.lsec"))
    path.write_bytes(b"bad")
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    assert getattr(cache_cli, command)() == (1 if command == "verify_impl" else 0)
    output = capsys.readouterr()
    assert output.err == f"embedding cache entry refused ({path.name}): torn entry, 3 bytes\n"
    assert output.out == (
        "valid=0 expired=0 refused=1\n"
        if command == "verify_impl"
        else "expired=0 superseded_units=0 temporary=0 refused=1\n"
    )
    assert path.read_bytes() == b"bad"


def test_invalidate_error_goes_to_stderr(store, monkeypatch, capsys) -> None:
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    assert cache_cli.invalidate_impl("bad", None) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "invalidate needs a 32-hex ESI and, optionally, a 64-hex hash\n"


def test_status_totals_and_ignores_unrelated_files(store) -> None:
    for entry in [_doc("a"), _unit("b", 0), _unit("b", 1)]:
        store.put(entry)
    paths = list(store.root.rglob("*.lsec"))
    directory = paths[0].parent
    (directory / "notes.txt").write_bytes(b"ignored")
    (directory / ".staged.tmp.1.lsec").write_bytes(b"staged")
    report = cache_admin.status(store)
    total = sum(p.stat().st_size for p in paths)
    assert report.lines() == [f"esi={ESI[:8]} documents=1 units=2 bytes={total}", "temporary=1"]


def test_multiple_expired_entries_are_counted(store) -> None:
    for text in ["a", "b"]:
        store.put(_doc(text, created_at=NOW - timedelta(days=40)))
    assert cache_admin.verify(store).expired == 2
    assert cache_admin.prune(store).expired == 2
    assert not list(store.root.rglob("*.lsec"))


def test_misnamed_unit_retains_impossible_identity_and_diagnostic(store) -> None:
    entry = _unit("a")
    store.put(entry)
    path = next(store.root.rglob("*.lsec"))
    path = path.rename(path.with_name("invalid.lsec"))
    records = list(cache_admin.iter_entries(store))
    assert len(records) == 1
    assert records[0].unit == (-1, -1, "")
    report = cache_admin.verify(store)
    assert report.refused == [
        f"embedding cache entry refused ({entry.key.label()}): stored under another key"
    ]
    assert path.exists()


def test_temporary_at_cutoff_is_removed(store, monkeypatch) -> None:
    store.put(_doc("a"))
    path = next(store.root.rglob("*.lsec")).parent / ".entry.tmp.1"
    path.write_bytes(b"x")
    os.utime(path, (1000, 1000))
    monkeypatch.setattr("time.time", lambda: 4600)
    assert cache_admin.prune(store).temporary == 1
    assert not path.exists()


def test_remove_already_absent_file_is_idempotent(tmp_path) -> None:
    cache_admin._remove(tmp_path / "absent.lsec")
    assert list(tmp_path.iterdir()) == []


def test_superseded_units_removed_independent_of_vector_dimensions(store) -> None:
    store.put(_doc("finished"))
    store.put(_unit("finished", 0))
    store.put(_unit("finished", 1))
    store.put(_unit("pending", 0))
    assert cache_admin.prune(store).superseded_units == 2
    assert store.get(_doc("finished").key) is not None
    assert store.get(_unit("pending", 0).key) is not None
    assert store.get(_unit("finished", 0).key) is None
    assert store.get(_unit("finished", 1).key) is None
