"""The Caddy host in a box knows its admin address (ADR-0014 Decision 6, Migration).

What the migration tests lean on is pinned here: a global options block
adapts to the same servers subtree and records ``admin``; a load reaches
the instance only at the address it listens on; the socket is a file
with the ``|mode`` suffix's mode while the endpoint is there; and
``systemctl`` reflects the drop-in only after a ``daemon-reload``.
"""

import stat
from pathlib import Path

import pytest

from lovspor.release.caddy import FRAGMENT_ENV
from lovspor.release.errors import UnobservableError
from tests.unit.caddy_fakes import (
    DEFAULT_TCP,
    STOCK_EXEC_RELOAD,
    AdaptError,
    FakeCaddy,
    admin_listen,
    split_address,
    toy_adapt,
)

SITE = "lovspor.test {\n\thandle {\n\t\troot * /srv/site\n\t\tfile_server\n\t}\n}\n"


class Box:
    def __init__(self, tmp_path: Path) -> None:
        self.caddyfile = tmp_path / "Caddyfile"
        self.drop_in = tmp_path / "caddy.service.d" / "lovspor.conf"
        self.runtime = tmp_path / "run" / "caddy"
        self.runtime.mkdir(parents=True)
        self.socket = f"unix/{self.runtime / 'admin.sock'}"
        self.new = tmp_path / "Caddyfile.new"
        self.new.write_text(f"{{\n\tadmin {self.socket}|0660\n}}\n{SITE}", encoding="utf-8")
        self.caddyfile.write_text(SITE, encoding="utf-8")
        self.caddy = FakeCaddy(self.caddyfile, self.drop_in)
        self.caddy.restart()

    @property
    def socket_file(self) -> Path:
        return self.runtime / "admin.sock"

    def reload(self, path: Path, address: str) -> int:
        argv = ("caddy", "reload", "--config", str(path), "--adapter", "caddyfile")
        return self.caddy.run((*argv, "--address", address), {}).returncode


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


class TestToyAdapt:
    def test_a_global_options_block_leaves_the_servers_alone_and_records_admin(
        self, box: Box
    ) -> None:
        plain = toy_adapt(box.caddyfile, {})
        with_block = toy_adapt(box.new, {})

        assert with_block["apps"] == plain["apps"]
        assert "admin" not in plain
        assert with_block["admin"] == {"listen": f"{box.socket}|0660"}
        assert admin_listen(plain) == DEFAULT_TCP
        assert admin_listen(with_block) == f"{box.socket}|0660"

    def test_a_global_only_caddyfile_adapts_to_the_admin_option_alone(self, tmp_path: Path) -> None:
        probe = tmp_path / "probe"
        probe.write_text("{\n\tadmin unix//run/x.sock|0660\n}\n", encoding="utf-8")

        assert toy_adapt(probe, {}) == {"admin": {"listen": "unix//run/x.sock|0660"}}

    def test_an_unknown_global_option_and_an_unclosed_block_are_refused(
        self, tmp_path: Path
    ) -> None:
        unknown = tmp_path / "unknown"
        unknown.write_text("{\n\temail x@y\n}\n" + SITE, encoding="utf-8")
        unclosed = tmp_path / "unclosed"
        unclosed.write_text("{\n\tadmin off\n", encoding="utf-8")
        empty = tmp_path / "empty"
        empty.write_text("# nothing\n", encoding="utf-8")

        with pytest.raises(AdaptError, match="unrecognized global option: email x@y"):
            toy_adapt(unknown, {})
        with pytest.raises(AdaptError, match="never closes"):
            toy_adapt(unclosed, {})
        with pytest.raises(AdaptError, match="no site block"):
            toy_adapt(empty, {})

    def test_split_address_separates_the_creation_mode(self) -> None:
        assert split_address("unix//run/caddy/admin.sock|0660") == (
            "unix//run/caddy/admin.sock",
            0o660,
        )
        assert split_address("localhost:2019") == ("localhost:2019", None)


