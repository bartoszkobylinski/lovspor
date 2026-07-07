"""Extract ``§`` sections from rendered lovspor Markdown for embedding.

Used at sync time to decide what units to embed. Each ``EmbeddingSection``
gets one vector. The section parser in ``lovspor.mcp`` (``_parse_sections``)
serves a different need (it tracks ``parent_chapter`` for the
``get_section`` tool), so this module keeps a thin separate
implementation rather than coupling the sync path to the MCP module. Both
must recognize the same heading shapes or they drift.

Chaptered acts render sections at H3 (``### § 5-12.``); flat acts with no
chapter level render them at H2 (``## § 1.``). Both are matched — an
H3-only parser embedded zero sections for ~18% of acts (vrakloven,
særavgiftsloven, ...), leaving them invisible to ``semantic_search``.

A section's text is the lines between its ``### §`` heading and the
next ``###`` or ``##`` heading, stripped of leading / trailing
whitespace. The heading line itself is included in ``text`` because
the section title carries semantic content that should weight the
embedding (e.g. ``§ 5-12. Boligsparing for ungdom`` vs the body
text alone).

Tolerant of acts that have no sections at all (returns empty list)
and of acts whose ``### §`` headings have no title (matches the
optional-title regex pattern used elsewhere in the codebase).
"""

import re
from dataclasses import dataclass

_SECTION_HEADING = re.compile(r"^#{2,3} § ([\d-]+[a-z]?)(?:\.(?:\s+(.+?))?)?\s*$")
_SUBSECTION_PREFIX = "### "
_CHAPTER_PREFIX = "## "


@dataclass(frozen=True)
class EmbeddingSection:
    """One unit of embedding work — section_id plus the text that gets vectorized."""

    section_id: str
    text: str


def iter_sections(body: str) -> list[EmbeddingSection]:
    """Walk a frontmatter-stripped Markdown body and yield embedding sections.

    Boundary rules match the renderer's output exactly:

    - ``### § N-M. Title`` (chaptered) or ``## § N. Title`` (flat, no
      chapter level) opens a section; the title, if any, is kept in
      ``text`` because it carries meaning. The section pattern is tested
      before the chapter/subsection prefixes, so a flat act's ``## §``
      opens a section rather than being read as a chapter boundary.
    - ``### `` (anything else) closes the current section without
      opening a new one.
    - ``## `` (a chapter heading, i.e. an ``##`` line without ``§``)
      closes the current section.
    - End-of-input closes the current section.
    """
    sections: list[EmbeddingSection] = []
    current_id: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_id is not None:
            text = "\n".join(current_lines).strip()
            sections.append(EmbeddingSection(section_id=current_id, text=text))

    for line in body.split("\n"):
        match = _SECTION_HEADING.match(line)
        if match:
            _flush()
            current_id = match.group(1)
            current_lines = [line]
            continue
        if line.startswith(_SUBSECTION_PREFIX) or line.startswith(_CHAPTER_PREFIX):
            _flush()
            current_id = None
            current_lines = []
            continue
        if current_id is not None:
            current_lines.append(line)
    _flush()
    return sections


def strip_frontmatter(text: str) -> str:
    """Remove the YAML frontmatter block from a rendered lovspor file.

    The renderer always produces files starting with ``---\\n``,
    closed by ``\\n---\\n``. Tolerant of files that don't match
    (returns input unchanged) so a malformed file does not crash
    the indexer.
    """
    if not text.startswith("---\n"):
        return text
    closing = text.find("\n---\n", 4)
    if closing < 0:
        return text
    return text[closing + len("\n---\n") :].lstrip("\n")
