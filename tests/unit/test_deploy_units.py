"""The droplet's systemd units, as repository content (ADR-0014 Decision 4).

The drift timer pair is modelled on ``lovspor-fetch-corpus.timer`` /
``.service`` and carries the publish unit's identity and the probe
credential's ``LoadCredential=`` line (ADR:1054-1090). These tests read
the unit files, so a unit that drifts from the ADR's contract fails a
test rather than a droplet.
"""

import re
import shlex
import subprocess
from pathlib import Path

import pytest

_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "digitalocean"
_TIMER = _DEPLOY / "lovspor-site-drift.timer"
_SERVICE = _DEPLOY / "lovspor-site-drift.service"
_FETCH_TIMER = _DEPLOY / "lovspor-fetch-corpus.timer"
_PUBLISH = _DEPLOY / "lovspor-publish.service"
_FETCH_SERVICE = _DEPLOY / "lovspor-fetch-corpus.service"


def _directive(text: str, name: str) -> list[str]:
    return re.findall(rf"^{re.escape(name)}=(.*)$", text, flags=re.MULTILINE)


class TestDriftTimer:
    def test_runs_hourly_and_catches_missed_runs_like_the_fetch_timer(self) -> None:
        text = _TIMER.read_text(encoding="utf-8")
        fetch = _FETCH_TIMER.read_text(encoding="utf-8")

        (calendar,) = _directive(text, "OnCalendar")
        assert re.fullmatch(r"\*-\*-\* \*:\d{2}:00 UTC", calendar), calendar
        assert _directive(text, "Persistent") == ["true"] == _directive(fetch, "Persistent")
        assert _directive(text, "WantedBy") == ["timers.target"]

    def test_does_not_share_the_daily_fetch_minute(self) -> None:
        """Hourly on the fetch timer's minute would race the corpus refresh once a day."""
        (calendar,) = _directive(_TIMER.read_text(encoding="utf-8"), "OnCalendar")
        (fetch,) = _directive(_FETCH_TIMER.read_text(encoding="utf-8"), "OnCalendar")

        assert calendar.split(":")[1] != fetch.split(":")[1]


class TestDriftService:
    def test_is_a_root_oneshot_like_the_publish_unit(self) -> None:
        text = _SERVICE.read_text(encoding="utf-8")
        publish = _PUBLISH.read_text(encoding="utf-8")

        assert _directive(text, "Type") == ["oneshot"]
        assert _directive(text, "User") == ["root"] == _directive(publish, "User")
        assert _directive(text, "WorkingDirectory") == ["/opt/lovspor/app"]

    def test_receives_the_probe_credential_through_load_credential(self) -> None:
        """The secret never sits in the environment or the repository: systemd
        hands it to the unit's identity as $CREDENTIALS_DIRECTORY/site-probe."""
        text = _SERVICE.read_text(encoding="utf-8")

        assert _directive(text, "LoadCredential") == [
            "site-probe:/etc/lovspor/credentials/site-probe"
        ]
        assert not _directive(text, "Environment")
        assert not _directive(text, "EnvironmentFile")

    def test_runs_the_drift_check_from_the_venv_as_the_drift_timer(self) -> None:
        (exec_start,) = _directive(_SERVICE.read_text(encoding="utf-8"), "ExecStart")

        assert exec_start.startswith("/opt/lovspor/app/.venv/bin/lovspor site-drift-check ")
        assert "--observer drift-timer" in exec_start
        assert "--served-url https://lovspor.no/deployment-capabilities.json" in exec_start
        assert "--probe-token-file" not in exec_start

    def test_is_ordered_after_a_release_and_never_stops_one(self) -> None:
        """After= holds a timer-fired check behind an in-flight release's start
        job; Conflicts= would instead stop the release (#545)."""
        text = _SERVICE.read_text(encoding="utf-8")

        assert "lovspor-publish.service" in _directive(text, "After")
        assert "lovspor-publish.service" not in " ".join(_directive(text, "Conflicts"))

    def test_is_bounded(self) -> None:
        (timeout,) = _directive(_SERVICE.read_text(encoding="utf-8"), "TimeoutStartSec")

        assert int(timeout) <= 600


