"""Operator commands over the local embedding cache (#802, design §10).

None of them calls a provider. Output is counts and key prefixes only: no
vector values, no document text, no credentials.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.embeddings.cache import CACHE_DIR_VARIABLE
from lovspor.embeddings.cache_admin import invalidate, prune, status, verify
from lovspor.embeddings.cache_format import CachedVectors, EntryKey, ReadPolicy, UnitKey
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.embeddings.inputs import EmbeddingInput, hash_embedding_inputs
from lovspor.errors import EmbeddingCacheError
from tests.unit.cli_output import said

ESI = "0123456789abcdef0123456789abcdef"
OTHER_ESI = "fedcba9876543210fedcba9876543210"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
POLICY = ReadPolicy(max_age=timedelta(days=30), clock=lambda: NOW)


def _hash(text: str) -> str:
    return hash_embedding_inputs([EmbeddingInput("§ 1", text)])


def _doc(text: str, esi: str = ESI, created_at: datetime = NOW) -> CachedVectors:
    key = EntryKey(esi=esi, input_hash=_hash(text), dim=4, rows=1)
    vectors = np.array([[1, 2, 3, 4]], dtype=np.int8)
    return CachedVectors(key=key, vectors=vectors, scale=0.5, created_at=created_at)


def _unit(text: str, index: int = 0) -> CachedVectors:
    unit = UnitKey(index=index, count=2, input_hash=_hash(f"{text}-{index}"))
    key = EntryKey(esi=ESI, input_hash=_hash(text), dim=4, rows=1, unit=unit)
    vectors = np.array([[0.5, 0.5, 0.5, 0.5]], dtype=np.float32)
    return CachedVectors(key=key, vectors=vectors, scale=None, created_at=NOW)


@pytest.fixture
def store(tmp_path: Path) -> LocalEntryStore:
    return LocalEntryStore.open(tmp_path / "cache", (), POLICY)


def test_status_counts_entries_per_space(store: LocalEntryStore) -> None:
    store.put(_doc("a"))
    store.put(_doc("b"))
    store.put(_unit("c"))
    store.put(_doc("a", esi=OTHER_ESI))
    report = status(store)
    assert report.spaces[ESI].documents == 2
    assert report.spaces[ESI].units == 1
    assert report.spaces[OTHER_ESI].documents == 1
    assert report.spaces[ESI].bytes > 0


def test_status_of_an_empty_store(store: LocalEntryStore) -> None:
    assert status(store).spaces == {}


def test_verify_passes_a_clean_store(store: LocalEntryStore) -> None:
    store.put(_doc("a"))
    store.put(_unit("c"))
    report = verify(store)
    assert (report.valid, report.refused, report.expired) == (2, [], 0)


def test_verify_names_a_corrupt_entry(store: LocalEntryStore) -> None:
    store.put(_doc("a"))
    entry = next(store.root.rglob("*.lsec"))
    data = bytearray(entry.read_bytes())
    data[-40] ^= 1
    entry.write_bytes(bytes(data))
    report = verify(store)
    assert report.valid == 0
    assert len(report.refused) == 1
    assert _hash("a")[:12] in report.refused[0]


def test_verify_catches_an_entry_stored_under_another_name(store: LocalEntryStore) -> None:
    store.put(_doc("a"))
    entry = next(store.root.rglob("*.lsec"))
    entry.rename(entry.with_name(f"{_hash('b')}.lsec"))
    assert len(verify(store).refused) == 1


def test_verify_counts_expired_entries(store: LocalEntryStore) -> None:
    store.put(_doc("old", created_at=NOW - timedelta(days=40)))
    assert verify(store).expired == 1


def test_verify_refuses_a_misnamed_file(store: LocalEntryStore) -> None:
    store.put(_doc("a"))
    directory = next(store.root.rglob("*.lsec")).parent
    stray = directory / "not-a-key.lsec"
    stray.write_bytes(b"x")
    stray.chmod(0o600)
    assert len(verify(store).refused) == 1


def test_prune_removes_expired_superseded_units_and_old_temps(store: LocalEntryStore) -> None:
    store.put(_doc("old", created_at=NOW - timedelta(days=40)))
    store.put(_doc("done"))
    store.put(_unit("done"))
    store.put(_unit("pending"))
    temp = next(store.root.rglob("*.lsec")).parent / ".x.lsec.tmp.1.ab"
    temp.write_bytes(b"")
    report = prune(store, temp_grace=timedelta(0))
    assert (report.expired, report.superseded_units, report.temporary) == (1, 1, 1)
    assert store.get(_doc("done").key) is not None
    assert store.get(_unit("pending").key) is not None
    assert not temp.exists()


def test_prune_keeps_a_fresh_temporary_file(store: LocalEntryStore) -> None:
    store.put(_doc("a"))
    temp = next(store.root.rglob("*.lsec")).parent / ".x.lsec.tmp.1.ab"
    temp.write_bytes(b"")
    assert prune(store).temporary == 0
    assert temp.exists()


def test_prune_never_removes_a_corrupt_entry(store: LocalEntryStore) -> None:
    store.put(_doc("a"))
    entry = next(store.root.rglob("*.lsec"))
    entry.write_bytes(b"garbage")
    report = prune(store)
    assert entry.exists()
    assert len(report.refused) == 1


def test_invalidate_one_key(store: LocalEntryStore) -> None:
    store.put(_doc("a"))
    store.put(_doc("b"))
    assert invalidate(store, ESI, _hash("a")) == 1
    assert store.get(_doc("a").key) is None
    assert store.get(_doc("b").key) is not None


def test_invalidate_one_key_also_drops_its_units(store: LocalEntryStore) -> None:
    store.put(_unit("a", 0))
    store.put(_unit("a", 1))
    assert invalidate(store, ESI, _hash("a")) == 2
    assert store.get(_unit("a", 0).key) is None


def test_invalidate_a_whole_space(store: LocalEntryStore) -> None:
    store.put(_doc("a"))
    store.put(_unit("b"))
    store.put(_doc("a", esi=OTHER_ESI))
    assert invalidate(store, ESI, None) == 2
    assert store.get(_doc("a", esi=OTHER_ESI).key) is not None


@pytest.mark.parametrize(("esi", "input_hash"), [("x" * 32, None), (ESI, "../etc")])
def test_invalidate_refuses_a_malformed_key(
    store: LocalEntryStore, esi: str, input_hash: str | None
) -> None:
    with pytest.raises(EmbeddingCacheError):
        invalidate(store, esi, input_hash)


def test_cli_status_needs_the_cache_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CACHE_DIR_VARIABLE, raising=False)
    result = CliRunner().invoke(app, ["ops", "embedding-cache", "status"])
    assert result.exit_code == 2
    assert CACHE_DIR_VARIABLE in result.output


def test_cli_verify_exits_1_on_a_refused_entry(
    store: LocalEntryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.put(_doc("a"))
    next(store.root.rglob("*.lsec")).write_bytes(b"garbage")
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    result = CliRunner().invoke(app, ["ops", "embedding-cache", "verify"])
    assert result.exit_code == 1
    assert "refused=1" in result.output


def test_cli_status_prints_counts_only(
    store: LocalEntryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.put(_doc("a"))
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    result = CliRunner().invoke(app, ["ops", "embedding-cache", "status"])
    assert result.exit_code == 0
    assert "documents=1" in result.output
    assert str(store.root) not in result.output


def test_cli_invalidate_and_prune_run(
    store: LocalEntryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.put(_doc("a"))
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    runner = CliRunner()
    pruned = runner.invoke(app, ["ops", "embedding-cache", "prune"])
    assert pruned.exit_code == 0
    gone = runner.invoke(app, ["ops", "embedding-cache", "invalidate", "--esi", ESI])
    assert gone.exit_code == 0
    assert "removed=1" in gone.output


def test_cli_verify_fails_on_an_expired_entry_and_names_it(
    store: LocalEntryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §5.3: an expired entry fails validation, so verify lists it and exits 1."""
    store.put(_doc("expired", created_at=datetime(2000, 1, 1, tzinfo=UTC)))
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    result = CliRunner().invoke(app, ["ops", "embedding-cache", "verify"])
    assert result.exit_code == 1
    assert "expired=1" in result.output
    assert _hash("expired")[:12] in result.output


