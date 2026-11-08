"""``prune`` drops unit checkpoints only behind a validated document (#827 review).

Unit checkpoints are paid provider requests. ``prune`` may remove them only
once the document entry they would rebuild has itself passed the checks a
sync applies: safe file access, the identity its location names, digest,
retention, and the same dimension as the units. A document entry that merely
exists (corrupt, expired, under another key, of another dimension, or a
symlink) proves nothing, so every unit stays and a later run reuses them.
"""

import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache import CACHE_DIR_VARIABLE, read_policy_from_env, with_embedding_cache
from lovspor.embeddings.cache_admin import prune
from lovspor.embeddings.cache_format import CachedVectors, EntryKey
from lovspor.embeddings.cache_store import LocalEntryStore, entry_location
from lovspor.errors import EmbeddingCacheError
from tests.unit.test_embedding_cache_admin import NOW, POLICY, _doc, _hash, _unit
from tests.unit.test_embedding_cache_boundary import _Provider, _settings, _write

_TEXT = "paid"


@pytest.fixture
def store(tmp_path: Path) -> LocalEntryStore:
    return LocalEntryStore.open(tmp_path / "cache", (), POLICY)


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "embedding-cache"
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(root))
    return root


def _document_path(store: LocalEntryStore, key: EntryKey) -> Path:
    parts, name = entry_location(key)
    return store.root.joinpath(*parts, name)


def _put_units(store: LocalEntryStore) -> list[CachedVectors]:
    units = [_unit(_TEXT, 0), _unit(_TEXT, 1)]
    for unit in units:
        store.put(unit)
    return units


def _corrupt(store: LocalEntryStore) -> None:
    store.put(_doc(_TEXT))
    path = _document_path(store, _doc(_TEXT).key)
    data = bytearray(path.read_bytes())
    data[-40] ^= 0x01
    path.chmod(0o600)
    path.write_bytes(bytes(data))


def _wrong_dimension(store: LocalEntryStore) -> None:
    key = EntryKey(esi=_doc(_TEXT).key.esi, input_hash=_hash(_TEXT), dim=8, rows=1)
    vectors = np.ones((1, 8), dtype=np.int8)
    store.put(CachedVectors(key=key, vectors=vectors, scale=0.5, created_at=NOW))


def _swapped_identity(store: LocalEntryStore) -> None:
    store.put(_doc("another document"))
    other = _document_path(store, _doc("another document").key)
    _document_path(store, _doc(_TEXT).key).write_bytes(other.read_bytes())
    _document_path(store, _doc(_TEXT).key).chmod(0o600)


def _symlinked(store: LocalEntryStore) -> None:
    store.put(_doc("another document"))
    other = _document_path(store, _doc("another document").key)
    _document_path(store, _doc(_TEXT).key).symlink_to(other)


_INVALID_DOCUMENTS: dict[str, Callable[[LocalEntryStore], None]] = {
    "corrupt": _corrupt,
    "wrong-dimension": _wrong_dimension,
    "swapped-identity": _swapped_identity,
}


@pytest.mark.parametrize("plant", _INVALID_DOCUMENTS.values(), ids=_INVALID_DOCUMENTS.keys())
def test_an_invalid_document_keeps_every_unit_checkpoint(
    store: LocalEntryStore,
    plant: Callable[[LocalEntryStore], None],
) -> None:
    units = _put_units(store)
    plant(store)
    document = _document_path(store, _doc(_TEXT).key)

    report = prune(store)

    assert report.superseded_units == 0
    assert report.refused, "the invalid document is reported, not trusted"
    assert os.path.lexists(document), "the invalid document is kept as evidence"
    for unit in units:
        assert store.get(unit.key) is not None


def test_a_symlinked_document_stops_prune_before_it_removes_anything(
    store: LocalEntryStore,
) -> None:
    """The walk refuses a symlink; nothing is removed before that refusal."""
    units = _put_units(store)
    _symlinked(store)
    store.put(_doc("stale", created_at=NOW - timedelta(days=40)))
    expired = _document_path(store, _doc("stale").key)

    with pytest.raises(EmbeddingCacheError, match="symlink"):
        prune(store, temp_grace=timedelta(0))

    assert _document_path(store, _doc(_TEXT).key).is_symlink()
    assert expired.exists()
    for unit in units:
        assert store.get(unit.key) is not None


def test_an_expired_document_is_pruned_and_every_unit_checkpoint_kept(
    store: LocalEntryStore,
) -> None:
    units = _put_units(store)
    store.put(_doc(_TEXT, created_at=NOW - timedelta(days=40)))

    report = prune(store)

    assert (report.expired, report.superseded_units) == (1, 0)
    for unit in units:
        assert store.get(unit.key) is not None


def test_a_valid_document_supersedes_its_unit_checkpoints(
    store: LocalEntryStore,
) -> None:
    units = _put_units(store)
    store.put(_doc(_TEXT))

    report = prune(store)

    assert (report.superseded_units, report.refused) == (2, [])
    assert store.get(_doc(_TEXT).key) is not None
    for unit in units:
        assert store.get(unit.key) is None


def test_units_kept_behind_a_corrupt_document_are_reused_without_paying_again(
    cache_dir: Path,
    tmp_path: Path,
) -> None:
    """A killed run, a corrupt document, prune, then the document removed: resume pays 2 of 4."""
    settings = _settings(tmp_path)
    with pytest.raises(RuntimeError, match="job killed"):
        _write(settings, with_embedding_cache(_Provider(fail_after=2), settings))
    unit_dirs = list(cache_dir.rglob("units/*"))
    assert len(unit_dirs) == 1
    space, input_hash = unit_dirs[0].parent.parent, unit_dirs[0].name
    documents = space / "documents"
    documents.mkdir(mode=0o700, exist_ok=True)
    corrupt = documents / f"{input_hash}.lsec"
    corrupt.write_bytes(b"not an entry")
    corrupt.chmod(0o600)

    store = LocalEntryStore.open(cache_dir, (), read_policy_from_env())
    report = prune(store)
    assert (report.superseded_units, len(report.refused)) == (0, 1)
    corrupt.unlink()  # the operator's removal of the refused document

    resumed = _Provider()
    _write(settings, with_embedding_cache(resumed, settings))
    assert resumed.requests == 2  # 7 inputs = 4 requests; 2 were checkpointed
