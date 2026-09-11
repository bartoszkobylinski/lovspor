"""The rehearsal instance and its harness, as repository content (ADR-0014 Validation (g)).

The rehearsal itself cannot run here — there is no ``caddy`` binary and
no systemd on a CI runner — so what these tests hold is the seam: the
harness carries no assertion of its own (every one of them is
``src/lovspor/release/rehearsal.py``, unit-tested against ``FakeCaddy``),
it hands the command every path and address of the second instance, and
it names none of the production ones.

``rehearsal_form`` is the one piece of the harness with real logic, so it
is extracted and run under ``bash`` against both of the repository's own
Caddyfiles — the pattern ``test_deploy_landing.py`` established.
"""

import os
import re
import subprocess
from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "digitalocean"
_SCRIPT = _DEPLOY / "rehearse-migration.sh"
_UNIT = _DEPLOY / "caddy-rehearsal.service"
_CADDY_UNIT_NAME = "caddy-rehearsal"
_NEW_CADDYFILE = _DEPLOY / "Caddyfile"
_README = _DEPLOY / "README.md"
_REH_ADMIN = "unix//run/caddy-rehearsal/admin.sock"


def _directive(text: str, name: str) -> list[str]:
    return re.findall(rf"^{re.escape(name)}=(.*)$", text, flags=re.MULTILINE)


def _code(text: str) -> str:
    """The file without its comments; every assertion below is about what systemd or bash reads."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _function(code: str, name: str) -> str:
    start = code.index(f"{name}() {{")
    end = code.index("\n}\n", start)
    return code[start : end + 3]


def _rehearsal_form(source: Path) -> str:
    """Run the harness's own rewriting function over ``source``, nothing else."""
    code = _code(_SCRIPT.read_text(encoding="utf-8"))
    script = (
        "set -euo pipefail\n"
        'REH_ADMIN="unix//run/caddy-rehearsal/admin.sock"\n'
        "REH_ETC=/etc/caddy/rehearsal\n"
        "REH_PORT=8443\n"
        f"{_function(code, 'rehearsal_form')}\n"
        f'rehearsal_form "{source}"\n'
    )
    done = subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
    return done.stdout


class TestTheSecondInstancesUnit:
    def test_runs_its_own_configuration_and_never_the_serving_one(self) -> None:
        text = _UNIT.read_text(encoding="utf-8")

        (start,) = _directive(text, "ExecStart")
        assert "/etc/caddy/rehearsal/Caddyfile" in start
        assert "--config /etc/caddy/Caddyfile" not in start

    def test_ships_the_stock_reload_line_so_the_drop_in_has_something_to_reset(self) -> None:
        """Sub-step (iii)'s negative fixture puts this line back and asserts it reaches nothing."""
        text = _UNIT.read_text(encoding="utf-8")

        (reload_line,) = _directive(text, "ExecReload")
        assert "--address" not in reload_line
        assert reload_line.startswith("/usr/bin/caddy reload --config /etc/caddy/rehearsal/")

    def test_leaves_the_runtime_directory_to_the_migrations_drop_in(self) -> None:
        """As caddy.service does: the rehearsal proves the drop-in is what recreates the socket."""
        text = _UNIT.read_text(encoding="utf-8")

        assert _directive(text, "RuntimeDirectory") == []
        assert _directive(text, "RuntimeDirectoryMode") == []

    def test_cannot_be_enabled(self) -> None:
        """No [Install]: a second Caddy must not survive a reboot."""
        unit = _code(_UNIT.read_text(encoding="utf-8"))

        assert "[Install]" not in unit
        assert _directive(unit, "WantedBy") == []

    def test_binds_no_privileged_port(self) -> None:
        """The rejected-load fixture binds :443 and must fail; no capability makes that certain."""
        unit = _code(_UNIT.read_text(encoding="utf-8"))

        assert _directive(unit, "AmbientCapabilities") == []
        assert _directive(unit, "User") == ["caddy"]


