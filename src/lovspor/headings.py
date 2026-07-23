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

_ID_PART = r"[0-9]+(?:[A-Za-z]+|\s[A-Za-z](?![A-Za-z]))?"
"""One dash-separated component of a section id: digits, optionally
followed by a letter suffix.

The two alternatives are deliberately asymmetric. An *adjacent* suffix
may run to several letters — the Ukraine sanctions forskrift alone
carries ``§ 17aa``, ``§ 8cd``, ``§ 19ac``, ``§ 20-7ca`` — because
nothing in the corpus writes a word directly against a number, so
greed is safe there.

A *spaced* suffix is capped at one letter and must be the last of its
run. Norwegian legal prose is full of ``§ 5 og`` and ``§ 12 i
skatteloven``; without that restriction the pattern would read ``og``
as a suffix. Capping it accepts ``§ 8-7 a.`` and rejects ``§ 5 og`` —
though see :data:`SECTION_ID` on why even this only holds anchored."""

_ID_SUBNUMBER = r"(?:\.[0-9]+)*"
"""Optional dot sub-numbering (``§ 8.1``, ``§ 21.1``, ``§ 9 a.1``).

Requiring a digit after the dot is what keeps this from colliding with
the far more common titleless-with-dangling-dot shape ``## § 14.``,
where the dot belongs to the heading punctuation rather than the id."""

SECTION_ID = re.compile(rf"{_ID_PART}(?:-{_ID_PART})*{_ID_SUBNUMBER}")
"""A complete section id: one or more :data:`_ID_PART` joined by ``-``.

Covers every shape the renderer emits — ``1``, ``5-12``, ``5a``,
``5-10a``, ``8-7 a``, ``35 a``, ``2 A-1``, ``3-4 A``, ``10-4-1``.

**Use anchored only.** Allowing a space before the letter suffix makes
the pattern ambiguous against running prose: unanchored, ``§ 12 i
skatteloven`` reads as id ``12 i``, because ``i`` is a legitimate
suffix letter and a legitimate Norwegian preposition. Nothing in the
text distinguishes them. Both current uses pin their ends —
``canonical_section_id`` calls ``fullmatch`` and :data:`SECTION_HEADING`
anchors the whole line — so the ambiguity never arises. A future
unanchored use (scanning body prose for references) would need a
different, stricter pattern."""

SECTION_HEADING = re.compile(
    rf"^#{{2,6}} § ({SECTION_ID.pattern})\.?(?:\s+(.+?))?\s*$",
)
"""Matches a section heading produced by the lovspor renderer, capturing
the raw section id and the optional title.

Heading depth is H2 through H6. Chaptered acts render sections at H3
under a ``## Kapittel``; flat acts with no chapter level render them at
H2 (``## § 1.``) — ~18% of multi-version acts (vrakloven,
særavgiftsloven, hittegodslova, ...); deeply nested forskrifter go as
far as H6 (``###### § 10-4-1.`` in skattebetalingsforskriften). Every
depth must be recognized, because an unrecognized heading is not a
degraded result — it is a section no tool in the server can return.

The id grammar admits a space before a letter suffix. Lovdata writes
both ``### § 5-10a.`` and ``### § 8-7 a.``, and an adjacent-only
pattern silently dropped 2 347 headings across 301 documents —
arbeidsmiljøloven's whole kapittel 2 A (varsling) and folketrygdloven
§ 8-7 a (sykefraværsoppfølging) among them.

The title group is OPTIONAL because Lovdata's source XML sometimes
ships a ``legalArticleValue`` with no accompanying ``title`` field,
and the separating dot is optional and independent of it: chaptered
titleless sections render bare (``### § 5``), flat titleless ones carry
a dangling dot (``## § 14.``), and byggeforskrift-for-longyearbyen
separates id from title with nothing but a space (``### § 2 Plan og
bygningslovens anvendelse``). The ``\\.?(?:\\s+(.+?))?`` shape matches
all of them, plus ``§ 13. (Opphevet)``."""

ANY_HEADING = re.compile(r"^#{2,6} ")
"""Any H2-H6 heading line. A heading that is not a ``§`` section closes
the section being accumulated, so prose under a ``### Merknad`` or
``#### Vedlegg`` grouping is not silently attributed to the § above."""

_INTERNAL_WHITESPACE = re.compile(r"\s+")


def raw_section_id(heading_line: str) -> str | None:
    """Return the section id exactly as written, or ``None`` if not a section."""
    match = SECTION_HEADING.match(heading_line)
    return match.group(1) if match else None


def canonical_section_id(section_id: str) -> str:
    """Fold a section id to the form used as a lookup key.

    Strips a leading ``§``, surrounding whitespace and a trailing dot,
    then removes whitespace *inside* the id and lowercases it, so every
    spelling of one section resolves to one key::

        "§ 8-7 a"   "8-7 a"   "8-7a"   "§8-7A"   -> "8-7a"
        "§ 2 A-1"   "2 A-1"   "2a-1"   "§2A-1"   -> "2a-1"

    This is not cosmetic. The corpus disagrees with itself about the
    same section: headings render ``### § 8-7 a.`` while the link
    targets inside those very files render ``§8-7a`` — and across
    arbeidsmiljøloven kapittel 2 A the link targets disagree with each
    other (``§2a-1`` alongside ``§2A-6``). Folding both sides to one key
    is what lets a cross-reference resolve to the section it names
    instead of being reported as a dangling reference.

    Input that does not look like a section id at all (``"5-12 ledd
    2"``, a chapter word) is returned stripped but otherwise untouched,
    so the lookup fails with its available-ids recovery message rather
    than on a mangled key.
    """
    stripped = section_id.strip().removeprefix("§").strip().rstrip(".").strip()
    if not SECTION_ID.fullmatch(stripped):
        return stripped
    return _INTERNAL_WHITESPACE.sub("", stripped).lower()
