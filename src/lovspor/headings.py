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

_TITLE = r"(?:\.(?:\s+(.+?))?|\s+([A-ZÆØÅÄÖÜ].+?))?"
"""The title, reachable two ways, and the asymmetry between them is the point.

After a dot, anything goes — the dot is itself the evidence that what
follows is a title (``§ 13. (Opphevet)``).

Without a dot, the title must start with a capital. Seven headings in
the corpus need the dotless form (``### § 2 Plan og bygningslovens
anvendelse``, ``## § 21.1 Straff``) and every one of them is
capitalised, because Norwegian legal titles are. Allowing a lowercase
word there instead manufactures sections out of ordinary prose:
``### § 5 og andre bestemmelser`` parsed as a real § 5 titled "og andre
bestemmelser", and ``### § 12 i skatteloven`` as a § "12 i" that exists
in no act. A fabricated section is worse than a missed one — it hides
the real content block, and it validates citations to a paragraph that
was never written.

A future Lovdata shape that breaks the capitalisation assumption is not
silent: corpus_audit reports it as an unparsed heading."""

SECTION_HEADING = re.compile(rf"^#{{2,6}} § ({SECTION_ID.pattern}){_TITLE}\s*$")
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

The title is OPTIONAL because Lovdata's source XML sometimes ships a
``legalArticleValue`` with no accompanying ``title`` field: chaptered
titleless sections render bare (``### § 5``) and flat titleless ones
carry a dangling dot (``## § 14.``).

When a title is present it arrives through one of the two branches of
:data:`_TITLE` — after a dot, where anything may follow (``§ 13.
(Opphevet)``), or after a bare space, where it must begin with a
capital (``### § 2 Plan og bygningslovens anvendelse``). See that
constant for why the dotless branch is the constrained one; without the
constraint it manufactured sections out of prose."""

ANY_HEADING = re.compile(r"^#{2,6} ")
"""Any H2-H6 heading line. A heading that is not a ``§`` section closes
the section being accumulated, so prose under a ``### Merknad`` or
``#### Vedlegg`` grouping is not silently attributed to the § above."""

_INTERNAL_WHITESPACE = re.compile(r"\s+")


def parse_section_heading(heading_line: str) -> tuple[str, str | None] | None:
    """Split a section heading into ``(id as written, title or None)``.

    The title arrives in one of two capture groups depending on whether a
    dot separated it — see :data:`_TITLE`. Callers should use this rather
    than reading groups off the match, so the two-branch shape stays an
    implementation detail of this module.
    """
    match = SECTION_HEADING.match(heading_line)
    if match is None:
        return None
    return match.group(1), match.group(2) or match.group(3)


def raw_section_id(heading_line: str) -> str | None:
    """Return the section id exactly as written, or ``None`` if not a section."""
    parsed = parse_section_heading(heading_line)
    return parsed[0] if parsed else None


BLOCK_ID_PREFIX = "#"
"""Marks an id that names a non-``§`` content block rather than a section.

A ``§`` id always starts with a digit, so the prefix cannot collide with
one. It also makes the distinction visible in every payload the server
returns: a caller that sees ``#takster-fra-1-juli-2026`` knows not to
cite it as a paragraph of law."""

_NON_SLUG_CHAR = re.compile(r"[^a-z0-9æøåäöü]+")
_MAX_BLOCK_ID_CHARS = 60


def block_id(heading_text: str) -> str:
    """Derive a stable, addressable id for a non-``§`` heading.

    Whole swathes of the corpus live under headings that carry no ``§``
    at all: the takst tables of takstforskriften sit under ``### Takster
    fra 1. juli 2026``, and the European Convention on Human Rights is
    rendered into menneskerettsloven as ``### Art 1. Obligation to
    respect human rights``. Roughly 40% of corpus text sits outside any
    ``§``, and none of it could be addressed or embedded, because the
    parsers treated such a heading purely as a boundary and dropped the
    lines that followed.

    Returns an empty string for a heading with no slug-able characters,
    which the callers treat as a plain boundary — the old behaviour.

    Note for mutation runs: the two ``strip("-")`` calls below yield
    equivalent mutants under mutmut's character-set mutation. The input
    has already been lowercased and filtered to ``[a-z0-9æøåäöü-]``, so
    the extra uppercase characters mutmut adds to the strip set cannot
    occur and no input distinguishes the programs. Not a coverage gap.
    """
    slug = _NON_SLUG_CHAR.sub("-", heading_text.lower()).strip("-")
    if not slug:
        return ""
    return BLOCK_ID_PREFIX + slug[:_MAX_BLOCK_ID_CHARS].rstrip("-")


def is_block_id(section_id: str) -> bool:
    """True when ``section_id`` names a content block, not a ``§`` section."""
    return section_id.startswith(BLOCK_ID_PREFIX)


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
