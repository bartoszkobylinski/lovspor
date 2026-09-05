"""Corpus Markdown to inert HTML (ADR-0013 Decision 2).

The publication layer preserves text; it must never become an execution
channel. Every character of corpus text passes through :func:`html.escape`
before any markup is assembled, so raw HTML-like material in a body
renders as visible text. The only ``<a>`` elements are those whose ref
target the resolver maps to an emitted canonical URL — and even then the
resulting href must be a site-relative path, so a lying resolver cannot
smuggle in a scheme.

The grammar handled is the closed renderer-v8 body grammar: headings,
paragraphs, ordered/unordered lists, pipe tables, blockquotes, ``**``/``*``
emphasis and ``[text](target)`` links. Anything else is a paragraph.
"""

import html
import re
from collections.abc import Callable

from lovspor.headings import parse_section_heading
from lovspor.publish.inventory import normalise_pid

LinkResolver = Callable[[str], str | None]
"""Maps a body link target (e.g. ``lov/2024-12-20-96/§3``) to a canonical
site path, or ``None`` when nothing emitted answers to it."""

_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$")
_ORDERED_ITEM = re.compile(r"^\d+\.\s+(.*)$")
_UNORDERED_ITEM = re.compile(r"^[-*]\s+(.*)$")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_STRONG_EM = re.compile(r"\*\*\*(.+?)\*\*\*")
_STRONG = re.compile(r"\*\*(.+?)\*\*")
_EMPHASIS = re.compile(r"\*(.+?)\*")
_TABLE_RULE = re.compile(r"^\|?[\s:|-]+\|?$")
_CELL_BOUNDARY = re.compile(r"(?<!\\)\|")
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_PLACEHOLDER = re.compile("\x00(\\d+)\x00")


def render_body_html(
    lines: list[str],
    resolve: LinkResolver,
    suppressed_anchor_pids: frozenset[str] = frozenset(),
) -> str:
    """Render body lines to HTML blocks joined by newlines.

    ``suppressed_anchor_pids`` carries the document's duplicate pids
    (``DocumentPlan.duplicate_pids``): a section heading whose normalised
    pid is in the set renders without an ``id`` attribute, because a
    duplicated anchor is invalid HTML and would misdirect citations
    (ADR-0013 Decision 1) — the pid's identity is ambiguous, so no anchor
    may claim it.
    """
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        block, index = _render_block(lines, index, resolve, suppressed_anchor_pids)
        blocks.append(block)
    return "\n".join(blocks)


def _render_block(
    lines: list[str],
    index: int,
    resolve: LinkResolver,
    suppressed_anchor_pids: frozenset[str] = frozenset(),
) -> tuple[str, int]:
    """Render the block starting at ``index``; return (html, next index)."""
    line = lines[index]
    if _HEADING.match(line):
        return _render_heading(line, resolve, suppressed_anchor_pids), index + 1
    if line.startswith("|"):
        return _render_table(lines, index, resolve)
    if _ORDERED_ITEM.match(line):
        return _render_list(lines, index, resolve, ordered=True)
    if _UNORDERED_ITEM.match(line):
        return _render_list(lines, index, resolve, ordered=False)
    if line.startswith(">"):
        return _render_blockquote(lines, index, resolve, suppressed_anchor_pids)
    return _render_paragraph(lines, index, resolve)


def _render_heading(
    line: str,
    resolve: LinkResolver,
    suppressed_anchor_pids: frozenset[str],
) -> str:
    match = _HEADING.match(line)
    assert match is not None  # noqa: S101 — guarded by caller
    level = len(match.group(1))
    text = _inline(match.group(2), resolve)
    section = parse_section_heading(line)
    if section is not None:
        pid = normalise_pid(section[0])
        if pid not in suppressed_anchor_pids:
            return f'<h{level} id="paragraf-{pid}">{text}</h{level}>'
    return f"<h{level}>{text}</h{level}>"


def _render_paragraph(
    lines: list[str],
    index: int,
    resolve: LinkResolver,
) -> tuple[str, int]:
    """Consecutive non-blank, non-structural lines form one paragraph."""
    collected: list[str] = []
    while index < len(lines):
        line = lines[index]
        if not line.strip() or _is_structural(line):
            break
        collected.append(line)
        index += 1
    text = _inline("\n".join(collected), resolve)
    return f"<p>{text}</p>", index


def _is_structural(line: str) -> bool:
    return bool(
        _HEADING.match(line)
        or line.startswith(("|", ">"))
        or _ORDERED_ITEM.match(line)
        or _UNORDERED_ITEM.match(line),
    )


def _render_list(
    lines: list[str],
    index: int,
    resolve: LinkResolver,
    *,
    ordered: bool,
) -> tuple[str, int]:
    pattern = _ORDERED_ITEM if ordered else _UNORDERED_ITEM
    items: list[str] = []
    while index < len(lines):
        match = pattern.match(lines[index])
        if match is None:
            break
        items.append(f"<li>{_inline(match.group(1), resolve)}</li>")
        index += 1
    tag = "ol" if ordered else "ul"
    body = "\n".join(items)
    return f"<{tag}>\n{body}\n</{tag}>", index


