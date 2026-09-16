"""Document and provision pages (ADR-0013 Decisions 4-5).

A page is a pure function of its plan, its lines and the build's
provenance. No wall-clock value and no global corpus commit may appear
here: per-document provenance carries the document's own source revision,
and the byte-identical build invariant depends on nothing else leaking in.

The NLOD transformation statement is a licence-contract requirement
(NLOD 2.0 requires marking changed information as changed), not styling;
its wording is fixed here and versioned with the site schema.

The shell is the site's, not a second design of its own (ADR-0014
Decision 5, issue #335): the stylesheet and the header and footer come
from the one template source ``lovspor.site`` owns, so a reader who lands
on a law page from a search engine can reach ``/``, ``/lov/`` and
``/forskrift/`` instead of meeting a dead end. Both are
corpus-state-independent, which is what keeps ADR-0013's churn invariant
true: neither takes a plan, a manifest or a fact, so no corpus update can
move a byte of them.
"""

import html as html_escape
from collections.abc import Iterator
from functools import cache

from pydantic import BaseModel, ConfigDict

from lovspor.headings import parse_section_heading
from lovspor.publish.html import LinkResolver, render_body_html
from lovspor.publish.inventory import DocumentPlan, ProvisionRef, normalise_pid
from lovspor.site.chrome import Chrome, chrome_html
from lovspor.site.style import stylesheet

SITE_ORIGIN = "https://lovspor.no"

COMPANION_NAME = "index.json"
"""The machine-readable twin's filename beside a page (ADR-0013 Decision 4).

One spelling for the emitter that writes it, the head that announces it and
the sitemap that lists it, so the three cannot drift apart.
"""

_NLOD_URL = "https://data.norge.no/nlod/no/2.0"

_NLOD_STATEMENT = (
    "Inneholder data under Norsk lisens for offentlige data (NLOD 2.0), "
    "tilgjengeliggjort av Lovdata. Informasjonen er transformert og "
    "strukturert av Lovspor og gjengis ikke i sin opprinnelige form. "
    "Lovspor er ikke offisiell kunngjøringskilde."
)


class PageProvenance(BaseModel):
    """Build-supplied provenance for one document's pages.

    ``source_revision`` is the last corpus commit touching this document's
    own Markdown — never the global HEAD (ADR-0013 Decision 3).
    """

    model_config = ConfigDict(frozen=True)

    source_revision: str
    xml_hash: str
    renderer_version: int | None


class PageHead(BaseModel):
    """What the shell needs above ``<body>``: language, title, path, twin.

    ``companion`` says whether an ``index.json`` sits beside this page. The
    emitter writes one for every document and provision page and none for the
    browse indexes, so the page carries the answer as a declared property
    rather than letting the head infer it from the shape of a path — a guess
    that would advertise a file the release does not serve the first time a
    route gained a level.
    """

    model_config = ConfigDict(frozen=True)

    lang: str
    title: str
    path: str
    companion: bool = True


def document_url(plan: DocumentPlan) -> str:
    return f"/{plan.route}/{plan.slug}/"


def provision_url(plan: DocumentPlan, pid: str) -> str:
    return f"/{plan.route}/{plan.slug}/paragraf/{pid}/"


def document_page_html(
    plan: DocumentPlan,
    body_lines: list[str],
    provenance: PageProvenance,
    resolve: LinkResolver,
) -> str:
    """The canonical document page: title, TOC, full text, provenance."""
    title = plan.title or plan.slug
    suppressed = frozenset(plan.duplicate_pids)
    heading, rest = _title_split(body_lines)
    parts = [
        render_body_html(heading, resolve, suppressed),
        _toc_html(plan),
        render_body_html(rest, resolve, suppressed),
        _provenance_html(plan, provenance),
    ]
    content = "\n".join(part for part in parts if part)
    return layout(PageHead(lang=plan.language, title=title, path=document_url(plan)), content)


def provision_page_html(
    plan: DocumentPlan,
    provision: ProvisionRef,
    provenance: PageProvenance,
    section_lines: list[str],
    resolve: LinkResolver,
) -> str:
    """One provision's canonical page: exact text, parent, neighbours."""
    doc_title = plan.title or plan.slug
    title = f"§ {provision.heading_id}. {provision.title or ''}".rstrip(". ")
    parts = [
        _breadcrumb_html(plan),
        render_body_html(section_lines, resolve),
        _neighbours_html(plan, provision),
        _provenance_html(plan, provenance),
    ]
    head = PageHead(
        lang=plan.language,
        title=f"{title} — {doc_title}",
        path=provision_url(plan, provision.pid),
    )
    return layout(head, "\n".join(part for part in parts if part))


def section_slices(body_lines: list[str]) -> dict[str, list[str]]:
    """Map each unique pid to its lines: heading up to the next boundary.

    A boundary is any heading line — another section or a chapter. On a
    duplicate pid the first slice wins here, but the inventory withholds
    those pages entirely, so the choice is never published.
    """
    slices: dict[str, list[str]] = {}
    for pid, start, end in _section_spans(body_lines):
        slices.setdefault(pid, body_lines[start:end])
    return slices


def _section_spans(body_lines: list[str]) -> Iterator[tuple[str, int, int]]:
    """(pid, start, end) for every section heading, in document order."""
    starts = [
        (index, parsed[0])
        for index, line in enumerate(body_lines)
        if (parsed := parse_section_heading(line)) is not None
    ]
    boundaries = [index for index, line in enumerate(body_lines) if line.startswith("#")]
    for start, heading_id in starts:
        end = next((b for b in boundaries if b > start), len(body_lines))
        yield normalise_pid(heading_id), start, end


