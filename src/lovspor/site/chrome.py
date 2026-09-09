"""The shared chrome of corpus pages and site pages (ADR-0014 Decision 5).

One template source, two generators: the site build includes the same
partials in ``_base.html``, and the corpus generator's ``pages.layout()``
renders them through ``chrome_html`` (wired in PR 4). The chrome is
corpus-state-independent by construction — fixed strings and fixed
internal links only, no counts, dates, commit identifiers, freshness or
capability state — and that is enforced structurally: ``chrome_html``
takes the page language and the language-switch target and nothing
else, so there is no parameter through which a state value could arrive
(ADR:1170-1176). The templates render under ``StrictUndefined`` with
exactly those two names in scope, so a partial reaching for ``fact`` or
a manifest value fails here rather than silently on a corpus page.

``chrome_html("nb")`` is the corpus variant: Norwegian, no language
switch, no status badge (ADR:1190-1191). The chrome adds no external
link and no new scheme (ADR:1180-1181).
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
    environment = site_environment()
    context = {"lang": lang, "language_switch_href": language_switch_href}
    return Chrome(
        header=environment.get_template("_chrome_header.html").render(context),
        footer=environment.get_template("_chrome_footer.html").render(context),
    )
