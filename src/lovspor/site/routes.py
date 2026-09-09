"""The route tree of lovspor.no (ADR-0014 Decision 2).

A route not listed here is not part of v1 (ADR:508-560). Every route
exists from the first build: a page whose feature is not built is
emitted with a visible status — ``planned``, ``research``,
``early_access`` — never omitted, never written as if shipped
(ADR:562-568). Every site route has a Norwegian page and an ``/en/``
twin with ``hreflang`` alternates both ways and ``rel=canonical`` to
itself; ``/observatory/`` migrates verbatim and has no twin (ADR:620-624).
``/terms`` is reserved, not emitted; ``/mcp`` is an endpoint, not a page
(ADR:626-628).

``/connect/<client>/`` pages are a projection of the client-capability
registry, never the reverse (ADR:565-572): ``client_routes`` is that
projection's hook and yields nothing until a registry exists.

Copy: the landing and the observatory keep the titles and descriptions
of the hand-written pages they replace; the other routes carry short,
honest placeholders until their content lands.
"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.facts import Lang
from lovspor.site.templates import PageStatus

EN_PREFIX = "/en"

RoutePath = Annotated[str, StringConstraints(pattern=r"^/(?:[a-z0-9-]+/)*$")]


class Localised(BaseModel):
    """One string per page language; ``en`` is absent on a route without a twin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nb: str
    en: str | None = None

    def text(self, lang: Lang) -> str:
        value = self.nb if lang == "nb" else self.en
        if value is None:
            raise ValueError(f"no {lang} copy")
        return value


class SiteRoute(BaseModel):
    """One Decision-2 route: its path, template stem, status and copy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: RoutePath
    template: str
    status: PageStatus
    twin: bool = True
    title: Localised
    description: Localised

    @model_validator(mode="after")
    def _twin_has_english_copy(self) -> Self:
        if self.path.startswith(EN_PREFIX + "/"):
            raise ValueError("a route is named by its Norwegian path; the twin is derived")
        if self.twin and (self.title.en is None or self.description.en is None):
            raise ValueError(f"{self.path}: a route with an en twin needs en title and description")
        return self


class EmittedPage(BaseModel):
    """One page of the built tree: a route in one language."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    lang: Lang
    route: SiteRoute

    @property
    def route_path(self) -> str:
        return self.route.path

    @property
    def template(self) -> str:
        return f"pages/{self.route.template}.{self.lang}.html"

    @property
    def alternate(self) -> str | None:
        if not self.route.twin:
            return None
        return self.route.path if self.lang == "en" else en_path(self.route.path)

    def head_context(self) -> dict[str, object]:
        """What ``_base.html`` needs: head values, the hreflang pair, the switch."""
        alternates: tuple[tuple[str, str], ...] = ()
        if self.route.twin:
            alternates = (
                ("nb", canonical_url(self.route.path)),
                ("en", canonical_url(en_path(self.route.path))),
            )
        return {
            "lang": self.lang,
            "title": self.route.title.text(self.lang),
            "description": self.route.description.text(self.lang),
            "canonical": canonical_url(self.path),
            "alternates": alternates,
            "language_switch_href": self.alternate,
            "status": self.route.status,
        }


def en_path(path: str) -> str:
    return f"{EN_PREFIX}{path}"


def canonical_url(path: str) -> str:
    return f"{SITE_ORIGIN}{path}"


def _route(path: str, status: PageStatus, title: Localised, description: Localised) -> SiteRoute:
    return SiteRoute(
        path=path, template="placeholder", status=status, title=title, description=description
    )


