"""A Caddy host in a box, for the control-plane tests (ADR-0014 Decision 6).

``FakeCaddy`` plays the three parts the transaction talks to: the
``Runner`` (``caddy adapt``, ``caddy validate``, ``caddy reload
--address``, ``systemctl reload caddy``, ``systemctl daemon-reload``,
``systemctl show``), the ``AdminClient`` (``GET /config/``) and the
process itself (a reload loads the composed Caddyfile; a restart loads
it whole). Its adapter is a toy — it understands the directives the
host's Caddyfile, the release fragment and the corpus redirect maps use,
and nothing else — but it adapts real files from disk, so a fragment
naming release A's root beside release B's redirect map adapts to a
configuration equal to neither, exactly as Caddy's would.

The admin endpoint has an address. The instance listens where the
configuration it last loaded says (``admin`` in the global options
block, else ``localhost:2019``), a load delivered to any other address
is refused with *connection refused*, and a Unix-socket address is a
file: created with the ``|mode`` suffix's mode when the configuration
loads, removed when the endpoint moves away — as Caddy's is.
"""

import copy
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lovspor.release.caddy import Completed
from lovspor.release.errors import UnobservableError

_PLACEHOLDER = re.compile(r"\{\$([A-Z_]+)(?::([^}]*))?\}")
ADMIN_DOWN = "connect: no such file or directory"
"""What a client dialling an admin endpoint no process is bound to reports."""
_UNIX_PREFIX = "unix/"
DEFAULT_TCP = "localhost:2019"
"""Caddy's default admin address: what an instance without the global option listens on."""
STOCK_EXEC_RELOAD = "/usr/bin/caddy reload --config /etc/caddy/Caddyfile --force"
"""The stock unit's reload line (``caddyserver/dist``): no ``--address``."""
_JOB_FAILED = "Job for caddy.service failed because the control process"
_LOAD_REFUSED = (
    "Error: sending configuration to instance: caddy responded with error: HTTP 400: "
    '{"error":"loading config: loading new config: http app module: start: listen tcp :443"}'
)


UMASK_MODE = 0o644
"""The mode a socket gets when the admin address carries no ``|mode`` suffix.

Caddy takes it from the umask, so the real value is the unit's; the fake
pins umask 022's so a test asserting *not* ``0660`` says the same thing on
every machine. The point the fixtures make is that the mode is not the
one the suffix would have set, never which mode it is instead.
"""


class AdaptError(Exception):
    """The toy adapter refuses the Caddyfile, as ``caddy adapt`` would."""


