"""Behavioral probes for the cache publication and format survivor surface."""

import os
import stat
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.cache_format import decode_entry, encode_entry
from lovspor.embeddings.cache_store import _publish as _publish_at
from tests.unit.test_embedding_cache_format import _doc_entry, _policy, _unit_entry


def _publish(directory: Path, final: Path, payload: bytes) -> None:
    """Publish ``final`` through a descriptor for ``directory``, as the store does."""
    handle = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        _publish_at(handle, final.name, payload)
    finally:
        os.close(handle)


@pytest.mark.parametrize("mask", [0o000, 0o077, 0o777])
def test_publication_is_private_immutable_and_cleans_staging(tmp_path: Path, mask: int) -> None:
    final = tmp_path / "entry.lsec"
    old_mask = os.umask(mask)
    try:
        _publish(tmp_path, final, b"first")
        assert stat.S_IMODE(final.stat().st_mode) == 0o600
        assert final.read_bytes() == b"first"
        with pytest.raises(FileExistsError):
            _publish(tmp_path, final, b"second")
        assert final.read_bytes() == b"first"
        assert list(tmp_path.iterdir()) == [final]
    finally:
        os.umask(old_mask)


def test_publication_preserves_an_existing_destination_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"untouched")
    final = tmp_path / "entry.lsec"
    final.symlink_to(target)
    with pytest.raises(FileExistsError):
        _publish(tmp_path, final, b"replacement")
    assert final.is_symlink()
    assert target.read_bytes() == b"untouched"
    assert set(tmp_path.iterdir()) == {target, final}


@pytest.mark.parametrize("unit", [False, True])
def test_strided_vectors_have_the_same_wire_bytes_as_contiguous_vectors(unit: bool) -> None:
    entry = _unit_entry() if unit else _doc_entry()
    backing = np.zeros((entry.key.rows, entry.key.dim * 2), dtype=entry.vectors.dtype)
    backing[:, ::2] = entry.vectors
    strided = backing[:, ::2]
    assert not strided.flags.c_contiguous
    wire = encode_entry(replace(entry, vectors=strided))
    assert wire == encode_entry(entry)
    decoded = decode_entry(wire, entry.key, _policy())
    np.testing.assert_array_equal(decoded.vectors, entry.vectors)
    assert decoded.vectors.dtype == entry.vectors.dtype


@pytest.mark.parametrize("unit", [False, True])
def test_wire_round_trip_rejects_byteswapped_float_input(unit: bool) -> None:
    entry = _unit_entry() if unit else _doc_entry()
    wire = encode_entry(entry)
    assert encode_entry(decode_entry(wire, entry.key, _policy())) == wire
    if unit:
        swapped = entry.vectors.astype(np.dtype(">f4"))
        with pytest.raises(ValueError, match="float32 vectors"):
            encode_entry(replace(entry, vectors=swapped))


@pytest.mark.parametrize("target_exists", [True, False])
def test_publication_does_not_follow_a_staging_symlink(tmp_path: Path, target_exists: bool) -> None:
    """A swapped temporary name must not publish the target's inode (§5.1).

    An audit hook changes only files, immediately before the real link syscall;
    publication itself runs in this pytest process without patched store code.
    """
    target = tmp_path / "outside"
    if target_exists:
        target.write_bytes(b"outside content")
    final = tmp_path / "entry.lsec"
    active = True
    swapped: list[Path] = []
    staged_inodes: list[int] = []

    def swap_staging(event: str, args: tuple[object, ...]) -> None:
        # _link_in links names relative to the directory's descriptor.
        if active and event == "os.link" and args[1] == final.name:
            staged = tmp_path / str(args[0])
            staged.unlink()
            staged.symlink_to(target)
            swapped.append(staged)
            staged_inodes.append(staged.lstat().st_ino)

    sys.addaudithook(swap_staging)
    try:
        _publish(tmp_path, final, b"intended content")
    finally:
        # Audit hooks cannot be removed; deactivate this one even on failure.
        active = False
    assert len(swapped) == 1
    assert final.is_symlink()
    assert final.readlink() == target
    assert final.lstat().st_ino == staged_inodes[0]
    if target_exists:
        assert target.read_bytes() == b"outside content"
        assert set(tmp_path.iterdir()) == {target, final}
    else:
        assert not target.exists()
        assert list(tmp_path.iterdir()) == [final]