def test_cli_invalidate_input_hash_removes_only_that_documents_entries(
    store: LocalEntryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §10: --input-hash selects one document, including its units."""
    entries = [_doc("a"), _unit("a", 0), _unit("a", 1), _doc("b"), _doc("a", OTHER_ESI)]
    for entry in entries:
        store.put(entry)
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    result = CliRunner().invoke(
        app,
        ["ops", "embedding-cache", "invalidate", "--esi", ESI, "--input-hash", _hash("a")],
    )
    assert result.exit_code == 0
    assert said("removed=3", result.output)
    for entry in entries[:3]:
        assert store.get(entry.key) is None
    for entry in entries[3:]:
        restored = store.get(entry.key)
        assert restored is not None
        np.testing.assert_array_equal(restored.vectors, entry.vectors)


@pytest.mark.parametrize(
    "arguments", [["--esi", "x" * 32], ["--esi", ESI, "--input-hash", "../etc"]]
)
def test_cli_invalidate_refuses_malformed_keys_without_deleting_entries(
    store: LocalEntryStore, monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    store.put(_doc("a"))
    before = {path: path.read_bytes() for path in store.root.rglob("*.lsec")}
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    result = CliRunner().invoke(app, ["ops", "embedding-cache", "invalidate", *arguments])
    assert result.exit_code == 1
    assert said("invalidate needs a 32-hex ESI and, optionally, a 64-hex hash", result.output)
    assert {path: path.read_bytes() for path in store.root.rglob("*.lsec")} == before


def test_cli_invalidate_requires_esi_without_deleting_entries(
    store: LocalEntryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.put(_doc("a"))
    before = {path: path.read_bytes() for path in store.root.rglob("*.lsec")}
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    result = CliRunner().invoke(app, ["ops", "embedding-cache", "invalidate"])
    assert result.exit_code == 2
    assert said("Missing option '--esi'", result.output)
    assert {path: path.read_bytes() for path in store.root.rglob("*.lsec")} == before


def test_cli_verify_reports_mixed_entries_without_modifying_evidence(
    store: LocalEntryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design §10: verify validates every entry and lists every failed key."""
    fresh = datetime.now(UTC)
    entries = [
        _doc("valid", created_at=fresh),
        _doc("expired", created_at=datetime(2000, 1, 1, tzinfo=UTC)),
        _doc("corrupt-one", created_at=fresh),
        _doc("corrupt-two", esi=OTHER_ESI, created_at=fresh),
    ]
    for entry in entries:
        store.put(entry)
    for entry in entries[2:]:
        path = next(store.root.rglob(f"{entry.key.input_hash}.lsec"))
        data = bytearray(path.read_bytes())
        data[-40] ^= 1
        path.write_bytes(data)
    before = {path: path.read_bytes() for path in store.root.rglob("*.lsec")}
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(store.root))
    monkeypatch.setenv("LOVSPOR_EMBEDDING_CACHE_MAX_AGE_DAYS", "30")

    result = CliRunner().invoke(app, ["ops", "embedding-cache", "verify"])

    assert result.exit_code == 1
    assert "valid=1 expired=1 refused=2" in result.stdout
    for entry in entries[1:]:
        assert entry.key.input_hash[:12] in result.stderr
    assert entries[0].key.input_hash[:12] not in result.stderr
    assert str(store.root) not in result.output
    assert {path: path.read_bytes() for path in store.root.rglob("*.lsec")} == before
