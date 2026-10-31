"""The route tree of lovspor.no (ADR-0014 Decision 2).

A route not listed here is not part of v1 (ADR:508-560). Every route
exists from the first build: a page whose feature is not built is
emitted with a visible status — ``planned``, ``research``,
``early_access`` — never omitted, never written as if shipped
(ADR:562-568). Every site route has a Norwegian page, an ``/en/`` twin
and, since the owner's 2026-10-10 decision, a ``/pl/`` twin, with
``hreflang`` alternates naming all three on each and ``rel=canonical`` to
itself; ``/observatory/`` migrates verbatim and has no twin (ADR:620-624).
``/mcp`` is an endpoint, not a page (ADR:626-628). ``/terms/`` was
reserved there and is emitted since the hosted OAuth sign-in needed a
privacy policy and terms of use to link (owner decision 2026-09-30).

``/connect/<client>/`` pages are a projection of the client-capability
registry, never the reverse (ADR:565-572): ``client_routes`` is that
projection. ``CLIENT_REGISTRY`` is the registry: one ``ConnectClient`` per
client whose procedure this repository records as run against the hosted
service (docs/mcp.md, 2026-09-30) — claude.ai and ChatGPT. A client with
no recorded procedure gets no page; ``/connect/`` names it as untested
instead (owner decision 2026-10-10 split the one page into a chooser and
one guide per registered client). Since #648 (owner request 2026-10-20)
``/connect/`` is again one guide with a section per client, dated by each
entry's ``walked_on``; the per-client guides stay, so published links resolve.

Copy: the landing and the observatory keep the titles and descriptions
of the hand-written pages they replace; ``/connect/`` and ``/docs/`` carry
pages written against this repository's own evidence, and so do
``/privacy/`` and ``/terms/``, whose every processing statement is read
from the code and deploy recipe that does the processing; ``/about/``
gathers on one page what the other pages already state (site audit
2026-10-22: a published placeholder is the first URL a stranger guesses);
the remaining routes carry short, honest placeholders until their content
lands.
"""

from datetime import date
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.facts import Lang
from lovspor.site.templates import PageStatus

EN_PREFIX = "/en"
PL_PREFIX = "/pl"
TWIN_LANGS: tuple[Lang, ...] = ("nb", "en", "pl")
_PREFIXES: dict[Lang, str] = {"nb": "", "en": EN_PREFIX, "pl": PL_PREFIX}

RoutePath = Annotated[str, StringConstraints(pattern=r"^/(?:[a-z0-9-]+/)*$")]


