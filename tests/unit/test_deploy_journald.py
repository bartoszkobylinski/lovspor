"""The droplet's journald retention, as repository content.

The lovspor.no privacy page (PR #474) states the server log is kept at
most 30 days. journald keeps logs until its disk cap otherwise, so the
promise holds on a rebuilt droplet only if provisioning ships the drop-in
the owner applied by hand on 2026-09-30. These tests read the files, so
a retention that drifts from the page fails a test rather than a droplet.
"""

import re
from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "digitalocean"
_CONF = _DEPLOY / "journald-retention.conf"
_PROVISION = _DEPLOY / "provision.sh"
_CADDYFILE = _DEPLOY / "Caddyfile"
_TARGET = "/etc/systemd/journald.conf.d/retention.conf"


def _script() -> str:
    return _PROVISION.read_text(encoding="utf-8")


class TestRetentionFile:
    def test_matches_the_drop_in_applied_on_the_droplet(self) -> None:
        assert _CONF.read_text(encoding="utf-8") == "[Journal]\nMaxRetentionSec=30day\n"


class TestProvisionInstallsIt:
    def test_installs_the_file_at_the_journald_drop_in_path(self) -> None:
        install = re.search(
            r'install -D -m 0644 "\$APP_DIR/deploy/digitalocean/journald-retention\.conf" \\\n'
            rf"\s+{re.escape(_TARGET)}\n",
            _script(),
        )

        assert install is not None

    def test_restarts_journald_after_installing(self) -> None:
        script = _script()
        restart = script.find("systemctl restart systemd-journald")

        assert restart > script.find(_TARGET) > -1


class TestCaddyAccessLogIsBounded:
    def test_caddy_rotates_its_own_access_log(self) -> None:
        text = _CADDYFILE.read_text(encoding="utf-8")

        assert re.search(r"^\s+roll_size 10MiB$", text, flags=re.MULTILINE)
        assert re.search(r"^\s+roll_keep 5$", text, flags=re.MULTILINE)
