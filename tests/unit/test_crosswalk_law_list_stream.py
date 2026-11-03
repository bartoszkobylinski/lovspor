"""A law list read from a pipe is refused after the bounded prefix, without waiting for EOF."""

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from lovspor.crosswalk.commands import MAX_LAW_LIST_BYTES, read_law_ids
from lovspor.crosswalk.errors import CrosswalkError

_READER = """
import sys
from pathlib import Path
from lovspor.crosswalk.commands import read_law_ids
from lovspor.crosswalk.errors import CrosswalkError
try:
    read_law_ids(Path(sys.argv[1]))
except CrosswalkError as error:
    print(error, flush=True)
else:
    raise AssertionError('oversized law list accepted')
"""


def test_law_list_pipe_is_refused_after_limit_plus_one_byte(tmp_path: Path) -> None:
    fifo = tmp_path / "laws.txt"
    os.mkfifo(fifo)
    release, written = threading.Event(), threading.Event()

    def produce() -> None:
        try:
            with fifo.open("wb", buffering=0) as stream:
                stream.write(b"#" * (MAX_LAW_LIST_BYTES + 1))
                written.set()
                release.wait(45)
        except OSError:
            written.set()

    process = subprocess.Popen(
        [sys.executable, "-c", _READER, str(fifo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    writer = threading.Thread(target=produce, daemon=True)
    writer.start()
    try:
        assert written.wait(30)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert stdout.strip() == f"law list too large: {fifo}"
    finally:
        release.set()
        if process.poll() is None:
            process.kill()
        writer.join(5)


def test_law_list_rejects_oversized_open_pipe_without_another_byte(tmp_path: Path) -> None:
    fifo = tmp_path / "open-laws.txt"
    os.mkfifo(fifo)
    release = threading.Event()
    expired = threading.Event()
    failures: list[Exception] = []

    def produce() -> None:
        try:
            with fifo.open("wb", buffering=0) as stream:
                remaining = memoryview(b"#" * (MAX_LAW_LIST_BYTES + 1))
                while remaining:
                    remaining = remaining[stream.write(remaining) :]
                # Keep EOF and the next byte unavailable until the reader returns.
                # The deadline only releases a broken reader so the test can fail.
                if not release.wait(10):
                    expired.set()
        except OSError as error:
            failures.append(error)

    writer = threading.Thread(target=produce, daemon=True)
    writer.start()
    try:
        with pytest.raises(CrosswalkError, match="law list too large") as caught:
            read_law_ids(fifo)
        assert str(caught.value) == f"law list too large: {fifo}"
        assert not expired.is_set(), "reader waited for another byte or EOF"
    finally:
        release.set()
        writer.join(5)
    assert not writer.is_alive()
    assert not failures