def _resolve(text: str, env: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        value = env.get(match.group(1), os.environ.get(match.group(1)))
        if value is None:
            value = match.group(2) or ""
        return value

    return _PLACEHOLDER.sub(replace, text)


def _inline_imports(text: str, env: Mapping[str, str]) -> list[str]:
    """``import`` is a preprocessor step: the imported file's lines replace the line."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("import "):
            lines.append(line)
            continue
        pattern = line.split(None, 1)[1]
        for path in _matching(pattern):
            if not path.is_file():
                raise AdaptError(f"import {pattern}: file does not exist")
            lines.extend(_inline_imports(_resolve(path.read_text(encoding="utf-8"), env), env))
    return lines


def _matching(pattern: str) -> list[Path]:
    """A glob may match nothing; a plain path must exist."""
    if not any(char in pattern for char in "*?["):
        return [Path(pattern)]
    parent = Path(pattern).parent
    return sorted(parent.glob(Path(pattern).name))


_PLAIN: dict[str, dict[str, Any]] = {"file_server": {"handler": "file_server"}}


def _directive(line: str, matchers: dict[str, list[str]]) -> dict[str, Any] | None:
    words = line.split()
    handlers: dict[str, dict[str, Any]] = {}
    match words:
        case ["vars", name, value]:
            handlers[line] = {"handler": "vars", name: value}
        case ["root", "*", path]:
            handlers[line] = {"handler": "vars", "root": path}
        case ["reverse_proxy", upstream]:
            handlers[line] = {"handler": "reverse_proxy", "upstreams": [{"dial": upstream}]}
        case ["redir", source, target, "301"]:
            handlers[line] = {
                "match": [{"path": [source]}],
                "handler": "static_response",
                "headers": {"Location": [target]},
                "status_code": 301,
            }
        case ["respond", matcher, "410"]:
            handlers[line] = {
                "match": [{"path": matchers[matcher]}],
                "handler": "static_response",
                "status_code": 410,
            }
    return handlers.get(line, _PLAIN.get(line))


def _routes(lines: list[str], matchers: dict[str, list[str]]) -> list[dict[str, Any]]:
    """The site block's routes: one per ``handle``, the rest as bare handlers."""
    routes: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for line in lines:
        if line.startswith("@"):
            name, _, rest = line.partition(" path ")
            matchers[name] = rest.split()
        elif line.startswith("handle"):
            words = line.split()
            route: dict[str, Any] = {"handle": [{"handler": "subroute", "routes": []}]}
            if len(words) > 2:
                route["match"] = [{"path": matchers[words[1]]}]
            stack.append(route)
        elif line == "}":
            if stack and not (block := stack.pop()).get("ignore"):
                routes.append(block)
        elif line.endswith("{"):
            stack.append({"ignore": True})
        elif (handler := _directive(line, matchers)) is not None:
            if stack and not stack[-1].get("ignore"):
                stack[-1]["handle"][0]["routes"].append({"handle": [handler]})
            elif not stack:
                routes.append({"handle": [handler]})
    return routes


def _global_options(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    """A leading ``{ … }`` is the global options block; the toy knows ``admin`` alone."""
    if not lines or lines[0] != "{":
        return {}, lines
    try:
        end = lines.index("}")
    except ValueError as error:
        raise AdaptError("global options block never closes") from error
    options: dict[str, str] = {}
    for line in lines[1:end]:
        match line.split():
            case ["admin", listen]:
                options["admin"] = listen
            case _:
                raise AdaptError(f"unrecognized global option: {line}")
    return options, lines[end + 1 :]


def toy_adapt(caddyfile: Path, env: Mapping[str, str]) -> dict[str, Any]:
    """The Caddyfile as Caddy's JSON: one server, one site route, a subroute of routes."""
    lines = _inline_imports(_resolve(caddyfile.read_text(encoding="utf-8"), env), env)
    lines = [line for line in lines if line and not line.startswith("#")]
    options, lines = _global_options(lines)
    config: dict[str, Any] = {}
    if "admin" in options:
        config["admin"] = {"listen": options["admin"]}
    if not lines:
        if options:
            return config
        raise AdaptError("no site block")
    if not lines[0].endswith("{"):
        raise AdaptError("no site block")
    host = lines[0][:-1].strip()
    if not host:
        raise AdaptError("site block without a host: is LOVSPOR_DOMAIN set?")
    body = lines[1:-1] if lines[-1] == "}" else lines[1:]
    routes = _routes(body, {})
    site = {
        "match": [{"host": [name.strip() for name in host.split(",")]}],
        "handle": [{"handler": "subroute", "routes": routes}],
        "terminal": True,
    }
    config["apps"] = {"http": {"servers": {"srv0": {"listen": [":443"], "routes": [site]}}}}
    return config


def split_address(listen: str) -> tuple[str, int | None]:
    """Caddy's ``unix/<path>|<mode>``: the address a client dials, and the creation mode."""
    address, _, mode = listen.partition("|")
    return address, int(mode, 8) if mode else None


def admin_listen(config: Mapping[str, Any]) -> str:
    admin = config.get("admin")
    listen = admin.get("listen") if isinstance(admin, dict) else None
    return str(listen) if listen else DEFAULT_TCP


def _socket_path(address: str) -> Path | None:
    if address.startswith(_UNIX_PREFIX):
        return Path(address[len(_UNIX_PREFIX) :])
    return None


def _exec_reload_of(drop_in: Path | None) -> str:
    """What ``systemctl show`` reports after a ``daemon-reload``: the drop-in's, else stock."""
    if drop_in is None or not drop_in.is_file():
        return STOCK_EXEC_RELOAD
    commands = [STOCK_EXEC_RELOAD]
    for line in drop_in.read_text(encoding="utf-8").splitlines():
        if line == "ExecReload=":
            commands = []
        elif line.startswith("ExecReload="):
            commands.append(line[len("ExecReload=") :])
    return commands[-1] if commands else ""


def _address_flag(command: str) -> str | None:
    words = command.split()
    if "--address" in words:
        return words[words.index("--address") + 1]
    return None


class FakeAdmin:
    """An ``AdminClient`` bound to one address, as ``HttpxAdminClient`` is."""

    def __init__(self, caddy: "FakeCaddy", address: str) -> None:
        self.caddy = caddy
        self.address = address

    def running_config(self) -> object:
        return self.caddy.running_config_at(self.address)


class FakeCaddy:
    """Runner + AdminClient + the process, over real files."""

    def __init__(self, caddyfile: Path, drop_in: Path | None = None) -> None:
        self.caddyfile = caddyfile
        self.drop_in = drop_in
        self.running: dict[str, Any] | None = None
        self.admin_address = DEFAULT_TCP
        self.admin_up = True
        self.fail_reloads = 0
        self.knows_mode_suffix = True
        self.socket_users: set[str] = set()
        """Identities other than the caller's that can open the admin socket."""
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.reloads = 0
        self.restarts = 0
        self.daemon_reloads = 0
        self.exec_reload = _exec_reload_of(drop_in)

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        self.calls.append((tuple(argv), dict(env)))
        match list(argv):
            case ["caddy", "adapt", "--config", path, "--adapter", "caddyfile"]:
                return self._adapt(Path(path), env)
            case ["caddy", "validate", "--config", path, "--adapter", "caddyfile"]:
                done = self._adapt(Path(path), env)
                return Completed(done.returncode, "", done.stderr)
            case ["caddy", "reload", "--config", path, "--adapter", "caddyfile", "--address", to]:
                return self._reload(Path(path), env, to)
            case ["systemctl", *rest]:
                return self._systemctl(rest, argv)
            case ["sudo", "-u", user, "curl", *rest]:
                return self._as_user(user, rest)
        raise AssertionError(f"unexpected command: {argv}")

    def _as_user(self, user: str, argv: list[str]) -> Completed:
        """``curl --unix-socket`` run as another identity; only a listed user gets through.

        A socket the user may not open refuses the connection, which is
        what ``curl`` reports as exit 7 — the same exit as a socket that
        is not there, since neither answered.
        """
        wanted = argv[argv.index("--unix-socket") + 1] if "--unix-socket" in argv else ""
        listening = _socket_path(self.admin_address)
        if listening is None or str(listening) != wanted or user not in self.socket_users:
            return Completed(7, "", f"curl: (7) Couldn't connect to server ({wanted})")
        return Completed(0, json.dumps(self.running), "")

    def _systemctl(self, words: list[str], argv: Sequence[str]) -> Completed:
        match words:
            case ["reload", _]:
                return self._systemctl_reload()
            case ["restart", _]:
                return self._systemctl_restart()
            case ["daemon-reload"]:
                self.daemon_reloads += 1
                self.exec_reload = _exec_reload_of(self.drop_in)
                return Completed(0, "", "")
            case ["show", _, "-p", "ExecReload"]:
                return Completed(0, self._show_exec_reload(), "")
        raise AssertionError(f"unexpected command: {argv}")

    def _show_exec_reload(self) -> str:
        if not self.exec_reload:
            return "ExecReload=\n"
        return (
            f"ExecReload={{ path={self.exec_reload.split()[0]} ; argv[]={self.exec_reload} ; "
            "ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; "
            "status=0/0 }\n"
        )

    def _adapt(self, path: Path, env: Mapping[str, str]) -> Completed:
        try:
            config = toy_adapt(path, env)
        except (AdaptError, OSError) as error:
            return Completed(1, "", f"Error: adapting config: {error}")
        listen = admin_listen(config)
        if "|" in listen and not self.knows_mode_suffix:
            return Completed(
                1, "", f"Error: adapting config: parsing 'admin': invalid address '{listen}'"
            )
        return Completed(0, json.dumps(config), "")

    def _systemctl_restart(self) -> Completed:
        """The unit restarted: the composed Caddyfile loaded whole, the admin endpoint with it.

        Unlike a reload this needs no reachable admin endpoint, which is
        why it is the offline rollback's last step.
        """
        done = self._adapt(self.caddyfile, {})
        if done.returncode != 0:
            return Completed(1, "", _JOB_FAILED)
        self._stop()
        if self._apply(json.loads(done.stdout)) is not None:
            return Completed(1, "", _JOB_FAILED)
        self.admin_up = True
        self.restarts += 1
        return Completed(0, "", "")

    def _stop(self) -> None:
        """The process exits: Go unlinks its socket and ``RuntimeDirectory=`` clears its directory.

        So a restart never inherits a mode or a group a hand set on the
        file that was there — the whole point of the creation-mode suffix.
        """
        socket = _socket_path(self.admin_address)
        if socket is not None:
            socket.unlink(missing_ok=True)

    def _systemctl_reload(self) -> Completed:
        """The unit's ``ExecReload=`` line: the composed file, delivered where the line says."""
        if self.fail_reloads:
            self.fail_reloads -= 1
            return Completed(1, "", _JOB_FAILED)
        done = self._adapt(self.caddyfile, {})
        if done.returncode != 0:
            return Completed(1, "", _JOB_FAILED)
        target = (
            _address_flag(self.exec_reload)
            or split_address(admin_listen(json.loads(done.stdout)))[0]
        )
        if target != self.admin_address:
            return Completed(1, "", _JOB_FAILED)
        self.reloads += 1
        self.restart()
        return Completed(0, "", "")

    def _reload(self, path: Path, env: Mapping[str, str], to: str) -> Completed:
        """``caddy reload --address``: adapt ``path``, deliver it explicitly to ``to``."""
        done = self._adapt(path, env)
        if done.returncode != 0:
            return done
        if self.fail_reloads:
            self.fail_reloads -= 1
            return Completed(1, "", _LOAD_REFUSED)
        if to != self.admin_address:
            return Completed(
                1,
                "",
                f'Error: sending configuration to instance: performing request: Post "{to}'
                '/load": dial: connect: connection refused',
            )
        failure = self._apply(json.loads(done.stdout))
        if failure is not None:
            return Completed(1, "", failure)
        self.reloads += 1
        return Completed(0, "", "")

    def _apply(self, config: dict[str, Any]) -> str | None:
        """The instance runs ``config``; its admin endpoint moves to what the config names."""
        address, mode = split_address(admin_listen(config))
        socket = _socket_path(address)
        if socket is not None:
            try:
                socket.touch()
            except OSError as error:
                return f"Error: loading new config: admin: listen unix {socket}: {error}"
            socket.chmod(mode if mode is not None else UMASK_MODE)
        previous = _socket_path(self.admin_address)
        if previous is not None and address != self.admin_address:
            # Go's net.UnixListener unlinks its socket file on Close.
            previous.unlink(missing_ok=True)
        self.admin_address = address
        self.running = copy.deepcopy(config)
        return None

    def restart(self) -> None:
        """Load the composed Caddyfile whole, with the active fragment."""
        failure = self._apply(toy_adapt(self.caddyfile, {}))
        if failure is not None:
            raise AssertionError(failure)

    def load(self, config: dict[str, Any]) -> None:
        """A hand ``POST /load``: the running configuration changes, the files do not."""
        self.running = copy.deepcopy(config)

    def admin_client(self, address: str) -> FakeAdmin:
        return FakeAdmin(self, address)

    def running_config_at(self, address: str) -> object:
        """``GET /config/`` dialled at ``address``: refused unless the instance listens there."""
        if address != self.admin_address:
            raise UnobservableError("admin_unreachable", f"{address}: connection refused")
        return self.running_config()

    def running_config(self) -> object:
        if not self.admin_up:
            raise UnobservableError("admin_unreachable", ADMIN_DOWN)
        if self.running is None:
            raise UnobservableError("admin_unreachable", "connection refused")
        return copy.deepcopy(self.running)


class FakeOwnership:
    """``pwd``, ``grp`` and ``chown`` as tables: lookups answer, chowns are recorded, not done."""

    def __init__(self, users: Mapping[str, int], groups: Mapping[str, int]) -> None:
        self.users = dict(users)
        self.groups = dict(groups)
        self.chowns: list[tuple[Path, int, int]] = []

    def uid_of(self, user: str) -> int:
        return self.users[user]

    def gid_of(self, group: str) -> int:
        return self.groups[group]

    def chown(self, path: Path, uid: int, gid: int) -> None:
        self.chowns.append((path, uid, gid))
