"""A second storage failure must not suppress the original failure's heartbeat.

Contract: sweep_failures.record_failed_run promises a failed record that cannot
land is reported on stderr, never raised in place of the original failure.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import typer
from pytest_httpx import HTTPXMock

from lovspor.errors import StorageUnavailableError
from lovspor.exclusive_workload import ExclusiveWorkloadHeldError
from lovspor.observatory.heartbeat import ENV_HEARTBEAT_URL
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.observatory.sweep_failures import end_unswept, failed_run, refuse_sweep
from lovspor.observatory.sweeps import sweeps_path


@pytest.mark.parametrize("entry", ["preflight", "storage", "deferred"])
@pytest.mark.parametrize("archive", ["missing", "record_blocked"])
def test_failed_record_write_preserves_original_reason_and_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
    capsys: pytest.CaptureFixture[str],
    entry: str,
    archive: str,
) -> None:
    root = ObservatoryRoot(tmp_path / "archive", [])
    if archive == "record_blocked":
        root.path.mkdir()
        # A real filesystem error, independent of user permissions.
        sweeps_path(root).mkdir()
    endpoint = "https://heartbeat.example.invalid/run/fail"
    monkeypatch.setenv(ENV_HEARTBEAT_URL, endpoint.removesuffix("/fail"))
    httpx_mock.add_response(url=endpoint, method="POST")
    started = datetime(2026, 10, 4, tzinfo=UTC)
    reason = {
        "preflight": "registry_missing",
        "storage": "storage_unavailable" if archive == "missing" else "storage_write_failed",
        "deferred": "deferred_exclusive_workload",
    }[entry]
    original = (
        ExclusiveWorkloadHeldError("observatory-sweep", None, tmp_path / "host.lock")
        if entry == "deferred"
        else StorageUnavailableError("original capture write failed")
    )

    with pytest.raises(typer.Exit) as caught:
        if entry == "preflight":
            refuse_sweep(root, failed_run(started, reason, "abc123"))
        else:
            end_unswept(root, started, original, "abc123")

    assert caught.value.exit_code == 1
    if entry != "preflight":
        assert caught.value.__cause__ is original
    stderr = capsys.readouterr().err
    assert f"reason: {reason}\n" in stderr
    assert "run record not written:" in stderr
    request = httpx_mock.get_request(url=endpoint, method="POST")
    assert request is not None
    body = json.loads(request.content)
    assert body["status"] == "failed"
    assert body["failure_reason"] == reason
    assert body["engine_commit"] == "abc123"
    assert body["started_at"] == "2026-10-04T00:00:00Z"
    if archive == "missing":
        assert list(tmp_path.iterdir()) == []
    else:
        assert list(root.path.iterdir()) == [sweeps_path(root)]
        assert list(sweeps_path(root).iterdir()) == []
