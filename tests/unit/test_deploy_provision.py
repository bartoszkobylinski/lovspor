"""Provisioning a fresh droplet, as repository content (ADR-0014 Decision 6, Migration).

A box that has never published is the migration's mirror image: it gets
the release group and the whole ``caddy.service`` drop-in at once, so
Caddy binds the admin API to the permissioned socket from its first
start — the two-phase install, with the ``ExecReload=`` pair held back,
is the live droplet's business alone. It gets a placeholder release
fragment, because the Caddyfile imports the active one by a plain path
and a missing import is a hard ``caddy validate`` failure. It gets the
probe credential's directory and the drift timer. It does **not** get a
flat site root: the site is built into the release envelope.

These tests read the script, and run the pieces that must behave —
against ``tmp_path``, never the real destinations, which need root.
"""

import re
import subprocess
from pathlib import Path

from lovspor.release.caddy import FRAGMENT_ENV, config_pair
from lovspor.release.migrate import MigrationHost, drop_in_text
from tests.unit.caddy_fakes import admin_listen, toy_adapt

_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "digitalocean"
_PROVISION = _DEPLOY / "provision.sh"
_CADDYFILE = _DEPLOY / "Caddyfile"
_FRAGMENT = "/etc/caddy/lovspor-release.caddy"


def _script() -> str:
    return _PROVISION.read_text(encoding="utf-8")


def _heredoc(name: str) -> str:
    """The body of one quoted here-document, verbatim — nothing in it expands."""
    match = re.search(rf"<<'{name}'\n(?P<body>.*?)\n{name}\n", _script(), re.DOTALL)
    assert match is not None, f"provision.sh no longer writes a {name} here-document"
    return match.group("body") + "\n"


def _go_live() -> str:
    match = re.search(r"cat <<EOF\n(?P<body>.*?)\nEOF\n", _script(), re.DOTALL)
    assert match is not None, "provision.sh no longer prints the go-live steps"
    return match.group("body")


def _fragment_snippet(fragment: Path) -> str:
    """The placeholder's guarded write, retargeted at a writable path."""
    guard = rf"if \[ ! -f {re.escape(_FRAGMENT)} \]; then\n.*?\nfi\n"
    match = re.search(guard, _script(), re.DOTALL)
    assert match is not None, "provision.sh no longer guards the placeholder fragment"
    return match.group(0).replace(_FRAGMENT, str(fragment))


