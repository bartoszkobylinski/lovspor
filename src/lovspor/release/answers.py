"""What an adapted Caddy configuration answers for one URL (ADR-0014 Validation).

The staged first-migration rehearsal asserts "every corpus URL the old
configuration answers is answered by the new one". That sentence needs a
definition of *answers*, and this module is it:

    an **answer** is the terminal handler that produces the response, the
    root in effect when it runs, the file that root resolves the URL to —
    by its root-relative name and by the SHA-256 of its bytes — the status
    code, and, where the handler has one, the ``Location`` header or the
    proxy upstream.

Two answers are the **same response** when everything but the root is
equal. The root is excluded deliberately and is the one difference the
migration is for: the old configuration serves the corpus through the
``lovspor-current`` symlink and the site from a hand-written directory,
the new one serves both out of the immutable release envelope. An
equality that included the root would fail on every legitimate
difference; one that excluded the file and its bytes would pass on a
configuration serving the wrong tree. So the comparison is about *what*
is served, and the roots are asserted separately and positively by
``staged`` — the corpus from ``<release>/corpus``, the site from
``<release>/site``.

The walk is Caddy's own, over Caddy's own JSON: routes in order, a
``match`` of OR'd matcher sets, ``handle`` blocks as mutually exclusive
``group`` members, ``subroute`` recursion, ``vars`` carrying ``root``
forward, and ``file_server`` / ``static_response`` / ``reverse_proxy`` as
the three terminals. It models a subset — and refuses, by name, anything
outside it: a matcher or a handler the dry-run does not understand raises
:class:`UnroutableConfigError` rather than being skipped, because a
comparison that silently ignores what it cannot read proves nothing.
"""

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from lovspor.release.errors import UnroutableConfigError

NOT_FOUND = 404
"""What a ``file_server`` answers for a URL no file under its root resolves."""
INDEX_FILE = "index.html"
"""Caddy's first default index; the corpus and the site tree write only this one."""
_TRANSPARENT = frozenset({"headers", "encode", "subroute", "vars"})
"""Handlers that shape a response without deciding it; ``subroute`` is walked, not applied."""
_KNOWN_MATCHERS = frozenset({"path", "host"})
"""``host`` is satisfied unconditionally — both Caddyfiles derive it from the same
``{$LOVSPOR_DOMAIN}`` placeholder, and :func:`hosts` is what asserts they agree."""


class Answer(BaseModel):
    """One URL's response, as the routes and the files on disk decide it."""

    model_config = ConfigDict(frozen=True)

    handler: str
    root: str | None
    served: str | None
    digest: str | None
    status: int
    location: str | None
    upstream: str | None

    def same_response(self, other: "Answer") -> bool:
        """Everything but the root: the migration moves the root on purpose."""
        return self.model_dump(exclude={"root"}) == other.model_dump(exclude={"root"})

    def describe(self) -> str:
        where = self.served or "no file"
        return f"{self.handler} {self.status} {where} from {self.root or 'no root'}"


@dataclass
class _Request:
    """The request as the walk builds it: the URL, and the vars set so far.

    ``vars`` are request-scoped in Caddy, so ``root`` set inside one route
    is still in effect in the next — which is exactly how the release
    fragment's ``handle`` blocks read.
    """

    url: str
    root: str | None = None


def matches_path(patterns: Sequence[str], url: str) -> bool:
    """Caddy's ``path`` matcher: case-insensitive, ``*`` spanning any characters."""
    return any(re.fullmatch(_as_regex(pattern), url, re.IGNORECASE) for pattern in patterns)


def _as_regex(pattern: str) -> str:
    return "".join(".*" if part == "*" else re.escape(part) for part in re.split(r"(\*)", pattern))


def _matcher_set(entry: object, url: str) -> bool:
    """One matcher set: every key in it must hold."""
    if not isinstance(entry, dict):
        raise UnroutableConfigError(f"not a matcher set: {entry!r}")
    unknown = sorted(set(entry) - _KNOWN_MATCHERS)
    if unknown:
        raise UnroutableConfigError(f"the dry-run does not model the matcher {unknown[0]!r}")
    paths = entry.get("path")
    return matches_path(paths, url) if isinstance(paths, list) else True


def _matched(route: dict[str, object], url: str) -> bool:
    """A route's ``match`` is a list of matcher sets, any of which is enough."""
    match = route.get("match")
    if not isinstance(match, list):
        return True
    return any(_matcher_set(entry, url) for entry in match)


def _static(handler: dict[str, object]) -> Answer:
    headers = handler.get("headers")
    location = headers.get("Location") if isinstance(headers, dict) else None
    status = handler.get("status_code")
    return Answer(
        handler="static_response",
        root=None,
        served=None,
        digest=None,
        status=int(status) if isinstance(status, int) else NOT_FOUND,
        location=str(location[0]) if isinstance(location, list) and location else None,
        upstream=None,
    )


def _proxy(handler: dict[str, object]) -> Answer:
    upstreams = handler.get("upstreams")
    first = upstreams[0] if isinstance(upstreams, list) and upstreams else {}
    dial = first.get("dial") if isinstance(first, dict) else None
    return Answer(
        handler="reverse_proxy",
        root=None,
        served=None,
        digest=None,
        status=200,
        location=None,
        upstream=str(dial) if dial else None,
    )


