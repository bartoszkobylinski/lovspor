"""Every file inside the staging directory is created, read and replaced safely (#738).

Review of PR #773 (P1): ``_staging`` refused a symlinked staging directory, but
the leaves inside it, ``<sha>``, ``<sha>.next`` and ``journal.next``, were
written with ``Path.write_bytes``, which follows a symlink planted there and
overwrites whatever it points to outside the sandbox.
"""

import json
import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import corpus_leaves, corpus_transaction
from lovspor.forarbeider.corpus_transaction import JOURNAL, STAGING
from lovspor.forarbeider.corpus_tree import read_dataset
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from tests.unit.cli_output import said
from tests.unit.forarbeider_corpus_support import refusal_of
from tests.unit.forarbeider_promotion_support import planned, sandbox, simulate, tree
from tests.unit.forarbeider_release_support import Desk, at_a_terminal  # noqa: F401

pytestmark = pytest.mark.usefixtures("at_a_terminal")

SENTINEL = b"unrelated private data outside the sandbox"


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    return Desk(tmp_path / "desk")


@pytest.fixture
def box(desk: Desk, tmp_path: Path) -> Path:
    path = tmp_path / "sandbox"
    assert sandbox(desk, path).exit_code == 0
    return path


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    path = tmp_path / "outside"
    path.mkdir(mode=0o700)
    (path / "sentinel").write_bytes(SENTINEL)
    return path / "sentinel"


def _writes(desk: Desk, sha: str) -> dict[str, str]:
    """The planned files of plan ``sha``: dataset path to digest."""
    record = json.loads((desk.workspace / "plans" / f"{sha}.json").read_text())
    return {item["path"]: item["sha256"] for item in record["writes"]}


def _document_digest(desk: Desk, sha: str) -> str:
    return next(
        digest
        for path, digest in _writes(desk, sha).items()
        if path.endswith(".md") and "INDEX" not in path
    )


def _staging(box: Path) -> Path:
    staging = box / STAGING
    staging.mkdir(mode=0o700)
    return staging


def _leaf(desk: Desk, sha: str, which: str) -> str:
    document = _document_digest(desk, sha)
    return {"staged": document, "copy": f"{document}.next", "journal": "journal.next"}[which]


@pytest.mark.parametrize("which", ["staged", "copy", "journal"])
def test_a_symlinked_staging_leaf_is_never_written_through(
    desk: Desk, box: Path, outside: Path, which: str
) -> None:
    sha = planned(desk, box)
    (_staging(box) / _leaf(desk, sha, which)).symlink_to(outside)

    result = simulate(desk, sha, box)

    # The filesystem first: a refusal after writing outside is still the defect.
    assert outside.read_bytes() == SENTINEL, "simulate wrote outside its sandbox"
    assert (box / STAGING / _leaf(desk, sha, which)).is_symlink()
    assert result.exit_code == 1, result.output
    assert said("refused: unsafe_output_path: staging", result.output)


def _bounded(action: Callable[[], Any], fifo: Path) -> tuple[bool, list[Any]]:
    """Run ``action`` with ``fifo`` in its way: (blocked, results).

    A writer that opens a FIFO waits for a reader forever, so after a bounded
    wait the test opens the reading end itself and the test fails at once.
    """
    results: list[Any] = []
    worker = threading.Thread(target=lambda: results.append(action()), daemon=True)
    worker.start()
    worker.join(timeout=5)
    blocked = worker.is_alive()
    if blocked:
        os.close(os.open(fifo, os.O_RDONLY | os.O_NONBLOCK))
        worker.join(timeout=5)
    return blocked, results


@pytest.mark.parametrize("which", ["staged", "copy", "journal"])
def test_a_fifo_at_a_staging_leaf_is_refused_without_blocking(
    desk: Desk, box: Path, which: str
) -> None:
    sha = planned(desk, box)
    fifo = _staging(box) / _leaf(desk, sha, which)
    os.mkfifo(fifo, 0o600)

    blocked, results = _bounded(lambda: simulate(desk, sha, box), fifo)

    assert not blocked, "simulate blocked on a FIFO in staging"
    assert results[0].exit_code == 1, results[0].output
    assert said("refused: unsafe_output_path: staging", results[0].output)


