"""A nonce-normalized comparison digest for served HTML (#738, FC1a; architecture §3.3).

regjeringen.no puts a fresh CSP nonce in every page it serves, so two fetches of
an unchanged page never share a raw SHA-256. This module derives a second digest
that ignores those nonces and nothing else. It is a *comparison* value only:
pages are stored, named and verified by their raw digest, and extraction reads
the raw bytes. Nothing here compares anything yet (FC1b does, after D4).

The rule (``NORMALIZATION_RULE``) replaces exactly two kinds of value with the
fixed token ``NONCE``, by a byte-level scan:

- the value of a ``nonce`` attribute on a start tag, written exactly
  ``nonce="<value>"`` (lowercase name, no space around ``=``, double quotes),
  when the tag has no other ``nonce`` attribute in any spelling;
- the ``content`` value of a ``<meta>`` start tag (lowercase name) written
  ``name="csp-nonce"`` and ``content="<value>"``, each exactly once in the tag,
  when the document has no other ``<meta>`` whose name is ``csp-nonce`` in any
  spelling or quoting.

``<value>`` is base64: ``[A-Za-z0-9+/]`` or the character reference ``&#x2B;``,
at least once, then at most two ``=``. Start tags are recognized by a strict
attribute grammar (whitespace-separated, values double-quoted, single-quoted
or unquoted, no ``<`` in a name); comments, end tags, raw-text element
contents (``script``, ``style`` and the like) and anything unterminated are
copied as served. The scan is one left-to-right pass: a tag that breaks the
grammar is skipped up to the byte where it broke, so the cost stays linear in
the page size whatever the input.

Fail closed: whatever the scan does not recognize keeps its bytes, so a change
there changes the normalized digest. Every byte outside a replaced value is
preserved, the token is itself a valid value, and the scan is deterministic,
so normalizing twice gives the same bytes.
"""

import hashlib
import re
from typing import NamedTuple

NORMALIZATION_RULE = "regjeringen-csp-nonce/1"
TOKEN = b"NONCE"

_WHITESPACE = frozenset(b" \t\n\f\r")
_NAME_STOP = _WHITESPACE | frozenset(b"\"'>/=<")
_UNQUOTED_STOP = _WHITESPACE | frozenset(b"\"'=<>`")
_QUOTES = frozenset(b"\"'")
_LETTERS = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_TAG_NAME = _LETTERS | frozenset(b"0123456789-")
_NONCE_VALUE = re.compile(rb"(?:[A-Za-z0-9+/]|&#x2B;)+={0,2}")
_RAW_TEXT = {
    name: re.compile(rb"</" + name + rb"[ \t\n\f\r/>]", re.IGNORECASE)
    for name in (
        b"script",
        b"style",
        b"textarea",
        b"title",
        b"xmp",
        b"iframe",
        b"noembed",
        b"noframes",
        b"noscript",
    )
}
_PLAINTEXT = b"plaintext"
_LETTER_BYTES = frozenset(bytes([letter]) for letter in _LETTERS)


class _Attribute(NamedTuple):
    """One attribute as served: name, the bytes between name and value, and the value."""

    name: bytes
    eq: bytes | None
    value: bytes | None
    value_start: int


class _Tag(NamedTuple):
    """One start tag that fits the grammar."""

    name: bytes
    attributes: tuple[_Attribute, ...]


def normalized_sha256(raw: bytes) -> str:
    """SHA-256 of :func:`normalize_regjeringen_html` applied to ``raw``."""
    return hashlib.sha256(normalize_regjeringen_html(raw)).hexdigest()


def normalize_regjeringen_html(raw: bytes) -> bytes:
    """``raw`` with each grammatical nonce value replaced by ``NONCE``; every other byte kept."""
    tags = _start_tags(raw)
    csp_metas = [tag for tag in tags if _is_csp_meta(tag)]
    spans = [span for tag in tags if (span := _nonce_span(tag)) is not None]
    if len(csp_metas) == 1 and (span := _meta_content_span(csp_metas[0])) is not None:
        spans.append(span)
    return _replace(raw, sorted(spans))


def _start_tags(raw: bytes) -> list[_Tag]:
    """Every grammatical start tag outside comments and raw text, in document order."""
    tags: list[_Tag] = []
    position = 0
    while position != -1 and (opening := raw.find(b"<", position)) != -1:
        skipped = _skip_non_start_tag(raw, opening)
        if skipped is not None:
            position = skipped
            continue
        tag, position = _parse_tag(raw, opening)
        if tag is not None:
            tags.append(tag)
            position = _after_start_tag(raw, tag, position)
    return tags


def _after_start_tag(raw: bytes, tag: _Tag, position: int) -> int:
    """Where the scan resumes after ``tag``: past its raw text, or -1 if the rest is text."""
    lowered = tag.name.lower()
    if lowered == _PLAINTEXT:
        return -1
    closing = _RAW_TEXT.get(lowered)
    if closing is None:
        return position
    found = closing.search(raw, position)
    return -1 if found is None else found.start()