def served_file(root: Path, url: str) -> Path | None:
    """The file Caddy's ``file_server`` resolves ``url`` to under ``root``, or ``None``."""
    target = root / url.lstrip("/")
    if target.is_dir():
        target = target / INDEX_FILE
    return target if target.is_file() else None


def _hidden(path: Path, hide: tuple[str, ...]) -> bool:
    """``hide`` as this dry-run reads it: an exact path, or a bare file name."""
    return path.as_posix() in hide or path.name in hide


def _file_server(handler: dict[str, object], request: _Request) -> Answer:
    raw = handler.get("hide")
    hide = tuple(str(entry) for entry in raw) if isinstance(raw, list) else ()
    found = served_file(Path(request.root), request.url) if request.root else None
    if found is not None and _hidden(found, hide):
        found = None
    return Answer(
        handler="file_server",
        root=request.root,
        served=found.relative_to(request.root).as_posix() if found and request.root else None,
        digest=hashlib.sha256(found.read_bytes()).hexdigest() if found else None,
        status=200 if found else NOT_FOUND,
        location=None,
        upstream=None,
    )


def _apply(handler: object, request: _Request) -> Answer | None:
    """One handler: a terminal answers, ``vars`` records, anything unmodelled refuses."""
    if not isinstance(handler, dict):
        raise UnroutableConfigError(f"not a handler: {handler!r}")
    kind = handler.get("handler")
    if kind == "vars":
        root = handler.get("root")
        request.root = str(root) if isinstance(root, str) else request.root
        return None
    if kind == "static_response":
        return _static(handler)
    if kind == "reverse_proxy":
        return _proxy(handler)
    if kind == "file_server":
        return _file_server(handler, request)
    if kind in _TRANSPARENT:
        return None
    raise UnroutableConfigError(f"the dry-run does not model the handler {kind!r}")


def _handlers(route: dict[str, object], request: _Request) -> Answer | None:
    handle = route.get("handle")
    for handler in handle if isinstance(handle, list) else []:
        if isinstance(handler, dict) and handler.get("handler") == "subroute":
            answer = _walk(handler.get("routes"), request)
        else:
            answer = _apply(handler, request)
        if answer is not None:
            return answer
    return None


def _walk(routes: object, request: _Request) -> Answer | None:
    """Routes in order; a ``handle`` group is consumed by its first matching member."""
    consumed: set[str] = set()
    for route in routes if isinstance(routes, list) else []:
        if not isinstance(route, dict):
            raise UnroutableConfigError(f"not a route: {route!r}")
        group = route.get("group")
        if (isinstance(group, str) and group in consumed) or not _matched(route, request.url):
            continue
        if isinstance(group, str):
            consumed.add(group)
        answer = _handlers(route, request)
        if answer is not None:
            return answer
    return None


def _servers(config: object) -> Iterator[dict[str, object]]:
    apps = config.get("apps") if isinstance(config, dict) else None
    http = apps.get("http") if isinstance(apps, dict) else None
    servers = http.get("servers") if isinstance(http, dict) else None
    for server in servers.values() if isinstance(servers, dict) else ():
        if isinstance(server, dict):
            yield server


def _checked(url: str) -> str:
    """A URL the dry-run will ask about: absolute, and not climbing out of any root."""
    if not url.startswith("/") or ".." in PurePosixPath(url).parts:
        raise UnroutableConfigError(f"not a served URL: {url!r}")
    return url


def answer_for(config: object, url: str) -> Answer | None:
    """What ``config`` answers for ``url``; ``None`` when no route reaches a response."""
    request = _Request(_checked(url))
    for server in _servers(config):
        answer = _walk(server.get("routes"), request)
        if answer is not None:
            return answer
    return None


def _nodes(node: object) -> Iterator[dict[str, object]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _nodes(item)


def roots(config: object) -> tuple[str, ...]:
    """Every filesystem root the adapted routes name, in the order they appear.

    Read off the routes, never off the Caddyfile's text: a root that
    arrives through an imported fragment, a placeholder or a snippet is
    invisible in the file and plain here.
    """
    found = [
        node["root"]
        for node in _nodes(config)
        if node.get("handler") == "vars" and isinstance(node.get("root"), str)
    ]
    return tuple(dict.fromkeys(str(root) for root in found))


def matcher_paths(config: object) -> tuple[str, ...]:
    """Every path any route matcher names — the URL set a configuration speaks about."""
    found: list[str] = []
    for node in _nodes(config):
        paths = node.get("path")
        if isinstance(paths, list):
            found.extend(str(path) for path in paths)
    return tuple(dict.fromkeys(found))


def hidden_paths(config: object) -> tuple[str, ...]:
    """Every path a ``file_server`` hides — Caddy's own record of what the file imported.

    The adapter puts the Caddyfile and every file it imported into the
    ``hide`` list beside the ``file_server``, so this is where a
    configuration says which redirect map it is actually serving under.
    """
    found: list[str] = []
    for node in _nodes(config):
        hide = node.get("hide")
        if node.get("handler") == "file_server" and isinstance(hide, list):
            found.extend(str(entry) for entry in hide)
    return tuple(dict.fromkeys(found))


def hosts(config: object) -> tuple[str, ...]:
    """Every host name the routes match on; the dry-run asks both configurations to agree."""
    found: list[str] = []
    for node in _nodes(config):
        names = node.get("host")
        if isinstance(names, list):
            found.extend(str(name) for name in names)
    return tuple(sorted(set(found)))
