"""The Caddy host in a box knows its admin address (ADR-0014 Decision 6, Migration).

What the migration tests lean on is pinned here: a global options block
adapts to the same servers subtree and records ``admin``; a load reaches
the instance only at the address it listens on; the socket is a real
socket file with the ``|mode`` suffix's mode, left behind when the
endpoint moves away and cleared at a stop only while the loaded drop-in
sets ``RuntimeDirectory=`` (#303); an explicit-address load the instance
refuses still moves the admin endpoint first, as the droplet's Caddy
v2.11.4 did (#302), while a refused ``systemctl reload`` fails its job
before anything moves; and ``systemctl`` reflects the drop-in only after
a ``daemon-reload``.
"""

import contextlib
import json
import socket
import stat
from pathlib import Path

import pytest

from lovspor.release.caddy import FRAGMENT_ENV, Completed, config_pair
from lovspor.release.errors import UnobservableError
from tests.unit.caddy_fakes import (
    DEFAULT_TCP,
    STOCK_EXEC_RELOAD,
    AdaptError,
    FakeCaddy,
    admin_listen,
    plant_socket,
    split_address,
    toy_adapt,
)

SITE = "lovspor.test {\n\thandle {\n\t\troot * /srv/site\n\t\tfile_server\n\t}\n}\n"
UNKNOWN_OPTION = "{\n\temail x@y\n}\n"
"""A Caddyfile the toy adapter refuses, as ``caddy validate`` would."""
REFUSED_AT_START = (
    "Error: loading config: loading new config: http app module: start: listening on :443: "
    "listen tcp :443: bind: permission denied"
)
"""What the droplet's Caddy v2.11.4 reported for a load it refused at app start (#302)."""


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
        return self.load(path, address).returncode

    def load(self, path: Path, address: str) -> Completed:
        argv = ("caddy", "reload", "--config", str(path), "--adapter", "caddyfile")
        return self.caddy.run((*argv, "--address", address), {})


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


class TestToyAdapt:
    def test_a_global_options_block_leaves_the_servers_alone_and_records_admin(
        self, box: Box
    ) -> None:
        plain = toy_adapt(box.caddyfile, {})
        with_block = toy_adapt(box.new, {}, config_file=box.caddyfile)

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

    def test_the_socket_file_is_a_real_socket(self, box: Box) -> None:
        """The rollback removes nothing at the socket's name that is not a socket, so the
        file the fake makes must be one — a regular file would exercise the refusal instead."""
        box.reload(box.new, DEFAULT_TCP)

        assert stat.S_ISSOCK(box.socket_file.lstat().st_mode)

    @pytest.mark.parametrize("taken_by", ["socket", "file"])
    def test_a_load_onto_a_socket_name_already_taken_fails_and_moves_nothing(
        self, box: Box, taken_by: str
    ) -> None:
        """Binding over an existing file fails, as a fresh ``listen unix`` does.

        Whether Caddy clears a stale file before it binds has not been
        observed, so the fake does not: the pessimistic answer.
        """
        if taken_by == "socket":
            plant_socket(box.socket_file)
        else:
            box.socket_file.write_text("", encoding="utf-8")
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
                DEFAULT_TCP,
            ),
            {},
        )

        assert done.returncode == 1
        assert done.stderr.startswith(
            f"Error: loading new config: admin: listen unix {box.socket_file}"
        )
        assert box.caddy.admin_address == DEFAULT_TCP
        assert box.caddy.running_config() == before
        assert box.caddy.reloads == 0

    def test_a_load_back_over_the_socket_leaves_the_socket_file_behind(self, box: Box) -> None:
        """#303: Caddy v2.11.4 kept the file after a load moved its endpoint back to TCP."""
        box.reload(box.new, DEFAULT_TCP)

        assert box.reload(box.caddyfile, box.socket) == 0

        assert box.caddy.admin_address == DEFAULT_TCP
        assert stat.S_ISSOCK(box.socket_file.lstat().st_mode)
        with pytest.raises(UnobservableError):
            box.caddy.running_config_at(box.socket)
        assert box.caddy.running_config_at(DEFAULT_TCP) == toy_adapt(box.caddyfile, {})

    def test_a_reload_to_the_same_socket_keeps_the_socket_file(self, box: Box) -> None:
        """Forced, so the same configuration is really loaded again (#320) and rebinds nothing."""
        box.reload(box.new, DEFAULT_TCP)
        forced = ("caddy", "reload", "--config", str(box.new), "--adapter", "caddyfile")

        assert box.caddy.run((*forced, "--address", box.socket, "--force"), {}).returncode == 0

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


