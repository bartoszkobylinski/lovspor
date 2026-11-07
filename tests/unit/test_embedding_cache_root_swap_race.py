"""A root swapped between its check and its use cannot redirect the store (#825 review).

Checking the root with ``lstat`` before each walk still leaves a window: the
root can be replaced by a symlink after the check passes and before the walk
uses the path. The store must hold the directory it checked, not its name.
The swap below fires the moment the root's ownership check passes, whatever
code performs it.
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from lovspor.embeddings import cache_store
from tests.unit.test_embedding_cache_store import _entry, _key, _open, _unit


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
