"""Admin walks must enforce the symlink refusal in cache design §5.1.

These are contract tests: cache_admin promises directory checks identical to
serving, whether or not the symlink's target currently exists.
"""

from pathlib import Path

import pytest

from lovspor.embeddings import cache_admin
from lovspor.embeddings.cache_store import LocalEntryStore
from lovspor.errors import EmbeddingCacheError
from tests.unit.test_embedding_cache_admin import ESI, POLICY


@pytest.mark.parametrize("command", ["verify", "prune"])
@pytest.mark.parametrize("target_exists", [False, True], ids=["dangling", "live"])
@pytest.mark.parametrize(
    "relative_path",
    ["schema-1", f"schema-1/{ESI}/documents", f"schema-1/{ESI}/units"],
    ids=["schema", "documents", "units"],
)
def test_admin_refuses_symlinked_directories_even_when_dangling(
    tmp_path: Path, command: str, target_exists: bool, relative_path: str
) -> None:
    store = LocalEntryStore.open(tmp_path / "cache", (), POLICY)
    target = tmp_path / "outside"
    if target_exists:
        target.mkdir(mode=0o700)
    link = store.root / relative_path
    directory = store.root
    for component in Path(relative_path).parts[:-1]:
        directory /= component
        directory.mkdir(mode=0o700)
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(EmbeddingCacheError, match="symlink"):
        getattr(cache_admin, command)(store)

    assert link.is_symlink()
    assert target.exists() is target_exists
    if target_exists:
        assert list(target.iterdir()) == []


@pytest.mark.parametrize("command", ["verify", "prune"])
def test_admin_accepts_absent_schema_directory(tmp_path: Path, command: str) -> None:
    """An absent cache is empty; a dangling symlink is an unsafe object."""
    store = LocalEntryStore.open(tmp_path / "cache", (), POLICY)
    report = getattr(cache_admin, command)(store)
    assert report.refused == []
    assert report.expired == 0
    assert list(store.root.iterdir()) == []