class TestRefusedAtStart:
    """#302: on the droplet's Caddy v2.11.4 a load refused at app start still moves the
    admin endpoint — the refused configuration's endpoint starts, the site fails to start,
    the endpoint it was listening on stops — and R stays the previous configuration."""

    def test_a_refused_load_moves_the_endpoint_to_the_socket_and_not_the_configuration(
        self, box: Box
    ) -> None:
        before = box.caddy.running_config()
        box.caddy.fail_reloads = 1

        done = box.load(box.new, DEFAULT_TCP)

        assert (done.returncode, done.stderr) == (1, REFUSED_AT_START)
        assert box.caddy.admin_address == box.socket
        assert stat.S_ISSOCK(box.socket_file.lstat().st_mode)
        assert stat.S_IMODE(box.socket_file.stat().st_mode) == 0o660
        assert box.caddy.running_config_at(box.socket) == before
        with pytest.raises(UnobservableError):
            box.caddy.running_config_at(DEFAULT_TCP)
        assert box.caddy.reloads == 0
        assert box.caddy.fail_reloads == 0

    def test_the_previous_configuration_delivered_to_the_socket_then_moves_it_back(
        self, box: Box
    ) -> None:
        before = box.caddy.running_config()
        box.caddy.fail_reloads = 1
        box.load(box.new, DEFAULT_TCP)

        forced = ("caddy", "reload", "--config", str(box.caddyfile), "--adapter", "caddyfile")
        assert box.caddy.run((*forced, "--address", box.socket, "--force"), {}).returncode == 0

        assert box.caddy.admin_address == DEFAULT_TCP
        assert box.caddy.running_config_at(DEFAULT_TCP) == before
        assert stat.S_ISSOCK(box.socket_file.lstat().st_mode)

    def test_the_same_configuration_without_force_is_unchanged_and_moves_nothing(
        self, box: Box
    ) -> None:
        """#320: the droplet's Caddy logged ``config is unchanged`` and kept the socket."""
        before = box.caddy.running_config()
        box.caddy.fail_reloads = 1
        box.load(box.new, DEFAULT_TCP)

        done = box.load(box.caddyfile, box.socket)

        assert (done.returncode, done.stderr) == (0, "")
        assert box.caddy.admin_address == box.socket
        assert box.caddy.running_config_at(box.socket) == before
        with pytest.raises(UnobservableError):
            box.caddy.running_config_at(DEFAULT_TCP)

    def test_a_refused_load_back_to_tcp_stops_the_socket_and_leaves_its_file(
        self, box: Box
    ) -> None:
        box.reload(box.new, DEFAULT_TCP)
        running = box.caddy.running_config()
        box.caddy.fail_reloads = 1

        done = box.load(box.caddyfile, box.socket)

        assert done.stderr == REFUSED_AT_START
        assert box.caddy.admin_address == DEFAULT_TCP
        assert box.caddy.running_config_at(DEFAULT_TCP) == running
        assert stat.S_ISSOCK(box.socket_file.lstat().st_mode)
        with pytest.raises(UnobservableError):
            box.caddy.running_config_at(box.socket)

    def test_a_load_to_an_address_nothing_listens_on_never_reaches_the_start(
        self, box: Box
    ) -> None:
        box.caddy.fail_reloads = 1

        done = box.load(box.new, box.socket)

        assert done.returncode == 1 and "connection refused" in done.stderr
        assert box.caddy.admin_address == DEFAULT_TCP
        assert not box.socket_file.exists()
        assert box.caddy.fail_reloads == 1

    @pytest.mark.parametrize("obstacle", ["socket already there", "no runtime directory"])
    def test_a_refused_load_whose_socket_cannot_be_bound_moves_nothing(
        self, box: Box, obstacle: str
    ) -> None:
        if obstacle == "socket already there":
            plant_socket(box.socket_file)
        else:
            box.runtime.rmdir()
        before = box.caddy.running_config()
        box.caddy.fail_reloads = 1

        done = box.load(box.new, DEFAULT_TCP)

        assert done.returncode == 1
        assert done.stderr.startswith(
            f"Error: loading new config: admin: listen unix {box.socket_file}"
        )
        assert box.caddy.admin_address == DEFAULT_TCP
        assert box.caddy.running_config_at(DEFAULT_TCP) == before
        assert box.caddy.fail_reloads == 1

    def test_a_refused_systemctl_reload_still_fails_its_job_before_anything_moves(
        self, box: Box
    ) -> None:
        """Only the explicit-address load was observed refused (#302); the stock line's job
        failure stays what the control-plane tests have always leaned on."""
        before = box.caddy.running_config()
        box.caddy.fail_reloads = 1

        done = box.caddy.run(("systemctl", "reload", "caddy"), {})

        assert done.returncode == 1 and done.stderr.startswith("Job for caddy.service failed")
        assert box.caddy.admin_address == DEFAULT_TCP
        assert box.caddy.running_config_at(DEFAULT_TCP) == before
        assert box.caddy.fail_reloads == 0
        assert box.caddy.reloads == 0