@pytest.mark.parametrize("which", ["staged", "copy", "journal"])
def test_a_hard_link_at_a_staging_leaf_is_refused_not_truncated(
    desk: Desk, box: Path, outside: Path, which: str
) -> None:
    sha = planned(desk, box)
    os.link(outside, _staging(box) / _leaf(desk, sha, which))

    result = simulate(desk, sha, box)

    assert outside.read_bytes() == SENTINEL
    assert outside.stat().st_nlink == 2
    assert result.exit_code == 1, result.output
    assert said("refused: unsafe_output_path: staging", result.output)


def test_a_staging_leaf_others_can_write_is_refused(desk: Desk, box: Path) -> None:
    sha = planned(desk, box)
    leftover = _staging(box) / _leaf(desk, sha, "staged")
    leftover.write_bytes(b"leftover")
    leftover.chmod(0o666)

    result = simulate(desk, sha, box)

    assert result.exit_code == 1, result.output
    assert said("refused: unsafe_output_path: staging", result.output)
    assert leftover.read_bytes() == b"leftover"


@pytest.mark.parametrize("which", ["staged", "copy", "journal"])
def test_a_corrupt_private_leftover_is_replaced_and_the_retry_applies(
    desk: Desk, box: Path, which: str
) -> None:
    sha = planned(desk, box)
    leftover = _staging(box) / _leaf(desk, sha, which)
    leftover.write_bytes(b"left by an interrupted run")
    leftover.chmod(0o600)

    result = simulate(desk, sha, box)

    assert result.exit_code == 0, result.output
    assert read_dataset(box).manifest_sha256 == _writes(desk, sha)["forarbeider/manifest.json"]
    assert not (box / STAGING).exists()
    assert not (box / JOURNAL).exists()


