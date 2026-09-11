"""Tests for lovspor.atomic_io."""

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from lovspor.atomic_io import atomic_write_bytes, atomic_write_text


@pytest.fixture
def loose_umask() -> Iterator[None]:
    """``umask 022`` for one test: an unmoded write lands on ``0644``.

    What ``strict_umask`` cannot distinguish: under ``umask 077`` the umask's
    own mode and ``mkstemp``'s hardcoded ``0600`` read the same, so a test
    about which of the two a path got has to run under a looser one.
    """
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def test_atomic_write_text_writes_content(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_text_honours_the_encoding(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "ø", encoding="latin-1")
    assert target.read_bytes() == b"\xf8"


def test_atomic_write_text_creates_nothing_when_the_codec_refuses_the_string(
    tmp_path: Path,
) -> None:
    """Encoded before anything is created, so a refused string leaves no file at all."""
    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(tmp_path / "f.txt", "ø", encoding="ascii")

    assert list(tmp_path.iterdir()) == []


def test_atomic_write_text_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "f.txt"
    atomic_write_text(target, "deep")
    assert target.read_text(encoding="utf-8") == "deep"


def test_atomic_write_text_leaves_no_tmp_file(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "x")
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_text_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("OLD", encoding="utf-8")
    atomic_write_text(target, "NEW")
    assert target.read_text(encoding="utf-8") == "NEW"


def test_atomic_write_text_preserves_original_and_cleans_tmp_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed replace must leave the previous file intact (no torn write a
    concurrent reader could see) and must not leave a stray .tmp behind."""
    target = tmp_path / "f.txt"
    target.write_text("ORIGINAL", encoding="utf-8")

    def boom(self: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_text(target, "NEW")

    assert target.read_text(encoding="utf-8") == "ORIGINAL"
    assert not (tmp_path / "f.txt.tmp").exists()


@pytest.mark.skipif(os.getuid() == 0, reason="root writes through a read-only directory")
def test_atomic_write_text_reraises_the_error_when_the_staging_file_cannot_be_made(
    tmp_path: Path,
) -> None:
    target = tmp_path / "f.txt"
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(OSError):
            atomic_write_text(target, "payload")
    finally:
        tmp_path.chmod(0o700)

    assert list(tmp_path.iterdir()) == []


def test_atomic_write_text_cleanup_tolerates_a_staging_file_that_is_already_gone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cleanup unlink must tolerate a staging file something else removed — it
    must not mask the real failure behind a FileNotFoundError of its own."""
    target = tmp_path / "f.txt"

    def boom(self: Path, _target: Path) -> None:
        self.unlink()
        raise OSError("replace failed after the staging file went away")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="replace failed after the staging file went away"):
        atomic_write_text(target, "payload")

    assert list(tmp_path.iterdir()) == []


def test_atomic_write_bytes_writes_payload_verbatim(tmp_path: Path) -> None:
    """Captured bodies are arbitrary bytes: no decode, no newline translation."""
    target = tmp_path / "blob"
    payload = b"\x00\xff\r\n not text \xc3\xb8"

    atomic_write_bytes(target, payload)

    assert target.read_bytes() == payload


def test_atomic_write_bytes_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "blobs" / "ab" / "abc"

    atomic_write_bytes(target, b"x")

    assert target.read_bytes() == b"x"


