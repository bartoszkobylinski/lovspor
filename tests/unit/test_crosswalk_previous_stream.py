"""A previous-release stream must be refused after the bounded prefix is read."""

import os
import threading
from pathlib import Path

import pytest

from lovspor.crosswalk.commands import read_previous
from lovspor.crosswalk.errors import CrosswalkError


def test_previous_release_refuses_oversize_without_waiting_for_eof(tmp_path: Path) -> None:
    # Keep the reader in this process so mutation testing exercises the mutated
    # function as well as recording this test in its selection tracing.
    previous = tmp_path / "complete.jsonl"
    previous.write_bytes(b"{}\n")
    assert read_previous(previous) == b"{}\n"

    fifo = tmp_path / "previous.jsonl"
    os.mkfifo(fifo)
    release = threading.Event()
    waited_for_eof = threading.Event()
    errors: list[BaseException] = []

    def produce() -> None:
        try:
            with fifo.open("wb", buffering=0) as stream:
                chunk = b"x" * (1024 * 1024)
                for _ in range(512):
                    # Raw writes may be short, so send the entire chunk.
                    remaining = memoryview(chunk)
                    while remaining:
                        remaining = remaining[stream.write(remaining) :]
                stream.write(b"x")
                # Exactly limit+1 bytes are available. An unbounded read, or a
                # read of limit+2, cannot finish until this writer closes.
                if not release.wait(5):
                    waited_for_eof.set()
        except OSError as error:
            errors.append(error)

    writer = threading.Thread(target=produce, daemon=True)
    writer.start()
    try:
        with pytest.raises(CrosswalkError) as caught:
            read_previous(fifo)
        assert str(caught.value) == f"previous release too large: {fifo}"
        assert not waited_for_eof.is_set(), "reader waited for EOF beyond the bounded prefix"
    finally:
        release.set()
        writer.join(timeout=10)
    assert not writer.is_alive()
    assert not errors