def _run(snippet: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", snippet], check=False, capture_output=True, text=True)


class TestTheDropIn:
    def test_is_the_one_text_the_first_migration_also_writes(self) -> None:
        """One source of truth. A fresh box and a migrated box must end with the
        same drop-in: one directive of drift and `systemctl reload caddy` reaches
        a different endpoint than the one the release commands address."""
        assert _heredoc("DROP_IN") == drop_in_text(MigrationHost(), with_exec_reload=True)

    def test_a_fresh_box_gets_the_pair_the_migration_installs_last(self) -> None:
        """On the live droplet the `ExecReload=` pair is held back until after
        the cutover — a line naming the socket would reach nothing while Caddy
        still answers on TCP. A fresh box starts on the socket, so it gets it."""
        drop_in = _heredoc("DROP_IN")

        assert "\nExecReload=\n" in drop_in
        assert "--address unix//run/caddy/admin.sock" in drop_in
        assert "RuntimeDirectory=caddy" in drop_in
        assert "RuntimeDirectoryMode=2770" in drop_in
        assert "ExecStartPre=+/usr/bin/chgrp lovspor-release /run/caddy" in drop_in

    def test_keeps_the_environment_file_the_domain_comes_from(self) -> None:
        assert "EnvironmentFile=/etc/default/caddy-lovspor" in _heredoc("DROP_IN")
        assert "LOVSPOR_DOMAIN=lovspor.example.com" in _script()


class TestTheReleaseGroup:
    def test_is_created_idempotently_and_holds_root(self) -> None:
        text = _script()

        assert "groupadd --system --force lovspor-release" in text
        assert "usermod -aG lovspor-release root" in text

    def test_never_holds_the_app_user(self) -> None:
        """ADR-0014 Decision 6: root — the release and reconcile identity — can
        open the admin socket; `User=lovspor`, the network-facing MCP service,
        must not be able to rewrite what Caddy serves without touching a file."""
        text = _script()

        assert 'usermod -aG lovspor-release "$APP_USER"' not in text
        assert "usermod -aG lovspor-release lovspor\n" not in text


class TestThePlaceholderFragment:
    def test_is_written_on_a_box_that_has_never_published(self, tmp_path: Path) -> None:
        fragment = tmp_path / "lovspor-release.caddy"

        result = _run(_fragment_snippet(fragment))

        assert result.returncode == 0, result.stderr
        assert fragment.read_text(encoding="utf-8") == _heredoc("FRAGMENT")

    def test_never_overwrites_the_live_releases_own_fragment(self, tmp_path: Path) -> None:
        """`provision.sh` is idempotent and re-runnable, and this is the one file
        on the box that names what is being served."""
        fragment = tmp_path / "lovspor-release.caddy"
        live = f"vars lovspor_release {'a' * 64}\n"
        fragment.write_text(live, encoding="utf-8")

        result = _run(_fragment_snippet(fragment))

        assert result.returncode == 0, result.stderr
        assert fragment.read_text(encoding="utf-8") == live

    def test_composes_with_the_caddyfile_into_a_configuration_naming_no_release(
        self, tmp_path: Path
    ) -> None:
        """The Caddyfile's `import` is a plain path, deliberately: a glob that
        matched nothing would leave the site block empty and 404 every request
        with no error anywhere, while a missing import fails `caddy validate`.
        That is why provisioning must write *some* fragment — and this one
        declares no `lovspor_release` var, so `release live` reads `none`."""
        fragment = tmp_path / "lovspor-release.caddy"
        fragment.write_text(_heredoc("FRAGMENT"), encoding="utf-8")

        config = toy_adapt(
            _CADDYFILE, {"LOVSPOR_DOMAIN": "lovspor.test", FRAGMENT_ENV: str(fragment)}
        )

        assert config_pair(config).release_id is None
        assert admin_listen(config) == "unix//run/caddy/admin.sock|0660"

    def test_says_so_with_a_503_and_declares_no_release(self) -> None:
        body = _heredoc("FRAGMENT")

        assert "no release published yet" in body
        assert "503" in body
        assert "vars" not in body

    def test_is_installed_beside_the_caddyfile_it_belongs_to(self) -> None:
        text = _script()

        assert 'install -m644 "$APP_DIR/deploy/digitalocean/Caddyfile" /etc/caddy/Caddyfile' in text
        assert text.index("/etc/caddy/Caddyfile") < text.index(f"if [ ! -f {_FRAGMENT} ]")


class TestTheProbeCredential:
    def test_its_directory_is_installed_root_only(self) -> None:
        assert "install -d -m 700 /etc/lovspor/credentials" in _script()

    def test_provisioning_never_carries_a_token(self) -> None:
        """The token prints once, at go-live; a provisioning script that wrote
        one would have to hold it (docs/mcp.md § Release probe and drift check)."""
        assert "lsp_" not in _script()

    def test_the_go_live_steps_issue_it_before_the_first_release(self) -> None:
        go_live = _go_live()

        assert "tokens issue --label site-probe" in go_live
        assert "/etc/lovspor/credentials/site-probe" in go_live
        assert go_live.index("site-probe") < go_live.index("lovspor-publish")


class TestTheRetiredSiteRoot:
    def test_no_flat_site_root_and_no_page_copying(self) -> None:
        """ADR-0014 Decision 6: the site is built by `lovspor build-site` into
        the release envelope, so there is no tree for provisioning to fill."""
        text = _script()

        assert "install -d /var/www/lovspor\n" not in text
        assert "deploy/digitalocean/site" not in text
        assert "/var/www/lovspor/" not in text

    def test_the_releases_root_is_still_created_for_the_build_user(self) -> None:
        expected = 'install -d -o "$APP_USER" -g "$APP_USER" -m 755 /var/www/lovspor-releases'

        assert expected in _script()


class TestTheUnits:
    def test_installs_the_drift_pair_from_the_verified_checkout(self) -> None:
        text = _script()

        for unit in (
            "lovspor-mcp.service",
            "lovspor-fetch-corpus.service",
            "lovspor-fetch-corpus.timer",
            "lovspor-publish.service",
            "lovspor-site-drift.service",
            "lovspor-site-drift.timer",
        ):
            assert f'install -m644 "$APP_DIR/deploy/digitalocean/{unit}" /etc/systemd/system/' in (
                text
            ), unit

    def test_enables_and_starts_the_drift_timer_like_the_fetch_timer(self) -> None:
        """ADR-0014: installed and enabled the way `lovspor-fetch-corpus.timer`
        is. Until the first release it fails hourly with
        `served_document_unavailable` — the truthful reading of a box that has
        published nothing, and one `systemctl --failed` shows."""
        text = _script()

        assert "systemctl enable --now lovspor-fetch-corpus.timer" in text
        assert "systemctl enable --now lovspor-site-drift.timer" in text
        assert "systemctl enable lovspor-mcp.service" in text
