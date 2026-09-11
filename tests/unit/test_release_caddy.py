"""Caddy as the release procedure sees it: pairs, argv, the admin endpoint (ADR-0014 Decision 6)."""

import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.release.caddy import (
    DEFAULT_ADMIN,
    DEFAULT_CADDYFILE,
    DEFAULT_FRAGMENT,
    FRAGMENT_ENV,
    Completed,
    ConfigPair,
    FallbackAdminClient,
    HttpxAdminClient,
    SubprocessRunner,
    adapt,
    adapt_config,
    admin_base,
    admin_listen,
    canonical_hash,
    config_pair,
    reload,
    validate,
)
from lovspor.release.errors import CommitRefusedError, ControlPlaneError, UnobservableError

ID_A = "a" * 64
ID_B = "b" * 64


def _site(routes: list[dict[str, Any]], host: str = "lovspor.no") -> dict[str, Any]:
    return {
        "match": [{"host": [host]}],
        "handle": [{"handler": "subroute", "routes": routes}],
        "terminal": True,
    }


def _config(*sites: dict[str, Any]) -> dict[str, Any]:
    servers = {"srv0": {"listen": [":443"], "routes": list(sites)}}
    return {"admin": {"listen": DEFAULT_ADMIN}, "apps": {"http": {"servers": servers}}}


def _routes(content_id: str, root: str = "/r/a") -> list[dict[str, Any]]:
    return [
        {"handle": [{"handler": "vars", "lovspor_release": content_id}]},
        {
            "match": [{"path": ["/lov/*"]}],
            "handle": [
                {
                    "handler": "subroute",
                    "routes": [
                        {"handle": [{"handler": "vars", "root": f"{root}/corpus"}]},
                        {"handle": [{"handler": "file_server"}]},
                    ],
                }
            ],
        },
    ]