CAPTURED_PREVIOUS = Path(__file__).parent / "fixtures" / "caddy_adapt" / "previous.json"
CAPTURED_PROPOSED = CAPTURED_PREVIOUS.with_name("proposed.json")
CAPTURED_RELEASE = (
    "/var/www/lovspor-releases/3d1f7a0c9b2e4856af03c17d5e9b6284fa71c0d38e52194b7c6a0fd8e31b5947"
)


def _hides(node: object) -> list[list[str]]:
    """Every ``file_server`` handler's ``hide`` list, in document order."""
    if isinstance(node, list):
        return [hidden for item in node for hidden in _hides(item)]
    if not isinstance(node, dict):
        return []
    own = [node.get("hide", [])] if node.get("handler") == "file_server" else []
    return own + [hidden for value in node.values() for hidden in _hides(value)]


class TestHide:
    """#316: every ``file_server`` hides the Caddyfile by the path Caddy was handed.

    On the droplet, Caddy v2.11.4 ran the configuration it loaded from
    ``/etc/caddy/rehearsal/Caddyfile.pre-envelope`` with that path in both
    ``hide`` lists, where ``caddy adapt`` of ``…/Caddyfile`` named ``…/Caddyfile``
    — the whole difference, and a different pair.
    """

    def test_the_committed_capture_hides_the_path_it_was_adapted_as(self) -> None:
        """Caddy v2.8.4 on the capture's world: ``Caddyfile.previous``, as the script named it;
        the other entry is the redirect map it imports."""
        hides = _hides(json.loads(CAPTURED_PREVIOUS.read_text(encoding="utf-8")))

        assert hides and all(hidden[0] == "/etc/caddy/Caddyfile.previous" for hidden in hides)

    def test_the_committed_capture_hides_every_imported_file_sorted(self) -> None:
        """#317: beside ``Caddyfile.proposed``, the release's fragment and the redirect map the
        fragment imports — the map first, so the order is sorted, not the order of import."""
        hides = _hides(json.loads(CAPTURED_PROPOSED.read_text(encoding="utf-8")))

        listed = [
            "/etc/caddy/Caddyfile.proposed",
            f"{CAPTURED_RELEASE}/corpus/redirects.caddy",
            f"{CAPTURED_RELEASE}/release.caddy",
        ]
        assert hides == [listed, listed]

    def test_in_the_committed_capture_the_fragment_path_alone_moves_the_release_pair(self) -> None:
        """#317's evidence: the same capture with only the fragment's path spelled ``.next``."""
        raw = CAPTURED_PROPOSED.read_text(encoding="utf-8")
        fragment = f"{CAPTURED_RELEASE}/release.caddy"

        served = config_pair(json.loads(raw))
        staged = config_pair(json.loads(raw.replace(fragment, fragment + ".next")))

        assert raw.count(fragment) == 2
        assert staged.release_id == served.release_id == CAPTURED_RELEASE.rsplit("/", 1)[1]
        assert (served.config_hash[:12], staged.config_hash[:12]) == (
            "532753583c47",
            "dbeff478487d",
        )

    def _release(self, box: Box, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        """The Caddyfile importing a fragment through the placeholder; the fragment, a map."""
        monkeypatch.delenv(FRAGMENT_ENV, raising=False)
        maps = box.caddyfile.with_name("corpus")
        maps.mkdir()
        redirects = maps / "redirects.caddy"
        redirects.write_text("redir /a /b 301\n", encoding="utf-8")
        fragment = box.caddyfile.with_name("lovspor-release.caddy")
        fragment.write_text(
            f"handle {{\n\tvars lovspor_release {'a' * 64}\n\timport {maps}/redirects*.caddy\n"
            "\troot * /srv/site\n\tfile_server\n}\n",
            encoding="utf-8",
        )
        box.caddyfile.write_text(
            f"lovspor.test {{\n\timport {{${FRAGMENT_ENV}:{fragment}}}\n}}\n", encoding="utf-8"
        )
        return fragment, redirects

    def test_a_composed_release_hides_the_fragment_by_the_path_it_was_imported_from(
        self, box: Box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fragment, redirects = self._release(box, monkeypatch)
        staged = fragment.with_name(fragment.name + ".next")
        staged.write_bytes(fragment.read_bytes())

        served = toy_adapt(box.caddyfile, {})
        at_next = toy_adapt(box.caddyfile, {FRAGMENT_ENV: str(staged)})

        assert _hides(served) == [sorted(map(str, (box.caddyfile, redirects, fragment)))]
        assert _hides(at_next) == [sorted(map(str, (box.caddyfile, redirects, staged)))]
        assert config_pair(at_next).release_id == config_pair(served).release_id == "a" * 64
        assert config_pair(at_next) != config_pair(served)
        stand_in = toy_adapt(
            box.caddyfile, {FRAGMENT_ENV: str(staged)}, imported_as={staged: fragment}
        )
        assert stand_in == served

    def test_adapt_reload_and_restart_each_hide_the_fragment_they_read(
        self, box: Box, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``caddy adapt`` and ``caddy reload`` read the fragment the variable names; a unit
        reload or restart reads the placeholder's default, the active fragment."""
        fragment, _ = self._release(box, monkeypatch)
        staged = fragment.with_name(fragment.name + ".next")
        staged.write_bytes(fragment.read_bytes())
        argv = ("caddy", "adapt", "--config", str(box.caddyfile), "--adapter", "caddyfile")
        reload = ("caddy", "reload", *argv[2:], "--address", DEFAULT_TCP)

        adapted = box.caddy.run(argv, {FRAGMENT_ENV: str(staged)})
        assert str(staged) in _hides(json.loads(adapted.stdout))[0]
        assert box.caddy.run(reload, {FRAGMENT_ENV: str(staged)}).returncode == 0
        assert str(staged) in _hides(box.caddy.running_config())[0]
        for unit in (("systemctl", "restart", "caddy"), ("systemctl", "reload", "caddy")):
            box.caddy.load({"apps": {}})
            assert box.caddy.run(unit, {}).returncode == 0
            assert str(fragment) in _hides(box.caddy.running_config())[0]

    def test_the_same_bytes_at_another_path_adapt_to_another_pair(self, box: Box) -> None:
        backup = box.caddyfile.with_name("Caddyfile.pre-envelope")
        backup.write_bytes(box.caddyfile.read_bytes())

        served = toy_adapt(box.caddyfile, {})
        kept = toy_adapt(backup, {})

        assert _hides(served) == [[str(box.caddyfile)]]
        assert _hides(kept) == [[str(backup)]]
        assert config_pair(kept) != config_pair(served)
        assert toy_adapt(backup, {}, config_file=box.caddyfile) == served

    def test_adapt_reload_and_restart_each_hide_the_path_they_were_handed(self, box: Box) -> None:
        backup = box.caddyfile.with_name("Caddyfile.pre-envelope")
        backup.write_bytes(box.caddyfile.read_bytes())
        argv = ("caddy", "adapt", "--config", str(backup), "--adapter", "caddyfile")

        adapted = box.caddy.run(argv, {})
        assert _hides(json.loads(adapted.stdout)) == [[str(backup)]]
        assert box.reload(backup, DEFAULT_TCP) == 0
        assert _hides(box.caddy.running_config()) == [[str(backup)]]
        assert box.caddy.run(("systemctl", "restart", "caddy"), {}).returncode == 0
        assert _hides(box.caddy.running_config()) == [[str(box.caddyfile)]]

    def test_an_adapted_configuration_shares_nothing_with_the_next_one(self, box: Box) -> None:
        """The hide list is written into the handler, so no two adaptations may share one."""
        other = box.caddyfile.with_name("Caddyfile.other")
        other.write_bytes(box.caddyfile.read_bytes())
        first = toy_adapt(box.caddyfile, {})

        toy_adapt(other, {})

        assert _hides(first) == [[str(box.caddyfile)]]


class TestPlantSocket:
    def test_leaves_a_socket_nothing_listens_on_under_a_path_too_long_to_bind(
        self, tmp_path: Path
    ) -> None:
        """macOS caps a socket address near 104 bytes, and pytest's ``tmp_path`` is longer."""
        deep = tmp_path.joinpath(*["a-directory-name-long-enough"] * 4)
        deep.mkdir(parents=True)
        path = deep / "admin.sock"
        assert len(str(path)) > 104
        cwd = Path.cwd()

        plant_socket(path)

        assert stat.S_ISSOCK(path.lstat().st_mode)
        assert Path.cwd() == cwd
        with (
            contextlib.chdir(deep),
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client,
            pytest.raises(ConnectionRefusedError),
        ):
            client.connect(path.name)


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

    def test_restart_loads_the_file_on_disk_whole_and_needs_no_admin_endpoint(
        self, box: Box
    ) -> None:
        """The offline rollback's last step: a restart reads the Caddyfile itself,
        so it works on a box whose admin endpoint answers nowhere."""
        box.reload(box.new, DEFAULT_TCP)
        box.caddy.admin_up = False
        box.caddyfile.write_text(SITE, encoding="utf-8")

        done = box.caddy.run(("systemctl", "restart", "caddy"), {})

        assert done.returncode == 0
        assert box.caddy.restarts == 1
        assert box.caddy.admin_up is True
        assert box.caddy.admin_address == DEFAULT_TCP
        with pytest.raises(UnobservableError):
            box.caddy.running_config_at(box.socket)

    def _runtime_drop_in(self, box: Box) -> None:
        box.drop_in.parent.mkdir(parents=True, exist_ok=True)
        box.drop_in.write_text(
            "[Service]\nRuntimeDirectory=caddy\nRuntimeDirectoryMode=2770\n", encoding="utf-8"
        )

    @pytest.mark.parametrize(
        ("drop_in", "cleared"),
        [
            ("none", False),
            ("written, not loaded", False),
            ("loaded", True),
            ("loaded, then removed and reloaded", False),
        ],
    )
    def test_a_stop_clears_socket_files_only_under_a_loaded_runtime_directory(
        self, box: Box, drop_in: str, cleared: bool
    ) -> None:
        """#303: once the drop-in that set ``RuntimeDirectory=`` was gone, a dead socket
        file survived the offline rollback's restart and a second one. Whether a live
        socket's file outlives its process was not observed; left, it is pessimistic."""
        box.reload(box.new, DEFAULT_TCP)
        box.reload(box.caddyfile, box.socket)
        if drop_in != "none":
            self._runtime_drop_in(box)
        if drop_in.startswith("loaded"):
            box.caddy.run(("systemctl", "daemon-reload"), {})
        if drop_in.endswith("reloaded"):
            box.drop_in.unlink()
            box.caddy.run(("systemctl", "daemon-reload"), {})

        assert box.caddy.run(("systemctl", "restart", "caddy"), {}).returncode == 0

        assert box.socket_file.exists() is not cleared
        assert box.caddy.admin_address == DEFAULT_TCP

    def test_a_restart_under_runtime_directory_binds_a_fresh_socket_with_the_suffix_mode(
        self, box: Box
    ) -> None:
        """So a restart never inherits a mode a hand set: the point of the creation-mode suffix."""
        self._runtime_drop_in(box)
        box.caddy.run(("systemctl", "daemon-reload"), {})
        box.reload(box.new, DEFAULT_TCP)
        box.caddyfile.write_text(box.new.read_text(encoding="utf-8"), encoding="utf-8")
        box.socket_file.chmod(0o600)

        assert box.caddy.run(("systemctl", "restart", "caddy"), {}).returncode == 0

        assert stat.S_IMODE(box.socket_file.stat().st_mode) == 0o660
        assert box.caddy.running_config_at(box.socket) == toy_adapt(box.caddyfile, {})

    def test_a_restart_of_a_caddyfile_that_does_not_adapt_fails(self, box: Box) -> None:
        box.caddyfile.write_text(UNKNOWN_OPTION, encoding="utf-8")

        done = box.caddy.run(("systemctl", "restart", "caddy"), {})

        assert done.returncode == 1
        assert done.stderr.startswith("Job for caddy.service failed")
        assert box.caddy.restarts == 0

    def test_an_unknown_command_is_an_assertion(self, box: Box) -> None:
        with pytest.raises(AssertionError, match="unexpected command"):
            box.caddy.run(("systemctl", "isolate", "rescue.target"), {})
        with pytest.raises(AssertionError, match="unexpected command"):
            box.caddy.run(("journalctl", "-u", "caddy"), {})
