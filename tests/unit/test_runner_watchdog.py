"""Tests for scripts/ops/runner_watchdog.py (issue #493).

On 2026-10-01 GitHub listed `mac-mini-lovspor` as `offline` while its
LaunchDaemon was `running` and `Runner.Listener` alive; `codex-author` sat
`queued` for hours until a manual `launchctl kickstart -k`. The watchdog makes
that restart, and only on that state.
"""

from __future__ import annotations

import importlib.util
import json
import plistlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = _ROOT / "scripts" / "ops" / "runner_watchdog.py"
PLIST = _ROOT / "deploy" / "launchd" / "no.lovspor.runner-watchdog.plist"
_LABEL = "actions.runner.bartoszkobylinski-lovspor.mac-mini-lovspor"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("runner_watchdog", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


watchdog = _load()


def test_offline_while_running_restarts_on_the_threshold_reading() -> None:
    first = watchdog.decide("offline", True, 0, 2)
    second = watchdog.decide("offline", True, first.streak, 2)

    assert (first.action, first.streak) == ("wait", 1)
    assert (second.action, second.streak) == ("kickstart", 0)


def test_a_threshold_of_one_restarts_on_the_first_reading() -> None:
    assert watchdog.decide("offline", True, 0, 1).action == "kickstart"


@pytest.mark.parametrize("status", ["online", "missing", "unknown"])
def test_anything_but_offline_resets_the_streak(status: str) -> None:
    decision = watchdog.decide(status, True, 5, 2)

    assert (decision.action, decision.streak) == ("wait", 0)
    assert status in decision.reason


def test_offline_with_the_daemon_down_is_launchds_to_restart() -> None:
    decision = watchdog.decide("offline", False, 1, 2)

    assert (decision.action, decision.streak) == ("wait", 0)
    assert "launchd" in decision.reason


def test_an_unreachable_api_is_no_evidence_and_keeps_the_streak() -> None:
    """The outage that drops the session also drops the API; a restart cannot
    fix the network, and forgetting the streak would delay the real restart."""
    decision = watchdog.decide(None, True, 1, 2)

    assert (decision.action, decision.streak) == ("wait", 1)
    assert "unreachable" in decision.reason


def test_runner_status_reads_the_named_runner_only() -> None:
    payload = {
        "total_count": 2,
        "runners": [
            {"name": "mikrus-codex", "status": "offline"},
            {"name": "mac-mini-lovspor", "status": "online"},
        ],
    }

    assert watchdog.runner_status(payload, "mac-mini-lovspor") == "online"
    assert watchdog.runner_status(payload, "gone") == "missing"
    assert watchdog.runner_status([], "mac-mini-lovspor") == "missing"


def test_daemon_running_reads_launchctls_state_line() -> None:
    printed = f"system/{_LABEL} = {{\n\tactive count = 1\n\tstate = running\n\tpid = 999\n}}\n"

    assert watchdog.daemon_running(printed) is True
    assert watchdog.daemon_running(printed.replace("running", "not running")) is False
    assert watchdog.daemon_running("") is False


def _stub(path: Path, body: str) -> str:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _sandbox(tmp_path: Path, status: str, state: str) -> list[str]:
    """`gh` answers the runners API with `status`, `launchctl print` with
    `state`; every `launchctl` call is recorded."""
    payload = json.dumps({"runners": [{"name": "mac-mini-lovspor", "status": status}]})
    (tmp_path / "payload.json").write_text(payload, encoding="utf-8")
    gh = _stub(
        tmp_path / "gh",
        f'[ "$GH_TOKEN" = s3cret ] || exit 4\ncat "{tmp_path}/payload.json"',
    )
    launchctl = _stub(
        tmp_path / "launchctl",
        f'echo "$@" >> "{tmp_path}/launchctl-calls"\n'
        f'[ "$1" = print ] && printf "\\tstate = {state}\\n"\nexit 0',
    )
    (tmp_path / "token").write_text("s3cret\n", encoding="utf-8")
    return [
        "--repo", "o/r", "--runner", "mac-mini-lovspor", "--label", _LABEL,
        "--token-file", str(tmp_path / "token"), "--state-file", str(tmp_path / "state.json"),
        "--gh", gh, "--launchctl", launchctl,
    ]  # fmt: skip


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "launchctl-calls").read_text(encoding="utf-8").splitlines()


def test_main_kickstarts_on_the_second_offline_reading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _sandbox(tmp_path, "offline", "running")

    assert watchdog.main(args) == 0
    assert _calls(tmp_path) == [f"print system/{_LABEL}"]
    assert json.loads((tmp_path / "state.json").read_text()) == {"offline_streak": 1}

    assert watchdog.main(args) == 0
    assert _calls(tmp_path)[-1] == f"kickstart -k system/{_LABEL}"
    assert json.loads((tmp_path / "state.json").read_text()) == {"offline_streak": 0}
    assert f"kickstart system/{_LABEL} exit=0" in capsys.readouterr().out


@pytest.mark.parametrize(("status", "state"), [("online", "running"), ("offline", "waiting")])
def test_main_never_kickstarts_outside_offline_while_running(
    tmp_path: Path, status: str, state: str
) -> None:
    args = _sandbox(tmp_path, status, state)

    for _ in range(3):
        assert watchdog.main(args) == 0

    assert not any(call.startswith("kickstart") for call in _calls(tmp_path))


def test_main_treats_a_refused_token_as_no_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _sandbox(tmp_path, "offline", "running")
    (tmp_path / "token").write_text("expired\n", encoding="utf-8")
    (tmp_path / "state.json").write_text('{"offline_streak": 1}', encoding="utf-8")

    assert watchdog.main(args) == 0

    assert not any(call.startswith("kickstart") for call in _calls(tmp_path))
    assert json.loads((tmp_path / "state.json").read_text()) == {"offline_streak": 1}
    assert "gh exit=4" in capsys.readouterr().err


def test_a_corrupt_state_file_starts_the_streak_over(tmp_path: Path) -> None:
    args = _sandbox(tmp_path, "offline", "running")
    (tmp_path / "state.json").write_text("not json", encoding="utf-8")

    assert watchdog.main(args) == 0

    assert json.loads((tmp_path / "state.json").read_text()) == {"offline_streak": 1}


def test_the_script_runs_on_the_system_python(tmp_path: Path) -> None:
    """The daemon runs it with /usr/bin/python3 (3.9 on macOS): no repo venv,
    no syntax newer than the interpreter that will execute it."""
    python = Path("/usr/bin/python3")
    if not python.exists():
        pytest.skip("no system python3")
    done = subprocess.run(
        [str(python), str(SCRIPT), "--help"], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr


def test_the_plist_runs_the_installed_copy_as_a_root_daemon_every_five_minutes() -> None:
    plist = plistlib.loads(PLIST.read_bytes())
    argv = plist["ProgramArguments"]

    assert plist["Label"] == "no.lovspor.runner-watchdog"
    assert argv[:2] == ["/usr/bin/python3", "/usr/local/libexec/lovspor/runner_watchdog.py"]
    assert argv[argv.index("--label") + 1] == _LABEL
    assert argv[argv.index("--runner") + 1] == "mac-mini-lovspor"
    assert plist["StartInterval"] == 300
    assert "UserName" not in plist
    # The token never sits in a world-readable plist.
    assert argv[argv.index("--token-file") + 1].startswith("/var/root/")
    assert "GH_TOKEN" not in plist.get("EnvironmentVariables", {})
