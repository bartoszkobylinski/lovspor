"""Behavioral checks for the cache-store mutation remediation surface."""

import errno
import sys
from pathlib import Path
from types import FrameType

import pytest

from lovspor.embeddings.cache_store import _open_private_dir, fsync_dir
from lovspor.errors import EmbeddingCacheError, EmbeddingCacheStorageError
from tests.unit.test_embedding_cache_store import _key, _open


def test_rejected_directory_names_the_offending_component(tmp_path: Path) -> None:
    path = tmp_path / "not-a-directory"
    path.write_bytes(b"preserved")
    with pytest.raises(
        EmbeddingCacheError,
        match="^embedding cache path is not a directory: not-a-directory$",
    ):
        _open_private_dir(path, None)
    assert path.read_bytes() == b"preserved"


def test_symlink_directory_is_refused_by_name(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    path = tmp_path / "alias"
    path.symlink_to(target, target_is_directory=True)
    with pytest.raises(EmbeddingCacheError, match="^embedding cache refuses a symlink: alias$"):
        _open_private_dir(path, None)
    assert path.is_symlink()
    assert list(target.iterdir()) == []


def test_directory_replaced_after_failed_open_still_reports_refusal(tmp_path: Path) -> None:
    """A failed no-follow open must remain a refusal if the name changes again.

    Schedule a real filesystem replacement at the exception event, between the
    failed syscall and its diagnostic stat. No cache function or syscall is replaced.
    """
    path = tmp_path / "changed-directory"
    path.write_bytes(b"old file")
    swapped = False

    def replace_after_failure(frame: FrameType, event: str, arg: object) -> object:
        nonlocal swapped
        if (
            event == "exception"
            and "_open_private_dir" in frame.f_code.co_name
            and frame.f_code.co_filename.endswith("cache_store.py")
            and not swapped
        ):
            _, error, _ = arg  # type: ignore[misc]
            if isinstance(error, OSError) and error.errno == errno.ENOTDIR:
                path.unlink()
                path.mkdir(mode=0o700)
                swapped = True
        return replace_after_failure

    previous = sys.gettrace()
    sys.settrace(replace_after_failure)
    try:
        with pytest.raises(
            EmbeddingCacheError, match="^embedding cache refuses changed-directory$"
        ):
            _open_private_dir(path, None)
    finally:
        sys.settrace(previous)
    assert swapped
    assert path.is_dir()
    assert list(path.iterdir()) == []


def test_missing_root_is_an_error_rather_than_an_entry_miss(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    store = _open(root)
    root.rmdir()
    with pytest.raises(EmbeddingCacheStorageError, match="root is gone"):
        store.get(_key())
    assert not root.exists()


def test_entry_miss_does_not_create_directories(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    store = _open(root)
    assert store.get(_key()) is None
    assert list(root.iterdir()) == []


def test_directory_fsync_succeeds_on_a_real_directory(tmp_path: Path) -> None:
    entry = tmp_path / "new-name"
    entry.write_bytes(b"content")
    fsync_dir(tmp_path)
    assert entry.read_bytes() == b"content"
    assert list(tmp_path.iterdir()) == [entry]
