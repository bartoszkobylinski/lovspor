"""Deterministic YAML front matter serializer.

Custom minimal implementation rather than ``PyYAML`` for three reasons:

1. **Determinism.** PyYAML's ``Dumper`` has quirks (types hints, ``...``
   document-end markers, quote-style heuristics) that make byte-identical
   output across versions and platforms harder than it should be. We own
   the output format.
2. **No dependency.** One fewer thing to audit and lock.
3. **Scope.** Lovdata front matter is flat key-value plus list-of-string.
   A full YAML implementation is overkill.

Output format:

    ---
    key: "string value"
    count: 5
    flag: true
    items:
      - "first"
      - "second"
    ---

Key order is the Pydantic model's field-declaration order (deterministic
via Python's dict insertion order guarantee).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


def serialize_frontmatter(model: BaseModel) -> str:
    """Return a deterministic YAML front matter block ending in a newline.

    The block is delimited by ``---`` on its own lines, as expected by
    Markdown front matter conventions.
    """
    lines = ["---"]
    for key, value in model.model_dump().items():
        lines.extend(_emit_field(key, value))
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _emit_field(key: str, value: Any) -> list[str]:
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        out = [f"{key}:"]
        out.extend(f"  - {_format_scalar(item)}" for item in value)
        return out
    return [f"{key}: {_format_scalar(value)}"]


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, datetime):
        return _format_scalar(value.isoformat())
    return f'"{_escape_string(str(value))}"'


def _escape_string(text: str) -> str:
    """Escape a Python string for emission inside a YAML double-quoted scalar.

    A raw newline inside ``"..."`` is folded by YAML parsers to a single
    space (lossy round-trip). Emit control characters as escape sequences
    instead: the resulting YAML parses back to the exact input string.

    Backslash must be escaped first so the subsequent escape insertions
    are not themselves doubled.
    """
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