def test_atomic_write_bytes_leaves_no_tmp_file(tmp_path: Path) -> None:
    target = tmp_path / "blob"

    atomic_write_bytes(target, b"x")

    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_bytes_preserves_original_and_cleans_tmp_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "blob"
    target.write_bytes(b"ORIGINAL")

    def boom(self: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_bytes(target, b"NEW")

    assert target.read_bytes() == b"ORIGINAL"
    assert not (tmp_path / "blob.tmp").exists()


@pytest.mark.skipif(os.getuid() == 0, reason="root writes through a read-only directory")
def test_atomic_write_bytes_reraises_the_error_when_the_staging_file_cannot_be_made(
    tmp_path: Path,
) -> None:
    target = tmp_path / "blob"
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(OSError):
            atomic_write_bytes(target, b"payload")
    finally:
        tmp_path.chmod(0o700)

    assert list(tmp_path.iterdir()) == []


class TestStagingFile:
    """#280: the staging name is predictable, and on the droplet the writer is root."""

    def test_the_staging_file_is_the_targets_dot_tmp_sibling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[Path] = []
        original = Path.replace

        def recording_replace(self: Path, target: Path) -> Path:
            seen.append(self)
            return original(self, target)

        monkeypatch.setattr(Path, "replace", recording_replace)
        atomic_write_text(tmp_path / "f.txt", "x")
        atomic_write_bytes(tmp_path / "blob", b"x")

        assert seen == [tmp_path / "f.txt.tmp", tmp_path / "blob.tmp"]

    def test_a_symlink_at_the_staging_name_is_never_written_through(self, tmp_path: Path) -> None:
        victim = tmp_path / "victim"
        victim.write_text("SECRET", encoding="utf-8")
        planted = tmp_path / "f.txt.tmp"
        planted.symlink_to(victim)

        atomic_write_text(tmp_path / "f.txt", "NEW")

        assert victim.read_text(encoding="utf-8") == "SECRET"
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "NEW"
        assert planted.is_symlink()

    def test_a_symlink_at_the_bytes_staging_name_is_never_written_through(
        self, tmp_path: Path
    ) -> None:
        victim = tmp_path / "victim"
        victim.write_bytes(b"SECRET")
        planted = tmp_path / "blob.tmp"
        planted.symlink_to(victim)

        atomic_write_bytes(tmp_path / "blob", b"NEW")

        assert victim.read_bytes() == b"SECRET"
        assert (tmp_path / "blob").read_bytes() == b"NEW"
        assert planted.is_symlink()

    def test_a_dangling_symlink_at_the_staging_name_creates_nothing_at_its_target(
        self, tmp_path: Path
    ) -> None:
        absent = tmp_path / "absent"
        (tmp_path / "f.txt.tmp").symlink_to(absent)

        atomic_write_text(tmp_path / "f.txt", "NEW")

        assert not absent.exists()
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "NEW"

    def test_a_dangling_symlink_at_the_bytes_staging_name_creates_nothing_at_its_target(
        self, tmp_path: Path
    ) -> None:
        absent = tmp_path / "absent"
        (tmp_path / "blob.tmp").symlink_to(absent)

        atomic_write_bytes(tmp_path / "blob", b"NEW")

        assert not absent.exists()
        assert (tmp_path / "blob").read_bytes() == b"NEW"

    def test_the_target_is_the_file_this_call_wrote_not_a_planted_link(
        self, tmp_path: Path
    ) -> None:
        """The link must not be renamed onto the target: later reads would go through it."""
        victim = tmp_path / "victim"
        victim.write_text("SECRET", encoding="utf-8")
        (tmp_path / "f.txt.tmp").symlink_to(victim)

        atomic_write_text(tmp_path / "f.txt", "NEW")
        victim.write_text("MOVED", encoding="utf-8")

        assert not (tmp_path / "f.txt").is_symlink()
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "NEW"

    def test_a_stale_regular_file_at_the_staging_name_is_left_untouched(
        self, tmp_path: Path
    ) -> None:
        """A ``.tmp`` a kill -9 left behind is stepped around, not consumed: the code
        cannot tell it from someone else's file, so it neither opens nor removes it."""
        stale = tmp_path / "f.txt.tmp"
        stale.write_text("STALE", encoding="utf-8")

        atomic_write_text(tmp_path / "f.txt", "NEW")

        assert stale.read_text(encoding="utf-8") == "STALE"
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "NEW"

    def test_a_directory_at_the_staging_name_does_not_stop_the_write(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt.tmp").mkdir()

        atomic_write_text(tmp_path / "f.txt", "NEW")

        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "NEW"
        assert (tmp_path / "f.txt.tmp").is_dir()

    def test_the_stepped_aside_file_is_a_unique_name_in_the_targets_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same directory, so ``replace`` stays on one filesystem; prefixed by the name
        it stepped aside from, so an operator reading the directory can place it."""
        (tmp_path / "f.txt.tmp").symlink_to(tmp_path / "victim")
        seen: list[Path] = []
        original = Path.replace

        def recording_replace(self: Path, target: Path) -> Path:
            seen.append(self)
            return original(self, target)

        monkeypatch.setattr(Path, "replace", recording_replace)
        atomic_write_text(tmp_path / "f.txt", "NEW")

        assert [staged.parent for staged in seen] == [tmp_path]
        assert seen[0].name.startswith("f.txt.tmp.")
        assert seen[0].name != "f.txt.tmp"

    def test_the_stepped_aside_file_does_not_linger(self, tmp_path: Path) -> None:
        planted = tmp_path / "f.txt.tmp"
        planted.symlink_to(tmp_path / "victim")

        atomic_write_text(tmp_path / "f.txt", "NEW")

        assert sorted(path.name for path in tmp_path.iterdir()) == ["f.txt", "f.txt.tmp"]

    def test_a_failed_write_removes_the_stepped_aside_file_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "f.txt"
        target.write_text("ORIGINAL", encoding="utf-8")
        (tmp_path / "f.txt.tmp").symlink_to(tmp_path / "victim")

        def boom(self: Path, _target: Path) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr(Path, "replace", boom)
        with pytest.raises(OSError, match="simulated replace failure"):
            atomic_write_text(target, "NEW")

        assert target.read_text(encoding="utf-8") == "ORIGINAL"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["f.txt", "f.txt.tmp"]


class TestMode:
    def test_the_default_mode_is_the_umasks(self, tmp_path: Path, strict_umask: None) -> None:
        atomic_write_text(tmp_path / "t", "x")
        atomic_write_bytes(tmp_path / "b", b"x")

        assert stat.S_IMODE((tmp_path / "t").stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "b").stat().st_mode) == 0o600

    def test_the_default_mode_follows_a_looser_umask_too(
        self, tmp_path: Path, loose_umask: None
    ) -> None:
        atomic_write_text(tmp_path / "t", "x")
        atomic_write_bytes(tmp_path / "b", b"x")

        assert stat.S_IMODE((tmp_path / "t").stat().st_mode) == 0o644
        assert stat.S_IMODE((tmp_path / "b").stat().st_mode) == 0o644

    def test_an_explicit_mode_wins_over_the_umask(self, tmp_path: Path, strict_umask: None) -> None:
        atomic_write_text(tmp_path / "t", "x", mode=0o644)
        atomic_write_bytes(tmp_path / "b", b"x", mode=0o644)

        assert stat.S_IMODE((tmp_path / "t").stat().st_mode) == 0o644
        assert stat.S_IMODE((tmp_path / "b").stat().st_mode) == 0o644

    def test_the_mode_is_set_on_the_temp_file_before_the_rename(
        self, tmp_path: Path, strict_umask: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reader never sees the final path with the wrong mode."""
        seen: list[int] = []
        original = Path.replace

        def recording_replace(self: Path, target: Path) -> Path:
            seen.append(stat.S_IMODE(self.stat().st_mode))
            return original(self, target)

        monkeypatch.setattr(Path, "replace", recording_replace)
        atomic_write_text(tmp_path / "t", "x", mode=0o644)
        atomic_write_bytes(tmp_path / "b", b"x", mode=0o640)

        assert seen == [0o644, 0o640]

    def test_an_existing_file_is_replaced_mode_included(
        self, tmp_path: Path, strict_umask: None
    ) -> None:
        target = tmp_path / "t"
        target.write_text("old", encoding="utf-8")
        target.chmod(0o600)

        atomic_write_text(target, "new", mode=0o644)

        assert stat.S_IMODE(target.stat().st_mode) == 0o644
        assert target.read_text(encoding="utf-8") == "new"

    def test_a_chmod_failure_preserves_the_original_and_cleans_the_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "t"
        target.write_text("original", encoding="utf-8")

        def fail_fchmod(_descriptor: int, _mode: int) -> None:
            raise OSError("chmod failed")

        monkeypatch.setattr(os, "fchmod", fail_fchmod)

        with pytest.raises(OSError, match="chmod failed"):
            atomic_write_text(target, "replacement", mode=0o644)

        assert target.read_text(encoding="utf-8") == "original"
        assert not (tmp_path / "t.tmp").exists()

    def test_the_mode_lands_on_the_file_written_not_on_a_planted_links_target(
        self, tmp_path: Path, strict_umask: None
    ) -> None:
        """``fchmod`` on the descriptor: a link at the staging name cannot redirect it."""
        victim = tmp_path / "victim"
        victim.write_text("SECRET", encoding="utf-8")
        victim.chmod(0o600)
        (tmp_path / "t.tmp").symlink_to(victim)

        atomic_write_text(tmp_path / "t", "NEW", mode=0o644)

        assert stat.S_IMODE(victim.stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "t").stat().st_mode) == 0o644

    def test_the_staging_file_is_never_created_wider_than_the_mode_asked_for(
        self, tmp_path: Path, loose_umask: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The window between the create and the fchmod is the one moment the content
        exists under a mode this call did not pick; it must not be a wider one."""
        created: list[int] = []
        original = os.fchmod

        def recording_fchmod(descriptor: int, mode: int) -> None:
            created.append(stat.S_IMODE(os.fstat(descriptor).st_mode))
            original(descriptor, mode)

        monkeypatch.setattr(os, "fchmod", recording_fchmod)
        atomic_write_text(tmp_path / "t", "x", mode=0o600)
        atomic_write_bytes(tmp_path / "b", b"x", mode=0o600)

        assert created == [0o600, 0o600]

    def test_an_explicit_mode_survives_stepping_aside(
        self, tmp_path: Path, strict_umask: None
    ) -> None:
        (tmp_path / "t.tmp").symlink_to(tmp_path / "victim")

        atomic_write_text(tmp_path / "t", "NEW", mode=0o644)

        assert stat.S_IMODE((tmp_path / "t").stat().st_mode) == 0o644

    def test_an_unmoded_write_that_stepped_aside_is_owner_only(
        self, tmp_path: Path, loose_umask: None
    ) -> None:
        """``mkstemp`` ignores the umask, so the anomaly path errs closed rather than
        open: 0600 where an unobstructed write would have taken the umask's 0644."""
        (tmp_path / "t.tmp").symlink_to(tmp_path / "victim")

        atomic_write_text(tmp_path / "t", "NEW")

        assert stat.S_IMODE((tmp_path / "t").stat().st_mode) == 0o600
