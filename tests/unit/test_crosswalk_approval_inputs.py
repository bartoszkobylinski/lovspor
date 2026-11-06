"""Approval proof inputs are regular files, refused unread otherwise (review of #799).

Opening a FIFO for reading waits for a writer, and reading it waits for data, so
a FIFO named as `approval.json.sig` (or a symlink to one) could hang the gate.
Every test here runs the call in a daemon thread with a deadline: a regression
fails the test instead of hanging the suite.
"""

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from lovspor.crosswalk import approval as approvals
from lovspor.crosswalk.commands import manifest_impl, read_signed_approval
from lovspor.crosswalk.errors import CrosswalkError
from tests.unit.crosswalk_signing import approval_body, approval_dir, new_signer, trust

_DEADLINE = 10.0


def _outcome(call: Callable[[], object]) -> BaseException | None:
    """What ``call`` raised, or None; fails if it has not returned by the deadline."""
    caught: list[BaseException | None] = []

    def run() -> None:
        try:
            call()
            caught.append(None)
        except BaseException as exc:
            caught.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(_DEADLINE)
    if worker.is_alive():
        pytest.fail(f"call still blocked after {_DEADLINE}s")
    return caught[0]


def _fifo(path: Path) -> Path:
    os.mkfifo(path)
    return path


def _link(path: Path, target: Path) -> Path:
    path.symlink_to(target)
    return path


def _approval(directory: Path) -> Path:
    approval = directory / approvals.APPROVAL_FILE
    approval.write_text(json.dumps(approval_body(b"")), encoding="utf-8")
    return approval


@pytest.mark.parametrize("kind", ["fifo", "symlink-to-fifo", "directory", "device"])
def test_a_signature_that_is_not_a_regular_file_is_refused_unread(
    tmp_path: Path, kind: str
) -> None:
    sig = tmp_path / "approval.json.sig"
    if kind == "fifo":
        _fifo(sig)
    elif kind == "symlink-to-fifo":
        _link(sig, _fifo(tmp_path / "pipe"))
    elif kind == "directory":
        sig.mkdir()
    else:
        _link(sig, Path(os.devnull))
    raised = _outcome(lambda: approvals.signed_approval(b"{}", None, sig))  # type: ignore[arg-type]
    assert isinstance(raised, CrosswalkError)
    assert str(raised) == f"approval signature is not a regular file: {sig}"


@pytest.mark.parametrize("name", [approvals.APPROVAL_FILE, approvals.APPROVED_INPUT_FILE])
def test_a_fifo_in_the_proof_directory_is_refused_without_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """The approved input is read only after a trusted signature, so sign a real one."""
    signer = new_signer(tmp_path / "keys")
    trust(monkeypatch, signer)
    proof = approval_dir(tmp_path / "proof", b"", signer)
    (proof / name).unlink()
    _fifo(proof / name)
    data = tmp_path / "rc.jsonl"
    data.write_bytes(b"")
    cache = tmp_path / "cache"
    cache.mkdir()
    raised = _outcome(lambda: manifest_impl(data, tmp_path / "m.json", cache, proof))
    assert isinstance(raised, CrosswalkError)
    assert str(raised).endswith(f"is not a regular file: {proof / name}")


def test_a_cli_signature_fifo_is_refused_without_hanging(tmp_path: Path) -> None:
    approval = _approval(tmp_path)
    _fifo(approvals.signature_path(approval))
    raised = _outcome(lambda: read_signed_approval(approval))
    assert isinstance(raised, CrosswalkError)
    assert "not a regular file" in str(raised)


def test_a_symlink_to_a_regular_signature_is_still_read(tmp_path: Path) -> None:
    real = tmp_path / "real.sig"
    real.write_bytes(b"x" * 10)
    link = _link(tmp_path / "approval.json.sig", real)
    assert approvals.read_regular(link, 64, "approval signature") == b"x" * 10


@pytest.mark.parametrize(("size", "refused"), [(64, False), (65, True)])
def test_read_regular_bounds_a_regular_file(tmp_path: Path, size: int, refused: bool) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x" * size)
    if refused:
        with pytest.raises(CrosswalkError, match=f"^thing too large: {path}$"):
            approvals.read_regular(path, 64, "thing")
    else:
        assert approvals.read_regular(path, 64, "thing") == b"x" * 64


def test_read_regular_reports_a_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    with pytest.raises(CrosswalkError, match=f"^thing unreadable: {missing}$"):
        approvals.read_regular(missing, 64, "thing")


def _open_descriptors() -> int:
    return len(list(Path("/dev/fd").iterdir()))


def test_refusing_a_non_regular_file_leaks_no_descriptor(tmp_path: Path) -> None:
    pipe = _fifo(tmp_path / "pipe")
    before = _open_descriptors()
    for _ in range(20):
        with pytest.raises(CrosswalkError, match="not a regular file"):
            approvals.read_regular(pipe, 64, "thing")
    assert _open_descriptors() == before
