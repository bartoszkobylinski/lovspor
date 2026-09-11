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
from pathlib import Path

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
_NEVER_IN_A_PATH = (
    ("%", "percent-encoding is not decoded here"),
    ("\\", "a backslash is not a path separator here"),
    ("?", "a query string is not part of the path"),
    ("#", "a fragment is not part of the path"),
)
"""Characters that end the question rather than being interpreted.

Decoding ``%2e%2e%2f`` would make this module a URL parser and give one
tree two names; taking it literally would let a traversal through. A
backslash separates nothing on this host and is the usual way to smuggle
one past a POSIX-only check. A query and a fragment are not part of the
path Caddy matches or the file it serves. None of them can appear in a
URL the dry-run derives — ADR-0013's publish check refuses such a slug —
so refusing costs nothing and guessing would cost containment.
"""


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
    """The file Caddy's ``file_server`` resolves ``url`` to under ``root``, or ``None``.

    Joined segment by segment from a path :func:`request_path` has already
    accepted — never ``root / url``, which Python resolves to ``url``
    alone the moment it is absolute — and proven contained afterwards, so
    a name that passes the rules and a symlink that does not both stop
    here. A trailing slash is the one thing resolved rather than refused:
    it names a directory, and serving its index is Caddy's own
    ``file_server`` behaviour, not a rewrite of the URL.
    """
    target = _inside(root, root.joinpath(*request_path(url)), url)
    if target.is_dir():
        target = _inside(root, target / INDEX_FILE, url)
    return target if target.is_file() else None


def _hidden(path: Path, hide: tuple[str, ...]) -> bool:
    """``hide`` as this dry-run reads it: an exact path, or a bare file name."""
    return path.as_posix() in hide or path.name in hide


def _nothing_served(root: str | None) -> Answer:
    """``file_server`` with no file under ``root`` for this URL: a 404, and nothing else."""
    return Answer(
        handler="file_server",
        root=root,
        served=None,
        digest=None,
        status=NOT_FOUND,
        location=None,
        upstream=None,
    )


def _file_server(handler: dict[str, object], request: _Request) -> Answer:
    raw = handler.get("hide")
    hide = tuple(str(entry) for entry in raw) if isinstance(raw, list) else ()
    root = Path(request.root) if request.root is not None else None
    found = served_file(root, request.url) if root is not None else None
    if root is None or found is None or _hidden(found, hide):
        return _nothing_served(request.root)
    return Answer(
        handler="file_server",
        root=request.root,
        served=found.relative_to(root).as_posix(),
        digest=hashlib.sha256(found.read_bytes()).hexdigest(),
        status=200,
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


def broken_url_rule(url: str) -> str | None:
    """Which rule ``url`` breaks as a path this dry-run can ask about, or ``None``.

    One rule set, read by the evaluator to refuse and by ``staged``'s URL
    derivers to skip, so nothing the dry-run asks is something it would
    then decline to answer. Everything outside the shape is refused
    rather than normalised: the two configurations' answers are compared
    *keyed by URL*, so two spellings that collapse to one key would hide
    a difference behind a normalisation this model invented.
    """
    if not url.startswith("/"):
        return "a served URL is absolute"
    if url.startswith("//"):
        return "a second leading slash is a network-path reference, not a path"
    for character, rule in _NEVER_IN_A_PATH:
        if character in url:
            return rule
    return _broken_segment(url.split("/")[1:])


def _broken_segment(segments: list[str]) -> str | None:
    """A trailing empty segment is the directory form; every other oddity is refused."""
    if "" in segments[:-1]:
        return "an empty path segment"
    if ".." in segments:
        return ".. climbs out of the root"
    if "." in segments:
        return ". is not a path segment"
    return None


def is_servable_url(url: str) -> bool:
    """Whether ``url`` is a path this dry-run can ask either configuration about."""
    return broken_url_rule(url) is None


def request_path(url: str) -> list[str]:
    """``url``'s segments, or :class:`UnroutableConfigError` naming it and the rule it broke."""
    broken = broken_url_rule(url)
    if broken is not None:
        raise UnroutableConfigError(f"not a served URL ({broken}): {url!r}")
    return url.split("/")[1:]


def contained(root: Path, target: Path) -> bool:
    """Whether ``target`` is ``root`` or under it, with both sides fully resolved.

    Normalised containment, never a string prefix: ``lovspor-current``
    starts with ``lovspor`` and is a different tree. Resolving both sides
    is also what keeps the OLD configuration legitimate — its root *is* a
    symlink — while a symlink inside a root that lands elsewhere is not.
    """
    anchor, resolved = root.resolve(), target.resolve()
    return anchor == resolved or anchor in resolved.parents


def _inside(root: Path, target: Path, url: str) -> Path:
    """``target``, proven to be inside ``root``; a path that escapes ends the question."""
    if not contained(root, target):
        raise UnroutableConfigError(f"not a served URL (it resolves outside {root}): {url!r}")
    return target


def answer_for(config: object, url: str) -> Answer | None:
    """What ``config`` answers for ``url``; ``None`` when no route reaches a response."""
    request_path(url)
    request = _Request(url)
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