class TestConfigPair:
    def test_names_the_release_by_its_var_and_hashes_the_site_routes(self) -> None:
        routes = _routes(ID_A)

        pair = config_pair(_config(_site(routes)))

        assert pair == ConfigPair(release_id=ID_A, config_hash=canonical_hash(routes))
        assert pair.describe() == f"({ID_A}, {canonical_hash(routes)[:12]})"

    def test_the_hash_is_canonical_over_key_order_and_whitespace(self) -> None:
        routes = _routes(ID_A)
        reordered = json.loads(json.dumps(routes)[::-1][::-1])  # a copy
        reordered[0] = {"handle": [{"lovspor_release": ID_A, "handler": "vars"}]}

        assert config_pair(_config(_site(routes))) == config_pair(_config(_site(reordered)))
        assert canonical_hash({"b": 1, "a": [1, 2]}) == canonical_hash({"a": [1, 2], "b": 1})
        assert canonical_hash(json.loads('{"a":\n 1}')) == canonical_hash({"a": 1})

    def test_the_hash_is_the_sha256_of_the_compact_sorted_utf_8_serialisation(self) -> None:
        """No insignificant whitespace, keys sorted, non-ASCII kept as is: the bytes are fixed."""
        subtree = {"b": 1, "a": ["\u00e6\u00f8\u00e5", 2]}

        assert (
            canonical_hash(subtree)
            == hashlib.sha256('{"a":["\u00e6\u00f8\u00e5",2],"b":1}'.encode()).hexdigest()
        )

    def test_the_same_id_with_one_handler_changed_is_another_pair(self) -> None:
        """The id alone is never trusted: a hand-edited handler moves the hash."""
        edited = config_pair(_config(_site(_routes(ID_A, root="/r/b"))))

        original = config_pair(_config(_site(_routes(ID_A))))
        assert edited.release_id == original.release_id == ID_A
        assert edited.config_hash != original.config_hash

    def test_the_host_matcher_is_outside_the_hash(self) -> None:
        one = config_pair(_config(_site(_routes(ID_A), host="lovspor.no")))
        other = config_pair(_config(_site(_routes(ID_A), host="lovspor.test")))

        assert one == other

    def test_a_configuration_without_the_var_names_no_release(self) -> None:
        routes = [{"handle": [{"handler": "vars", "root": "/var/www/lovspor"}]}]
        config = _config(_site(routes))

        pair = config_pair(config)

        assert pair.release_id is None
        assert pair.config_hash == canonical_hash(config["apps"]["http"]["servers"])
        assert pair.describe().startswith("(none, ")

    @pytest.mark.parametrize(
        "value",
        ["", " ", "\t", "not-an-id", "A" * 64, "a" * 63, "a" * 65, ID_A + " ", " " + ID_A],
    )
    def test_a_release_var_that_is_not_a_release_id_names_no_release(self, value: str) -> None:
        """R is read back off Caddy's own admin API, so the var is untrusted input.

        A Caddyfile placeholder that expands to nothing leaves the var present and
        empty; a hand edit leaves whatever was typed. Neither names a release, and
        neither may reach the marker, which refuses anything but a release id --
        as a traceback, where the operator is owed a named refusal (issue #271).
        """
        config = _config(_site(_routes(value)))

        pair = config_pair(config)

        assert pair.release_id is None
        assert pair.config_hash == canonical_hash(config["apps"]["http"]["servers"])
        assert pair.describe().startswith("(none, ")

    @pytest.mark.parametrize("value", [None, 42, True, [ID_A], {"id": ID_A}])
    def test_a_release_var_that_is_not_a_string_names_no_release(self, value: object) -> None:
        """The admin API answers JSON: the var can be any node, not only a string."""
        routes = [{"handle": [{"handler": "vars", "lovspor_release": value}]}]

        assert config_pair(_config(_site(routes))).release_id is None

    def test_the_key_names_a_release_only_on_a_vars_handler(self) -> None:
        routes = [{"handle": [{"handler": "file_server", "lovspor_release": ID_A}]}]

        assert config_pair(_config(_site(routes))).release_id is None

    def test_a_var_that_is_not_a_release_id_never_makes_the_pair_ambiguous(self) -> None:
        """Only names of releases can disagree about which release is served."""
        mixed = _config(_site(_routes(ID_A) + _routes("")))

        assert config_pair(mixed).release_id == ID_A

    def test_two_release_vars_are_refused_as_ambiguous(self) -> None:
        both = _config(_site(_routes(ID_A)), _site(_routes(ID_B), host="other"))
        with pytest.raises(ControlPlaneError, match="more than one lovspor_release"):
            config_pair(both)

        mixed = _config(_site(_routes(ID_A) + _routes(ID_B)))
        with pytest.raises(ControlPlaneError, match="more than one lovspor_release"):
            config_pair(mixed)

    @pytest.mark.parametrize("config", [None, [], {}, {"apps": []}, {"apps": {"http": {}}}])
    def test_anything_without_servers_is_no_release(self, config: object) -> None:
        pair = config_pair(config)

        assert pair.release_id is None
        assert pair.config_hash == canonical_hash(None)

    def test_a_server_without_routes_is_no_release_hashed_over_the_servers(self) -> None:
        servers = {"srv0": {"listen": [":443"]}, "srv1": "not a server"}

        pair = config_pair({"apps": {"http": {"servers": servers}}})

        assert pair == ConfigPair(release_id=None, config_hash=canonical_hash(servers))

    def test_only_a_lone_subroute_is_unwrapped_for_the_hash(self) -> None:
        """A subroute beside another handler is not the site block's one subroute."""
        inner = [{"handle": [{"handler": "vars", "lovspor_release": ID_A}]}]
        handle = [{"handler": "subroute", "routes": inner}, {"handler": "file_server"}]
        route = {"match": [{"host": ["x"]}], "handle": handle}

        pair = config_pair(_config(route))

        assert pair == ConfigPair(release_id=ID_A, config_hash=canonical_hash(handle))
        assert pair.config_hash != canonical_hash(inner)

    def test_a_site_route_without_a_single_subroute_hashes_its_handle_list(self) -> None:
        handle = [{"handler": "vars", "lovspor_release": ID_A}, {"handler": "file_server"}]
        route = {"match": [{"host": ["x"]}], "handle": handle}

        pair = config_pair(_config(route))

        assert pair == ConfigPair(release_id=ID_A, config_hash=canonical_hash(handle))


