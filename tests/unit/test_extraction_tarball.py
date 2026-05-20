"""Tests for lovspor.extraction.tarball.

Fixture tarballs are built in-memory at test time rather than committed
as binaries: keeps the repo small, makes the contents visible in code,
and avoids binary artifacts in git history.
"""

import io
import tarfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.errors import ExtractionError
from lovspor.extraction.tarball import (
    TarballMember,
    _check_safe_name,
    iter_tarball_xml,
)

_XML_ONE = b"<law>first</law>"
_XML_TWO = b"<forskrift>second</forskrift>"


def _add_file(tar: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    info.type = tarfile.REGTYPE
    tar.addfile(info, io.BytesIO(content))


def _add_symlink(tar: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    tar.addfile(info)


def _add_hardlink(tar: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.LNKTYPE
    info.linkname = target
    tar.addfile(info)


def _add_directory(tar: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.DIRTYPE
    tar.addfile(info)


def _build_tarball(path: Path, build: object) -> Path:
    """Build a tarball and return the path. `build` callback receives a TarFile."""
    with tarfile.open(path, mode="w:bz2") as tar:
        build(tar)  # type: ignore[operator]
    return path


def test_iter_tarball_xml_yields_regular_xml_members(tmp_path: Path) -> None:
    archive = _build_tarball(
        tmp_path / "benign.tar.bz2",
        lambda tar: (
            _add_file(tar, "nl/lov-1.xml", _XML_ONE),
            _add_file(tar, "nl/lov-2.xml", _XML_TWO),
        ),
    )
    members = list(iter_tarball_xml(archive))
    assert {m.name for m in members} == {"nl/lov-1.xml", "nl/lov-2.xml"}
    by_name = {m.name: m.content for m in members}
    assert by_name["nl/lov-1.xml"] == _XML_ONE
    assert by_name["nl/lov-2.xml"] == _XML_TWO


def test_iter_tarball_xml_skips_non_xml_members(tmp_path: Path) -> None:
    archive = _build_tarball(
        tmp_path / "mixed.tar.bz2",
        lambda tar: (
            _add_file(tar, "nl/lov-1.xml", _XML_ONE),
            _add_file(tar, "readme.txt", b"not xml"),
            _add_file(tar, "metadata.json", b"{}"),
        ),
    )
    members = list(iter_tarball_xml(archive))
    assert [m.name for m in members] == ["nl/lov-1.xml"]


def test_iter_tarball_xml_skips_directories(tmp_path: Path) -> None:
    archive = _build_tarball(
        tmp_path / "dirs.tar.bz2",
        lambda tar: (
            _add_directory(tar, "nl/"),
            _add_file(tar, "nl/lov-1.xml", _XML_ONE),
        ),
    )
    members = list(iter_tarball_xml(archive))
    assert [m.name for m in members] == ["nl/lov-1.xml"]


def test_iter_tarball_xml_skips_symlinks(tmp_path: Path) -> None:
    archive = _build_tarball(
        tmp_path / "symlinks.tar.bz2",
        lambda tar: (
            _add_file(tar, "real.xml", _XML_ONE),
            _add_symlink(tar, "link.xml", "real.xml"),
        ),
    )
    members = list(iter_tarball_xml(archive))
    assert [m.name for m in members] == ["real.xml"]


def test_iter_tarball_xml_skips_hardlinks(tmp_path: Path) -> None:
    """Pin the hardlink-skip behavior per Codex's edge-case observation.

    Hardlinks are filtered by the ``member.isfile()`` gate (LNKTYPE does
    not satisfy isfile()). This test locks that behavior in."""
    archive = _build_tarball(
        tmp_path / "hardlinks.tar.bz2",
        lambda tar: (
            _add_file(tar, "real.xml", _XML_ONE),
            _add_hardlink(tar, "hard.xml", "real.xml"),
        ),
    )
    members = list(iter_tarball_xml(archive))
    assert [m.name for m in members] == ["real.xml"]


def test_iter_tarball_xml_continues_when_extractfile_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMember:
        def __init__(self, name: str) -> None:
            self.name = name

        def isfile(self) -> bool:
            return True

    class FakeTar:
        def __init__(self) -> None:
            self.members = [FakeMember("missing.xml"), FakeMember("kept.xml")]

        def __enter__(self) -> "FakeTar":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> object:
            return iter(self.members)

        def extractfile(self, member: FakeMember) -> io.BytesIO | None:
            if member.name == "missing.xml":
                return None
            return io.BytesIO(_XML_ONE)

    monkeypatch.setattr(tarfile, "open", lambda *args, **kwargs: FakeTar())

    assert list(iter_tarball_xml(tmp_path / "archive.tar.bz2")) == [
        TarballMember(name="kept.xml", content=_XML_ONE),
    ]


def test_iter_tarball_xml_rejects_parent_reference(tmp_path: Path) -> None:
    archive = _build_tarball(
        tmp_path / "evil.tar.bz2",
        lambda tar: _add_file(tar, "../escape.xml", b"<evil/>"),
    )
    with pytest.raises(ExtractionError, match="parent reference"):
        list(iter_tarball_xml(archive))


def test_iter_tarball_xml_rejects_nested_parent_reference(
    tmp_path: Path,
) -> None:
    archive = _build_tarball(
        tmp_path / "nested-evil.tar.bz2",
        lambda tar: _add_file(tar, "a/../../escape.xml", b"<evil/>"),
    )
    with pytest.raises(ExtractionError, match="parent reference"):
        list(iter_tarball_xml(archive))


def test_iter_tarball_xml_rejects_backslash_parent_reference(
    tmp_path: Path,
) -> None:
    archive = _build_tarball(
        tmp_path / "win-evil.tar.bz2",
        lambda tar: _add_file(tar, "a\\..\\escape.xml", b"<evil/>"),
    )
    with pytest.raises(ExtractionError, match="parent reference"):
        list(iter_tarball_xml(archive))


def test_iter_tarball_xml_rejects_absolute_path(tmp_path: Path) -> None:
    archive = _build_tarball(
        tmp_path / "absolute.tar.bz2",
        lambda tar: _add_file(tar, "/etc/passwd", b"<evil/>"),
    )
    with pytest.raises(ExtractionError, match="absolute"):
        list(iter_tarball_xml(archive))


def test_iter_tarball_xml_rejects_windows_absolute_path(tmp_path: Path) -> None:
    """Mutation hardening: leading backslash absolute paths must also be
    rejected. Without this test, a mutation that drops '\\\\' from the
    startswith() tuple ('/', '\\\\') would pass on POSIX-style tests."""
    archive = _build_tarball(
        tmp_path / "win-absolute.tar.bz2",
        lambda tar: _add_file(tar, "\\evil.xml", b"<evil/>"),
    )
    with pytest.raises(ExtractionError, match="absolute"):
        list(iter_tarball_xml(archive))


def test_check_safe_name_rejects_null_byte() -> None:
    """Python's tarfile truncates names at null bytes on write (POSIX header
    limitation), so we cannot build an integration fixture easily. The
    null-byte branch is still defense-in-depth against hand-crafted
    tarballs that bypass tarfile's write path. Tested directly on the
    private helper."""
    archive = Path("/dummy")
    with pytest.raises(ExtractionError) as exc_info:
        _check_safe_name("with\x00null.xml", archive)
    assert str(exc_info.value) == (
        f"{archive}: member name contains null byte: 'with\\x00null.xml'"
    )


def test_check_safe_name_rejects_absolute_with_exact_message() -> None:
    archive = Path("/dummy")
    with pytest.raises(ExtractionError) as exc_info:
        _check_safe_name("/escape.xml", archive)
    assert str(exc_info.value) == f"{archive}: member name is absolute: '/escape.xml'"


def test_check_safe_name_rejects_parent_reference_with_exact_message() -> None:
    archive = Path("/dummy")
    with pytest.raises(ExtractionError) as exc_info:
        _check_safe_name("a/../escape.xml", archive)
    assert str(exc_info.value) == (
        f"{archive}: member name has parent reference: 'a/../escape.xml'"
    )


def test_iter_tarball_xml_raises_on_malformed_archive(
    tmp_path: Path,
) -> None:
    bogus = tmp_path / "not-a-tarball.tar.bz2"
    bogus.write_bytes(b"this is not a bz2 tarball at all")
    with pytest.raises(ExtractionError) as exc_info:
        list(iter_tarball_xml(bogus))
    assert str(exc_info.value).startswith(f"{bogus}: malformed archive: ")


def test_iter_tarball_xml_raises_on_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.tar.bz2"
    with pytest.raises(ExtractionError) as exc_info:
        list(iter_tarball_xml(missing))
    assert str(exc_info.value).startswith(f"{missing}: cannot open archive: ")


def test_iter_tarball_xml_returns_empty_for_empty_archive(
    tmp_path: Path,
) -> None:
    archive = _build_tarball(
        tmp_path / "empty.tar.bz2",
        lambda _tar: None,
    )
    assert list(iter_tarball_xml(archive)) == []


def test_tarball_member_is_frozen() -> None:
    member = TarballMember(name="x.xml", content=b"<x/>")
    with pytest.raises(ValidationError):
        member.name = "mutated.xml"  # type: ignore[misc]


def test_tarball_member_preserves_content_bytes() -> None:
    raw = bytes(range(256))
    member = TarballMember(name="binary.xml", content=raw)
    assert member.content == raw
    assert len(member.content) == 256
