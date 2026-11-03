"""Filesystem behavior checks for the remaining corpus-tree mutations."""

from pathlib import Path

import pytest

from lovspor.forarbeider.corpus_tree import (
    MARKER,
    create_sandbox,
    read_dataset,
    require_sandbox,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason


@pytest.mark.parametrize("location", ["dataset", "baseline"])
@pytest.mark.parametrize("target_kind", ["directory", "file", "missing"])
def test_tree_refuses_symlinks_without_following_targets(
    tmp_path: Path, location: str, target_kind: str
) -> None:
    box = create_sandbox(tmp_path / "box", ())
    target = tmp_path / "outside"
    if target_kind == "directory":
        target.mkdir()
    elif target_kind == "file":
        target.write_bytes(b"outside bytes\n")
    if location == "dataset":
        root = box / "forarbeider"
        root.mkdir()
    else:
        root = box
    (root / "link").symlink_to(target, target_is_directory=target_kind == "directory")

    with pytest.raises(ForarbeiderRefusedError) as caught:
        if location == "dataset":
            read_dataset(box)
        else:
            require_sandbox(box, ())
    assert caught.value.reason is RefusalReason.UNSAFE_INPUT_PATH
    assert caught.value.detail == ("forarbeider" if location == "dataset" else "sandbox")
    assert (root / "link").is_symlink()
    if target_kind == "file":
        assert target.read_bytes() == b"outside bytes\n"
    elif target_kind == "directory":
        assert list(target.iterdir()) == []
    else:
        assert not target.exists()


@pytest.mark.parametrize("name", [MARKER, ".git/config"])
@pytest.mark.parametrize("damage", ["missing", "symlink", "wrong_bytes", "oversized"])
def test_damaged_identity_refuses_with_the_public_sandbox_error(
    tmp_path: Path, name: str, damage: str
) -> None:
    box = create_sandbox(tmp_path / "box", ())
    assert require_sandbox(box, ()) == box
    identity = box / name
    identity.unlink()
    if damage == "symlink":
        identity.symlink_to(tmp_path / "missing-identity")
    elif damage == "wrong_bytes":
        identity.write_bytes(b'[remote "outside"]\nurl = https://example.invalid/repo\n')
    elif damage == "oversized":
        with identity.open("wb") as stream:
            stream.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        require_sandbox(box, ())
    assert caught.value.reason is RefusalReason.CORPUS_NOT_SANDBOX
    assert caught.value.detail == "sandbox"
    assert str(caught.value) == "corpus_not_sandbox: sandbox"
    assert not (box / "forarbeider").exists()
