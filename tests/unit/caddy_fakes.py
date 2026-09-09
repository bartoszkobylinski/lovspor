"""A Caddy host in a box, for the control-plane tests (ADR-0014 Decision 6).

``FakeCaddy`` plays the three parts the transaction talks to: the
``Runner`` (``caddy adapt``, ``caddy validate``, ``systemctl reload
caddy``), the ``AdminClient`` (``GET /config/``) and the process itself
(a reload loads the composed Caddyfile; a restart loads it whole). Its
adapter is a toy — it understands the directives the host's Caddyfile,
the release fragment and the corpus redirect maps use, and nothing else
— but it adapts real files from disk, so a fragment naming release A's
root beside release B's redirect map adapts to a configuration equal to
neither, exactly as Caddy's would.
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
_ADMIN_DOWN = "connect: no such file or directory"


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


def toy_adapt(caddyfile: Path, env: Mapping[str, str]) -> dict[str, Any]:
    """The Caddyfile as Caddy's JSON: one server, one site route, a subroute of routes."""
    lines = _inline_imports(_resolve(caddyfile.read_text(encoding="utf-8"), env), env)
    lines = [line for line in lines if line and not line.startswith("#")]
    if not lines or not lines[0].endswith("{"):
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
    return {"apps": {"http": {"servers": {"srv0": {"listen": [":443"], "routes": [site]}}}}}


class FakeCaddy:
    """Runner + AdminClient + the process, over real files."""

    def __init__(self, caddyfile: Path) -> None:
        self.caddyfile = caddyfile
        self.running: dict[str, Any] | None = None
        self.admin_up = True
        self.fail_reloads = 0
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.reloads = 0

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        self.calls.append((tuple(argv), dict(env)))
        match list(argv):
            case ["caddy", "adapt", "--config", path, "--adapter", "caddyfile"]:
                return self._adapt(Path(path), env)
            case ["caddy", "validate", "--config", path, "--adapter", "caddyfile"]:
                done = self._adapt(Path(path), env)
                return Completed(done.returncode, "", done.stderr)
            case ["systemctl", "reload", "caddy"]:
                return self._reload()
        raise AssertionError(f"unexpected command: {argv}")

    def _adapt(self, path: Path, env: Mapping[str, str]) -> Completed:
        try:
            return Completed(0, json.dumps(toy_adapt(path, env)), "")
        except (AdaptError, OSError) as error:
            return Completed(1, "", f"Error: adapting config: {error}")

    def _reload(self) -> Completed:
        if self.fail_reloads:
            self.fail_reloads -= 1
            return Completed(1, "", "Job for caddy.service failed because the control process")
        self.reloads += 1
        self.restart()
        return Completed(0, "", "")

    def restart(self) -> None:
        """Load the composed Caddyfile whole, with the active fragment."""
        self.running = toy_adapt(self.caddyfile, {})

    def load(self, config: dict[str, Any]) -> None:
        """A hand ``POST /load``: the running configuration changes, the files do not."""
        self.running = copy.deepcopy(config)

    def running_config(self) -> object:
        if not self.admin_up:
            raise UnobservableError("admin_unreachable", _ADMIN_DOWN)
        if self.running is None:
            raise UnobservableError("admin_unreachable", "connection refused")
        return copy.deepcopy(self.running)