class TestAdminListen:
    def test_the_global_admin_option_or_none(self) -> None:
        assert admin_listen(_config(_site(_routes(ID_A)))) == DEFAULT_ADMIN
        assert admin_listen({"admin": {"listen": "unix//run/x.sock|0660"}}) == (
            "unix//run/x.sock|0660"
        )

    @pytest.mark.parametrize(
        "config",
        [None, [], {}, {"admin": None}, {"admin": []}, {"admin": {}}, {"admin": {"listen": ""}}],
    )
    def test_anything_without_a_listen_address_is_none(self, config: object) -> None:
        assert admin_listen(config) is None


class RecordingRunner:
    def __init__(self, *answers: Completed) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        self.calls.append((tuple(argv), dict(env)))
        return self.answers.pop(0)


class TestCommands:
    def test_adapt_runs_a_fixed_argv_with_the_fragment_in_the_environment(
        self, tmp_path: Path
    ) -> None:
        config = _config(_site(_routes(ID_A)))
        runner = RecordingRunner(Completed(0, json.dumps(config), ""))

        pair = adapt(runner, tmp_path / "Caddyfile", tmp_path / "next")

        assert pair.release_id == ID_A
        assert runner.calls == [
            (
                (
                    "caddy",
                    "adapt",
                    "--config",
                    str(tmp_path / "Caddyfile"),
                    "--adapter",
                    "caddyfile",
                ),
                {FRAGMENT_ENV: str(tmp_path / "next")},
            )
        ]

    def test_adapt_config_is_the_json_itself_and_adapt_its_pair(self, tmp_path: Path) -> None:
        config = _config(_site(_routes(ID_A)))
        runner = RecordingRunner(Completed(0, json.dumps(config), ""))

        assert adapt_config(runner, tmp_path / "Caddyfile", tmp_path / "next") == config
        assert runner.calls[0][0][:2] == ("caddy", "adapt")
        assert runner.calls[0][1] == {FRAGMENT_ENV: str(tmp_path / "next")}

    def test_adapt_config_failures_are_named(self, tmp_path: Path) -> None:
        with pytest.raises(ControlPlaneError, match="caddy adapt failed .*: boom"):
            adapt_config(RecordingRunner(Completed(1, "", "boom\n")), tmp_path, tmp_path / "f")
        with pytest.raises(ControlPlaneError, match="caddy adapt produced no JSON"):
            adapt_config(RecordingRunner(Completed(0, "{", "")), tmp_path, tmp_path / "f")

    def test_adapt_failures_are_named(self, tmp_path: Path) -> None:
        with pytest.raises(ControlPlaneError, match="caddy adapt failed .*: boom"):
            adapt(RecordingRunner(Completed(1, "", "boom\n")), tmp_path, tmp_path / "f")
        with pytest.raises(ControlPlaneError) as caught:
            adapt(RecordingRunner(Completed(0, "not json", "")), tmp_path, tmp_path / "f")
        assert str(caught.value) == "caddy adapt produced no JSON"

    def test_validate_runs_the_validate_argv_and_refuses_on_failure(self, tmp_path: Path) -> None:
        runner = RecordingRunner(Completed(0, "", "Valid configuration\n"))

        validate(runner, tmp_path / "Caddyfile", tmp_path / "next")

        (argv, env) = runner.calls[0]
        assert argv[:2] == ("caddy", "validate")
        assert argv[2:] == ("--config", str(tmp_path / "Caddyfile"), "--adapter", "caddyfile")
        assert env == {FRAGMENT_ENV: str(tmp_path / "next")}
        with pytest.raises(CommitRefusedError, match="caddy validate refused .*: nope"):
            validate(RecordingRunner(Completed(1, "", "nope")), tmp_path, tmp_path / "next")

    def test_reload_is_systemctl_reload_caddy_with_no_environment(self) -> None:
        runner = RecordingRunner(Completed(0, "", ""))

        done = reload(runner)

        assert done.returncode == 0
        assert runner.calls == [(("systemctl", "reload", "caddy"), {})]

    def test_the_defaults_are_the_droplets(self) -> None:
        assert Path("/etc/caddy/Caddyfile") == DEFAULT_CADDYFILE
        assert Path("/etc/caddy/lovspor-release.caddy") == DEFAULT_FRAGMENT
        assert DEFAULT_ADMIN == "unix//run/caddy/admin.sock"


