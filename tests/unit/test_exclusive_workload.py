"""Tests for the host-level mutual exclusion shared by long workloads."""

import fcntl
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


def test_lock_path_falls_back_to_the_xdg_state_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No override, no XDG variable: the lock lives under ``~/.local/state``,
    the state directory — not config, not cache — like the docstring says."""
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_lock_path({}) == (
        tmp_path / ".local" / "state" / "lovspor" / "exclusive-workload.lock"
    )


def test_a_lock_held_without_a_record_is_refused_as_unidentified(tmp_path: Path) -> None:
    """The flock is the decision; the record only names the holder. A foreign
    holder that wrote nothing is still a holder — refused, and said so."""
    path = tmp_path / "workload.lock"

    with path.open("a+", encoding="utf-8") as foreign:
        fcntl.flock(foreign.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ExclusiveWorkloadHeldError) as raised, exclusive_workload("llhb", path):
            pytest.fail("entered the critical section against a foreign holder")

    assert raised.value.holder is None
    assert "held by an unidentified process" in str(raised.value)


def test_holder_record_round_trips_a_non_ascii_owner(tmp_path: Path) -> None:
    path = tmp_path / "workload.lock"

    with exclusive_workload("observatory-sweep-\u00f8", path) as held:
        assert read_holder(path) == held
        assert read_holder(path) is not None
        assert read_holder(path).owner == "observatory-sweep-\u00f8"
