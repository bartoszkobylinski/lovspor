"""Caddy as the release procedure sees it (ADR-0014 Decision 6).

Two boundaries, both injected so every step of the transaction is
unit-tested with fakes:

* :class:`Runner` runs one fixed-argv command, never a shell — ``caddy
  validate`` and ``caddy adapt`` over the composed Caddyfile, and
  ``systemctl reload caddy``. The Caddyfile imports the active fragment
  through the ``{$LOVSPOR_RELEASE_FRAGMENT:…}`` placeholder (the role
  ``LOVSPOR_SITE_ROOT`` played for ADR-0013), so the same file is
  validated and adapted against ``.next`` by setting that variable in
  the command's environment and nothing else.
* :class:`AdminClient` reads the running configuration, ``GET /config/``
  over the admin endpoint — a Unix socket in v1, TCP allowed for the
  migration window. Only Caddy says what it serves.

What the running configuration can attest is the *adapted JSON*: the
``import`` line is a preprocessor step and is invisible after adaptation,
so a configuration is named by the pair (``release_id``, ``config_hash``)
— the ``lovspor_release`` var the fragment left in the site block, and the
canonical SHA-256 (sorted keys, no insignificant whitespace) of that
block's ``routes`` subtree, the ``vars``, roots, redirects and proxy
handlers together. R and D are the same pair read from the two sources.
"""

import hashlib
import json
import os
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from lovspor.release.envelope import RELEASE_VAR
from lovspor.release.errors import CommitRefusedError, ControlPlaneError, UnobservableError

FRAGMENT_ENV = "LOVSPOR_RELEASE_FRAGMENT"
"""The Caddyfile placeholder naming the fragment to import; set per command."""
DEFAULT_CADDYFILE = Path("/etc/caddy/Caddyfile")
DEFAULT_FRAGMENT = Path("/etc/caddy/lovspor-release.caddy")
DEFAULT_ADMIN = "unix//run/caddy/admin.sock"
"""Caddy's own spelling of a Unix-socket admin address."""
_UNIX_PREFIX = "unix/"
_TCP_PREFIX = "tcp/"
_HTTP_OK = 200


class Completed(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        """Run ``argv`` with ``env`` laid over the process environment."""


class SubprocessRunner:
    """The real one: fixed argv, captured output, no shell."""

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        try:
            result = subprocess.run(  # noqa: S603
                list(argv),
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise ControlPlaneError(f"cannot run {argv[0]}: {error}") from error
        return Completed(result.returncode, result.stdout, result.stderr)


class AdminClient(Protocol):
    def running_config(self) -> object:
        """The JSON Caddy is running, or :class:`UnobservableError`."""


def admin_base(address: str) -> tuple[str, httpx.BaseTransport | None]:
    """``(base URL, transport)`` for Caddy's address forms: ``unix/<path>``, ``[tcp/]host:port``."""
    if address.startswith(_UNIX_PREFIX):
        # The caddy CLI dials the socket under a bogus loopback host; so do we.
        return "http://127.0.0.1", httpx.HTTPTransport(uds=address[len(_UNIX_PREFIX) :])
    return "http://" + address.removeprefix(_TCP_PREFIX), None


class HttpxAdminClient:
    """``GET /config/`` over the admin endpoint at ``address``."""

    def __init__(self, address: str = DEFAULT_ADMIN, timeout_seconds: float = 5.0) -> None:
        self.address = address
        self.timeout_seconds = timeout_seconds

    def running_config(self) -> object:
        base, transport = admin_base(self.address)
        try:
            with httpx.Client(base_url=base, transport=transport) as client:
                response = client.get("/config/", timeout=self.timeout_seconds)
        except httpx.HTTPError as error:
            raise UnobservableError("admin_unreachable", f"{self.address}: {error}") from error
        if response.status_code != _HTTP_OK:
            raise UnobservableError(
                "admin_unreachable", f"{self.address}: HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as error:
            raise UnobservableError(
                "admin_unreachable", f"{self.address}: /config/ is not JSON"
            ) from error


class ConfigPair(BaseModel):
    """What names a configuration: the release var and the site block's routes hash."""

    model_config = ConfigDict(frozen=True)

    release_id: str | None
    config_hash: str

    def describe(self) -> str:
        return f"({self.release_id or 'none'}, {self.config_hash[:12]})"


def canonical_hash(subtree: object) -> str:
    canonical = json.dumps(subtree, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _release_vars(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        if node.get("handler") == "vars" and isinstance(node.get(RELEASE_VAR), str):
            yield node[RELEASE_VAR]
        for value in node.values():
            yield from _release_vars(value)
    elif isinstance(node, list):
        for item in node:
            yield from _release_vars(item)


def _servers(config: object) -> object:
    if not isinstance(config, dict):
        return None
    http = config.get("apps", {}).get("http", {}) if isinstance(config.get("apps"), dict) else {}
    return http.get("servers") if isinstance(http, dict) else None


def _site_routes(config: object) -> list[dict[str, object]]:
    servers = _servers(config)
    if not isinstance(servers, dict):
        return []
    routes: list[dict[str, object]] = []
    for server in servers.values():
        found = server.get("routes", []) if isinstance(server, dict) else []
        routes.extend(route for route in found if isinstance(route, dict))
    return routes


def _routes_subtree(route: dict[str, object]) -> object:
    """The site block's ``routes``: the one subroute's, else the handle list itself."""
    handle = route.get("handle")
    if isinstance(handle, list) and len(handle) == 1 and isinstance(handle[0], dict):
        only = handle[0]
        if only.get("handler") == "subroute":
            return only.get("routes")
    return handle


def config_pair(config: object) -> ConfigPair:
    """The pair of an adapted or running configuration; no var means no release."""
    named = [(set(_release_vars(route)), route) for route in _site_routes(config)]
    named = [(ids, route) for ids, route in named if ids]
    if not named:
        return ConfigPair(release_id=None, config_hash=canonical_hash(_servers(config)))
    if len(named) > 1 or len(named[0][0]) > 1:
        raise ControlPlaneError(f"configuration carries more than one {RELEASE_VAR} var")
    ((ids, route),) = named
    return ConfigPair(release_id=ids.pop(), config_hash=canonical_hash(_routes_subtree(route)))


def adapt(runner: Runner, caddyfile: Path, fragment: Path) -> ConfigPair:
    """What Caddy would load from ``caddyfile`` importing ``fragment``."""
    argv = ("caddy", "adapt", "--config", str(caddyfile), "--adapter", "caddyfile")
    done = runner.run(argv, {FRAGMENT_ENV: str(fragment)})
    if done.returncode != 0:
        raise ControlPlaneError(f"caddy adapt failed for {fragment}: {done.stderr.strip()}")
    try:
        config = json.loads(done.stdout)
    except ValueError as error:
        raise ControlPlaneError("caddy adapt produced no JSON") from error
    return config_pair(config)


def validate(runner: Runner, caddyfile: Path, fragment: Path) -> None:
    argv = ("caddy", "validate", "--config", str(caddyfile), "--adapter", "caddyfile")
    done = runner.run(argv, {FRAGMENT_ENV: str(fragment)})
    if done.returncode != 0:
        raise CommitRefusedError(f"caddy validate refused {fragment}: {done.stderr.strip()}")


def reload(runner: Runner) -> Completed:
    """``systemctl reload caddy`` — the drop-in's explicit-address reload line."""
    return runner.run(("systemctl", "reload", "caddy"), {})