class TestSubprocessRunner:
    def test_runs_the_argv_with_the_overlay_and_captures_output(self) -> None:
        argv = [sys.executable, "-c", "import os, sys; print(os.environ['X']); sys.exit(3)"]

        done = SubprocessRunner().run(argv, {"X": "y"})

        assert done == Completed(3, "y\n", "")

    def test_a_missing_binary_is_a_named_refusal(self) -> None:
        with pytest.raises(ControlPlaneError, match="cannot run /nonexistent/caddy"):
            SubprocessRunner().run(["/nonexistent/caddy", "adapt"], {})

    def test_explicitly_disables_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, object] = {}

        def fake_run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        SubprocessRunner().run(["caddy", "adapt"], {})

        assert seen["check"] is False


class TestAdminAddress:
    def test_a_unix_socket_address_dials_the_socket(self) -> None:
        base, transport = admin_base("unix//run/caddy/admin.sock")

        assert base == "http://127.0.0.1"
        assert isinstance(transport, httpx.HTTPTransport)
        assert transport._pool._uds == "/run/caddy/admin.sock"

    @pytest.mark.parametrize("address", ["localhost:2019", "tcp/localhost:2019"])
    def test_a_tcp_address_is_a_loopback_url(self, address: str) -> None:
        assert admin_base(address) == ("http://localhost:2019", None)


class _UnixSocketAdmin:
    """One canned ``GET /config/`` answer over a real Unix socket, served from a thread."""

    def __init__(self, path: Path, body: bytes) -> None:
        self.received = b""
        self._body = body
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen(1)
        self._server.settimeout(5)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            connection, _ = self._server.accept()
        except OSError:
            return
        with connection:
            connection.settimeout(5)
            self.received = connection.recv(65536)
            head = (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(self._body)
            )
            connection.sendall(head + self._body)

    def close(self) -> None:
        self._server.close()
        self._thread.join(timeout=5)


