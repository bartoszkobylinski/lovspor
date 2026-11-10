"""``scripts/quality/run_suite.py``: the deep gate's unit step (issue #864).

Several agent sessions push at once on one Mac that also hosts the CI runners;
each push used to start a full single-core suite. The step now runs the push's
scope, in one of a few machine-wide slots, at nice 10.
"""

import fcntl
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
QUALITY = REPO / "scripts" / "quality"


def _load() -> ModuleType:
    if str(QUALITY) not in sys.path:
        sys.path.insert(0, str(QUALITY))
    spec = importlib.util.spec_from_file_location("run_suite_under_test", QUALITY / "run_suite.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rs = _load()
Scope = rs.Scope


def _hold(path: Path) -> object:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


class TestQos:
    def test_nice_lowers_the_priority_even_where_taskpolicy_exists(self) -> None:
        """Not `taskpolicy -b`: background QoS pins a process to the efficiency
        cores on Apple Silicon even when the performance cores are idle, which
        made a full suite run at 4:44 CPU in 30 min (#864). Nice yields to
        interactive work and still uses every core when the machine is free."""
        prefix = rs.qos_prefix(lambda name: f"/usr/bin/{name}")

        assert prefix == ["/usr/bin/nice", "-n", "10"]

    def test_without_either_the_suite_runs_plain(self) -> None:
        assert rs.qos_prefix(lambda name: None) == []


class TestArgv:
    def test_the_network_marker_is_two_argv_items(self) -> None:
        """Without them pytest would read `network` as a test path (issue #359)."""
        argv = rs.pytest_argv(Scope(full="x", tests=(), changed=0, python_changed=True))

        assert argv[:3] == [sys.executable, "-m", "pytest"]
        index = argv.index("-m", 3)
        assert argv[index + 1] == "not network"

    def test_a_full_scope_runs_the_unit_directory(self) -> None:
        argv = rs.pytest_argv(Scope(full="x", tests=(), changed=0, python_changed=True))

        assert argv[-1] == "tests/unit/"

    def test_a_narrow_scope_runs_only_its_files(self) -> None:
        scope = Scope(full=None, tests=("tests/unit/test_a.py",), changed=1, python_changed=True)

        argv = rs.pytest_argv(scope)

        assert argv[-1] == "tests/unit/test_a.py"
        assert "tests/unit/" not in argv


class TestRunPytest:
    def test_the_qos_prefix_runs_ahead_of_the_pytest_argv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing or reordered prefix would start pytest at full priority,
        exactly the contention issue #864 moved the suite out of."""
        monkeypatch.setattr(rs, "qos_prefix", lambda: ["/usr/bin/nice", "-n", "10"])
        seen: dict[str, object] = {}

        def fake_run(argv: list[str], cwd: Path, check: bool) -> object:
            seen["argv"] = argv
            seen["cwd"] = cwd
            seen["check"] = check

            class Result:
                returncode = 0

            return Result()

        monkeypatch.setattr(rs.subprocess, "run", fake_run)

        code = rs.run_pytest([sys.executable, "-m", "pytest", "tests/unit/test_a.py"])

        assert code == 0
        assert seen["argv"] == [
            "/usr/bin/nice",
            "-n",
            "10",
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_a.py",
        ]
        assert seen["cwd"] == rs.ROOT
        assert seen["check"] is False

    def test_the_exit_code_is_the_subprocess_returncode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(rs, "qos_prefix", list)

        class Result:
            returncode = 7

        monkeypatch.setattr(rs.subprocess, "run", lambda *a, **k: Result())

        assert rs.run_pytest(["anything"]) == 7


class TestScopeLine:
    def test_full(self) -> None:
        scope = Scope(full="pyproject.toml changed", tests=(), changed=3, python_changed=True)

        assert rs.scope_line(scope) == "unit scope: full (pyproject.toml changed)"

    def test_narrow(self) -> None:
        scope = Scope(full=None, tests=("a", "b"), changed=5, python_changed=True)

        assert rs.scope_line(scope) == "unit scope: 2 test files from 5 changed paths"

    def test_nothing(self) -> None:
        scope = Scope(full=None, tests=(), changed=1, python_changed=False)

        assert rs.scope_line(scope) == "unit scope: nothing to run (no Python change)"

    def test_a_python_change_no_test_reaches(self) -> None:
        scope = Scope(full=None, tests=(), changed=1, python_changed=True)

        assert rs.scope_line(scope) == (
            "unit scope: nothing to run (no unit test reaches the change)"
        )


class TestSlots:
    def test_a_free_slot_is_taken_at_once(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        handle = rs.acquire_slot(tmp_path, 2, poll=0.0, sleep=lambda _: None)

        assert handle is not None
        assert "waiting" not in capsys.readouterr().out

    def test_the_second_slot_is_taken_when_the_first_is_held(self, tmp_path: Path) -> None:
        _first = _hold(tmp_path / "slot-0.lock")

        handle = rs.acquire_slot(tmp_path, 2, poll=0.0, sleep=lambda _: None)

        assert Path(handle.name).name == "slot-1.lock"

    def test_with_every_slot_held_it_waits_once_and_takes_the_freed_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        held = [_hold(tmp_path / f"slot-{n}.lock") for n in range(2)]
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) == 3:
                held.pop(0).close()  # type: ignore[attr-defined]

        handle = rs.acquire_slot(tmp_path, 2, poll=0.01, sleep=sleep)

        assert sleeps == [0.01, 0.01, 0.01]
        assert Path(handle.name).name == "slot-0.lock"
        assert capsys.readouterr().out.count("waiting for a test slot (2 busy)") == 1

    def test_closing_the_handle_releases_the_slot(self, tmp_path: Path) -> None:
        rs.acquire_slot(tmp_path, 1, poll=0.0, sleep=lambda _: None).close()

        assert _hold(tmp_path / "slot-0.lock") is not None

    @pytest.mark.parametrize(("value", "expected"), [(None, 2), ("3", 3), ("0", 1), ("many", 2)])
    def test_the_slot_count_comes_from_the_environment(
        self, value: str | None, expected: int
    ) -> None:
        env = {} if value is None else {"LOVSPOR_SUITE_SLOTS": value}

        assert rs.slot_count(env) == expected

    def test_the_slots_live_in_the_users_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", "/home/someone")

        assert rs.slot_dir() == Path("/home/someone/.cache/lovspor/suite-slots")


class TestMain:
    def test_nothing_to_run_exits_zero_without_a_slot(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        scope = Scope(full=None, tests=(), changed=1, python_changed=False)
        monkeypatch.setattr(rs.unit_scope, "scope_for_push", lambda root, env: scope)
        monkeypatch.setattr(rs, "acquire_slot", pytest.fail)

        assert rs.main({}) == 0
        assert capsys.readouterr().out.strip() == "unit scope: nothing to run (no Python change)"

    @pytest.mark.parametrize(("pytest_code", "expected"), [(0, 0), (1, 1), (5, 0)])
    def test_the_exit_code_is_pytests(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        pytest_code: int,
        expected: int,
    ) -> None:
        """Exit 5 (nothing collected after the marker filter) is not a failure of a scope."""
        scope = Scope(full=None, tests=("tests/unit/test_a.py",), changed=1, python_changed=True)
        seen: list[list[str]] = []
        monkeypatch.setattr(rs.unit_scope, "scope_for_push", lambda root, env: scope)
        monkeypatch.setattr(rs, "slot_dir", lambda: tmp_path)
        monkeypatch.setattr(rs, "run_pytest", lambda argv: seen.append(argv) or pytest_code)

        assert rs.main({}) == expected
        assert seen[0][-1] == "tests/unit/test_a.py"

    def test_a_failing_full_run_keeps_exit_five(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A full run that collects nothing is broken, not empty."""
        scope = Scope(full="x", tests=(), changed=0, python_changed=True)
        monkeypatch.setattr(rs.unit_scope, "scope_for_push", lambda root, env: scope)
        monkeypatch.setattr(rs, "slot_dir", lambda: tmp_path)
        monkeypatch.setattr(rs, "run_pytest", lambda argv: 5)

        assert rs.main({}) == 5
