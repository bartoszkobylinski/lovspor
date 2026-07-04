"""Document frontmatter model and extraction from Lovdata HTML.

Lovdata's XML is HTML5 with semantic classes. Document metadata lives in
``<header><dl class="data-document-key-info">`` as ``<dt class="X">/<dd
class="X">`` pairs, where the class name identifies the field. See the
``<header>`` extraction map below for the subset we consume.

The full frontmatter ``LegalDocumentFrontMatter`` combines metadata
extracted from the XML with provenance fields supplied by the caller
(``FrontmatterContext``). This split is intentional: the XML does not
know its own file name, content hash, retrieval time, or source dataset.
"""

import re
from datetime import datetime
from io import BytesIO
from typing import Any

from lxml import etree
from pydantic import BaseModel, ConfigDict

from lovspor.errors import ParseError
from lovspor.parsing.xml_normalizer import safe_parser

_DATE_ONLY = re.compile(r"^(\d{4}-\d{2}-\d{2})")


class LegalDocumentFrontMatter(BaseModel):
    """Complete YAML front matter for a rendered legal document."""

    model_config = ConfigDict(frozen=True)

    id: str
    slug: str
    type: str
    ref_id: str | None
    title: str
    short_title: str | None
    language: str
    ministry: list[str]
    date_in_force: str | None
    last_change_in_force: str | None
    last_updated: str | None
    xml_hash: str
    source_provider: str
    source_dataset: str
    source_license: str
    retrieved_at: datetime
    status: str
    eu_basis: list[str] = []
    """EU / EEA legal documents this Norwegian act implements, as
    CELEX identifiers (e.g. ``["32016R0679", "32014L0090"]``).
    Sourced from Lovdata's ``<dd class="eeaReferences">`` block. Empty
    list when the act has no EEA references or only the EØS-avtalen
    (EEA agreement) annex reference without specific directives /
    regulations."""


class FrontmatterContext(BaseModel):
    """Caller-provided provenance fields that the XML cannot supply."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    slug: str
    doc_type: str
    xml_hash: str
    source_dataset: str
    retrieved_at: datetime
    status: str = "current"
    source_provider: str = "Lovdata"
    source_license: str = "NLOD 2.0"


def build_frontmatter(
    xml_bytes: bytes,
    context: FrontmatterContext,
) -> LegalDocumentFrontMatter:
    """Parse Lovdata HTML, extract metadata, and combine with caller context."""
    extracted = extract_xml_metadata(xml_bytes)
    return LegalDocumentFrontMatter(
        id=context.doc_id,
        slug=context.slug,
        type=context.doc_type,
        ref_id=extracted["ref_id"],
        title=extracted["title"],
        short_title=extracted["short_title"],
        language=extracted["language"],
        ministry=extracted["ministry"],
        date_in_force=extracted["date_in_force"],
        last_change_in_force=extracted["last_change_in_force"],
        last_updated=extracted["last_updated"],
        xml_hash=context.xml_hash,
        source_provider=context.source_provider,
        source_dataset=context.source_dataset,
        source_license=context.source_license,
        retrieved_at=context.retrieved_at,
        status=context.status,
        eu_basis=extracted["eu_basis"],
    )


def extract_xml_metadata(xml_bytes: bytes) -> dict[str, Any]:
    try:
        tree = etree.parse(BytesIO(xml_bytes), parser=safe_parser(remove_blank_text=False))
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"malformed XML: {exc}") from exc
    root = tree.getroot()
    dl = root.find(".//header//dl")
    if dl is None:
        raise ParseError("no <header><dl> found in document")
    title = _find_dd(dl, "title") or _find_dd(dl, "titleShort")
    if title is None:
        raise ParseError("no <dd class='title'> in document header")
    return {
        "title": title,
        "short_title": _find_dd(dl, "titleShort"),
        "ref_id": _find_dd(dl, "refid"),
        "language": root.get("lang") or "",
        "ministry": _find_dd_list(dl, "ministry"),
        "date_in_force": _parse_date(_find_dd(dl, "dateInForce")),
        "last_change_in_force": _parse_date(
            _find_dd(dl, "lastChangeInForce"),
        ),
        "last_updated": _parse_date(_find_dd(dl, "lastupdated")),
        "eu_basis": _find_eu_basis(dl),
    }


def _find_dd(dl: etree._Element, cls: str) -> str | None:
    dd = dl.find(f"./dd[@class='{cls}']")
    if dd is None:
        return None
    text = etree.tostring(dd, method="text", encoding="unicode")
    return text.strip() or None


def _find_dd_list(dl: etree._Element, cls: str) -> list[str]:
    dd = dl.find(f"./dd[@class='{cls}']")
    if dd is None:
        return []
    items = dd.findall(".//li")
    if not items:
        text = etree.tostring(dd, method="text", encoding="unicode").strip()
        return [text] if text else []
    return [etree.tostring(li, method="text", encoding="unicode").strip() for li in items]


def _find_eu_basis(dl: etree._Element) -> list[str]:
    """Extract EU CELEX identifiers from the ``<dd class='eeaReferences'>``
    block. Lovdata renders each implemented EU document as
    ``<a href="eu/<celex>">label</a>`` inside the EEA references block.

    CELEX format: ``3<year><type-letter><number>`` (e.g. ``32016R0679``
    for Regulation 679/2016 = GDPR; ``32014L0090`` for Directive
    2014/90/EU). Lovdata stores them lowercase; we normalize to
    uppercase to match the EU canonical form.

    Deduplicated in source order so the first occurrence wins. Empty
    list when there is no ``eeaReferences`` block, or it contains only
    the EØS-avtalen annex link without any specific EU document
    references (per Sprint 8 PR-D Q3=A).
    """
    dd = dl.find("./dd[@class='eeaReferences']")
    if dd is None:
        return []
    seen: list[str] = []
    for anchor in dd.findall(".//a"):
        href = anchor.get("href") or ""
        if not href.startswith("eu/"):
            continue
        celex = href[len("eu/") :].upper()
        if celex and celex not in seen:
            seen.append(celex)
    return seen


def _parse_date(raw: str | None) -> str | None:
    """Extract leading ISO date from a field like '2021-06-21 (struktur)'."""
    if raw is None:
        return None
    match = _DATE_ONLY.match(raw)
    return match.group(1) if match else None