@cache
def _corpus_chrome() -> Chrome:
    """The Norwegian chrome every corpus page carries (ADR-0014 Decision 5).

    Zero arguments, deliberately: there is no parameter through which a
    count, a commit or a capability could arrive, so the chrome cannot
    churn when the corpus changes. Cached because the corpus build renders
    it on every one of ~93k pages and it is the same bytes every time.
    """
    return chrome_html("nb")


def _twin_link_html(head: PageHead) -> str:
    """The link to this page's machine-readable twin, or nothing when it has none.

    ADR-0013 Decision 4 writes ``index.json`` beside every document and
    provision page because static hosting cannot content-negotiate — and
    until #340 nothing said so, leaving an agent that arrived from a search
    engine to scrape the HTML instead. The href is a fixed function of the
    page's own path: no fact, no count, no corpus state reaches it, so it
    moves no byte when the corpus moves.
    """
    if not head.companion:
        return ""
    href = html_escape.escape(f"{SITE_ORIGIN}{head.path}{COMPANION_NAME}", quote=True)
    return f'<link rel="alternate" type="application/json" href="{href}">\n'


def _head_html(head: PageHead) -> str:
    """Everything above ``<body>``: escaped values, canonical, twin, one stylesheet."""
    safe_title = html_escape.escape(head.title, quote=True)
    canonical = html_escape.escape(f"{SITE_ORIGIN}{head.path}", quote=True)
    return (
        "<!doctype html>\n"
        f'<html lang="{html_escape.escape(head.lang, quote=True)}">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{safe_title}</title>\n"
        f'<link rel="canonical" href="{canonical}">\n'
        f"{_twin_link_html(head)}"
        f"<style>\n{stylesheet()}</style>\n"
        "</head>\n"
    )


def layout(head: PageHead, content: str) -> str:
    """The shared shell: head, the site's chrome, the page's own ``<main>``."""
    chrome = _corpus_chrome()
    return (
        f"{_head_html(head)}"
        "<body>\n"
        '<div class="wrap">\n'
        f"{chrome.header}"
        f"<main>\n{content}\n</main>\n"
        f"{chrome.footer}"
        "</div>\n"
        "</body>\n"
        "</html>\n"
    )


def _title_split(body_lines: list[str]) -> tuple[list[str], list[str]]:
    """The document's own ``#`` heading, and everything after it.

    The contents belong under the law's title, not above it: a reader who
    lands on a document page meets the list of provisions second, once the
    page has said which law this is. A body that opens with anything else
    yields an empty heading and is left in the order it came in, because
    the alternative is guessing where a title was meant to be.
    """
    for index, line in enumerate(body_lines):
        if line.startswith("# "):
            return body_lines[: index + 1], body_lines[index + 1 :]
        if line.strip():
            break
    return [], body_lines


def _toc_html(plan: DocumentPlan) -> str:
    """Links to every provision page — none at all for a duplicate-pid doc.

    The paragraph number is its own element: a law is cited and scanned by
    number, so the numbers form one column the eye can run down, and the
    titles another. ~87k provision pages hang off these lists.
    """
    if plan.duplicate_pids or not plan.provisions:
        return ""
    items = "\n".join(
        f'<li><a href="{provision_url(plan, p.pid)}">'
        f'<span class="pid">§ {html_escape.escape(p.heading_id)}</span>'
        f"{' ' + html_escape.escape(p.title) if p.title else ''}</a></li>"
        for p in plan.provisions
    )
    return f'<nav class="toc" aria-label="Paragrafer"><ul>\n{items}\n</ul></nav>'


def _breadcrumb_html(plan: DocumentPlan) -> str:
    title = html_escape.escape(plan.title or plan.slug)
    return f'<nav aria-label="Del av"><a href="{document_url(plan)}">{title}</a></nav>'


def _neighbours_html(plan: DocumentPlan, current: ProvisionRef) -> str:
    pids = [p.pid for p in plan.provisions]
    index = pids.index(current.pid)
    links: list[str] = []
    if index > 0:
        url = provision_url(plan, pids[index - 1])
        links.append(f'<a href="{url}" rel="prev">Forrige paragraf</a>')
    if index + 1 < len(pids):
        url = provision_url(plan, pids[index + 1])
        links.append(f'<a href="{url}" rel="next">Neste paragraf</a>')
    return f'<nav aria-label="Naboer">{" · ".join(links)}</nav>' if links else ""


def _provenance_html(plan: DocumentPlan, provenance: PageProvenance) -> str:
    rows = _provenance_rows(plan, provenance)
    body = "\n".join(
        f"<dt>{html_escape.escape(k)}</dt><dd>{html_escape.escape(v)}</dd>" for k, v in rows
    )
    return (
        '<section class="provenance" aria-label="Kildeinformasjon">\n'
        f"<p>{html_escape.escape(_NLOD_STATEMENT)} "
        f'<a href="{_NLOD_URL}">Lisenstekst</a>.</p>\n'
        f"<dl>\n{body}\n</dl>\n</section>"
    )


def _provenance_rows(
    plan: DocumentPlan,
    provenance: PageProvenance,
) -> list[tuple[str, str]]:
    rows = [
        ("Kilde", "Lovdata (gjeldende regelverk)"),
        ("Referanse", plan.ref_id),
        ("Hentet", plan.retrieved_at),
        ("Kilderevisjon", provenance.source_revision[:12]),
        ("Innholdshash (XML)", provenance.xml_hash),
    ]
    if provenance.renderer_version is not None:
        rows.append(("Rendererversjon", str(provenance.renderer_version)))
    if plan.date_in_force:
        rows.append(("I kraft", plan.date_in_force))
    if plan.last_change_in_force:
        rows.append(("Siste endring i kraft", plan.last_change_in_force))
    return rows
