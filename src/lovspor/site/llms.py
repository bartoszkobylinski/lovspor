"""``/llms.txt``: where an agent is told what this site holds (#340).

The site publishes a machine-readable twin of every page, two sitemaps and
a tool surface, and until now nothing at the root said so. This is that
statement, in the emerging ``llms.txt`` convention: what the corpus is, how
to read a page as data, where the indexes are, how to ask instead of crawl,
and — as plainly as ``/docs/`` states them — what is *not* here.

**It belongs to the site build, not the corpus publish**, for two reasons
that agree. The serving one: ``release/envelope.py`` routes only ``/lov``,
``/forskrift``, ``/sitemap.xml``, ``/sitemaps/*``, ``/robots.txt`` and
``/site-manifest.json`` to the corpus tree, and everything else at the root
to the site tree — so a file written beside ``robots.txt`` would not answer
at ``https://lovspor.no/llms.txt`` at all. The provenance one: it states
counts, and the fact ledger that keeps counts honest lives here
(ADR-0014 Decision 4).

So every number in the file is read from an artifact and recorded in the
ledger, exactly as ``/docs/`` and ``/status/`` read theirs; the build then
carries those readings into ``site-facts.json`` under this file's own page
name. Nothing else in the document carries a digit except the licence
identifier, and a test proves it: a count typed here would drift the moment
the corpus moved, and would look just as authoritative while it did.

The language is English. The corpus is Norwegian and the site is bilingual,
but this file addresses tooling rather than a reader, and the convention's
ecosystem is English; the document says plainly that the law it indexes is
Norwegian, and links the Norwegian pages.
"""

from lovspor.site.facts import FactLedger, FactRegistry, Lang, fact_text_renderer

LLMS_NAME = "llms.txt"
LLMS_PAGE = f"/{LLMS_NAME}"
LLMS_LANG: Lang = "en"

_OPENING = (
    "# lovspor\n"
    "\n"
    "> The text of Norwegian acts and central regulations, published so that a\n"
    "> machine can quote it exactly instead of recalling it. The source is\n"
    "> Lovdata's open data, under the NLOD 2.0 licence.\n"
    "\n"
    "lovspor publishes {documents} documents: acts passed by the Storting, and the\n"
    "central regulations issued under them by a ministry or directorate. The text is\n"
    "Lovdata's, transformed and structured by lovspor and not reproduced in its\n"
    "original form. lovspor is not an official channel of publication, and nothing\n"
    "here is legal advice.\n"
)

_TWIN = (
    "\n"
    "## Read a page as data\n"
    "\n"
    "Every act, regulation and provision page has a machine-readable twin beside\n"
    "it: append index.json to its URL. The browse indexes at /lov/ and /forskrift/\n"
    "have none — they list documents rather than carrying one. The companions\n"
    "sitemap below is the authoritative list.\n"
    "\n"
    "The twin carries that page's exact body text, its canonical URL, the\n"
    "corpus revision it was rendered from, the digest of the rendered page, and its\n"
    "licence and attribution. Prefer it to scraping the HTML, which wraps the same\n"
    "text in navigation.\n"
    "\n"
    "- https://lovspor.no/lov/SLUG/index.json — one act, whole\n"
    "- https://lovspor.no/lov/SLUG/paragraf/NUMBER/index.json — one provision\n"
    "- https://lovspor.no/forskrift/SLUG/index.json — one regulation\n"
)

_INDEXES = (
    "\n"
    "## Find the pages\n"
    "\n"
    "- https://lovspor.no/sitemap.xml — every page\n"
    "- https://lovspor.no/sitemaps/companions.xml — every index.json twin\n"
    "- https://lovspor.no/site-manifest.json — the corpus state this release was\n"
    "  built from\n"
    "- https://lovspor.no/site-facts.json — every number this site states, each\n"
    "  with the artifact and field it was read from\n"
    "- https://lovspor.no/robots.txt — what may be crawled\n"
)

_ASK = (
    "\n"
    "## Ask instead of crawling\n"
    "\n"
    "https://lovspor.no/mcp is a Model Context Protocol server with {tools}\n"
    "read-only tools: search, retrieval, citation checks, verbatim quote\n"
    "verification, and the corpus's own history. It requires a token — a call\n"
    "without credentials is refused — and robots.txt disallows it deliberately,\n"
    "because it is called, not crawled. How to connect, and what each tool\n"
    "answers: https://lovspor.no/docs/\n"
)

_LIMITS = (
    "\n"
    "## What is not here\n"
    "\n"
    "- No case law, preparatory works, circulars or municipal regulations. A rule\n"
    "  can be binding and still be absent here, so an empty result is not evidence\n"
    "  that no such rule exists.\n"
    "- Not the law in force on a date. The history tools answer what the corpus\n"
    "  held at a date, not which provisions were legally in force then. Those are\n"
    "  two different questions, and only the first can be answered from here.\n"
    "- No paraphrase checking. Quote verification confirms that a verbatim string\n"
    "  appears in a provision; whether a rewording is legally faithful, it cannot\n"
    "  say.\n"
)

_TEMPLATE = _OPENING + _TWIN + _INDEXES + _ASK + _LIMITS


def llms_txt(registry: FactRegistry, ledger: FactLedger) -> bytes:
    """The site-root ``llms.txt``, with both counts read from the ledger.

    A pure function of the registry: no clock, no path, no corpus scan, so
    two builds of one release produce the same bytes.
    """
    fact = fact_text_renderer(LLMS_PAGE, LLMS_LANG, registry, ledger)
    return _TEMPLATE.format(
        documents=fact("corpus.documents", kind="corpus"),
        tools=fact("code.tool_surface.tool_count", kind="code"),
    ).encode("utf-8")
