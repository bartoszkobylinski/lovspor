"""The deploy reinstalls the droplet's systemd units (issue #484).

The ordinary deploy — pull, ``uv sync``, restart — never touched
``/etc/systemd/system``, so a unit changed in the repository stayed stale
on the box: on 2026-09-30 the installed fetch unit lacked the #234 retry
and the drift pair was not installed at all. ``sync-units.sh`` is the one
place that installs the units, from provisioning and from every deploy,
and it fails loudly when an installed unit is not the repository's.

These tests run the real script against ``tmp_path`` with a recording
``systemctl`` first on ``PATH`` — never the real destination, which needs
root.
"""

import os
import re
import subprocess
from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "digitalocean"
_SYNC = _DEPLOY / "sync-units.sh"
_PROVISION = _DEPLOY / "provision.sh"
_README = _DEPLOY / "README.md"
# Installed by rehearse-migration.sh for one rehearsal and removed after it;
# the box never runs it as part of the service.
_NOT_DEPLOYED = {"caddy-rehearsal.service"}


def _deployed_units() -> set[str]:
    shipped = {p.name for p in _DEPLOY.iterdir() if p.suffix in {".service", ".timer"}}
    return shipped - _NOT_DEPLOYED


def _fake_systemctl(tmp_path: Path, exit_code: int = 0) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "systemctl"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/systemctl.log"\nexit {exit_code}\n')
    fake.chmod(0o755)
    return bin_dir


def _run(tmp_path: Path, *args: str, systemctl_exit: int = 0) -> subprocess.CompletedProcess[str]:
    unit_dir = tmp_path / "system"
    unit_dir.mkdir(exist_ok=True)
    bin_dir = tmp_path / "bin"
    if not bin_dir.exists():
        _fake_systemctl(tmp_path, systemctl_exit)
    env = {**os.environ, "LOVSPOR_UNIT_DIR": str(unit_dir)}
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    command = ["bash", str(_SYNC), *args]
    return subprocess.run(command, check=False, capture_output=True, text=True, env=env)


def _systemctl_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "systemctl.log"
    return log.read_text().splitlines() if log.exists() else []


class TestTheScript:
    def test_is_strict_bash(self) -> None:
        text = _SYNC.read_text(encoding="utf-8")

        assert text.startswith("#!/usr/bin/env bash\n")
        assert "set -euo pipefail" in text

    def test_names_every_unit_the_box_runs(self) -> None:
        listed = set(re.findall(r"^\s+(\S+\.(?:service|timer))$", _SYNC.read_text(), re.M))

        assert listed == _deployed_units()

    def test_never_changes_what_runs(self) -> None:
        """Enabling and starting is provisioning's; restarting is the deploy's."""
        text = _SYNC.read_text(encoding="utf-8")

        for verb in ("enable", "disable", "start", "stop", "restart", "mask"):
            assert not re.search(rf"systemctl\s+(?:--\S+\s+)*{verb}\b", text), verb


class TestInstall:
    def test_installs_every_unit_byte_for_byte_and_reloads(self, tmp_path: Path) -> None:
        result = _run(tmp_path)

        assert result.returncode == 0, result.stderr
        for unit in _deployed_units():
            installed = tmp_path / "system" / unit
            assert installed.read_bytes() == (_DEPLOY / unit).read_bytes(), unit
            assert installed.stat().st_mode & 0o777 == 0o644, unit
            assert f"OK   {unit}" in result.stdout
        assert _systemctl_calls(tmp_path) == ["daemon-reload"]

    def test_repairs_a_stale_unit(self, tmp_path: Path) -> None:
        stale = tmp_path / "system" / "lovspor-fetch-corpus.service"
        stale.parent.mkdir()
        stale.write_text("[Service]\nExecStart=/bin/true\n")

        result = _run(tmp_path)

        assert result.returncode == 0, result.stderr
        assert stale.read_bytes() == (_DEPLOY / stale.name).read_bytes()

    def test_is_idempotent(self, tmp_path: Path) -> None:
        first = _run(tmp_path)
        second = _run(tmp_path)

        assert (first.returncode, second.returncode) == (0, 0)
        assert first.stdout == second.stdout

    def test_a_failed_daemon_reload_fails_the_deploy(self, tmp_path: Path) -> None:
        result = _run(tmp_path, systemctl_exit=1)

        assert result.returncode != 0


class TestCheck:
    def test_a_box_matching_the_repository_passes(self, tmp_path: Path) -> None:
        assert _run(tmp_path).returncode == 0

        result = _run(tmp_path, "--check")

        assert result.returncode == 0, result.stderr

    def test_drift_exits_non_zero_naming_each_unit_and_installs_nothing(
        self, tmp_path: Path
    ) -> None:
        assert _run(tmp_path).returncode == 0
        stale = tmp_path / "system" / "lovspor-fetch-corpus.service"
        stale.write_text("stale\n")
        (tmp_path / "system" / "lovspor-site-drift.timer").unlink()
        (tmp_path / "systemctl.log").unlink()

        result = _run(tmp_path, "--check")

        assert result.returncode == 1
        assert "DIFF lovspor-fetch-corpus.service" in result.stdout
        assert "DIFF lovspor-site-drift.timer" in result.stdout
        assert "2 unit(s)" in result.stderr
        assert stale.read_text() == "stale\n"
        assert _systemctl_calls(tmp_path) == []

    def test_an_unknown_argument_is_refused(self, tmp_path: Path) -> None:
        result = _run(tmp_path, "--bogus")

        assert result.returncode == 2
        assert not (tmp_path / "system" / "lovspor-mcp.service").exists()


class TestTheSupportedPaths:
    def test_provisioning_installs_the_units_through_the_same_script(self) -> None:
        text = _PROVISION.read_text(encoding="utf-8")

        assert 'bash "$APP_DIR/deploy/digitalocean/sync-units.sh"' in text
        assert 'install -m644 "$APP_DIR/deploy/digitalocean/lovspor-' not in text

    def test_the_documented_deploy_syncs_the_units_before_the_restart(self) -> None:
        readme = _README.read_text(encoding="utf-8")
        block = readme[readme.index("**Deploy an update**") :]
        block = block[: block.index("```\n\n")]

        sync = "sudo bash /opt/lovspor/app/deploy/digitalocean/sync-units.sh"
        assert block.index("uv sync --frozen --no-dev") < block.index(sync)
        assert block.index(sync) < block.index("sudo systemctl restart lovspor-mcp")
