"""Caddy as the release procedure sees it: pairs, argv, the admin endpoint (ADR-0014 Decision 6)."""

import json
import sys
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
    HttpxAdminClient,
    SubprocessRunner,
    adapt,
    admin_base,
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

    def test_a_site_route_without_a_single_subroute_hashes_its_handle_list(self) -> None:
        handle = [{"handler": "vars", "lovspor_release": ID_A}, {"handler": "file_server"}]
        route = {"match": [{"host": ["x"]}], "handle": handle}

        pair = config_pair(_config(route))

        assert pair == ConfigPair(release_id=ID_A, config_hash=canonical_hash(handle))


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

    def test_adapt_failures_are_named(self, tmp_path: Path) -> None:
        with pytest.raises(ControlPlaneError, match="caddy adapt failed .*: boom"):
            adapt(RecordingRunner(Completed(1, "", "boom\n")), tmp_path, tmp_path / "f")
        with pytest.raises(ControlPlaneError, match="no JSON"):
            adapt(RecordingRunner(Completed(0, "not json", "")), tmp_path, tmp_path / "f")

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


class TestAdminAddress:
    def test_a_unix_socket_address_dials_the_socket(self) -> None:
        base, transport = admin_base("unix//run/caddy/admin.sock")

        assert base == "http://127.0.0.1"
        assert isinstance(transport, httpx.HTTPTransport)

    @pytest.mark.parametrize("address", ["localhost:2019", "tcp/localhost:2019"])
    def test_a_tcp_address_is_a_loopback_url(self, address: str) -> None:
        assert admin_base(address) == ("http://localhost:2019", None)


class TestHttpxAdminClient:
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
        with pytest.raises(UnobservableError, match="HTTP 403"):
            HttpxAdminClient("localhost:2019").running_config()

        httpx_mock.add_response(url="http://localhost:2019/config/", text="<html>")
        with pytest.raises(UnobservableError, match="not JSON"):
            HttpxAdminClient("localhost:2019").running_config()
