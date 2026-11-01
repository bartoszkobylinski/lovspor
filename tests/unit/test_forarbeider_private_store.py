"""Private staging primitives under the review workflow (#738, ADR-0018 PR3A)."""

import os
import stat
import tracemalloc
from pathlib import Path

import pytest

from lovspor.forarbeider import staging
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.staging import (
    checked_directory,
    ensure_private_directory,
    private_directory_exists,
    read_private,
    write_observation,
)

_MIB = 1024 * 1024


def _refusal(call: object, *arguments: object) -> RefusalReason:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        call(*arguments)  # type: ignore[operator]
    return caught.value.reason


def _private(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def test_a_large_existing_file_is_refused_without_loading_it(tmp_path: Path) -> None:
    target = tmp_path / "chapter.json"
    with target.open("wb") as stream:
        stream.truncate(64 * _MIB)
    target.chmod(0o600)
    tracemalloc.start()
    try:
        assert _refusal(write_observation, target, "short") is RefusalReason.OBSERVATION_EXISTS
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < _MIB
    assert target.stat().st_size == 64 * _MIB


def test_same_size_different_bytes_is_still_a_different_observation(tmp_path: Path) -> None:
    target = _private(tmp_path / "chapter.json", b"AAAA")
    assert _refusal(write_observation, target, "BBBB") is RefusalReason.OBSERVATION_EXISTS
    assert target.read_bytes() == b"AAAA"


def test_read_private_returns_the_bytes_of_a_private_file(tmp_path: Path) -> None:
    assert read_private(_private(tmp_path / "a.json", b"held"), 4) == b"held"


def test_read_private_refuses_past_the_limit_by_size(tmp_path: Path) -> None:
    path = _private(tmp_path / "a.json", b"12345")
    assert _refusal(read_private, path, 4) is RefusalReason.INPUT_TOO_LARGE


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660])
def test_read_private_refuses_what_others_can_reach(tmp_path: Path, mode: int) -> None:
    path = _private(tmp_path / "a.json", b"held")
    path.chmod(mode)
    assert _refusal(read_private, path, 99) is RefusalReason.OBSERVATION_UNSAFE


def test_read_private_refuses_a_symlink(tmp_path: Path) -> None:
    real = _private(tmp_path / "real.json", b"held")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    assert _refusal(read_private, link, 99) is RefusalReason.OBSERVATION_UNSAFE


def test_read_private_refuses_a_second_name(tmp_path: Path) -> None:
    real = _private(tmp_path / "real.json", b"held")
    os.link(real, tmp_path / "second.json")
    assert _refusal(read_private, real, 99) is RefusalReason.OBSERVATION_UNSAFE


def test_read_private_of_nothing_is_unreadable(tmp_path: Path) -> None:
    assert _refusal(read_private, tmp_path / "gone.json", 9) is RefusalReason.INPUT_UNREADABLE


def test_staged_bytes_reach_the_disk_before_the_name_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "chapter.json"
    synced: list[tuple[bool, bool]] = []
    real = os.fsync

    def spy(descriptor: int) -> None:
        kind = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        synced.append((kind, target.exists()))
        real(descriptor)

    monkeypatch.setattr(staging.os, "fsync", spy)
    write_observation(target, "held")
    assert synced == [(False, False), (True, True)]


def test_an_existing_identical_file_is_not_synced_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "chapter.json"
    write_observation(target, "held")
    directories: list[int] = []
    real = os.fsync

    def spy(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directories.append(descriptor)
        real(descriptor)

    monkeypatch.setattr(staging.os, "fsync", spy)
    assert write_observation(target, "held") is False
    assert directories == []


def test_checked_directory_resolves_a_safe_absolute_path(tmp_path: Path) -> None:
    assert checked_directory(tmp_path / "ledger", ()) == (tmp_path / "ledger").resolve()


def test_checked_directory_expands_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert checked_directory(Path("~/ledger"), ()) == (tmp_path / "ledger").resolve()


def test_checked_directory_refuses_a_relative_path() -> None:
    assert _refusal(checked_directory, Path("ledger"), ()) is RefusalReason.UNSAFE_OUTPUT_PATH


@pytest.mark.parametrize("marker", ["dir", "file"])
def test_checked_directory_refuses_any_repository(tmp_path: Path, marker: str) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    git = repository / ".git"
    git.mkdir() if marker == "dir" else git.write_text("gitdir: x", encoding="utf-8")
    for path in (repository, repository / "nested" / "ledger"):
        assert _refusal(checked_directory, path, ()) is RefusalReason.UNSAFE_OUTPUT_PATH


def test_checked_directory_refuses_a_forbidden_tree_and_its_root(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    for path in (corpus, corpus / "ledger"):
        assert _refusal(checked_directory, path, (corpus,)) is RefusalReason.UNSAFE_OUTPUT_PATH


def test_a_symlink_loop_never_becomes_a_ledger(tmp_path: Path) -> None:
    """``Path.resolve`` raises on a loop before 3.13 and returns it after: both are refused."""
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        private_directory_exists(checked_directory(loop, ()))
    assert caught.value.reason in {
        RefusalReason.UNSAFE_OUTPUT_PATH,
        RefusalReason.REVIEW_LEDGER_UNSAFE,
    }


def test_ensure_creates_an_owner_only_directory(tmp_path: Path) -> None:
    path = tmp_path / "ledger"
    ensure_private_directory(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_ensure_accepts_a_private_directory_again(tmp_path: Path) -> None:
    path = tmp_path / "ledger"
    ensure_private_directory(path)
    ensure_private_directory(path)
    assert private_directory_exists(path) is True


def test_ensure_without_a_parent_is_a_write_failure(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "ledger"
    assert _refusal(ensure_private_directory, path) is RefusalReason.OUTPUT_WRITE_FAILED


def test_a_missing_directory_does_not_exist(tmp_path: Path) -> None:
    assert private_directory_exists(tmp_path / "gone") is False


@pytest.mark.parametrize("mode", [0o750, 0o705, 0o770])
def test_a_directory_others_can_reach_is_unsafe(tmp_path: Path, mode: int) -> None:
    path = tmp_path / "ledger"
    path.mkdir()
    path.chmod(mode)
    for check in (ensure_private_directory, private_directory_exists):
        assert _refusal(check, path) is RefusalReason.REVIEW_LEDGER_UNSAFE


def test_a_symlinked_directory_is_unsafe(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    assert _refusal(private_directory_exists, link) is RefusalReason.REVIEW_LEDGER_UNSAFE


def test_a_file_in_place_of_a_directory_is_unsafe(tmp_path: Path) -> None:
    path = _private(tmp_path / "ledger", b"")
    assert _refusal(private_directory_exists, path) is RefusalReason.REVIEW_LEDGER_UNSAFE


def test_a_directory_of_another_owner_is_unsafe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger"
    path.mkdir(mode=0o700)
    other = os.geteuid() + 1
    monkeypatch.setattr(staging.os, "geteuid", lambda: other)
    assert _refusal(private_directory_exists, path) is RefusalReason.REVIEW_LEDGER_UNSAFE
