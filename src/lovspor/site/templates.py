"""The one Jinja2 environment of the site build (ADR-0014 Decisions 3 and 5).

Both generators — the site build and, through ``pages.layout()``, the
corpus build — render the shared chrome through this environment, so its
settings are fixed here once:

* ``autoescape=True`` for every template: every artifact-sourced value is
  HTML-escaped at the rendering boundary (Decision 3).
* ``StrictUndefined``: a missing value fails the build instead of
  rendering blank, because a blank reads as zero (Decision 4).
* ``keep_trailing_newline``, ``trim_blocks``, ``lstrip_blocks``: byte-stable
  output that does not depend on template whitespace accidents.
* ``auto_reload=False``: templates are read once, at ``lovspor_commit``.
* No clock global, no ``now`` (Decision 3). ``fact`` and ``badge`` are not
  environment globals either: they are bound per page by
  ``page_globals`` so the ledger knows which page rendered a value.

The page-status vocabulary is one definition (Decision 2, ADR:562-568):
``current``, ``planned``, ``research``, ``early_access``. ``badge`` renders
it, in the page language, as the ``.tag`` span the landing already styles.
"""

from functools import partial
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from lovspor.site.errors import SiteBuildError
from lovspor.site.facts import FactLedger, FactRegistry, Lang, fact_renderer

PageStatus = Literal["current", "planned", "research", "early_access"]

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_STATUS_LABELS: dict[str, dict[Lang, str]] = {
    "current": {"nb": "Gjeldende", "en": "Current"},
    "planned": {"nb": "Planlagt", "en": "Planned"},
    "research": {"nb": "Forskning", "en": "Research"},
    "early_access": {"nb": "Tidlig tilgang", "en": "Early access"},
}
_BADGE = Markup('<span class="tag" data-status="{status}">{label}</span>')


def badge(status: PageStatus, lang: Lang) -> Markup:
    """The status badge of the one vocabulary; anything else fails the build."""
    labels = _STATUS_LABELS.get(status)
    if labels is None:
        raise SiteBuildError(f"page status {status!r} is not in the vocabulary")
    return _BADGE.format(status=status, label=labels[lang])


def site_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        auto_reload=False,
    )


def page_globals(
    page: str, lang: Lang, registry: FactRegistry, ledger: FactLedger
) -> dict[str, object]:
    """The render context of one page: its language, its ``fact`` and its ``badge``."""
    return {
        "lang": lang,
        "fact": fact_renderer(page, lang, registry, ledger),
        "badge": partial(badge, lang=lang),
    }
