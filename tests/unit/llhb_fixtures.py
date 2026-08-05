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
) -> CorpusReader:
    """Write a synthetic corpus of ``slug -> (title, body)`` and open a reader.

    ``removed`` entries become tombstone manifest records (no file on disk).
    """
    records: dict[str, ManifestRecord] = {}
    for index, (slug, (title, body)) in enumerate(docs.items()):
        records[f"nl-{index}"] = record_for(slug, title)
        path = root / "lover" / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nid: nl-{index}\ntitle: {title}\n---\n\n{body}", encoding="utf-8")
    for index, (slug, title) in enumerate(sorted((removed or {}).items())):
        records[f"nl-removed-{index}"] = record_for(slug, title, status="removed")
    write_manifest(
        Manifest(generated_at=GENERATED_AT, documents=records),
        root / "manifest.json",
    )
    return CorpusReader(root)


def standard_corpus(root: Path) -> CorpusReader:
    """The corpus most LLHB tests share: one plain act, one duplicate-id act."""
    return build_corpus(
        root,
        {
            "testloven": ("Lov om testing av verktøy (testloven)", TESTLOVEN_BODY),
            "dobbeltloven": ("Lov om doble paragrafer (dobbeltloven)", DOBBELTLOVEN_BODY),
        },
    )
