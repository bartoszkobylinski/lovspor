"""Behavioral regressions for the storage and failed-sweep survivor diffs."""

import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import typer

from lovspor.errors import StorageBoundaryError, StorageUnavailableError
from lovspor.observatory.freshness_index import indexed_capture_state
from lovspor.observatory.lf_ledger import _write_cursor
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, RetrievalProvenance
from lovspor.observatory.storage import ObservatoryRoot, ensure_below_root
from lovspor.observatory.sweep_failures import end_unswept, failed_run, refuse_sweep
from lovspor.observatory.sweeps import append_sweep_run, read_sweep_runs, sweeps_path

START = datetime(2026, 10, 4, tzinfo=UTC)


def artifact(payload: bytes) -> ArtifactObservation:
    return ArtifactObservation(
        authority_id="9999",
        url="https://example.invalid/f",
        observed_at=START,
        provenance=RetrievalProvenance(
            adapter="generic-html",
            channel="http",
            discovery_method="sitemap",
            user_agent="lovspor-observatory/0.1",
            rate_limit_seconds=2.0,
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
        content_type="text/html",
        http_status=200,
    )


@pytest.mark.parametrize("writer", ["observation", "sweep", "blob"])
def test_storage_failure_names_target_and_os_error(tmp_path: Path, writer: str) -> None:
    root = ObservatoryRoot(tmp_path, [])
    log = ObservationLog(root)
    payload = b"evidence"
    target = {
        "observation": log.log_path,
        "sweep": sweeps_path(root),
        "blob": log.blob_path(hashlib.sha256(payload).hexdigest()),
    }[writer]
    if writer == "blob":
        # A file where shard directories belong forces a real filesystem error.
        log.blobs_dir.write_bytes(b"blocked")
    else:
        target.mkdir()
    with pytest.raises(StorageUnavailableError) as caught:
        if writer == "observation":
            log.append(artifact(payload))
        elif writer == "sweep":
            append_sweep_run(root, failed_run(START, "storage_write_failed", "abc"))
        else:
            log.append_artifact(artifact(payload), payload)
    cause = caught.value.__cause__
    assert isinstance(cause, OSError)
    assert str(caught.value) == f"cannot write {target} under {root.path}: {cause}"
    assert root.path.is_dir()


def test_outside_directory_error_names_both_paths(tmp_path: Path) -> None:
    directory = tmp_path.parent / "outside"
    with pytest.raises(StorageBoundaryError) as caught:
        ensure_below_root(ObservatoryRoot(tmp_path, []), directory)
    assert str(caught.value) == f"{directory} is outside observatory root {tmp_path}"


_VANISH_AT: list[tuple[Path, Path]] = []
"""The (mkdir target, root) the audit hook removes the root at; empty = disarmed."""


def _vanish_at_mkdir(event: str, args: tuple[object, ...]) -> None:
    if event == "os.mkdir" and _VANISH_AT:
        target, root = _VANISH_AT[0]
        if os.fspath(args[0]) == str(target):  # type: ignore[arg-type]
            _VANISH_AT.clear()
            shutil.rmtree(root)


_HOOK: list[bool] = []


@pytest.fixture
def vanish_at_mkdir() -> Iterator[list[tuple[Path, Path]]]:
    """Remove the real root at a chosen ``os.mkdir``, in this process.

    An audit hook cannot be removed once added, so one hook is installed for
    the session and armed per test. In-process on purpose: mutmut swaps code
    only in the process it runs, so a child interpreter tested the unmutated
    module and killed nothing (PR #544 survivors).
    """
    if not _HOOK:
        sys.addaudithook(_vanish_at_mkdir)
        _HOOK.append(True)
    try:
        yield _VANISH_AT
    finally:
        _VANISH_AT.clear()


@pytest.mark.parametrize("depth", [1, 2])
def test_root_disappearing_at_mkdir_preserves_target_and_cause(
    tmp_path: Path, depth: int, vanish_at_mkdir: list[tuple[Path, Path]]
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    checked = ObservatoryRoot(root, [])
    target = root.joinpath(*["blobs", "shard"][:depth])
    vanish_at_mkdir.append((target, root))

    with pytest.raises(StorageUnavailableError) as caught:
        ensure_below_root(checked, target)

    cause = caught.value.__cause__
    assert isinstance(cause, FileNotFoundError)
    assert str(caught.value) == f"observatory root {root} is gone; {target} not written: {cause}"
    assert not root.exists()


@pytest.mark.parametrize("writer", ["index", "cursor"])
def test_sidecar_write_never_recreates_missing_root(tmp_path: Path, writer: str) -> None:
    root = tmp_path / "archive"
    log = ObservationLog(ObservatoryRoot(root, []))
    with pytest.raises(FileNotFoundError):
        if writer == "index":
            indexed_capture_state(log)
        else:
            _write_cursor(log, 0)
    assert not root.exists()


def test_refused_sweep_names_expected_root_on_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOVSPOR_OBSERVATORY_HEARTBEAT_URL", raising=False)
    root = ObservatoryRoot(tmp_path, [])
    with pytest.raises(typer.Exit) as caught:
        refuse_sweep(root, failed_run(START, "storage_write_failed", "abc"))
    assert caught.value.exit_code == 1
    output = capsys.readouterr()
    assert f"expected: {root.path}\n" in output.err
    assert f"expected: {root.path}" not in output.out
    assert read_sweep_runs(sweeps_path(root))[0].failure_reason == "storage_write_failed"


def test_unswept_record_preserves_engine_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOVSPOR_OBSERVATORY_HEARTBEAT_URL", raising=False)
    root = ObservatoryRoot(tmp_path, [])
    with pytest.raises(typer.Exit):
        end_unswept(root, START, StorageUnavailableError("disk refused write"), "a1b2c3")
    [run] = read_sweep_runs(sweeps_path(root))
    assert run.engine_commit == "a1b2c3"
    assert run.failure_reason == "storage_write_failed"


def test_blob_write_preserves_payload_and_record(tmp_path: Path) -> None:
    log = ObservationLog(ObservatoryRoot(tmp_path, []))
    payload = b"raw evidence\x00\xff"
    record = artifact(payload)
    log.append_artifact(record, payload)
    assert log.blob_path(record.sha256).read_bytes() == payload
    assert list(log.records()) == [record]


@pytest.mark.parametrize("writer", ["blob", "index", "cursor"])
def test_archive_vanishing_at_staging_is_not_recreated(tmp_path: Path, writer: str) -> None:
    # Delete actual directories at the OS open boundary, after shard setup.
    # The subprocess confines the audit hook to this single write.
    script = """
import shutil
import sys
from pathlib import Path
from lovspor.errors import StorageUnavailableError
from lovspor.observatory.freshness_index import indexed_capture_state
from lovspor.observatory.lf_ledger import _write_cursor
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.storage import ObservatoryRoot
root = Path(sys.argv[1]) / "archive"
root.mkdir()
log = ObservationLog(ObservatoryRoot(root, []))
writer = sys.argv[2]
def vanish(event, args):
    if event == "open" and isinstance(args[0], str):
        path = Path(args[0])
        if root in path.parents and path.name.endswith(".tmp"):
            shutil.rmtree(root)
sys.addaudithook(vanish)
try:
    if writer == "blob":
        log._store_blob(log.blob_path("a" * 64), b"evidence")
    elif writer == "index":
        indexed_capture_state(log)
    else:
        _write_cursor(log, 0)
except StorageUnavailableError as exc:
    assert writer == "blob"
    assert isinstance(exc.__cause__, FileNotFoundError)
except FileNotFoundError:
    assert writer != "blob"
else:
    raise AssertionError("write succeeded after archive vanished")
assert not root.exists()
assert list(root.parent.iterdir()) == []
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), writer],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
