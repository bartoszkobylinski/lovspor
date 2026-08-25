"""Tests for the host-level mutual exclusion shared by long workloads."""

import json
from pathlib import Path

import pytest

from lovspor.exclusive_workload import (
    ENV_LOCK_PATH,
    ExclusiveWorkloadHeldError,
    default_lock_path,
    exclusive_workload,
    read_holder,
)


def test_lock_path_precedence_and_xdg_fallback(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.lock"
    xdg = tmp_path / "state"

    assert default_lock_path({ENV_LOCK_PATH: str(explicit), "XDG_STATE_HOME": str(xdg)}) == explicit
    assert default_lock_path({"XDG_STATE_HOME": str(xdg)}) == (
        xdg / "lovspor" / "exclusive-workload.lock"
    )


def test_holder_record_exists_only_while_lock_is_held(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "workload.lock"

    with exclusive_workload("first", path) as held:
        assert read_holder(path) == held
        assert held.owner == "first"
        assert held.pid > 0
        assert held.since.endswith("+00:00")

    assert path.read_text(encoding="utf-8") == ""
    assert read_holder(path) is None


def test_contender_refuses_immediately_and_names_current_holder(tmp_path: Path) -> None:
    path = tmp_path / "workload.lock"

    with (
        exclusive_workload("observatory-sweep", path) as held,
        pytest.raises(ExclusiveWorkloadHeldError) as raised,
        exclusive_workload("llhb-run-arm", path),
    ):
        pytest.fail("a second workload entered the critical section")

    error = raised.value
    assert error.wanted_by == "llhb-run-arm"
    assert error.holder == held
    assert error.path == path
    assert "neither workload waits" in str(error)


@pytest.mark.parametrize("content", ["", "not-json", "[]", '{"owner":"x"}'])
def test_unreadable_advisory_record_never_fabricates_a_holder(tmp_path: Path, content: str) -> None:
    path = tmp_path / "workload.lock"
    path.write_text(content, encoding="utf-8")

    assert read_holder(path) is None


def test_lock_is_released_and_record_cleared_when_workload_raises(tmp_path: Path) -> None:
    path = tmp_path / "workload.lock"

    with pytest.raises(RuntimeError, match="boom"), exclusive_workload("failed", path):
        raise RuntimeError("boom")

    with exclusive_workload("replacement", path):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["owner"] == "replacement"
