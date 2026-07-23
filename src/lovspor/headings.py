"""Single source of truth for the ``§`` section-heading grammar.

Two consumers parse the renderer's Markdown headings: ``lovspor.mcp``
(``_parse_sections``, backing ``get_section`` / ``list_sections`` /
``diff_law_versions``) and ``lovspor.embeddings.sections``
(``iter_sections``, deciding what gets a vector). They used to hold
independent copies of the same pattern, with a comment asking future
readers to keep them in step.

The copies stayed byte-identical and still produced a corpus-wide
blind spot: a heading shape neither copy recognized is invisible to
BOTH the structured accessors and ``semantic_search``, so no tool can
cover for the other. The regex lives here, once, so that widening it
is a single edit and a single test surface.
"""

import re

SECTION_HEADING = re.compile(r"^#{2,3} § ([\d-]+[a-z]?)(?:\.(?:\s+(.+?))?)?\s*$")
"""Matches a section heading produced by the lovspor renderer, capturing
the section id (e.g. ``5-12``, ``1``, ``5-12a``) and the optional title.

Two hashes OR three: chaptered acts render sections at H3 under a
``## Kapittel`` (``### § 5-12. ...``), but flat acts with no chapter
level render them at H2 (``## § 1. ...``) — ~18% of multi-version acts
(vrakloven, særavgiftsloven, hittegodslova, ...). Both must be
recognized or the whole flat act parses to zero sections and becomes
invisible to ``get_section`` / ``list_sections`` / ``diff_law_versions``.

The title group is OPTIONAL because Lovdata's source XML sometimes
ships a ``legalArticleValue`` with no accompanying ``title`` field.
The trailing dot is optional and independent of the title: chaptered
titleless sections render bare (``### § 5``) while flat titleless ones
carry a dangling dot (``## § 14.``); the ``(?:\\.(?:\\s+(.+?))?)?``
shape matches ``§ 5``, ``§ 14.`` and ``§ 13. (Opphevet)`` alike."""


def raw_section_id(heading_line: str) -> str | None:
    """Return the section id exactly as written, or ``None`` if not a section."""
    match = SECTION_HEADING.match(heading_line)
    return match.group(1) if match else None