class Localised(BaseModel):
    """One string per page language; ``en`` and ``pl`` are absent on a route without twins."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nb: str
    en: str | None = None
    pl: str | None = None

    def text(self, lang: Lang) -> str:
        value = {"nb": self.nb, "en": self.en, "pl": self.pl}[lang]
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
    def _twins_have_their_copy(self) -> Self:
        if self.path.startswith((EN_PREFIX + "/", PL_PREFIX + "/")):
            raise ValueError("a route is named by its Norwegian path; the twins are derived")
        if not self.twin:
            return self
        for lang in TWIN_LANGS[1:]:
            if getattr(self.title, lang) is None or getattr(self.description, lang) is None:
                raise ValueError(
                    f"{self.path}: a route with a {lang} twin needs {lang} title and description"
                )
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
    def twins(self) -> tuple[tuple[Lang, str], ...]:
        """Every language's path of this route, this page's own included; none without twins."""
        if not self.route.twin:
            return ()
        return tuple((lang, lang_path(self.route.path, lang)) for lang in TWIN_LANGS)

    def head_context(self) -> dict[str, object]:
        """What ``_base.html`` needs: head values, the hreflang set, the switch.

        ``corpus`` is false here by definition: this is the site build, and
        the chrome's corpus variant is the one rendered for the corpus tree
        (ADR-0014 Amendment 2). It is declared rather than defaulted in the
        template, so a page that never says which frame it wants fails under
        ``StrictUndefined`` instead of quietly taking one.
        """
        twins = self.twins
        return {
            "lang": self.lang,
            "title": self.route.title.text(self.lang),
            "description": self.route.description.text(self.lang),
            "canonical": canonical_url(self.path),
            "alternates": tuple((lang, canonical_url(path)) for lang, path in twins),
            "language_switch": twins,
            "corpus": False,
            "status": self.route.status,
        }


def lang_path(path: str, lang: Lang) -> str:
    return f"{_PREFIXES[lang]}{path}"


def en_path(path: str) -> str:
    return lang_path(path, "en")


def canonical_url(path: str) -> str:
    return f"{SITE_ORIGIN}{path}"


def _route(path: str, status: PageStatus, title: Localised, description: Localised) -> SiteRoute:
    return SiteRoute(
        path=path, template="placeholder", status=status, title=title, description=description
    )


SITE_ROUTES: tuple[SiteRoute, ...] = (
    SiteRoute(
        path="/",
        template="landing",
        status="current",
        title=Localised(
            nb="lovspor — norsk lovtekst KI-en kan etterprøve",
            en="lovspor — grounded Norwegian law for AI",
            pl="lovspor — norweskie prawo, którego AI nie zmyśla",
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
            pl=(
                "lovspor daje asystentom AI prawdziwy tekst norweskich ustaw i rozporządzeń — "
                "z odesłaniami, które możesz sprawdzić, a nie numerami paragrafów zmyślonymi "
                "przez model."
            ),
        ),
    ),
    SiteRoute(
        path="/connect/",
        template="connect",
        status="current",
        title=Localised(nb="Koble til", en="Connect", pl="Połącz"),
        description=Localised(
            nb=(
                "Én veiledning for alle klienter: Claude og ChatGPT med stegene vi har kjørt, "
                "Claude Code, VS Code, de klientene vi ikke har testet, tre spørsmål å begynne "
                "med og hjelp når det ikke virker."
            ),
            en=(
                "One guide for every client: Claude and ChatGPT with the steps we have run, "
                "Claude Code, VS Code, the clients we have not tested, three questions to start "
                "with and help when it does not work."
            ),
            pl=(
                "Jedna instrukcja dla wszystkich klientów: Claude i ChatGPT z krokami, które "
                "przeszliśmy, Claude Code, VS Code, klienci, których nie sprawdziliśmy, trzy "
                "pytania na start i pomoc, gdy coś nie działa."
            ),
        ),
    ),
    _route(
        "/infrastructure/",
        "planned",
        Localised(nb="Infrastruktur", en="Infrastructure", pl="Infrastruktura"),
        Localised(
            nb="Hvordan lovspor er bygget og driftet — det som er i drift, og det som er planlagt.",
            en="How lovspor is built and run — what is in service, and what is planned.",
            pl="Jak lovspor jest zbudowany i utrzymywany — co działa, a co jest w planach.",
        ),
    ),
    _route(
        "/research/",
        "research",
        Localised(nb="Forskning", en="Research", pl="Badania"),
        Localised(
            nb="Forskningsarbeidet bak lovspor: LLHB, PL-Temporal og det som kommer etter.",
            en="The research behind lovspor: LLHB, PL-Temporal and what follows.",
            pl="Prace badawcze stojące za lovspor: LLHB, PL-Temporal i kolejne.",
        ),
    ),
    _route(
        "/research/llhb/",
        "research",
        Localised(nb="LLHB", en="LLHB", pl="LLHB"),
        Localised(
            nb="Metoden og resultatene i LLHB-referansen, lest fra de publiserte rapportene.",
            en=(
                "The methodology and results of the LLHB benchmark, read from the published "
                "reports."
            ),
            pl="Metoda i wyniki benchmarku LLHB, odczytane z opublikowanych raportów.",
        ),
    ),
    _route(
        "/research/pl-temporal/",
        "research",
        Localised(nb="PL-Temporal", en="PL-Temporal", pl="PL-Temporal"),
        Localised(
            nb="Hva PL-Temporal er, hvor arbeidet står, og hvor kodelageret ligger.",
            en="What PL-Temporal is, where the work stands, and where its repository lives.",
            pl="Czym jest PL-Temporal, na jakim etapie są prace i gdzie jest jego repozytorium.",
        ),
    ),
    SiteRoute(
        path="/status/",
        template="status",
        status="current",
        title=Localised(nb="Status", en="Status", pl="Status"),
        description=Localised(
            nb="Korpusets tilstand ved siste utgivelse og observasjonen av den driftede tjenesten.",
            en="The corpus state at the last release and the observation of the hosted service.",
            pl="Stan korpusu przy ostatnim wydaniu i obserwacja usługi hostowanej.",
        ),
    ),
    SiteRoute(
        path="/docs/",
        template="docs",
        status="current",
        title=Localised(nb="Dokumentasjon", en="Documentation", pl="Dokumentacja"),
        description=Localised(
            nb=(
                "Verktøyflaten i lovverk: hva hvert verktøy svarer på, hva det ikke svarer på, "
                "innlogging, korpuset bak og forbeholdene."
            ),
            en=(
                "The lovverk tool surface: what each tool answers, what it will not answer, "
                "authentication, the corpus behind it and the caveats."
            ),
            pl=(
                "Narzędzia lovverk: na co odpowiada każde z nich, na co nie odpowiada, "
                "logowanie, korpus i zastrzeżenia."
            ),
        ),
    ),
    SiteRoute(
        path="/about/",
        template="about",
        status="current",
        title=Localised(nb="Om lovspor", en="About lovspor", pl="O lovspor"),
        description=Localised(
            nb=(
                "Hvem som står bak, hvorfor, hvilke lisenser som gjelder, og hvordan du tar "
                "kontakt."
            ),
            en="Who is behind it, why, which licences apply, and how to get in touch.",
            pl="Kto za tym stoi, po co, jakie licencje obowiązują i jak się skontaktować.",
        ),
    ),
    _route(
        "/business/",
        "early_access",
        Localised(nb="For virksomheter", en="For business", pl="Dla firm"),
        Localised(
            nb="Den driftede tjenesten som et administrert tilbud — tidlig tilgang, uten priser.",
            en="The hosted service as a managed offer — early access, no prices.",
            pl="Usługa hostowana jako oferta zarządzana — wczesny dostęp, bez cennika.",
        ),
    ),
    SiteRoute(
        path="/privacy/",
        template="privacy",
        status="current",
        title=Localised(nb="Personvern", en="Privacy", pl="Prywatność"),
        description=Localised(
            nb="Hvilke opplysninger tjenesten behandler, og hvorfor.",
            en="What data the service processes, and why.",
            pl="Jakie dane przetwarza usługa i w jakim celu.",
        ),
    ),
    SiteRoute(
        path="/terms/",
        template="terms",
        status="current",
        title=Localised(nb="Vilkår for bruk", en="Terms of use", pl="Warunki korzystania"),
        description=Localised(
            nb="Vilkårene for å bruke lovspor.no og den driftede lovverk-tjenesten.",
            en="The terms for using lovspor.no and the hosted lovverk service.",
            pl="Warunki korzystania z lovspor.no i hostowanej usługi lovverk.",
        ),
    ),
    SiteRoute(
        path="/observatory/",
        template="observatory",
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


class ConnectClient(BaseModel):
    """One registry entry: a client whose connect procedure has been run.

    ``walked_on`` is the day the procedure was run against the hosted service,
    as the repository records it; ``/connect/`` renders it as a ledgered fact
    (#648), so a section never claims a test it cannot date.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+$")]
    walked_on: date
    title: Localised
    description: Localised

    def route(self) -> SiteRoute:
        """The client's guide: ``/connect/<slug>/``, template ``connect-<slug>``."""
        return SiteRoute(
            path=f"/connect/{self.slug}/",
            template=f"connect-{self.slug}",
            status="current",
            title=self.title,
            description=self.description,
        )


# Claude Code and VS Code keep a section on /connect/, not a guide of their own,
# so each walk is a date rather than a registry entry (#729).
CLAUDE_CODE_HOSTED_WALKED_ON = date(2026, 10, 30)  # docs/mcp.md "Connecting Claude Code"
VSCODE_HOSTED_WALKED_ON = date(2026, 10, 30)  # docs/mcp.md "Connecting VS Code"

CLIENT_REGISTRY: tuple[ConnectClient, ...] = (
    ConnectClient(
        slug="claude",
        walked_on=date(2026, 9, 30),  # docs/mcp.md "Connecting a chat app"
        title=Localised(nb="Koble til Claude", en="Connect Claude", pl="Połącz z Claude"),
        description=Localised(
            nb=(
                "Steg for steg: lovverk som connector i Claude, med innlogging via OAuth og "
                "ingenting å lime inn — og som lokal kopi i Claude Code."
            ),
            en=(
                "Step by step: lovverk as a connector in Claude, with OAuth sign-in and "
                "nothing to paste — and as a local copy in Claude Code."
            ),
            pl=(
                "Krok po kroku: lovverk jako konektor w Claude, z logowaniem przez OAuth i "
                "bez wklejania czegokolwiek — oraz jako lokalna kopia w Claude Code."
            ),
        ),
    ),
    ConnectClient(
        slug="chatgpt",
        walked_on=date(2026, 9, 30),  # docs/mcp.md "Connecting a chat app"
        title=Localised(nb="Koble til ChatGPT", en="Connect ChatGPT", pl="Połącz z ChatGPT"),
        description=Localised(
            nb=(
                "Steg for steg: lovverk som connector i ChatGPT, med innlogging via OAuth og "
                "ingenting å lime inn."
            ),
            en=(
                "Step by step: lovverk as a connector in ChatGPT, with OAuth sign-in and "
                "nothing to paste."
            ),
            pl=(
                "Krok po kroku: lovverk jako konektor w ChatGPT, z logowaniem przez OAuth i "
                "bez wklejania czegokolwiek."
            ),
        ),
    ),
)


def client_routes(registry: tuple[ConnectClient, ...] | None = None) -> tuple[SiteRoute, ...]:
    """``/connect/<client>/`` pages, one per registry entry, in registry order."""
    entries = CLIENT_REGISTRY if registry is None else registry
    return tuple(client.route() for client in entries)


def emitted_pages(registry: tuple[ConnectClient, ...] | None = None) -> tuple[EmittedPage, ...]:
    """Every page of the tree: Norwegian first, then the ``/en/``, then the ``/pl/`` twins."""
    routes = (*SITE_ROUTES, *client_routes(registry))
    norwegian = tuple(EmittedPage(path=route.path, lang="nb", route=route) for route in routes)
    twins = tuple(
        EmittedPage(path=lang_path(route.path, lang), lang=lang, route=route)
        for lang in TWIN_LANGS[1:]
        for route in routes
        if route.twin
    )
    return (*norwegian, *twins)
