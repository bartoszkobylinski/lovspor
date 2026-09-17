"""The shared chrome of corpus pages and site pages (ADR-0014 Decision 5).

One template source, two generators: the site build includes the same
partials in ``_base.html``, and the corpus generator's ``pages.layout()``
renders them through ``corpus_chrome_html``. The chrome is
corpus-state-independent by construction — fixed strings and fixed
internal links only, no counts, dates, commit identifiers, freshness or
capability state — and that is enforced structurally: ``chrome_html``
takes the page language and the language-switch target and nothing else,
and ``corpus_chrome_html`` takes nothing at all, so there is no parameter
through which a state value could arrive (ADR:1170-1176). The templates
render under ``StrictUndefined`` with exactly those names in scope, so a
partial reaching for ``fact`` or a manifest value fails here rather than
silently on a corpus page.

``corpus_chrome_html()`` is the corpus variant (ADR-0014 Amendment 2,
2026-09-16): Norwegian labels glossed in English and a link to the
English site, no status badge, and no per-page language switch. The
switch is what ``language_switch_href`` renders, and it promises a twin
at the other language's URL; no law page has one, so passing ``/en/``
through that parameter would publish a dead link on ~93 000 pages
instead of the honest link to the part of the site that is English. The
chrome adds no external link and no new scheme (ADR:1180-1181).
"""

from pydantic import BaseModel, ConfigDict

from lovspor.site.facts import Lang
from lovspor.site.templates import site_environment


class Chrome(BaseModel):
    """The rendered header and footer of one page."""

    model_config = ConfigDict(frozen=True)

    header: str
    footer: str


def chrome_html(lang: Lang, language_switch_href: str | None = None) -> Chrome:
    """Render the chrome for ``lang``; a switch target adds the language link."""
    return _render({"lang": lang, "language_switch_href": language_switch_href, "corpus": False})


def corpus_chrome_html() -> Chrome:
    """The Norwegian corpus frame, glossed in English (ADR-0014 Amendment 2).

    Zero parameters, deliberately: the corpus renders this on ~93k pages,
    and an argument is the only way a count, a commit or a capability
    could reach them, so its absence is what keeps a corpus update from
    moving a byte of the chrome.
    """
    return _render({"lang": "nb", "language_switch_href": None, "corpus": True})


def _render(context: dict[str, object]) -> Chrome:
    """Both partials of one variant, from the one environment."""
    environment = site_environment()
    return Chrome(
        header=environment.get_template("_chrome_header.html").render(context),
        footer=environment.get_template("_chrome_footer.html").render(context),
    )
