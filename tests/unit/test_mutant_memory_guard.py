"""Regression tests for the nightly mutant memory guard (#971)."""

import importlib.util
import signal
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ci/mutant_memory_guard.py"
spec = importlib.util.spec_from_file_location("mutant_memory_guard", SCRIPT)
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def process(proc, pid, cmdline, status):
    directory = proc / str(pid)
    directory.mkdir()
    (directory / "cmdline").write_bytes(cmdline)
    (directory / "status").write_text(status)


def test_scan_identifies_only_mutants_and_counts_resident_and_swap(tmp_path):
    process(tmp_path, 1, b"mutmut run\0", "VmRSS: 100 kB\nVmSwap: 20 kB\n")
    process(tmp_path, 2, b"mutmut: pkg.x__mutmut_12\0", "PPid: 1\nVmRSS: 200 kB\nVmSwap: 50 kB\n")
    process(tmp_path, 3, b"python\0mutmut: pkg.x__mutmut_13\0", "PPid: 1\nVmRSS: 900 kB\n")
    process(tmp_path, 4, b"mutmut: pkg.x__mutmut_14\0", "PPid: 99\nVmRSS: 900 kB\n")
    (tmp_path / "not-a-pid").mkdir()

    assert guard.mutant_processes(tmp_path) == [guard.Mutant(2, "pkg.x__mutmut_12", 130)]


@pytest.mark.parametrize(
    "title", [b"mutmut: ", b"mutmut: example__mutmut_x", b"mutmut: example__mutmut_1 extra"]
)
def test_invalid_titles_are_not_mutants(title):
    assert guard.mutant_name(title) is None


def test_watch_signals_only_above_allowance_and_announces_visibility(monkeypatch, tmp_path, capsys):
    process(tmp_path, 1, b"parent", "VmRSS: 100 kB\n")
    process(tmp_path, 2, b"mutmut: equal__mutmut_1", "PPid: 1\nVmRSS: 200 kB\n")
    process(tmp_path, 3, b"mutmut: over__mutmut_2", "PPid: 1\nVmRSS: 200 kB\nVmSwap: 1 kB\n")
    calls = []
    parents = iter([10, 10, 11])
    monkeypatch.setattr(guard.os, "getppid", lambda: next(parents))
    monkeypatch.setattr(guard.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(guard.time, "sleep", lambda _: None)

    guard.watch(tmp_path, 100, 2)

    assert calls == [(3, signal.SIGXCPU)]
    output = capsys.readouterr().out
    assert "mutant processes are visible, first equal__mutmut_1" in output
    assert "over__mutmut_2 (pid 3)" in output
    assert "sent SIGXCPU" in output


def test_stop_escalates_once_after_thirty_seconds(monkeypatch):
    calls = []
    clock = iter([0.0, 29.9, 30.0, 31.0])
    monkeypatch.setattr(guard.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(guard.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    mutant = guard.Mutant(42, "example__mutmut_1", 2049 * 1024)
    sent = {}
    for _ in range(4):
        guard.stop(mutant, sent, 2048 * 1024)
    assert calls == [(42, signal.SIGXCPU), (42, signal.SIGKILL)]


def test_missing_proc_exits_with_warning(tmp_path, capsys):
    assert guard.main(["--allowance-mb", "2048", "--proc", str(tmp_path / "missing")]) == 0
    assert "nothing is guarded" in capsys.readouterr().out


@pytest.mark.parametrize(
    "option,value",
    [
        ("--allowance-mb", "0"),
        ("--allowance-mb", "1.5"),
        ("--interval", "nan"),
        ("--interval", "0"),
    ],
)
def test_invalid_options_are_rejected(option, value):
    args = ["--allowance-mb", "2048"] if option == "--interval" else []
    with pytest.raises(SystemExit) as exc:
        guard.main([*args, option, value])
    assert exc.value.code == 2
