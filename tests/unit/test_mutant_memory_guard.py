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


def test_scan_recognises_untitled_fork_but_not_mutmut_or_other_commands(tmp_path):
    command = b"/venv/bin/python3\0/venv/bin/mutmut\0run\0pkg.*\0"
    process(tmp_path, 1, b"timeout\0", "VmRSS: 10 kB\n")
    process(tmp_path, 2, command, "PPid: 1\nVmRSS: 100 kB\nVmSwap: 20 kB\n")
    process(tmp_path, 3, command, "PPid: 2\nVmRSS: 200 kB\nVmSwap: 50 kB\n")
    process(tmp_path, 4, command + b"different\0", "PPid: 2\nVmRSS: 900 kB\n")
    other = b"python3\0/venv/bin/mutmut\0results\0"
    process(tmp_path, 5, other, "PPid: 1\n")
    process(tmp_path, 6, other, "PPid: 5\nVmRSS: 900 kB\n")
    process(tmp_path, 7, command, "PPid: 99\nVmRSS: 900 kB\n")

    assert guard.mutant_processes(tmp_path) == [guard.Mutant(3, guard.UNTITLED, 130)]


def test_scan_separates_unlimited_pool_from_limited_and_unreadable_forks(tmp_path):
    command = b"python3\0/venv/bin/mutmut\0run\0"
    process(tmp_path, 1, b"timeout\0", "VmRSS: 0 kB\n")
    process(tmp_path, 2, command, "PPid: 1\nVmRSS: 100 kB\n")
    for pid in range(3, 8):
        process(tmp_path, pid, command, "PPid: 2\nVmRSS: 200 kB\n")
    (tmp_path / "3" / "limits").write_text(
        "Max cpu time              unlimited            unlimited            seconds\n"
    )
    (tmp_path / "4" / "limits").write_text(
        "Max cpu time              60                   60                   seconds\n"
    )
    (tmp_path / "5" / "limits").write_text(
        "Max open files            unlimited            unlimited            files\n"
    )
    (tmp_path / "6" / "limits").write_text(
        "Max cpu time              60                   unlimited            seconds\n"
    )
    # PID 7 has no readable limits file: guard it when in doubt.

    mutants, unlimited = guard.forks(tmp_path)

    assert unlimited == [guard.Unlimited(3, 100)]
    assert mutants == [guard.Mutant(pid, guard.UNTITLED, 100) for pid in range(4, 8)]


def test_titled_mutant_is_guarded_even_with_unlimited_cpu(tmp_path):
    process(tmp_path, 1, b"parent", "VmRSS: 100 kB\n")
    process(tmp_path, 2, b"mutmut: example__mutmut_1", "PPid: 1\nVmRSS: 300 kB\n")
    (tmp_path / "2" / "limits").write_text(
        "Max cpu time              unlimited unlimited seconds\n"
    )

    assert guard.forks(tmp_path) == ([guard.Mutant(2, "example__mutmut_1", 200)], [])


def test_watch_leaves_pool_alone_then_guards_forks_after_threshold(monkeypatch, tmp_path, capsys):
    scans = iter(
        [
            ([], [guard.Unlimited(pid, 2048) for pid in range(1, 65)]),
            ([], [guard.Unlimited(65, 3072)]),
            ([], [guard.Unlimited(66, 4096)]),
        ]
    )
    parents = iter([10, 10, 10, 10, 11])
    calls = []
    monkeypatch.setattr(guard, "forks", lambda _: next(scans))
    monkeypatch.setattr(guard.os, "getppid", lambda: next(parents))
    monkeypatch.setattr(guard.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(guard.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(guard.time, "sleep", lambda _: None)

    guard.watch(tmp_path, 1024, 2)

    assert calls == [(66, signal.SIGXCPU)]
    output = capsys.readouterr().out
    assert output.count("forks of `mutmut run` under no CPU limit are running") == 1
    assert output.count(guard.POOL_EXCEEDED) == 1


def test_watch_reports_cumulative_scans_and_signals_untitled_fork(monkeypatch, tmp_path, capsys):
    command = b"python3\0/venv/bin/mutmut\0run\0"
    process(tmp_path, 1, b"timeout\0", "VmRSS: 0 kB\n")
    process(tmp_path, 2, command, "PPid: 1\nVmRSS: 100 kB\n")
    process(tmp_path, 3, command, "PPid: 2\nVmRSS: 1125 kB\n")
    calls = []
    parents = iter([10, 10, 10, 11])
    clock = iter([0.0, 1.0, 899.0, 900.0, 900.0])
    monkeypatch.setattr(guard.os, "getppid", lambda: next(parents))
    monkeypatch.setattr(guard.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(guard.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(guard.time, "sleep", lambda _: None)

    guard.watch(tmp_path, 1024, 2)

    assert calls == [(3, signal.SIGXCPU), (3, signal.SIGKILL)]
    output = capsys.readouterr().out
    assert "first pid 3, a fork of `mutmut run`" in output
    assert "untitled mutant (pid 3)" in output
    assert "0 titled and 1 untitled mutant process(es), 1 over the allowance" in output
    assert "largest held 1 MiB more than its parent" in output


def test_tally_counts_unique_processes_and_strict_allowance():
    tally = guard.Tally()
    assert tally.line() == "no mutant process seen in the scans so far"
    tally.note([guard.Mutant(1, "a__mutmut_1", 1024)], 1024)
    tally.note([guard.Mutant(1, "a__mutmut_1", 2048), guard.Mutant(2, guard.UNTITLED, 1)], 1024)
    assert tally.line() == (
        "scans so far saw 1 titled and 1 untitled mutant process(es), 1 over the allowance; "
        "the largest held 2 MiB more than its parent"
    )


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
