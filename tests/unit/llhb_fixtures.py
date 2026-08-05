"""Shared synthetic-corpus builders for LLHB unit tests.

All law text here is invented for tests (evals synthetic-corpus rule):
no Lovdata content, no real statutory wording.
"""

from datetime import UTC, datetime
from pathlib import Path

from lovspor.mcp import CorpusReader
from lovspor.storage.manifest import Manifest, ManifestRecord, write_manifest

GENERATED_AT = datetime(2026, 8, 5, 6, 0, tzinfo=UTC)

TESTLOVEN_BODY = """## Kapittel 1. Innledning

### § 1. Formål

Formålet med loven er å teste verktøy.

### § 5-12. Fradrag

Det gis fradrag for kostnader til testing av verktøy.

### § 33 i. Spesialregel

Denne bestemmelsen har et ekte i-suffiks.
"""

DOBBELTLOVEN_BODY = """## Kapittel 1. Første del

### § 6-2. Første versjon

Tekst nummer en om første tema.

## Kapittel 2. Andre del

### § 6-2. Andre versjon

Tekst nummer to om andre tema.
"""


def record_for(
    slug: str,
    title: str,
    *,
    status: str = "current",
) -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path=f"lover/{slug}.md",
        source_dataset="gjeldende-lover",
        last_seen=GENERATED_AT,
        status=status,  # type: ignore[arg-type]
        slug=slug,
        title=title,
        renderer_version=8,
        embedding_space_id="test-space",
        embedding_hash="a" * 64,
    )


def build_corpus(
    root: Path,
    docs: dict[str, tuple[str, str]],
    removed: dict[str, str] | None = None,
    ministries: dict[str, str] | None = None,
) -> CorpusReader:
    """Write a synthetic corpus of ``slug -> (title, body)`` and open a reader.

    ``removed`` entries become tombstone manifest records (no file on disk).
    ``ministries`` adds a ``ministry`` front-matter line per slug.
    """
    records: dict[str, ManifestRecord] = {}
    for index, (slug, (title, body)) in enumerate(docs.items()):
        records[f"nl-{index}"] = record_for(slug, title)
        path = root / "lover" / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        ministry = (ministries or {}).get(slug)
        ministry_line = f'ministry: "{ministry}"\n' if ministry else ""
        path.write_text(
            f"---\nid: nl-{index}\ntitle: {title}\n{ministry_line}---\n\n{body}",
            encoding="utf-8",
        )
    for index, (slug, title) in enumerate(sorted((removed or {}).items())):
        records[f"nl-removed-{index}"] = record_for(slug, title, status="removed")
    write_manifest(
        Manifest(generated_at=GENERATED_AT, documents=records),
        root / "manifest.json",
    )
    return CorpusReader(root)


def _act_body(sections: list[tuple[str, str, str]]) -> str:
    """Synthetic act body: (section_id, title, extra sentence) triples."""
    parts = ["## Kapittel 1. Alminnelige bestemmelser\n"]
    for section_id, title, extra in sections:
        parts.append(
            f"### § {section_id}. {title}\n\n"
            f"Virksomheten skal sørge for at {title.lower()} følges opp i praksis. "
            f"{extra}\n"
        )
    return "\n".join(parts)


def rich_corpus(root: Path) -> CorpusReader:
    """A corpus rich enough for pool generation: ministries, duplicate ids,
    a tombstone, quotable multi-sentence sections. All text synthetic."""
    extra = (
        "Dokumentasjonen skal være etterprøvbar og oppbevares slik at kontroll "
        "er mulig i ettertid uten urimelig arbeid."
    )
    docs = {
        "alfaloven": (
            "Lov om alfa-testing av verktøy (alfaloven)",
            _act_body(
                [
                    ("1-1", "Formål med alfa-testing", extra),
                    ("1-2", "Krav til dokumentasjon ved testing", extra),
                    ("2-1", "Rett til innsyn i testresultater", extra),
                ],
            ),
        ),
        "betaloven": (
            "Lov om beta-verktøy i virksomheter (betaloven)",
            _act_body(
                [
                    ("1", "Virkeområde for beta-verktøy", extra),
                    ("2", "Plikter for virksomheten ved beta-bruk", extra),
                ],
            ),
        ),
        "gammaloven": (
            "Lov om gamma-kontroll (gammaloven)",
            _act_body(
                [
                    ("5-1", "Melding om gamma-kontroll", extra),
                    ("5-2", "Gjennomføring av stedlig kontroll", extra),
                ],
            ),
        ),
        "deltaloven": (
            "Lov om delta-registrering (deltaloven)",
            _act_body(
                [
                    ("3", "Registreringsplikt for delta-enheter", extra),
                    ("4", "Sletting av delta-registreringer", extra),
                ],
            ),
        ),
        "dobbeltloven": (
            "Lov om doble paragrafer (dobbeltloven)",
            DOBBELTLOVEN_BODY,
        ),
    }
    ministries = {
        "alfaloven": "Testdepartementet",
        "betaloven": "Testdepartementet",
        "gammaloven": "Kontrolldepartementet",
        "deltaloven": "Kontrolldepartementet",
        "dobbeltloven": "Testdepartementet",
    }
    return build_corpus(
        root,
        docs,
        removed={"gamleloven": "Lov om gamle regler (gamleloven)"},
        ministries=ministries,
    )


def standard_corpus(root: Path) -> CorpusReader:
    """The corpus most LLHB tests share: one plain act, one duplicate-id act."""
    return build_corpus(
        root,
        {
            "testloven": ("Lov om testing av verktøy (testloven)", TESTLOVEN_BODY),
            "dobbeltloven": ("Lov om doble paragrafer (dobbeltloven)", DOBBELTLOVEN_BODY),
        },
    )
