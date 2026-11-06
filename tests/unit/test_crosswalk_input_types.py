"""Crosswalk inputs: a FIFO with no writer is refused after a bounded wait, never hung on (#817).

A pipe that has a writer stays a supported input (``--laws <(...)``, ``... | --laws
/dev/stdin``, a named FIFO whose writer opens it; see also
``test_crosswalk_law_list_stream.py``). Every reader here runs under a deadline, so
a reader that blocks fails the test instead of hanging it.
"""

import contextlib
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from lovspor.crosswalk import commands
from lovspor.crosswalk.commands import read_bounded, read_law_ids, read_previous
from lovspor.crosswalk.errors import CrosswalkError

_DEADLINE = 10.0
_SHORT_WAIT = 0.2


def _fifo(path: Path) -> Path:
    os.mkfifo(path)
    return path


def _release(fifo: Path) -> None:
    """Free a reader stuck in open(): a non-blocking writer open succeeds once it waits."""
    with contextlib.suppress(OSError):
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))


def _outcome(call: Callable[[Path], object], path: Path, fifo: Path) -> object:
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(call(path))
        except CrosswalkError as error:
            outcome.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(_DEADLINE)
    if worker.is_alive():
        _release(fifo)
        worker.join(_DEADLINE)
        pytest.fail(f"reader blocked on a FIFO: {path}")
    (result,) = outcome
    return result


def _refusal(call: Callable[[Path], object], path: Path, fifo: Path) -> str:
    result = _outcome(call, path, fifo)
    assert isinstance(result, CrosswalkError), result
    return str(result)


def _bounded(path: Path) -> bytes:
    return read_bounded(path, 64, "input")


@pytest.fixture
def short_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "FIFO_WRITER_WAIT_SECONDS", _SHORT_WAIT)


@pytest.mark.usefixtures("short_wait")
@pytest.mark.parametrize("via_symlink", [False, True])
def test_a_fifo_without_a_writer_is_refused_after_the_wait(
    tmp_path: Path, via_symlink: bool
) -> None:
    fifo = _fifo(tmp_path / "pipe")
    path = fifo
    if via_symlink:
        path = tmp_path / "link"
        path.symlink_to(fifo)
    started = time.monotonic()
    assert _refusal(_bounded, path, fifo) == f"input is a pipe with no writer: {path}"
    elapsed = time.monotonic() - started
    assert _SHORT_WAIT <= elapsed < 1.0


@pytest.mark.usefixtures("short_wait")
@pytest.mark.parametrize(
    ("call", "what"),
    [(read_law_ids, "law list"), (read_previous, "previous release")],
)
def test_every_caller_refuses_a_fifo_without_a_writer(
    tmp_path: Path, call: Callable[[Path], object], what: str
) -> None:
    fifo = _fifo(tmp_path / "pipe")
    assert _refusal(call, fifo, fifo) == f"{what} is a pipe with no writer: {fifo}"


def _late_writer(fifo: Path, delay: float, pause: float) -> threading.Thread:
    def produce() -> None:
        time.sleep(delay)
        with fifo.open("wb", buffering=0) as stream:
            time.sleep(pause)
            stream.write(b"# laws\nLOV-1999-01-01-1\n")

    writer = threading.Thread(target=produce, daemon=True)
    writer.start()
    return writer


@pytest.mark.parametrize(
    ("delay", "pause"),
    [(0.3, 0.0), (0.0, 0.3)],
    ids=["writer-opens-after-the-reader", "writer-open-without-data-yet"],
)
def test_a_pipe_whose_writer_comes_within_the_wait_is_read(
    tmp_path: Path, delay: float, pause: float
) -> None:
    fifo = _fifo(tmp_path / "laws")
    writer = _late_writer(fifo, delay, pause)
    assert _outcome(read_law_ids, fifo, fifo) == ["LOV-1999-01-01-1"]
    writer.join(_DEADLINE)
    assert not writer.is_alive()


def test_an_anonymous_pipe_as_in_process_substitution_is_read() -> None:
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, b"LOV-1999-01-01-1\n")
        os.close(write_end)
        assert read_law_ids(Path(f"/dev/fd/{read_end}")) == ["LOV-1999-01-01-1"]
    finally:
        os.close(read_end)


def test_fifo_without_writer_is_refused_at_the_exact_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fifo = _fifo(tmp_path / "pipe")
    # Advance the clock exactly to the documented writer-wait boundary.
    ticks = iter((100.0, 100.0 + commands.FIFO_WRITER_WAIT_SECONDS, 103.0))
    sleeps: list[float] = []
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", sleeps.append)

    with pytest.raises(CrosswalkError) as caught:
        read_bounded(fifo, 64, "input")

    assert str(caught.value) == f"input is a pipe with no writer: {fifo}"
    assert sleeps == [], "the writer wait must not continue once its deadline is reached"


def test_nonblocking_probe_consumes_only_the_first_byte() -> None:
    """The one-byte probe leaves the rest of the input available to its reader."""
    read_end, write_end = os.pipe()
    try:
        os.set_blocking(read_end, False)
        os.write(write_end, b"abc")
        assert commands._read_nonblocking(read_end) == b"a"
        assert os.read(read_end, 2) == b"bc"
    finally:
        os.close(write_end)
        os.close(read_end)


