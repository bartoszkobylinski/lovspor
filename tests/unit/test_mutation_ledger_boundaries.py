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


def test_repeated_checkpoints_preserve_results_and_metadata_but_retry_untrusted_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nightly carry contract applies across checkpoints, including result timings."""
    monkeypatch.chdir(tmp_path)
    codes = {F + "1": 0, F + "2": 36, F + "3": 1, F + "4": 35, F + "5": None}
    meta = _meta(codes)
    meta["durations_by_key"] = {key: index + 0.25 for index, key in enumerate(codes)}
    meta["estimated_durations_by_key"] = dict.fromkeys(codes, 2.5)
    path = _write_meta(tmp_path, "src/lovspor/mod.py.meta", codes)
    path.write_text(json.dumps(meta), encoding="utf-8")
    expected = {**meta, "exit_code_by_key": {**codes, F + "4": None}}
    archive = tmp_path / "ledger.tar.gz"

    for night in range(2):
        assert ledger.pack(archive) == 1
        path.unlink()
        tally = ledger.unpack(archive)

        assert json.loads(path.read_text(encoding="utf-8")) == expected
        assert (tally.files, tally.kept, tally.reset, tally.dropped) == (1, 3, 1 - night, 0)
        results = ledger.results_by_key(tmp_path)
        assert ledger.select([F + "*"], results) == [F + "4", F + "5"]
        assert ledger.select(list(codes), results) == [F + "4", F + "5"]


@pytest.mark.parametrize("code", [0, 36])
def test_fully_carried_survivors_or_timeouts_select_one_result_for_reporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """With no pending work, select must still choose one in-scope result for the tally."""
    monkeypatch.chdir(tmp_path)
    _write_meta(tmp_path, "mod.py.meta", {F + "2": code, F + "1": code})
    archive = tmp_path / "ledger.tar.gz"
    ledger.pack(archive)

    tally = ledger.unpack(archive)

    assert (tally.kept, tally.reset) == (2, 0)
    results = ledger.results_by_key(tmp_path)
    assert ledger.select([F + "*"], results) == [F + "1"]
    assert ledger.select([F + "2"], results) == [F + "2"]