def test_a_symlink_planted_in_staging_after_a_crash_is_not_read_or_followed(
    desk: Desk, box: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = planned(desk, box)
    real = corpus_transaction.replace

    def crash_before_the_index(source: Path, target: Path) -> None:
        if target.name == "INDEX.md":
            raise OSError("simulated crash")
        real(source, target)

    monkeypatch.setattr(corpus_transaction, "replace", crash_before_the_index)
    assert simulate(desk, sha, box).exit_code == 1
    monkeypatch.setattr(corpus_transaction, "replace", real)
    index = box / STAGING / _writes(desk, sha)["forarbeider/INDEX.md"]
    outside.write_bytes(index.read_bytes())
    index.unlink()
    index.symlink_to(outside)
    before = outside.read_bytes()

    result = simulate(desk, sha, box)

    assert outside.read_bytes() == before
    assert result.exit_code == 1, result.output
    assert said("refused: unsafe_output_path: staging", result.output)
    assert (box / JOURNAL).exists(), "the interrupted change stays journaled"
    assert "forarbeider/INDEX.md" not in tree(box)


def test_a_staging_directory_others_can_write_is_refused(desk: Desk, box: Path) -> None:
    sha = planned(desk, box)
    _staging(box).chmod(0o777)

    result = simulate(desk, sha, box)

    assert result.exit_code == 1, result.output
    assert said("refused: unsafe_output_path: staging", result.output)
    assert list((box / STAGING).iterdir()) == []


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


def test_a_leaf_round_trips_owner_only_through_its_directory(tmp_path: Path) -> None:
    root = _private_directory(tmp_path / "staging")
    with corpus_leaves.directory(root, "staging") as parent:
        corpus_leaves.write_leaf(parent, "leaf", b"bytes", "staging")
        corpus_leaves.write_leaf(parent, "leaf", b"other", "staging")
        assert corpus_leaves.read_leaf(parent, "leaf", "staging") == b"other"
    assert stat.S_IMODE((root / "leaf").stat().st_mode) == 0o600


@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink", "writable", "large"])
def test_reading_an_unsafe_leaf_is_refused_with_its_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root = _private_directory(tmp_path / "staging")
    leaf = root / "leaf"
    other = tmp_path / "other"
    other.write_bytes(b"x")
    if kind == "symlink":
        leaf.symlink_to(other)
    elif kind == "fifo":
        os.mkfifo(leaf, 0o600)
    elif kind == "hardlink":
        os.link(other, leaf)
    else:
        leaf.write_bytes(b"xy")
        leaf.chmod(0o622 if kind == "writable" else 0o600)
        monkeypatch.setattr(corpus_leaves, "MAX_CORPUS_FILE_BYTES", 1)
    with corpus_leaves.directory(root, "staging") as parent:
        fifo = leaf if kind == "fifo" else None
        refused = refusal_of(lambda: corpus_leaves.read_leaf(parent, "leaf", "here"), fifo)
    assert (refused.reason, refused.detail) == (RefusalReason.UNSAFE_OUTPUT_PATH, "here")


def test_reading_at_the_size_bound_returns_every_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _private_directory(tmp_path / "staging")
    (root / "leaf").write_bytes(b"xy")
    monkeypatch.setattr(corpus_leaves, "MAX_CORPUS_FILE_BYTES", 2)
    with corpus_leaves.directory(root, "staging") as parent:
        assert corpus_leaves.read_leaf(parent, "leaf", "staging") == b"xy"


def test_read_leaf_bounds_a_file_that_grows_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real concurrent writer cannot make a size-bounded read return extra bytes."""
    root = _private_directory(tmp_path / "staging")
    limit = 4
    monkeypatch.setattr(corpus_leaves, "MAX_CORPUS_FILE_BYTES", limit)
    leaf = root / "leaf"
    with leaf.open("wb") as stream:
        stream.truncate(limit)
    stop = threading.Event()

    def grow_and_shrink() -> None:
        with leaf.open("r+b", buffering=0) as stream:
            while not stop.is_set():
                stream.truncate(limit)
                stream.truncate(limit + 2)

    writer = threading.Thread(target=grow_and_shrink)
    writer.start()
    try:
        with corpus_leaves.directory(root, "staging") as parent:
            for _ in range(2000):
                try:
                    payload = corpus_leaves.read_leaf(parent, "leaf", "staging")
                except ForarbeiderRefusedError as error:
                    assert error.reason == RefusalReason.UNSAFE_OUTPUT_PATH
                else:
                    assert len(payload) <= limit + 1
    finally:
        stop.set()
        writer.join()


def test_missing_leaves_and_directories_raise_not_found(tmp_path: Path) -> None:
    root = _private_directory(tmp_path / "staging")
    with pytest.raises(FileNotFoundError), corpus_leaves.directory(tmp_path / "absent", "staging"):
        pass
    with corpus_leaves.directory(root, "staging") as parent, pytest.raises(FileNotFoundError):
        corpus_leaves.read_leaf(parent, "absent", "staging")


@pytest.mark.parametrize("kind", ["symlink", "file", "writable"])
def test_an_unsafe_directory_is_refused_with_its_detail(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "staging"
    if kind == "symlink":
        path.symlink_to(_private_directory(tmp_path / "real"))
    elif kind == "file":
        path.write_bytes(b"")
    else:
        _private_directory(path).chmod(0o702)
    with pytest.raises(ForarbeiderRefusedError) as caught, corpus_leaves.directory(path, "here"):
        pass
    assert (caught.value.reason, caught.value.detail) == (RefusalReason.UNSAFE_OUTPUT_PATH, "here")


def test_a_leaf_that_appears_between_check_and_create_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _private_directory(tmp_path / "staging")
    outside = tmp_path / "outside"
    outside.write_bytes(SENTINEL)

    def race(parent: int, name: str, detail: str) -> None:
        os.symlink(outside, name, dir_fd=parent)

    monkeypatch.setattr(corpus_leaves, "_remove_leftover", race)
    with (
        corpus_leaves.directory(root, "staging") as parent,
        pytest.raises(ForarbeiderRefusedError) as caught,
    ):
        corpus_leaves.write_leaf(parent, "leaf", b"bytes", "here")
    assert (caught.value.reason, caught.value.detail) == (RefusalReason.UNSAFE_OUTPUT_PATH, "here")
    assert outside.read_bytes() == SENTINEL


def test_a_leaf_is_read_with_a_bound_although_fstat_said_it_fits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file can grow between ``fstat`` and the read: the read itself stays bounded."""
    root = _private_directory(tmp_path / "staging")
    (root / "leaf").write_bytes(b"xy")
    asked: list[int] = []
    real = os.read

    def counted(descriptor: int, size: int) -> bytes:
        asked.append(size)
        return real(descriptor, size)

    with corpus_leaves.directory(root, "staging") as parent:
        monkeypatch.setattr(corpus_leaves.os, "read", counted)
        assert corpus_leaves.read_leaf(parent, "leaf", "staging") == b"xy"
        monkeypatch.undo()
    assert asked == [corpus_leaves.MAX_CORPUS_FILE_BYTES + 1]