def _skip_non_start_tag(raw: bytes, opening: int) -> int | None:
    """Past a comment, end tag or bogus comment at ``opening``; -1 if it never ends.

    ``None`` when ``opening`` may start a start tag. Nothing inside what is
    skipped is ever normalized, so markup quoted there keeps its bytes.
    """
    if raw.startswith(b"<!--", opening):
        closing = raw.find(b"-->", opening + 4)
        return -1 if closing == -1 else closing + 3
    after = raw[opening + 1 : opening + 2]
    if after == b"/" and raw[opening + 2 : opening + 3] in _LETTER_BYTES:
        return _parse_tag(raw, opening + 1)[1]  # an end tag: only its extent matters
    if after in (b"!", b"?", b"/"):
        closing = raw.find(b">", opening + 2)
        return -1 if closing == -1 else closing + 1
    return None


def _skip(raw: bytes, position: int, allowed: frozenset[int]) -> int:
    while position < len(raw) and raw[position] in allowed:
        position += 1
    return position


def _until(raw: bytes, position: int, stop: frozenset[int]) -> int:
    while position < len(raw) and raw[position] not in stop:
        position += 1
    return position


def _parse_tag(raw: bytes, opening: int) -> tuple[_Tag | None, int]:
    """The start tag at ``opening`` and the position after it.

    When the bytes there do not fit the grammar, ``None`` and the position where
    they stopped fitting (always past ``opening``), so no byte is scanned twice.
    """
    if raw[opening + 1 : opening + 2] not in _LETTER_BYTES:
        return None, opening + 1
    end = _skip(raw, opening + 1, _TAG_NAME)
    name = raw[opening + 1 : end]
    attributes: list[_Attribute] = []
    while True:
        after_space = _skip(raw, end, _WHITESPACE)
        if raw.startswith(b">", after_space):
            return _Tag(name, tuple(attributes)), after_space + 1
        if raw.startswith(b"/>", after_space):
            return _Tag(name, tuple(attributes)), after_space + 2
        if after_space == end or after_space >= len(raw):
            return None, max(after_space, opening + 1)
        attribute, end = _parse_attribute(raw, after_space)
        if attribute is None:
            return None, end
        attributes.append(attribute)


def _parse_attribute(raw: bytes, start: int) -> tuple[_Attribute | None, int]:
    name_end = _until(raw, start, _NAME_STOP)
    if name_end == start:
        return None, start + 1
    name = raw[start:name_end]
    equals = _skip(raw, name_end, _WHITESPACE)
    if not raw.startswith(b"=", equals):
        return _Attribute(name, None, None, name_end), name_end
    value_start = _skip(raw, equals + 1, _WHITESPACE)
    fits, value_end = _value_end(raw, value_start)
    if not fits:
        return None, value_end
    value = raw[value_start:value_end]
    return _Attribute(name, raw[name_end:value_start], value, value_start), value_end


def _value_end(raw: bytes, start: int) -> tuple[bool, int]:
    """Whether a value starts at ``start``, and its end; if not, where it stopped fitting."""
    if start >= len(raw):
        return False, start
    if raw[start] in _QUOTES:
        closing = raw.find(raw[start : start + 1], start + 1)
        return (False, len(raw)) if closing == -1 else (True, closing + 1)
    end = _until(raw, start, _UNQUOTED_STOP)
    return (True, end) if end > start else (False, start + 1)


def _named(tag: _Tag, name: bytes) -> list[_Attribute]:
    return [item for item in tag.attributes if item.name.lower() == name]


def _strict_value(attribute: _Attribute, name: bytes) -> tuple[int, int] | None:
    """The span inside the quotes of ``name="<nonce value>"`` written exactly so."""
    value = attribute.value
    if attribute.name != name or attribute.eq != b"=" or value is None:
        return None
    if not (value.startswith(b'"') and _NONCE_VALUE.fullmatch(value[1:-1])):
        return None
    start = attribute.value_start + 1
    return start, start + len(value) - 2


def _nonce_span(tag: _Tag) -> tuple[int, int] | None:
    nonces = _named(tag, b"nonce")
    return _strict_value(nonces[0], b"nonce") if len(nonces) == 1 else None


def _unquoted(attribute: _Attribute) -> bytes:
    value = attribute.value or b""
    return value[1:-1] if value[:1] in (b'"', b"'") else value


def _is_csp_meta(tag: _Tag) -> bool:
    if tag.name.lower() != b"meta":
        return False
    return any(_unquoted(item).lower() == b"csp-nonce" for item in _named(tag, b"name"))


def _meta_content_span(tag: _Tag) -> tuple[int, int] | None:
    names, contents = _named(tag, b"name"), _named(tag, b"content")
    if tag.name != b"meta" or len(names) != 1 or len(contents) != 1:
        return None
    (name,) = names
    exact = name.name == b"name" and name.eq == b"=" and name.value == b'"csp-nonce"'
    return _strict_value(contents[0], b"content") if exact else None


def _replace(raw: bytes, spans: list[tuple[int, int]]) -> bytes:
    parts: list[bytes] = []
    position = 0
    for start, end in spans:
        parts += [raw[position:start], TOKEN]
        position = end
    parts.append(raw[position:])
    return b"".join(parts)
