"""Byte-exact golden pages: the template is the contract (ADR-0013).

One equality against the complete rendered page pins every character
the template emits — the doctype, the head, the style block, the NLOD
statement, the provenance rows, the navigation. Any mutation of a
template string changes the bytes and fails here, which is the point:
the emitted representation is a published contract, not an incidental
formatting choice.
"""

from lovspor.publish.companion import document_companion
from lovspor.publish.inventory import DocumentPlan, ProvisionRef
from lovspor.publish.pages import (
    PageProvenance,
    document_page_html,
    provision_page_html,
)

PLAN = DocumentPlan.model_validate(
    {
        "doc_id": "nl-20200101-001",
        "slug": "testloven",
        "route": "lov",
        "title": "Testloven",
        "markdown_path": "lover/testloven.md",
        "source_dataset": "gjeldende-lover",
        "xml_hash": "a" * 64,
        "renderer_version": 8,
        "language": "nb",
        "ref_id": "lov/2020-01-01-1",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "date_in_force": "2020-06-01",
        "last_change_in_force": None,
        "provisions": (ProvisionRef(pid="1", heading_id="1", title="Formål"),),
        "duplicate_pids": {},
    },
)

PROVENANCE = PageProvenance(
    source_revision="b" * 40,
    xml_hash="a" * 64,
    renderer_version=8,
)

BODY_LINES = ["# Testloven", "", "### § 1. Formål", "", "Tekst."]

STYLE = (
    "body{margin:0 auto;max-width:46rem;padding:1rem;"
    "font-family:Georgia,serif;line-height:1.6}"
    "table{border-collapse:collapse}td,th{border:1px solid #999;padding:.3rem}"
    ".provenance{border-top:1px solid #999;margin-top:3rem;padding-top:1rem;"
    "font-size:.85rem;color:#333}"
    "nav.toc ul{columns:2}"
)

NLOD = (
    "Inneholder data under Norsk lisens for offentlige data (NLOD 2.0), "
    "tilgjengeliggjort av Lovdata. Informasjonen er transformert og "
    "strukturert av Lovspor og gjengis ikke i sin opprinnelige form. "
    "Lovspor er ikke offisiell kunngjøringskilde."
)

PROVENANCE_BLOCK = (
    '<section class="provenance" aria-label="Kildeinformasjon">\n'
    f"<p>{NLOD} "
    '<a href="https://data.norge.no/nlod/no/2.0">Lisenstekst</a>.</p>\n'
    "<dl>\n"
    "<dt>Kilde</dt><dd>Lovdata (gjeldende regelverk)</dd>\n"
    "<dt>Referanse</dt><dd>lov/2020-01-01-1</dd>\n"
    "<dt>Hentet</dt><dd>2026-01-01T00:00:00+00:00</dd>\n"
    f"<dt>Kilderevisjon</dt><dd>{'b' * 12}</dd>\n"
    f"<dt>Innholdshash (XML)</dt><dd>{'a' * 64}</dd>\n"
    "<dt>Rendererversjon</dt><dd>8</dd>\n"
    "<dt>I kraft</dt><dd>2020-06-01</dd>\n"
    "</dl>\n"
    "</section>"
)


def _shell(lang: str, title: str, canonical: str, content: str) -> str:
    return (
        "<!doctype html>\n"
        f'<html lang="{lang}">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        f'<link rel="canonical" href="{canonical}">\n'
        f"<style>{STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{content}\n"
        "</body>\n"
        "</html>\n"
    )


class TestGoldenPages:
    def test_document_page_is_byte_exact(self) -> None:
        expected = _shell(
            "nb",
            "Testloven",
            "https://lovspor.no/lov/testloven/",
            '<nav class="toc" aria-label="Paragrafer"><ul>\n'
            '<li><a href="/lov/testloven/paragraf/1/">§ 1. Formål</a></li>\n'
            "</ul></nav>\n"
            "<h1>Testloven</h1>\n"
            '<h3 id="paragraf-1">§ 1. Formål</h3>\n'
            "<p>Tekst.</p>\n"
            f"{PROVENANCE_BLOCK}",
        )
        assert document_page_html(PLAN, BODY_LINES, PROVENANCE, lambda _t: None) == expected

    def test_provision_page_is_byte_exact(self) -> None:
        expected = _shell(
            "nb",
            "§ 1. Formål — Testloven",
            "https://lovspor.no/lov/testloven/paragraf/1/",
            '<nav aria-label="Del av"><a href="/lov/testloven/">Testloven</a></nav>\n'
            '<h3 id="paragraf-1">§ 1. Formål</h3>\n'
            "<p>Tekst.</p>\n"
            f"{PROVENANCE_BLOCK}",
        )
        assert (
            provision_page_html(
                PLAN,
                PLAN.provisions[0],
                PROVENANCE,
                ["### § 1. Formål", "", "Tekst."],
                lambda _t: None,
            )
            == expected
        )

    def test_document_companion_is_value_exact(self) -> None:
        text = "\n".join(BODY_LINES)
        expected = {
            "schema_version": "1",
            "canonical_id": "nl-20200101-001",
            "canonical_url": "https://lovspor.no/lov/testloven/",
            "type": "lov",
            "ref_id": "lov/2020-01-01-1",
            "slug": "testloven",
            "title": "Testloven",
            "language": "nb",
            "text": text,
            "source": {
                "provider": "Lovdata",
                "dataset": "gjeldende-lover",
                "license": "NLOD 2.0",
                "license_url": "https://data.norge.no/nlod/no/2.0",
                "statement": NLOD.rsplit(" Lovspor er ikke", 1)[0],
            },
            "provenance": {
                "source_revision": "b" * 40,
                "representation_hash": "c" * 64,
                "xml_hash": "a" * 64,
                "renderer_version": 8,
                "retrieved_at": "2026-01-01T00:00:00+00:00",
            },
            "temporal": {
                "status": "current",
                "date_in_force": "2020-06-01",
                "last_change_in_force": None,
                "note": ("Current corpus state; not an applicability-at-date determination."),
            },
            "links": {"parent": None},
        }
        assert document_companion(PLAN, text, PROVENANCE, "c" * 64) == expected
