"""The droplet's systemd units, as repository content (ADR-0014 Decision 4).

The drift timer pair is modelled on ``lovspor-fetch-corpus.timer`` /
``.service`` and carries the publish unit's identity and the probe
credential's ``LoadCredential=`` line (ADR:1054-1090). These tests read
the unit files, so a unit that drifts from the ADR's contract fails a
test rather than a droplet.
"""

import re
from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "digitalocean"
_TIMER = _DEPLOY / "lovspor-site-drift.timer"
_SERVICE = _DEPLOY / "lovspor-site-drift.service"
_FETCH_TIMER = _DEPLOY / "lovspor-fetch-corpus.timer"
_PUBLISH = _DEPLOY / "lovspor-publish.service"


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

    def test_never_overlaps_a_release(self) -> None:
        text = _SERVICE.read_text(encoding="utf-8")

        assert "lovspor-publish.service" in _directive(text, "Conflicts")
        assert "lovspor-publish.service" in _directive(text, "After")

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
