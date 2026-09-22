"""robots.txt decisions with one semantics, whatever interpreter runs the crawl.

``urllib.robotparser`` answers a conflicting ``Allow`` / ``Disallow`` pair by
**file order** up to Python 3.13 and by **longest match** from 3.14, so the
observatory's compliance was a property of the venv, not of the code: on the
3.12 the nightly runs on, ``Allow: /`` followed by narrower ``Disallow`` lines
— an ordinary way to write the file — silenced every one of those lines
(issue #351). A Python bump would then have changed what the crawler fetches
without a line of this repository changing.

This module pins the semantics of RFC 9309 instead, and is the only place they
live:

* the rules are those of every group naming the crawler's **product token**
  (the User-Agent up to the first ``/``), matched case-insensitively and
  exactly and combined into one set; the ``*`` groups are the fallback and
  apply only when no group names the token;
* within that set the **most specific** rule decides — the one with the most
  octets, counted on the rule itself and not on the text a ``*`` swallowed —
  and an ``Allow`` wins a tie of equal length;
* ``*`` in a rule matches any run of characters and a trailing ``$`` anchors
  the rule to the end of the path;
* a rule with an empty path matches nothing (``Disallow:`` alone permits
  everything); with no matching rule the path is allowed;
* ``Sitemap:`` lines are collected from anywhere in the file.

Paths are compared percent-encoded on both sides, so ``/høring`` in a rule
and ``/h%C3%B8ring`` in a URL are the same path — while an encoded reserved
character stays encoded, so ``/a%2Fb`` is not ``/a/b``.
"""

import re
import string
from collections.abc import Iterable
from typing import NamedTuple
from urllib.parse import urlsplit

_WILDCARD_RUN = re.compile(r"[*]{2,}")
_ANCHOR_RUN = re.compile(r"[$][$*]+")
# RFC 3986 §2: an encoded unreserved octet means the same as the literal; an
# encoded reserved one does not (``%2F`` is not a path separator).
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_RESERVED = frozenset(":/?#[]@!$&'()*+,;=%")
_TOKEN = re.compile(r"(?P<escape>%[0-9A-Fa-f]{2})|.")


class Rule(NamedTuple):
    path: str
    allow: bool
    anchored: bool

    def matches(self, path: str) -> bool:
        """Whether this rule applies to ``path``.

        An empty, unanchored rule (``Disallow:`` alone) applies to nothing —
        that is how a file says "everything is permitted".
        """
        if not self.path and not self.anchored:
            return False
        if "*" not in self.path:
            return path == self.path if self.anchored else path.startswith(self.path)
        pattern = re.compile(_translate(self.path))
        return (pattern.fullmatch(path) if self.anchored else pattern.match(path)) is not None


class Group(NamedTuple):
    agents: tuple[str, ...]
    rules: tuple[Rule, ...]


class RobotsPolicy:
    """One host's robots.txt, parsed, with the decision rules above."""

    def __init__(self, groups: tuple[Group, ...], sitemaps: tuple[str, ...]) -> None:
        self._groups = groups
        self._sitemaps = sitemaps

    @classmethod
    def parse(cls, lines: Iterable[str]) -> "RobotsPolicy":
        builder = _Builder()
        for raw in lines:
            builder.feed(raw)
        return cls(builder.groups(), tuple(builder.sitemaps))

    def sitemaps(self) -> tuple[str, ...]:
        return self._sitemaps

    def allows(self, user_agent: str, url: str) -> bool:
        """RFC 9309 §2.2.2: the most specific matching rule decides.

        Specificity is the octets of the *rule*, not of the text it consumed:
        what a ``*`` swallows is not part of the rule, so ``Disallow: /files/*``
        does not outrank ``Allow: /files/public/`` on a long path (the
        codex-tests lane's round-3 finding). Ranked as ``(length, allow)`` so
        an ``Allow`` wins a tie; no matching rule means the path is allowed.
        """
        path = _request_path(url)
        matching = [
            (len(rule.path), rule.allow)
            for rule in self._rules_for(user_agent)
            if rule.matches(path)
        ]
        return max(matching)[1] if matching else True

    def _rules_for(self, user_agent: str) -> tuple[Rule, ...]:
        """Every rule addressed to this crawler, from every group that names it.

        RFC 9309 §2.2.1: a site may open several groups for one token, and
        they are one rule set. Taking only the first would let a later
        ``Disallow`` go unenforced.
        """
        token = user_agent.partition("/")[0].strip().lower()
        named = tuple(
            rule for group in self._groups if token in group.agents for rule in group.rules
        )
        if any(token in group.agents for group in self._groups):
            return named
        return tuple(rule for group in self._groups if "*" in group.agents for rule in group.rules)


class _Builder:
    """Fold the file's lines into groups: a run of ``User-agent`` lines opens
    one, its rules fill it, and the next ``User-agent`` after a rule closes it."""

    def __init__(self) -> None:
        self._groups: list[Group] = []
        self.sitemaps: list[str] = []
        self._agents: list[str] = []
        self._rules: list[Rule] = []

    def feed(self, raw: str) -> None:
        field, _, value = raw.partition("#")[0].partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            self._start_agent(value.lower())
        elif field in ("allow", "disallow") and self._agents:
            self._rules.append(_rule(value, allow=field == "allow"))
        elif field == "sitemap" and value:
            self.sitemaps.append(value)

    def groups(self) -> tuple[Group, ...]:
        if self._agents:
            self._close()
        return tuple(self._groups)

    def _start_agent(self, agent: str) -> None:
        # A User-agent line after rules opens a new group; one after another
        # User-agent line joins the group being opened.
        if self._rules:
            self._close()
        self._agents.append(agent)

    def _close(self) -> None:
        self._groups.append(Group(tuple(self._agents), tuple(self._rules)))
        self._agents, self._rules = [], []


def _rule(value: str, *, allow: bool) -> Rule:
    pattern = _ANCHOR_RUN.sub("$", _WILDCARD_RUN.sub("*", value))
    anchored = pattern.endswith("$")
    return Rule(_normalise(pattern.rstrip("$")), allow, anchored)


def _normalise(pattern: str) -> str:
    """Normalise the literal parts of a rule, leaving its wildcards alone."""
    return re.sub(r"[^*$]+", lambda m: _normalise_text(m[0]), pattern)


def _request_path(url: str) -> str:
    parts = urlsplit(url)
    path = _normalise_text(parts.path or "/")
    return f"{path}?{_normalise_text(parts.query)}" if parts.query else path


def _normalise_text(text: str) -> str:
    """Percent-encoding as RFC 9309 §2.2.2 compares it.

    An encoded unreserved octet is decoded, an encoded reserved one stays
    encoded, and anything outside ASCII is encoded exactly once — so ``/høring``
    and ``/h%C3%B8ring`` are one path and ``/a%2Fb`` and ``/a/b`` are two.
    """
    return "".join(_normalise_token(match) for match in _TOKEN.finditer(text))


def _normalise_token(match: re.Match[str]) -> str:
    if match["escape"]:
        octet = chr(int(match["escape"][1:], 16))
        return octet if octet in _UNRESERVED else match["escape"].upper()
    char = match[0]
    if char in _UNRESERVED or char in _RESERVED:
        return char
    return "".join(f"%{byte:02X}" for byte in char.encode("utf-8"))


def _translate(pattern: str) -> str:
    """The regex for a rule that contains ``*``; a plain prefix never gets here."""
    parts = [re.escape(part) for part in pattern.split("*")]
    return ".*?".join(parts[:-1]) + ".*" + parts[-1]
