"""The DOCX fixtures are byte-identical whenever they are built.

Their bytes are used as parametrize ids; under pytest-xdist every worker collects on its
own, so a time-dependent archive gave workers different ids and aborted the run.
"""

from __future__ import annotations

import io
import time
import zipfile

import pytest

from tests.unit.promotion_fixtures import docx_with_document, minimal_docx


def _entry_times(archive_bytes: bytes) -> list[tuple[int, ...]]:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        return [info.date_time for info in archive.infolist()]


def test_minimal_docx_entries_carry_the_fixed_epoch() -> None:
    assert _entry_times(minimal_docx()) == [(1980, 1, 1, 0, 0, 0)] * 2


def test_docx_with_document_entry_carries_the_fixed_epoch() -> None:
    assert _entry_times(docx_with_document(b"<x/>")) == [(1980, 1, 1, 0, 0, 0)]


def test_bytes_do_not_depend_on_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    first = minimal_docx()
    # A different wall clock must not change a single byte.
    monkeypatch.setattr(time, "localtime", lambda *_: (2001, 2, 3, 4, 5, 6, 0, 0, 0))
    assert minimal_docx() == first
    assert docx_with_document(b"<x/>") == docx_with_document(b"<x/>")