class TestHttpxAdminClient:
    def test_default_and_explicit_timeouts_are_preserved(self) -> None:
        assert HttpxAdminClient("localhost:2019").timeout_seconds == 5.0
        assert HttpxAdminClient("localhost:2019", 1.25).timeout_seconds == 1.25

    def test_reads_the_running_configuration(self, httpx_mock: HTTPXMock) -> None:
        config = _config(_site(_routes(ID_A)))
        httpx_mock.add_response(url="http://localhost:2019/config/", json=config)

        assert HttpxAdminClient("localhost:2019").running_config() == config

    def test_reads_over_the_socket_transport(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url="http://127.0.0.1/config/", json={"apps": {}})

        assert HttpxAdminClient(DEFAULT_ADMIN).running_config() == {"apps": {}}

    def test_no_answer_is_unobservable_admin_unreachable(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))

        with pytest.raises(UnobservableError, match="admin_unreachable") as caught:
            HttpxAdminClient("localhost:2019").running_config()
        assert caught.value.reason == "admin_unreachable"
        assert "localhost:2019" in caught.value.detail

    def test_a_non_200_or_non_json_answer_is_unobservable(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url="http://localhost:2019/config/", status_code=403)
        with pytest.raises(UnobservableError) as forbidden:
            HttpxAdminClient("localhost:2019").running_config()
        assert (forbidden.value.reason, forbidden.value.detail) == (
            "admin_unreachable",
            "localhost:2019: HTTP 403",
        )

        httpx_mock.add_response(url="http://localhost:2019/config/", text="<html>")
        with pytest.raises(UnobservableError) as markup:
            HttpxAdminClient("localhost:2019").running_config()
        assert (markup.value.reason, markup.value.detail) == (
            "admin_unreachable",
            "localhost:2019: /config/ is not JSON",
        )

    def test_passes_configured_timeout_to_the_request(
        self, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[object] = []
        original = httpx.Client.get

        def recording_get(client: httpx.Client, url: str, **kwargs: object) -> httpx.Response:
            seen.append(kwargs.get("timeout", "missing"))
            return original(client, url, **kwargs)

        monkeypatch.setattr(httpx.Client, "get", recording_get)
        httpx_mock.add_response(url="http://localhost:2019/config/", json={})

        HttpxAdminClient("localhost:2019", 1.25).running_config()

        assert seen == [1.25]

    def test_the_timeout_is_the_clients_on_every_leg_five_seconds_by_default(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url="http://localhost:2019/config/", json={}, is_reusable=True)

        HttpxAdminClient("localhost:2019").running_config()
        HttpxAdminClient("localhost:2019", timeout_seconds=2.5).running_config()

        timeouts = [request.extensions["timeout"] for request in httpx_mock.get_requests()]
        assert timeouts == [
            {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0},
            {"connect": 2.5, "read": 2.5, "write": 2.5, "pool": 2.5},
        ]

    def test_a_unix_address_dials_that_socket_and_nothing_on_the_network(self) -> None:
        """Caddy's default admin endpoint is a socket; the request has to reach it."""
        body = b'{"apps": {"http": {"servers": {}}}}'
        # tempfile, not tmp_path: sun_path is limited to ~100 bytes, and pytest's
        # per-test directory under macOS's /var/folders/... is longer than that.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admin.sock"
            admin = _UnixSocketAdmin(path, body)
            try:
                config = HttpxAdminClient(f"unix/{path}").running_config()
            finally:
                admin.close()

        assert config == json.loads(body)
        assert admin.received.startswith(b"GET /config/ HTTP/1.1\r\n")
        assert b"host: 127.0.0.1\r\n" in admin.received.lower()


class _Table:
    """Admin clients by address: a config, or the refusal ``UnobservableError`` carries."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, address: str) -> "_Table._Client":
        return _Table._Client(self, address)

    class _Client:
        def __init__(self, table: "_Table", address: str) -> None:
            self.table = table
            self.address = address

        def running_config(self) -> object:
            self.table.asked.append(self.address)
            answer = self.table.answers[self.address]
            if isinstance(answer, UnobservableError):
                raise answer
            return answer


class TestFallbackAdminClient:
    def test_the_primary_answering_is_never_followed_by_the_secondary(self) -> None:
        table = _Table({"unix//s": {"apps": {}}, "localhost:2019": {"other": 1}})
        client = FallbackAdminClient("unix//s", "localhost:2019", table)

        assert client.answered is None
        assert client.running_config() == {"apps": {}}
        assert client.answered == "unix//s"
        assert table.asked == ["unix//s"]

    def test_the_secondary_answers_when_the_primary_refuses(self) -> None:
        refused = UnobservableError("admin_unreachable", "unix//s: connection refused")
        table = _Table({"unix//s": refused, "localhost:2019": {"other": 1}})
        client = FallbackAdminClient("unix//s", "localhost:2019", table)

        assert client.probe() == "localhost:2019"
        assert client.answered == "localhost:2019"
        assert client.running_config() == {"other": 1}
        assert table.asked == ["unix//s", "localhost:2019", "unix//s", "localhost:2019"]

    def test_neither_answering_names_both_in_the_details_order(self) -> None:
        table = _Table(
            {
                "unix//s": UnobservableError("admin_unreachable", "unix//s: no such file"),
                "localhost:2019": UnobservableError("admin_unreachable", "localhost:2019: refused"),
            }
        )
        client = FallbackAdminClient("unix//s", "localhost:2019", table)

        with pytest.raises(UnobservableError) as caught:
            client.probe()
        assert caught.value.reason == "admin_unreachable"
        assert caught.value.detail == "unix//s: no such file; localhost:2019: refused"
        assert client.answered is None
        assert client.addresses == ("unix//s", "localhost:2019")

    def test_connects_through_httpx_by_default(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ConnectError("refused"), url="http://localhost:2019/config/")
        httpx_mock.add_response(url="http://localhost:2020/config/", json={"apps": {}})

        client = FallbackAdminClient("localhost:2019", "localhost:2020")

        assert client.connect is HttpxAdminClient
        assert client.running_config() == {"apps": {}}
        assert client.answered == "localhost:2020"