class TestReloadAddress:
    def test_a_load_delivered_to_the_wrong_address_is_refused_and_changes_nothing(
        self, box: Box
    ) -> None:
        before = box.caddy.running_config()

        done = box.caddy.run(
            (
                "caddy",
                "reload",
                "--config",
                str(box.new),
                "--adapter",
                "caddyfile",
                "--address",
                box.socket,
            ),
            {FRAGMENT_ENV: "x"},
        )

        assert done.returncode == 1
        assert "connection refused" in done.stderr and box.socket in done.stderr
        assert box.caddy.running_config() == before
        assert box.caddy.admin_address == DEFAULT_TCP
        assert not box.socket_file.exists()
        assert box.caddy.reloads == 0

    def test_a_load_over_tcp_moves_the_endpoint_to_the_socket(self, box: Box) -> None:
        assert box.reload(box.new, DEFAULT_TCP) == 0

        assert box.caddy.admin_address == box.socket
        assert box.caddy.reloads == 1
        assert stat.S_IMODE(box.socket_file.stat().st_mode) == 0o660
        assert box.caddy.running_config_at(box.socket) == toy_adapt(box.new, {})
        with pytest.raises(UnobservableError) as caught:
            box.caddy.running_config_at(DEFAULT_TCP)
        assert caught.value.detail == f"{DEFAULT_TCP}: connection refused"
        assert box.caddy.admin_client(box.socket).running_config() == toy_adapt(box.new, {})
        with pytest.raises(UnobservableError):
            box.caddy.admin_client(DEFAULT_TCP).running_config()

    def test_a_load_back_over_the_socket_removes_the_socket_file(self, box: Box) -> None:
        box.reload(box.new, DEFAULT_TCP)

        assert box.reload(box.caddyfile, box.socket) == 0

        assert box.caddy.admin_address == DEFAULT_TCP
        assert not box.socket_file.exists()
        assert box.caddy.running_config_at(DEFAULT_TCP) == toy_adapt(box.caddyfile, {})

    def test_a_reload_to_the_same_socket_keeps_the_socket_file(self, box: Box) -> None:
        box.reload(box.new, DEFAULT_TCP)

        assert box.reload(box.new, box.socket) == 0

        assert box.socket_file.exists() and box.caddy.admin_address == box.socket
        assert box.caddy.reloads == 2

    def test_a_socket_without_its_runtime_directory_fails_the_load(self, box: Box) -> None:
        box.runtime.rmdir()

        done = box.caddy.run(
            (
                "caddy",
                "reload",
                "--config",
                str(box.new),
                "--adapter",
                "caddyfile",
                "--address",
                DEFAULT_TCP,
            ),
            {},
        )

        assert done.returncode == 1 and "listen unix" in done.stderr
        assert box.caddy.admin_address == DEFAULT_TCP
        assert box.caddy.running_config() == toy_adapt(box.caddyfile, {})
        assert box.caddy.reloads == 0

    def test_an_invalid_file_fails_the_load_with_the_adapters_error(self, box: Box) -> None:
        box.new.write_text("{\n\tadmin off\n", encoding="utf-8")

        done = box.caddy.run(
            (
                "caddy",
                "reload",
                "--config",
                str(box.new),
                "--adapter",
                "caddyfile",
                "--address",
                DEFAULT_TCP,
            ),
            {},
        )

        assert done.returncode == 1 and "never closes" in done.stderr

    def test_a_caddy_without_the_mode_suffix_refuses_to_adapt(self, box: Box) -> None:
        box.caddy.knows_mode_suffix = False

        done = box.caddy.run(
            ("caddy", "adapt", "--config", str(box.new), "--adapter", "caddyfile"), {}
        )
        old = box.caddy.run(
            ("caddy", "adapt", "--config", str(box.caddyfile), "--adapter", "caddyfile"), {}
        )

        assert done.returncode == 1 and "invalid address" in done.stderr
        assert old.returncode == 0

    def test_a_restart_that_cannot_listen_is_an_assertion(self, box: Box) -> None:
        box.caddyfile.write_text(box.new.read_text(encoding="utf-8"), encoding="utf-8")
        box.runtime.rmdir()

        with pytest.raises(AssertionError, match="listen unix"):
            box.caddy.restart()


class TestSystemctl:
    def _drop_in(self, box: Box, exec_reload: bool) -> None:
        lines = ["[Service]", "EnvironmentFile=/etc/default/caddy-lovspor"]
        if exec_reload:
            lines += ["ExecReload=", f"ExecReload={STOCK_EXEC_RELOAD} --address {box.socket}"]
        box.drop_in.parent.mkdir(parents=True, exist_ok=True)
        box.drop_in.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_show_reports_the_stock_line_until_a_daemon_reload_loads_the_drop_in(
        self, box: Box
    ) -> None:
        self._drop_in(box, exec_reload=True)

        before = box.caddy.run(("systemctl", "show", "caddy", "-p", "ExecReload"), {})
        box.caddy.run(("systemctl", "daemon-reload"), {})
        after = box.caddy.run(("systemctl", "show", "caddy", "-p", "ExecReload"), {})

        assert before.stdout.startswith("ExecReload={ path=/usr/bin/caddy ; argv[]=")
        assert "--address" not in before.stdout
        assert f"--address {box.socket} ;" in after.stdout
        assert box.caddy.daemon_reloads == 1

    def test_a_drop_in_without_the_line_and_a_reset_alone_are_reported(self, box: Box) -> None:
        self._drop_in(box, exec_reload=False)
        box.caddy.run(("systemctl", "daemon-reload"), {})
        assert box.caddy.exec_reload == STOCK_EXEC_RELOAD

        box.drop_in.write_text("[Service]\nExecReload=\n", encoding="utf-8")
        box.caddy.run(("systemctl", "daemon-reload"), {})
        shown = box.caddy.run(("systemctl", "show", "caddy", "-p", "ExecReload"), {})

        assert box.caddy.exec_reload == ""
        assert shown.stdout == "ExecReload=\n"

    def test_the_stock_line_fails_against_a_running_socket(self, box: Box) -> None:
        """ADR-0014 Validation (g)(iii): ``--address`` is load-bearing."""
        box.reload(box.new, DEFAULT_TCP)
        assert box.caddyfile.read_text(encoding="utf-8") == SITE  # the previous Caddyfile

        done = box.caddy.run(("systemctl", "reload", "caddy"), {})

        assert done.returncode == 1
        assert done.stderr.startswith("Job for caddy.service failed")
        assert box.caddy.admin_address == box.socket

    def test_the_drop_in_line_reaches_the_socket_once_loaded(self, box: Box) -> None:
        box.reload(box.new, DEFAULT_TCP)
        box.caddyfile.write_text(box.new.read_text(encoding="utf-8"), encoding="utf-8")
        self._drop_in(box, exec_reload=True)

        stale = box.caddy.run(("systemctl", "reload", "caddy"), {})
        box.caddy.run(("systemctl", "daemon-reload"), {})
        loaded = box.caddy.run(("systemctl", "reload", "caddy"), {})

        assert stale.returncode == 0  # the stock line derives the socket from the file itself
        assert loaded.returncode == 0
        assert box.caddy.reloads == 3

    def test_an_unknown_command_is_an_assertion(self, box: Box) -> None:
        with pytest.raises(AssertionError, match="unexpected command"):
            box.caddy.run(("systemctl", "restart", "caddy"), {})
