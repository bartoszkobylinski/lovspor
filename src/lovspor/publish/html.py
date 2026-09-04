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
_STRONG = re.compile(r"\*\*(.+?)\*\*")
_EMPHASIS = re.compile(r"\*(.+?)\*")
_TABLE_RULE = re.compile(r"^\|?[\s:|-]+\|?$")


def render_body_html(lines: list[str], resolve: LinkResolver) -> str:
    """Render body lines to HTML blocks joined by newlines."""
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        block, index = _render_block(lines, index, resolve)
        blocks.append(block)
    return "\n".join(blocks)


def _render_block(
    lines: list[str],
    index: int,
    resolve: LinkResolver,
) -> tuple[str, int]:
    """Render the block starting at ``index``; return (html, next index)."""
    line = lines[index]
    if _HEADING.match(line):
        return _render_heading(line, resolve), index + 1
    if line.startswith("|"):
        return _render_table(lines, index, resolve)
    if _ORDERED_ITEM.match(line):
        return _render_list(lines, index, resolve, ordered=True)
    if _UNORDERED_ITEM.match(line):
        return _render_list(lines, index, resolve, ordered=False)
    if line.startswith(">"):
        return _render_blockquote(lines, index, resolve)
    return _render_paragraph(lines, index, resolve)


def _render_heading(line: str, resolve: LinkResolver) -> str:
    match = _HEADING.match(line)
    assert match is not None  # noqa: S101 — guarded by caller
    level = len(match.group(1))
    text = _inline(match.group(2), resolve)
    section = parse_section_heading(line)
    if section is not None:
        pid = normalise_pid(section[0])
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
) -> tuple[str, int]:
    quoted: list[str] = []
    while index < len(lines) and lines[index].startswith(">"):
        quoted.append(lines[index].removeprefix(">").strip())
        index += 1
    inner = render_body_html(quoted, resolve)
    return f"<blockquote>{inner}</blockquote>", index


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
    cells = line.strip().strip("|").split("|")
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

    Links are cut out of the raw text first so their pieces are escaped
    individually — target resolution sees the raw target, readers see only
    escaped text, and an unresolvable link degrades to its visible text.
    """
    parts: list[str] = []
    cursor = 0
    for match in _LINK.finditer(text):
        parts.append(_plain(text[cursor : match.start()]))
        parts.append(_link_html(match.group(1), match.group(2), resolve))
        cursor = match.end()
    parts.append(_plain(text[cursor:]))
    return "".join(parts)


def _plain(text: str) -> str:
    escaped = html.escape(text, quote=True)
    escaped = _STRONG.sub(r"<strong>\1</strong>", escaped)
    return _EMPHASIS.sub(r"<em>\1</em>", escaped)


def _link_html(label: str, target: str, resolve: LinkResolver) -> str:
    """An ``<a>`` only for a resolved, site-relative target; text otherwise.

    The path check is a second, independent gate: even a resolver that
    returns an absolute URL or a scheme cannot produce an href — the closed
    policy admits site-relative canonical paths and nothing else.
    """
    resolved = resolve(target)
    if resolved is None or not resolved.startswith("/"):
        return _plain(label)
    return f'<a href="{html.escape(resolved, quote=True)}">{_plain(label)}</a>'
