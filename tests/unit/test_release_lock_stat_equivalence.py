"""Boundary cases for the release lock's registered os.stat equivalence."""

import os
from pathlib import Path

import pytest


def test_stat_none_and_false_agree_for_dangling_and_missing_names(tmp_path: Path) -> None:
    (tmp_path / "dangling").symlink_to("absent")
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:

        def lookup(name: str, follow: bool | None) -> os.stat_result:
            return os.stat(name, dir_fd=directory, follow_symlinks=follow)  # type: ignore[arg-type]

        assert lookup("dangling", None) == lookup("dangling", False)
        assert lookup("dangling", False) == (tmp_path / "dangling").lstat()
        with pytest.raises(FileNotFoundError):
            lookup("dangling", True)
        for follow in (None, False):
            with pytest.raises(FileNotFoundError):
                lookup("absent", follow)
    finally:
        os.close(directory)
