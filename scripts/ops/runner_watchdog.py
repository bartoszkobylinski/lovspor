#!/usr/bin/env python3
"""Restart the self-hosted runner when GitHub has lost it but launchd has not (#493).

On 2026-10-01 the Mac mini runner went `offline` on GitHub while its LaunchDaemon
was `running` and `Runner.Listener` was alive: the listener lost its session in a
night of network outages and never re-registered. `KeepAlive` cannot see that —
the process never exited — so `codex-author` sat `queued` for hours until a human
ran `launchctl kickstart -k`. This script is that human, on a timer.

It kickstarts only when both sides agree on the failure: the runners API answers
and names the runner `offline`, and `launchctl print` says the daemon is running.
An API that cannot be reached is no evidence (the outage that caused this is also
what makes the API unreachable, and a restart cannot fix the network); a daemon
that is not running is launchd's to restart. One `offline` reading is not enough
either: a runner restarting or updating itself reads `offline` for a moment, so
the restart waits for `--threshold` consecutive readings.

Runs as root from a LaunchDaemon (`kickstart` in the system domain needs it), so
it uses the standard library only and runs on the system `/usr/bin/python3`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KICKSTART = "kickstart"
WAIT = "wait"


@dataclass(frozen=True)
class Decision:
    """What to do now, the offline streak to remember, and why."""

    action: str
    streak: int
    reason: str


def decide(api_status: str | None, daemon_running: bool, streak: int, threshold: int) -> Decision:
    """Decide from one reading. `api_status` is None when the API gave no answer."""
    if api_status is None:
        return Decision(WAIT, streak, "runners API unreachable: no evidence, no restart")
    if api_status != "offline":
        return Decision(WAIT, 0, f"runner is {api_status}")
    if not daemon_running:
        return Decision(WAIT, 0, "offline and the daemon is not running: launchd owns that")
    seen = streak + 1
    if seen < threshold:
        return Decision(WAIT, seen, f"offline while the daemon runs ({seen}/{threshold})")
    return Decision(KICKSTART, 0, f"offline while the daemon runs ({seen}/{threshold})")


def runner_status(payload: Any, name: str) -> str:
    """The runner's status in a runners-API payload, or "missing"."""
    runners = payload.get("runners", []) if isinstance(payload, dict) else []
    for runner in runners:
        if isinstance(runner, dict) and runner.get("name") == name:
            return str(runner.get("status") or "unknown")
    return "missing"


def daemon_running(launchctl_print: str) -> bool:
    """True when `launchctl print system/<label>` reports `state = running`."""
    return any(line.strip() == "state = running" for line in launchctl_print.splitlines())


def _run(argv: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # argv is a list built here from the operator's own plist; no shell is involved.
    return subprocess.run(  # noqa: S603
        argv, capture_output=True, text=True, timeout=60, env=env, check=False
    )


def _api_status(args: argparse.Namespace) -> str | None:
    env = dict(os.environ)
    env["GH_TOKEN"] = Path(args.token_file).read_text(encoding="utf-8").strip()
    endpoint = f"repos/{args.repo}/actions/runners?per_page=100"
    try:
        done = _run([args.gh, "api", endpoint], env)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"gh did not answer: {error}", file=sys.stderr)
        return None
    if done.returncode != 0:
        # A refused token reads the same as an outage from here; stderr tells them apart.
        print(f"gh exit={done.returncode}: {done.stderr.strip()}", file=sys.stderr)
        return None
    try:
        return runner_status(json.loads(done.stdout), args.runner)
    except json.JSONDecodeError:
        return None


def _daemon_running(args: argparse.Namespace) -> bool:
    try:
        done = _run([args.launchctl, "print", f"system/{args.label}"])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and daemon_running(done.stdout)


def _read_streak(path: Path) -> int:
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))["offline_streak"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _write_streak(path: Path, streak: int) -> None:
    path.write_text(json.dumps({"offline_streak": streak}) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--runner", required=True, help="runner name on GitHub")
    parser.add_argument("--label", required=True, help="the runner's LaunchDaemon label")
    parser.add_argument("--token-file", required=True, help="file holding a token for gh")
    parser.add_argument("--state-file", required=True, help="where the offline streak lives")
    parser.add_argument("--threshold", type=int, default=2, help="offline readings to restart")
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--launchctl", default="/bin/launchctl")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    state = Path(args.state_file)
    decision = decide(_api_status(args), _daemon_running(args), _read_streak(state), args.threshold)
    _write_streak(state, decision.streak)
    # time, not datetime.UTC: the daemon's /usr/bin/python3 is 3.9.
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"{stamp} {decision.action}: {decision.reason}")
    if decision.action != KICKSTART:
        return 0
    done = _run([args.launchctl, "kickstart", "-k", f"system/{args.label}"])
    print(f"{stamp} kickstart system/{args.label} exit={done.returncode} {done.stderr.strip()}")
    return done.returncode


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
