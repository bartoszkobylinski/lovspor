"""Ledger boundaries from the nightly carry-over contract (#943).

These tests exercise archive data only; they never generate or run mutants.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from tests.unit.test_mutation_ledger import F, _meta, _write_meta, ledger


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (1, 1),
        (3, 3),
        (0, 0),
        (2, None),
        (5, None),
        (33, None),
        (34, None),
        (35, None),
        (36, 36),
        (37, None),
        (-24, -24),
        (24, 24),
        (152, 152),
        (255, 255),
        (-11, None),
        (-9, None),
        (99, None),
        (None, None),
    ],
)
def test_restore_carries_kills_survivors_and_timeouts_and_resets_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int | None, expected: int | None
) -> None:
    """docs/agentic-ci.md: killed, survived and timeout carry over; suspicious never does."""
    monkeypatch.chdir(tmp_path)
    _write_meta(tmp_path, "mod.py.meta", {F + "1": code})
    archive = tmp_path / "ledger.tar.gz"
    ledger.pack(archive)

    tally = ledger.unpack(archive)

    assert ledger.results_by_key(tmp_path) == {F + "1": expected}
    assert (tally.files, tally.kept, tally.reset, tally.dropped) == (
        1,
        int(expected is not None),
        int(code is not None and expected is None),
        0,
    )


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_archive_links_cannot_overwrite_a_file_outside_mutants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: bytes
) -> None:
    monkeypatch.chdir(tmp_path)
    outside = tmp_path / "outside.meta"
    original = json.dumps(_meta({F + "1": 0})).encode()
    outside.write_bytes(original)
    archive = tmp_path / "ledger.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo("mutants/link.meta")
        link.type = kind
        link.linkname = str(outside)
        tar.addfile(link)
        data = json.dumps(_meta({F + "2": 1})).encode()
        regular = tarfile.TarInfo("mutants/valid.meta")
        regular.size = len(data)
        tar.addfile(regular, io.BytesIO(data))

    tally = ledger.unpack(archive)

    assert outside.read_bytes() == original
    assert not (tmp_path / "mutants/link.meta").exists()
    assert ledger.results_by_key(tmp_path) == {F + "2": 1}
    assert (tally.files, tally.kept, tally.dropped) == (1, 1, 0)


def test_empty_checkpoint_round_trips_without_selecting_outside_the_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    archive = tmp_path / "ledger.tar.gz"
    assert ledger.pack(archive) == 0

    tally = ledger.unpack(archive)

    assert (tally.files, tally.kept, tally.reset, tally.dropped) == (0, 0, 0, 0)
    assert ledger.select([F + "*"], ledger.results_by_key(tmp_path)) == [F + "*"]


def test_failed_checkpoint_keeps_the_previous_readable_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pack promises readers never see a half-written replacement."""
    monkeypatch.chdir(tmp_path)
    _write_meta(tmp_path, "mod.py.meta", {F + "1": 1})
    archive = tmp_path / "ledger.tar.gz"
    ledger.pack(archive)
    previous = archive.read_bytes()

    def fail_add(*args: object, **kwargs: object) -> None:
        raise OSError("checkpoint interrupted")

    monkeypatch.setattr(tarfile.TarFile, "add", fail_add)
    with pytest.raises(OSError, match="checkpoint interrupted"):
        ledger.pack(archive)

    assert archive.read_bytes() == previous
    assert ledger.unpack(archive).kept == 1
