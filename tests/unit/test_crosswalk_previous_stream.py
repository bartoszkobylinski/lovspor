"""A previous-release stream must be refused after the bounded prefix is read."""

import os
import subprocess
import sys
import threading
from pathlib import Path


def test_previous_release_refuses_oversize_without_waiting_for_eof(tmp_path: Path) -> None:
    # A producer may leave its output pipe open. Refusal must need only limit+1
    # bytes; waiting for EOF (or even one extra byte) can hang the command.
    fifo = tmp_path / "previous.jsonl"
    os.mkfifo(fifo)
    release = threading.Event()
    written = threading.Event()
    errors: list[BaseException] = []

    def produce() -> None:
        try:
            with fifo.open("wb", buffering=0) as stream:
                chunk = b"x" * (1024 * 1024)
                for _ in range(512):
                    stream.write(chunk)
                stream.write(b"x")
                written.set()
                release.wait(45)
        except (BrokenPipeError, OSError) as error:
            errors.append(error)
            written.set()

    script = """
import sys
from pathlib import Path
from lovspor.crosswalk.commands import read_previous
from lovspor.crosswalk.errors import CrosswalkError
try:
    read_previous(Path(sys.argv[1]))
except CrosswalkError as error:
    print(error, flush=True)
else:
    raise AssertionError('oversized previous release accepted')
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(fifo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    writer = threading.Thread(target=produce, daemon=True)
    writer.start()
    try:
        assert written.wait(30), "reader did not consume the bounded prefix"
        assert not errors
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert stdout.strip() == f"previous release too large: {fifo}"
    finally:
        release.set()
        if process.poll() is None:
            process.kill()
        process.communicate()
        writer.join(timeout=5)