@pytest.mark.parametrize("limit", [3, 4])
def test_an_open_writer_can_deliver_fragmented_data_after_the_wait_budget(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    """The documented deadline applies to finding a writer, not receiving its data."""
    read_end, write_end = os.pipe()

    def produce() -> None:
        try:
            time.sleep(0.05)
            os.write(write_end, b"ab")
            time.sleep(0.05)
            os.write(write_end, b"cd")
        finally:
            os.close(write_end)

    monkeypatch.setattr(commands, "FIFO_WRITER_WAIT_SECONDS", 0.0)
    writer = threading.Thread(target=produce, daemon=True)
    writer.start()
    try:
        path = Path(f"/dev/fd/{read_end}")
        result = _outcome(lambda path: read_bounded(path, limit, "input"), path, path)
        if limit == 4:
            assert result == b"abcd"
        else:
            assert isinstance(result, CrosswalkError)
            assert str(result) == f"input too large: {path}"
    finally:
        writer.join(_DEADLINE)
        os.close(read_end)
    assert not writer.is_alive()


def test_a_pipe_byte_counts_toward_the_limit() -> None:
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, b"abcde")
        os.close(write_end)
        path = Path(f"/dev/fd/{read_end}")
        with pytest.raises(CrosswalkError) as caught:
            read_bounded(path, 4, "input")
        assert str(caught.value) == f"input too large: {path}"
    finally:
        os.close(read_end)


def test_an_empty_regular_file_is_still_read(tmp_path: Path) -> None:
    path = tmp_path / "empty"
    path.write_bytes(b"")
    assert read_bounded(path, 4, "input") == b""


@pytest.mark.parametrize("special", ["directory", "/dev/null"])
def test_a_path_that_is_neither_a_file_nor_a_pipe_is_refused(tmp_path: Path, special: str) -> None:
    path = Path(special)
    if special == "directory":
        path = tmp_path / "dir"
        path.mkdir()
    with pytest.raises(CrosswalkError) as caught:
        read_bounded(path, 4, "input")
    assert str(caught.value) == f"input is not a regular file or pipe: {path}"


@pytest.mark.parametrize("via_symlink", [False, True], ids=["fifo", "symlink-to-fifo"])
def test_the_cli_refuses_a_fifo_input_without_hanging(tmp_path: Path, via_symlink: bool) -> None:
    fifo = _fifo(tmp_path / "crosswalk.jsonl")
    path = fifo
    if via_symlink:
        path = tmp_path / "input-link.jsonl"
        path.symlink_to(fifo)
    code = "from lovspor.cli import app; app()"
    argv = [sys.executable, "-c", code, "ops", "crosswalk", "check", "--input", str(path)]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    finally:
        _release(fifo)
    assert done.returncode == 1
    assert f"refused: crosswalk file is a pipe with no writer: {path}" in done.stderr


@pytest.mark.parametrize(
    ("limit", "overflow"), [(1, False), (4, False), (0, True), (1, True), (4, True)]
)
def test_pipe_size_boundary_matches_regular_files(
    tmp_path: Path, limit: int, overflow: bool
) -> None:
    payload = b"x" * (limit + overflow)
    regular = tmp_path / "input"
    regular.write_bytes(payload)
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, payload)
        os.close(write_end)
        write_end = -1
        pipe = Path(f"/dev/fd/{read_end}")
        for path in (regular, pipe):
            if overflow:
                with pytest.raises(CrosswalkError) as caught:
                    read_bounded(path, limit, "input")
                assert str(caught.value) == f"input too large: {path}"
            else:
                assert read_bounded(path, limit, "input") == payload
    finally:
        os.close(read_end)
        if write_end != -1:
            os.close(write_end)


@pytest.mark.parametrize("target_kind", ["regular", "directory", "device"])
def test_input_symlink_is_judged_by_its_target(tmp_path: Path, target_kind: str) -> None:
    target = tmp_path / "target"
    if target_kind == "regular":
        target.write_bytes(b"abcd")
    elif target_kind == "directory":
        target.mkdir()
    else:
        target = Path("/dev/null")
    link = tmp_path / "input-link"
    link.symlink_to(target)
    if target_kind == "regular":
        assert read_bounded(link, 4, "input") == b"abcd"
    else:
        with pytest.raises(CrosswalkError) as caught:
            read_bounded(link, 4, "input")
        assert str(caught.value) == f"input is not a regular file or pipe: {link}"


def test_a_terminal_is_refused_without_waiting_for_input() -> None:
    master, slave = os.openpty()
    try:
        path = Path(os.ttyname(slave))
        with pytest.raises(CrosswalkError) as caught:
            read_bounded(path, 4, "input")
        assert str(caught.value) == f"input is not a regular file or pipe: {path}"
    finally:
        os.close(slave)
        os.close(master)


@pytest.mark.usefixtures("short_wait")
def test_fifo_timeout_closes_the_input_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fifo = _fifo(tmp_path / "pipe")
    opened: list[int] = []
    original = commands._open_input

    def track_open(path: Path, what: str) -> tuple[int, bool]:
        descriptor, pipe = original(path, what)
        opened.append(descriptor)
        return descriptor, pipe

    monkeypatch.setattr(commands, "_open_input", track_open)
    assert _refusal(_bounded, fifo, fifo) == f"input is a pipe with no writer: {fifo}"
    (descriptor,) = opened
    with pytest.raises(OSError):
        os.fstat(descriptor)
