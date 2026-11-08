"""A root swapped between its check and its use cannot redirect the store (#825 review).

Checking the root with ``lstat`` before each walk still leaves a window: the
root can be replaced by a symlink after the check passes and before the walk
uses the path. The store must hold the directory it checked, not its name.
The swap below fires the moment the root's ownership check passes, whatever
code performs it.
"""

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from shutil import copytree

import numpy as np
import pytest

from lovspor.embeddings import cache_store
from lovspor.embeddings.cache_format import decode_entry, encode_entry
from tests.unit.test_embedding_cache_store import POLICY, _entry, _key, _open, _unit


@pytest.fixture
def swap_after_check(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path, Path], None]:
    """Arm a one-shot swap of ``root`` for a symlink to ``decoy``."""

    def arm(root: Path, decoy: Path) -> None:
        checked = cache_store._check_owner_only
        armed = iter([True])

        def check_then_swap(info: object, name: str) -> None:
            checked(info, name)  # type: ignore[arg-type]
            if name == root.name and next(armed, False):
                root.rename(root.with_name("held"))
                root.symlink_to(decoy, target_is_directory=True)

        monkeypatch.setattr(cache_store, "_check_owner_only", check_then_swap)

    return arm


def _files(tree: Path) -> Iterator[Path]:
    return (path.relative_to(tree) for path in tree.rglob("*.lsec"))


def test_a_write_lands_in_the_checked_root_not_the_swapped_in_target(
    tmp_path: Path,
    swap_after_check: Callable[[Path, Path], None],
) -> None:
    root, decoy = tmp_path / "cache", tmp_path / "decoy"
    store = _open(root)
    _open(decoy)
    swap_after_check(root, decoy)

    store.put(_unit())

    assert list(_files(decoy)) == []
    assert len(list(_files(tmp_path / "held"))) == 1


def test_a_read_comes_from_the_checked_root_not_the_swapped_in_target(
    tmp_path: Path,
    swap_after_check: Callable[[Path, Path], None],
) -> None:
    root, decoy = tmp_path / "cache", tmp_path / "decoy"
    store = _open(root)
    _open(decoy).put(_entry(value=99))
    swap_after_check(root, decoy)

    assert store.get(_key()) is None


@pytest.mark.parametrize("depth", [1, 2, 3, 4])
@pytest.mark.parametrize("operation", ["get", "put"])
def test_descendant_swapped_after_check_cannot_redirect_access(
    tmp_path: Path,
    swap_after_check: Callable[[Path, Path], None],
    depth: int,
    operation: str,
) -> None:
    """The store's module contract anchors every checked directory, including leaves."""
    root = tmp_path / "cache"
    store = _open(root)
    entry = _unit()
    store.put(entry)
    parts, name = cache_store.entry_location(entry.key)
    directory = root.joinpath(*parts[:depth])
    target = root.joinpath(*parts, name)
    relative = target.relative_to(directory)
    decoy = tmp_path / "decoy"
    copytree(directory, decoy)
    decoy_target = decoy / relative
    decoy_bytes = encode_entry(replace(entry, vectors=-entry.vectors))
    decoy_target.write_bytes(decoy_bytes)
    if operation == "put":
        target.unlink()
    swap_after_check(directory, decoy)

    found = store.get(entry.key) if operation == "get" else store.put(entry)

    assert found is not None
    np.testing.assert_array_equal(found.vectors, entry.vectors)
    held = directory.with_name("held")
    persisted = decode_entry((held / relative).read_bytes(), entry.key, POLICY)
    np.testing.assert_array_equal(persisted.vectors, entry.vectors)
    assert directory.is_symlink()
    assert directory.readlink() == decoy
    assert decoy_target.read_bytes() == decoy_bytes
    assert [path.relative_to(decoy) for path in decoy.rglob("*") if path.is_file()] == [relative]
    assert not list(held.rglob("*.tmp.*"))