def _render_blockquote(
    lines: list[str],
    index: int,
    resolve: LinkResolver,
    suppressed_anchor_pids: frozenset[str],
) -> tuple[str, int]:
    quoted: list[str] = []
    while index < len(lines) and _continues_quote(lines[index], quoted):
        line = lines[index]
        quoted.append(line.removeprefix(">").strip() if line.startswith(">") else line)
        index += 1
    # The suppression set must survive the recursion: a section heading
    # quoted inside a blockquote is still an anchor claim on the page.
    inner = render_body_html(quoted, resolve, suppressed_anchor_pids)
    return f"<blockquote>{inner}</blockquote>", index


def _continues_quote(line: str, quoted: list[str]) -> bool:
    """A ``>`` line always; a bare line only as CommonMark lazy paragraph
    continuation — non-blank, non-structural, and only while a quoted
    paragraph is open (the last quoted line is non-blank; a quoted blank
    ``>`` closes it). The corpus writes this shape (22 cases: change notes
    and the translated-act preambles), and dropping the continuation would
    leak quoted text out of its quote."""
    if line.startswith(">"):
        return True
    paragraph_open = bool(quoted) and bool(quoted[-1].strip())
    return paragraph_open and bool(line.strip()) and not _is_structural(line)


def _render_table(
    lines: list[str],
    index: int,
    resolve: LinkResolver,
) -> tuple[str, int]:
    rows: list[list[str]] = []
    while index < len(lines) and lines[index].startswith("|"):
        if not _TABLE_RULE.match(lines[index]):
            rows.append(_table_cells(lines[index], resolve))
        index += 1
    return _table_html(rows), index


def _table_cells(line: str, resolve: LinkResolver) -> list[str]:
    """Split on unescaped pipes only: ``\\|`` is cell content, not a
    boundary — the escape itself is then unwrapped by ``_inline``."""
    trimmed = line.strip()
    trimmed = trimmed.removeprefix("|").removesuffix("|")
    cells = _CELL_BOUNDARY.split(trimmed)
    return [_inline(cell.strip(), resolve) for cell in cells]


def _table_html(rows: list[list[str]]) -> str:
    if not rows:
        return "<table></table>"
    head = "".join(f"<th>{cell}</th>" for cell in rows[0])
    body_rows = ["<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows[1:]]
    body = "\n".join(body_rows)
    return f"<table>\n<thead><tr>{head}</tr></thead>\n<tbody>\n{body}\n</tbody>\n</table>"


def _inline(text: str, resolve: LinkResolver) -> str:
    """Escape text, then assemble the closed set of inline elements.

    Renderer escape sequences (``1\\.``, ``\\*``, ``\\(`` — 506 corpus
    documents carry them) are stashed behind NUL placeholders first, so an
    escaped character can never open a link or emphasis; NUL itself cannot
    occur in corpus text, which comes from XML. Links are then cut out of
    the stashed text so their pieces are escaped individually — target
    resolution sees the raw target, readers see only escaped text, and an
    unresolvable link degrades to its visible text.
    """
    stashed, literals = _stash_escapes(text)
    parts: list[str] = []
    cursor = 0
    for match in _LINK.finditer(stashed):
        parts.append(_plain(stashed[cursor : match.start()], literals))
        parts.append(
            _link_html(match.group(1), match.group(2), resolve, literals),
        )
        cursor = match.end()
    parts.append(_plain(stashed[cursor:], literals))
    return "".join(parts)


def _stash_escapes(text: str) -> tuple[str, list[str]]:
    """Replace ``\\<punct>`` with a placeholder; return (text, literals)."""
    literals: list[str] = []

    def stash(match: re.Match[str]) -> str:
        literals.append(match.group(1))
        return f"\x00{len(literals) - 1}\x00"

    return _ESCAPE.sub(stash, text), literals


def _plain(text: str, literals: list[str]) -> str:
    escaped = html.escape(text, quote=True)
    # Triple stars first: letting ** and * match independently interleaves
    # their tags (<strong><em>x</strong></em>) — mal-nested HTML.
    escaped = _STRONG_EM.sub(r"<strong><em>\1</em></strong>", escaped)
    escaped = _STRONG.sub(r"<strong>\1</strong>", escaped)
    escaped = _EMPHASIS.sub(r"<em>\1</em>", escaped)
    return _PLACEHOLDER.sub(
        lambda m: html.escape(literals[int(m.group(1))], quote=True),
        escaped,
    )


def _is_site_relative(path: str) -> bool:
    """A single leading slash and nothing sneakier.

    ``//host/x`` is protocol-relative — a browser resolves it to another
    origin — and ``/\\`` is its backslash spelling in some parsers, so
    both are refused along with anything not starting at the site root.
    """
    return path.startswith("/") and not path.startswith(("//", "/\\"))


def _link_html(
    label: str,
    target: str,
    resolve: LinkResolver,
    literals: list[str],
) -> str:
    """An ``<a>`` only for a resolved, site-relative target; text otherwise.

    The path check is a second, independent gate: even a resolver that
    returns an absolute URL or a scheme cannot produce an href — the closed
    policy admits site-relative canonical paths and nothing else.
    """
    resolved = resolve(target)
    if resolved is None or not _is_site_relative(resolved):
        return _plain(label, literals)
    href = html.escape(resolved, quote=True)
    return f'<a href="{href}">{_plain(label, literals)}</a>'