class TestTheHarness:
    def test_is_executable_and_strict(self) -> None:
        text = _SCRIPT.read_text(encoding="utf-8")

        assert os.access(_SCRIPT, os.X_OK)
        assert "set -euo pipefail" in text

    def test_hands_the_command_every_path_and_address_of_the_second_instance(self) -> None:
        code = _code(_SCRIPT.read_text(encoding="utf-8"))
        call = code[code.index("release rehearse") :]

        assert sorted(re.findall(r"--[a-z-]+", call)) == sorted(
            [
                "--unit",
                "--caddyfile",
                "--caddyfile-source",
                "--rejected-source",
                "--unsuffixed-source",
                "--fragment",
                "--releases",
                "--admin",
                "--tcp-admin",
                "--drop-in",
                "--runtime-dir",
                "--release-group",
                "--unprivileged-user",
            ]
        )

    def test_names_no_production_path_in_anything_it_drives(self) -> None:
        """The rehearsal command refuses two of these by name; the harness never offers them."""
        call = _code(_SCRIPT.read_text(encoding="utf-8"))
        call = call[call.index("release rehearse") :]

        for production in (
            '"caddy"',
            "/run/caddy/",
            "/var/www/lovspor-releases",
            "caddy.service.d",
        ):
            assert production not in call, production

    def test_asserts_nothing_the_rehearsal_asserts(self) -> None:
        """Every one of (i)-(v) is Python; the harness never reads the running host itself."""
        code = _code(_SCRIPT.read_text(encoding="utf-8"))

        for reading in ("curl", "systemctl show", "config/", "stat -c", "systemctl reload"):
            assert reading not in code, reading

    def test_takes_the_second_instance_down_however_it_ends(self) -> None:
        code = _code(_SCRIPT.read_text(encoding="utf-8"))

        assert "trap teardown EXIT" in code
        teardown = _function(code, "teardown")
        assert 'systemctl stop "$UNIT"' in teardown
        assert "/etc/systemd/system/$UNIT.service" in teardown
        assert "$REH_ETC" in teardown and "$REH_ROOT" in teardown

    def test_builds_the_envelope_as_the_build_user(self) -> None:
        code = _code(_SCRIPT.read_text(encoding="utf-8"))

        assert 'sudo -u "$BUILD_USER" "$LOVSPOR" release build' in code
        assert "--live none" in code

    def test_refuses_to_run_as_anyone_but_root(self) -> None:
        code = _code(_SCRIPT.read_text(encoding="utf-8"))

        assert '[ "$(id -u)" -eq 0 ] || die' in code


class TestRehearsalForm:
    """The harness's one piece of logic, run over the repository's own Caddyfiles."""

    def test_moves_the_admin_socket_the_fragment_and_the_log_of_the_new_caddyfile(self) -> None:
        rewritten = _rehearsal_form(_NEW_CADDYFILE)

        assert f"admin {_REH_ADMIN}|0660" in rewritten
        assert "unix//run/caddy/admin.sock" not in rewritten
        assert "/etc/caddy/rehearsal/lovspor-release.caddy" in rewritten
        assert "/var/log/caddy/lovspor-rehearsal.log" in rewritten

    def test_replaces_the_site_host_with_a_loopback_port_and_no_tls(self) -> None:
        """An explicit http:// scheme, so the second instance asks for no certificate."""
        rewritten = _rehearsal_form(_NEW_CADDYFILE)

        assert "http://localhost:8443 {" in rewritten
        assert not re.search(r"^\{\$LOVSPOR_DOMAIN\} \{$", rewritten, re.MULTILINE)

    def test_leaves_every_other_line_of_the_new_caddyfile_alone(self) -> None:
        original = _NEW_CADDYFILE.read_text(encoding="utf-8").splitlines()
        rewritten = _rehearsal_form(_NEW_CADDYFILE).splitlines()

        assert len(rewritten) == len(original)
        changed = [line for before, line in zip(original, rewritten, strict=True) if before != line]
        assert len(changed) == 4, changed

    def test_rewrites_a_pre_envelope_caddyfile_the_same_way(self, tmp_path: Path) -> None:
        """The previous Caddyfile has no global block, so only the host and the log move."""
        previous = tmp_path / "Caddyfile"
        previous.write_text(
            "{$LOVSPOR_DOMAIN} {\n"
            "\troot * /var/www/lovspor\n"
            "\tlog {\n\t\toutput file /var/log/caddy/lovspor.log\n\t}\n"
            "}\n",
            encoding="utf-8",
        )

        rewritten = _rehearsal_form(previous)

        assert rewritten.startswith("http://localhost:8443 {\n")
        assert "/var/log/caddy/lovspor-rehearsal.log" in rewritten
        assert "root * /var/www/lovspor\n" in rewritten


class TestRunbook:
    def test_the_readme_says_the_rehearsal_authorises_the_migration(self) -> None:
        text = _README.read_text(encoding="utf-8")

        assert "rehearse-migration.sh" in text
        assert _CADDY_UNIT_NAME in text
        assert "authorises" in text

    def test_the_readme_says_the_rehearsal_cannot_run_in_ci(self) -> None:
        """The honest constraint, where the operator reads it and not only in a PR."""
        text = _README.read_text(encoding="utf-8")

        assert "cannot run in CI" in text
