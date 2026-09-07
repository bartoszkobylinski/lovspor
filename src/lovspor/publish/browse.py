"""Browse indexes: /lov/ and /forskrift/, A-Å (ADR-0013 Decisions 1, 7).

One page per route in v1 — the ADR leaves pagination of the forskrift
index as an open question, and a single A-Å page is the smaller claim.
Ordering is Norwegian collation implemented locale-free: the byte-identical
build invariant forbids depending on the build machine's locale tables.
"""

import html as html_escape

from lovspor.publish.inventory import DocumentPlan, PublishInventory, Route
from lovspor.publish.pages import document_url, layout

BROWSE_ROUTES: tuple[Route, ...] = ("lov", "forskrift")

_ROUTE_TITLES: dict[Route, str] = {"lov": "Lover", "forskrift": "Forskrifter"}

_ALPHABET = "abcdefghijklmnopqrstuvwxyzæøå"
"""Norwegian alphabetical order: æ ø å collate after z, å last."""

_LETTER_ORDER = {letter: index for index, letter in enumerate(_ALPHABET)}

_OTHER_GROUP = "#"
"""Names starting outside the alphabet (digits, symbols) group here,
ahead of A — matching their sort position, so groups stay contiguous."""


def browse_index_url(route: Route) -> str:
    return f"/{route}/"


def display_name(plan: DocumentPlan) -> str:
    return plan.title or plan.slug


def collation_key(name: str) -> tuple[tuple[int, int], ...]:
    """Locale-free Norwegian sort key.

    Alphabet letters carry their Norwegian rank; any other character
    sorts ahead of every letter, by code point. Casefolded, so the key
    is one deterministic function of the name on every machine.
    """
    return tuple(
        (1, _LETTER_ORDER[ch]) if ch in _LETTER_ORDER else (0, ord(ch)) for ch in name.casefold()
    )


def browse_index_html(route: Route, inventory: PublishInventory) -> str:
    """The A-Å browse page for one route."""
    plans = sorted(
        (plan for plan in inventory.documents if plan.route == route),
        key=lambda plan: (collation_key(display_name(plan)), plan.slug),
    )
    heading = f"{_ROUTE_TITLES[route]} A–Å"  # noqa: RUF001 — deliberate EN DASH in the range
    parts = [f"<h1>{html_escape.escape(heading)}</h1>", _groups_html(plans)]
    content = "\n".join(part for part in parts if part)
    return layout("nb", heading, browse_index_url(route), content)


def _groups_html(plans: list[DocumentPlan]) -> str:
    """Sections in first-letter groups; input order is already sorted,
    and the group letter is monotonic under the collation key, so plain
    insertion grouping preserves the page order."""
    groups: dict[str, list[DocumentPlan]] = {}
    for plan in plans:
        groups.setdefault(_group_of(display_name(plan)), []).append(plan)
    return "\n".join(_group_html(letter, members) for letter, members in groups.items())


def _group_of(name: str) -> str:
    first = name.casefold()[:1]
    return first.upper() if first in _LETTER_ORDER else _OTHER_GROUP


def _group_html(letter: str, members: list[DocumentPlan]) -> str:
    safe = html_escape.escape(letter, quote=True)
    items = "\n".join(_entry_html(plan) for plan in members)
    return f'<section aria-label="{safe}">\n<h2>{safe}</h2>\n<ul>\n{items}\n</ul>\n</section>'


def _entry_html(plan: DocumentPlan) -> str:
    name = html_escape.escape(display_name(plan))
    return f'<li><a href="{document_url(plan)}">{name}</a></li>'