class TestDriftServiceAdminSocket:
    """The four facts are the drift check's first action (ADR-0014 Decision 4)."""

    def test_the_unit_records_the_socket_check_and_its_exit_code(self) -> None:
        text = _SERVICE.read_text(encoding="utf-8")

        assert "FIRST action" in text
        assert "0660" in text
        assert "localhost:2019" in text
        assert "4 = the admin socket precondition" in text

    def test_the_identity_that_drops_for_the_one_call_is_named(self) -> None:
        text = _SERVICE.read_text(encoding="utf-8")

        assert "drops to" in text
        assert "`lovspor` cannot" in text
        assert _directive(text, "User") == ["root"]


def _seconds(value: str) -> int:
    units = {"s": 1, "min": 60, "h": 3600}
    match = re.fullmatch(r"(\d+)(s|min|h)?", value)
    assert match, value
    return int(match[1]) * units[match[2] or "s"]


class TestFetchCorpusRetry:
    """A transient fetch failure is retried, boundedly (issue #234)."""

    def test_a_failed_run_is_retried(self) -> None:
        text = _FETCH_SERVICE.read_text(encoding="utf-8")

        assert _directive(text, "Type") == ["oneshot"]
        assert _directive(text, "Restart") == ["on-failure"]
        (delay,) = _directive(text, "RestartSec")
        assert _seconds(delay) == 10 * 60

    def test_retries_stop_before_they_can_loop(self) -> None:
        (burst,) = _directive(_FETCH_SERVICE.read_text(encoding="utf-8"), "StartLimitBurst")

        assert int(burst) == 4

    def test_the_retry_window_never_blocks_the_next_daily_run(self) -> None:
        text = _FETCH_SERVICE.read_text(encoding="utf-8")
        (window,) = _directive(text, "StartLimitIntervalSec")
        (burst,) = _directive(text, "StartLimitBurst")
        (delay,) = _directive(text, "RestartSec")
        (timeout,) = _directive(text, "TimeoutStartSec")

        assert _seconds(window) == 2 * 3600
        assert int(burst) * (_seconds(delay) + _seconds(timeout)) <= _seconds(window)

    def test_retry_directives_are_in_the_systemd_sections_that_honor_them(self) -> None:
        text = _FETCH_SERVICE.read_text(encoding="utf-8")
        unit, service = text.split("[Service]", maxsplit=1)

        assert "StartLimitIntervalSec=2h" in unit
        assert "StartLimitBurst=4" in unit
        assert "Restart=on-failure" in service
        assert "RestartSec=10min" in service


_ALERT = _DEPLOY / "lovspor-alert@.service"
_MCP = _DEPLOY / "lovspor-mcp.service"
_ALERTED = (_FETCH_SERVICE, _PUBLISH, _SERVICE)


class TestFailureAlerts:
    """A failed unit tells the operator (issue #478)."""

    def test_the_timer_and_release_units_name_the_alert_in_their_unit_section(self) -> None:
        for path in _ALERTED:
            unit, _service = path.read_text(encoding="utf-8").split("[Service]", maxsplit=1)

            assert _directive(unit, "OnFailure") == ["lovspor-alert@%n.service"], path.name

    def test_the_mcp_service_neither_alerts_nor_reads_the_alert_file(self) -> None:
        """Restart=on-failure every 5 s never reaches `failed` under the default
        start limit, and the webhook is not the network-facing service's to read."""
        text = _MCP.read_text(encoding="utf-8")

        assert not _directive(text, "OnFailure")
        assert "alert.env" not in text

    def test_the_fetch_retries_alert_once_when_they_are_exhausted(self) -> None:
        """RestartMode=direct skips OnFailure= on each auto-restart; the unit
        enters `failed` — and alerts — only when the start limit refuses a retry."""
        _unit, service = _FETCH_SERVICE.read_text(encoding="utf-8").split("[Service]", 1)

        assert _directive(service, "RestartMode") == ["direct"]

    def test_the_template_runs_the_alert_command_for_its_instance(self) -> None:
        (exec_start,) = _directive(_ALERT.read_text(encoding="utf-8"), "ExecStart")

        assert exec_start == "/opt/lovspor/app/.venv/bin/lovspor ops alert --unit %i"

    def test_the_template_never_alerts_about_itself(self) -> None:
        assert not _directive(_ALERT.read_text(encoding="utf-8"), "OnFailure")

    def test_the_template_is_a_bounded_root_oneshot(self) -> None:
        """Root reads the journal and the root-only alert file; no EnvironmentFile=,
        so the webhook is never in any process environment on the box."""
        text = _ALERT.read_text(encoding="utf-8")
        (timeout,) = _directive(text, "TimeoutStartSec")

        assert _directive(text, "Type") == ["oneshot"]
        assert _directive(text, "User") == ["root"]
        assert not _directive(text, "EnvironmentFile")
        assert int(timeout) <= 120


