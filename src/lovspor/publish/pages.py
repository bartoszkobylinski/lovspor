"""Document and provision pages (ADR-0013 Decisions 4-5).

A page is a pure function of its plan, its lines and the build's
provenance. No wall-clock value and no global corpus commit may appear
here: per-document provenance carries the document's own source revision,
and the byte-identical build invariant depends on nothing else leaking in.

The NLOD transformation statement is a licence-contract requirement
(NLOD 2.0 requires marking changed information as changed), not styling;
its wording is fixed here and versioned with the site schema.
"""

import html as html_escape
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

from lovspor.headings import parse_section_heading
from lovspor.publish.html import LinkResolver, render_body_html
from lovspor.publish.inventory import DocumentPlan, ProvisionRef, normalise_pid

SITE_ORIGIN = "https://lovspor.no"

_NLOD_URL = "https://data.norge.no/nlod/no/2.0"

_NLOD_STATEMENT = (
    "Inneholder data under Norsk lisens for offentlige data (NLOD 2.0), "
    "tilgjengeliggjort av Lovdata. Informasjonen er transformert og "
    "strukturert av Lovspor og gjengis ikke i sin opprinnelige form. "
    "Lovspor er ikke offisiell kunngjøringskilde."
)

_STYLE = (
    "body{margin:0 auto;max-width:46rem;padding:1rem;"
    "font-family:Georgia,serif;line-height:1.6}"
    "table{border-collapse:collapse}td,th{border:1px solid #999;padding:.3rem}"
    ".provenance{border-top:1px solid #999;margin-top:3rem;padding-top:1rem;"
    "font-size:.85rem;color:#333}"
    "nav.toc ul{columns:2}"
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
    """The canonical document page: full text, TOC, provenance."""
    title = plan.title or plan.slug
    parts = [
        _toc_html(plan),
        render_body_html(body_lines, resolve, frozenset(plan.duplicate_pids)),
        _provenance_html(plan, provenance),
    ]
    return _layout(plan.language, title, document_url(plan), "\n".join(parts))


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
    return _layout(
        plan.language,
        f"{title} — {doc_title}",
        provision_url(plan, provision.pid),
        "\n".join(parts),
    )


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


def _layout(lang: str, title: str, path: str, content: str) -> str:
    """The shared shell: escaped head values, canonical link, no scripts."""
    safe_title = html_escape.escape(title, quote=True)
    canonical = html_escape.escape(f"{SITE_ORIGIN}{path}", quote=True)
    return (
        "<!doctype html>\n"
        f'<html lang="{html_escape.escape(lang, quote=True)}">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{safe_title}</title>\n"
        f'<link rel="canonical" href="{canonical}">\n'
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{content}\n"
        "</body>\n"
        "</html>\n"
    )


def _toc_html(plan: DocumentPlan) -> str:
    """Links to every provision page — none at all for a duplicate-pid doc."""
    if plan.duplicate_pids or not plan.provisions:
        return ""
    items = "\n".join(
        f'<li><a href="{provision_url(plan, p.pid)}">'
        f"§ {html_escape.escape(p.heading_id)}"
        f"{'. ' + html_escape.escape(p.title) if p.title else ''}</a></li>"
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