SITE_ROUTES: tuple[SiteRoute, ...] = (
    SiteRoute(
        path="/",
        template="placeholder",
        status="current",
        title=Localised(
            nb="lovspor — norsk lovtekst KI-en kan etterprøve",
            en="lovspor — grounded Norwegian law for AI",
        ),
        description=Localised(
            nb=(
                "lovspor gir KI-verktøy den faktiske teksten i norske lover og forskrifter — "
                "med henvisninger du kan etterprøve, ikke paragrafnumre modellen har funnet på."
            ),
            en=(
                "lovspor gives AI assistants the verified text of Norwegian statutes and "
                "regulations — exact citations you can check, not hallucinated paragraph numbers."
            ),
        ),
    ),
    _route(
        "/connect/",
        "planned",
        Localised(nb="Koble til", en="Connect"),
        Localised(
            nb=(
                "Slik kobler du KI-verktøyet ditt til lovverk — én side per klient, med en "
                "testet framgangsmåte."
            ),
            en=(
                "How to connect your AI tool to lovverk — one page per client, with a tested "
                "procedure."
            ),
        ),
    ),
    _route(
        "/infrastructure/",
        "planned",
        Localised(nb="Infrastruktur", en="Infrastructure"),
        Localised(
            nb="Hvordan lovspor er bygget og driftet — det som er i drift, og det som er planlagt.",
            en="How lovspor is built and run — what is in service, and what is planned.",
        ),
    ),
    _route(
        "/research/",
        "research",
        Localised(nb="Forskning", en="Research"),
        Localised(
            nb="Forskningsarbeidet bak lovspor: LLHB, PL-Temporal og det som kommer etter.",
            en="The research behind lovspor: LLHB, PL-Temporal and what follows.",
        ),
    ),
    _route(
        "/research/llhb/",
        "research",
        Localised(nb="LLHB", en="LLHB"),
        Localised(
            nb="Metoden og resultatene i LLHB-referansen, lest fra de publiserte rapportene.",
            en=(
                "The methodology and results of the LLHB benchmark, read from the published "
                "reports."
            ),
        ),
    ),
    _route(
        "/research/pl-temporal/",
        "research",
        Localised(nb="PL-Temporal", en="PL-Temporal"),
        Localised(
            nb="Hva PL-Temporal er, hvor arbeidet står, og hvor kodelageret ligger.",
            en="What PL-Temporal is, where the work stands, and where its repository lives.",
        ),
    ),
    _route(
        "/status/",
        "current",
        Localised(nb="Status", en="Status"),
        Localised(
            nb="Korpusets tilstand ved siste utgivelse og observasjonen av den driftede tjenesten.",
            en="The corpus state at the last release and the observation of the hosted service.",
        ),
    ),
    _route(
        "/docs/",
        "planned",
        Localised(nb="Dokumentasjon", en="Documentation"),
        Localised(
            nb="Verktøyreferansen og bruksdokumentasjonen for lovverk.",
            en="The tool reference and usage documentation for lovverk.",
        ),
    ),
    _route(
        "/about/",
        "planned",
        Localised(nb="Om lovspor", en="About lovspor"),
        Localised(
            nb=(
                "Hvem som står bak, hvorfor, hvilke lisenser som gjelder, og hvordan du tar "
                "kontakt."
            ),
            en="Who is behind it, why, which licences apply, and how to get in touch.",
        ),
    ),
    _route(
        "/business/",
        "early_access",
        Localised(nb="For virksomheter", en="For business"),
        Localised(
            nb="Den driftede tjenesten som et administrert tilbud — tidlig tilgang, uten priser.",
            en="The hosted service as a managed offer — early access, no prices.",
        ),
    ),
    _route(
        "/privacy/",
        "planned",
        Localised(nb="Personvern", en="Privacy"),
        Localised(
            nb="Hvilke opplysninger tjenesten behandler, og hvorfor.",
            en="What data the service processes, and why.",
        ),
    ),
    SiteRoute(
        path="/observatory/",
        template="placeholder",
        status="current",
        twin=False,
        title=Localised(nb="lovspor-observatory — om roboten i loggene dine"),
        description=Localised(
            nb=(
                "lovspor-observatory henter kunngjøringer og lokale forskrifter fra norske "
                "kommuners nettsider. Her står hva den gjør, hvordan du stopper den, og hvem du "
                "kontakter."
            )
        ),
    ),
)


def client_routes(registry: object | None = None) -> tuple[SiteRoute, ...]:
    """``/connect/<client>/`` pages, derived from the client registry — none yet."""
    del registry
    return ()


def emitted_pages(registry: object | None = None) -> tuple[EmittedPage, ...]:
    """Every page of the built tree, Norwegian first, then the ``/en/`` twins."""
    routes = (*SITE_ROUTES, *client_routes(registry))
    norwegian = tuple(EmittedPage(path=route.path, lang="nb", route=route) for route in routes)
    english = tuple(
        EmittedPage(path=en_path(route.path), lang="en", route=route)
        for route in routes
        if route.twin
    )
    return (*norwegian, *english)