_BUSY_STATES = ("activating", "active", "reloading", "refreshing", "deactivating")
_IDLE_STATES = ("inactive", "failed")


def _condition_script() -> str:
    """The shell script of the drift unit's one ExecCondition=, as systemd hands it to sh."""
    (condition,) = _directive(_SERVICE.read_text(encoding="utf-8"), "ExecCondition")
    argv = shlex.split(condition)
    assert argv[:2] == ["/bin/sh", "-c"], condition
    (script,) = argv[2:]
    return script


def _run_condition(tmp_path: Path, state: str) -> int:
    """Run the condition with a stub systemctl reporting ``state`` for the publish unit."""
    stub = tmp_path / "systemctl"
    stub.write_text(
        '#!/bin/sh\n[ "$1 $2" = "is-active lovspor-publish.service" ] || exit 64\n'
        f"echo {state}\nexit 3\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin"}
    script = _condition_script()
    return subprocess.run(["/bin/sh", "-c", script], env=env, check=False).returncode


class TestDriftYieldsToRelease:
    """A running release skips the check; it is never stopped and never alerts (#545).

    ExecCondition= exit 1-254 skips the unit without marking it failed, so
    OnFailure= does not fire; 255 or a signal is a failure (systemd.service(5)).
    A running oneshot is ``activating``, for which ``systemctl is-active``
    exits 3, so the condition reads the printed state, not that exit code.
    """

    def test_the_condition_names_the_release_unit_without_systemd_expansions(self) -> None:
        (condition,) = _directive(_SERVICE.read_text(encoding="utf-8"), "ExecCondition")

        assert "lovspor-publish.service" in condition
        # systemd rewrites $ and % in Exec lines before sh ever sees them.
        assert "$" not in condition
        assert "%" not in condition

    @pytest.mark.parametrize("state", _BUSY_STATES)
    def test_a_running_release_skips_the_check(self, tmp_path: Path, state: str) -> None:
        code = _run_condition(tmp_path, state)

        assert 1 <= code <= 254, code

    @pytest.mark.parametrize("state", _IDLE_STATES)
    def test_an_idle_release_unit_lets_the_check_run(self, tmp_path: Path, state: str) -> None:
        assert _run_condition(tmp_path, state) == 0


_LONG_RUNNING = ("lovspor-publish.service", "lovspor-fetch-corpus.service")


class TestNoUnitStopsALongRunningJob:
    """Conflicts= is symmetric: starting either unit enqueues a stop of the other.

    Naming a long-running oneshot there lets a timer kill it mid-run, as the
    08:17 drift check cancelled a release on 2026-10-05 (#545).
    """

    def test_the_long_running_units_are_oneshots(self) -> None:
        for name in _LONG_RUNNING:
            text = (_DEPLOY / name).read_text(encoding="utf-8")

            assert _directive(text, "Type") == ["oneshot"], name

    def test_no_unit_conflicts_with_a_long_running_unit(self) -> None:
        for path in sorted(_DEPLOY.glob("*.service")):
            text = path.read_text(encoding="utf-8")
            conflicts = " ".join(_directive(text, "Conflicts")).split()

            assert not set(conflicts) & set(_LONG_RUNNING), f"{path.name}: {conflicts}"
            if path.name in _LONG_RUNNING:
                assert not conflicts, f"{path.name}: {conflicts}"

    def test_a_release_waits_for_a_running_fetch(self) -> None:
        """After= alone holds the publish start job until the fetch's job ends,
        so a build never resolves HEAD in a clone mid-pull."""
        text = _PUBLISH.read_text(encoding="utf-8")

        assert "lovspor-fetch-corpus.service" in _directive(text, "After")
