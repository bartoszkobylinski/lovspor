"""A synthetic corpus with a central part and an optional local dataset (ADR-0016).

All text is invented for tests: no Lovdata content and no municipal wording.
The local dataset mirrors the shapes the first promoted regulation has in
``lovverk`` (S4): ``lokale-forskrifter/manifest.json`` keyed by id, and per
document ``<authority_id>/<slug>.md`` with the S2 front matter,
``observations/<slug>.json`` and ``history/<slug>.json``.
"""

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from lovspor.storage.manifest import Manifest, ManifestRecord, write_manifest

GENERATED_AT = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)

PROVELOVEN_BODY = """## Kapittel 1. Innledning

### § 1. Formål

Loven skal sikre at prøver blir gjennomført på en ordnet måte.

### § 2. Virkeområde

Loven gjelder for alle prøver, jf. § 1 og prøveforskriften § 3.
"""

ORDENSLOVEN_BODY = """## § 1. Ordning

Hver sak skal ha en saksmappe.

## § 2. Arkiv

Saksmappen skal arkiveres i ti år.
"""

PROVEFORSKRIFTEN_BODY = """## § 1. Formål

Forskriften utfyller prøveloven.

## § 3. Prøvetid

Prøvetiden er seks måneder.
"""

CENTRAL = {
    "nl-19990101-001": ("lov", "gjeldende-lover", "proveloven", "Lov om prøver (prøveloven)"),
    "nl-20000202-002": ("lov", "gjeldende-lover", "ordensloven", "Lov om orden (ordensloven)"),
    "sf-20100303-0003": (
        "forskrift",
        "gjeldende-sentrale-forskrifter",
        "proveforskriften",
        "Forskrift om prøver (prøveforskriften)",
    ),
}
BODIES = {
    "proveloven": PROVELOVEN_BODY,
    "ordensloven": ORDENSLOVEN_BODY,
    "proveforskriften": PROVEFORSKRIFTEN_BODY,
}

LOCAL_SLUG = "forskrift-om-lekeplasser-i-provestad-kommune"
LOCAL_ID = "lk-9999-0a1b2c3d4e5f"
LOCAL_TITLE = "FORSKRIFT OM LEKEPLASSER I PRØVESTAD KOMMUNE"
LOCAL_URL = "https://www.provestad.kommune.example/forskrift-om-lekeplasser/"
OBSERVED_FIRST = "2026-08-24T11:05:06.922416Z"
OBSERVED_LAST = "2026-09-30T08:00:00Z"
LOCAL_BODY = """## § 1 Formål

Forskriften skal gi trygge lekeplasser i Prøvestad kommune.

## § 2 Vedlikehold

Lekeplassene skal kontrolleres hver vår, jf. § 1.
"""


def _central_record(doc_id: str) -> ManifestRecord:
    doc_type, dataset, slug, title = CENTRAL[doc_id]
    subdir = "lover" if dataset == "gjeldende-lover" else "forskrifter"
    return ManifestRecord(
        doc_type=doc_type,
        xml_hash=doc_id.replace("-", "")[:8] * 8,
        markdown_path=f"{subdir}/{slug}.md",
        source_dataset=dataset,
        last_seen=GENERATED_AT,
        status="current",
        slug=slug,
        title=title,
        total_changes=2,
        last_changed=f"2026-0{len(slug) % 7 + 1}-15",
        eu_basis=["32016R0679"] if slug == "proveloven" else None,
    )


def build_central(root: Path) -> None:
    """The central corpus: two lover, one forskrift, their history files."""
    root.mkdir(parents=True, exist_ok=True)
    records = {doc_id: _central_record(doc_id) for doc_id in CENTRAL}
    write_manifest(Manifest(generated_at=GENERATED_AT, documents=records), root / "manifest.json")
    for doc_id, record in records.items():
        path = root / record.markdown_path
        path.parent.mkdir(parents=True, exist_ok=True)
        front = f"---\nid: {json.dumps(doc_id)}\ntitle: {json.dumps(record.title)}\n---\n\n"
        path.write_text(f"{front}# {record.title}\n\n{BODIES[record.slug or '']}", "utf-8")
        _write_json(path.parent / "history" / f"{record.slug}.json", _history(doc_id, record))


def _history(doc_id: str, record: ManifestRecord) -> dict[str, Any]:
    event = {"date": "2026-04-27", "commit": "abc1234", "type": "added"}
    return {
        "schema_version": 1,
        "slug": record.slug,
        "doc_id": doc_id,
        "events": [{**event, "subject": f"add({record.doc_type}): {record.slug}"}],
    }


def local_record(
    slug: str = LOCAL_SLUG, authority_id: str = "9999", status: str = "current"
) -> dict[str, Any]:
    return {
        "authority_id": authority_id,
        "authority_type": "kommune",
        "content_hash": "c" * 64,
        "doc_type": "lokal-forskrift",
        "extractor_version": 2,
        "last_seen": OBSERVED_FIRST,
        "markdown_path": f"lokale-forskrifter/{authority_id}/{slug}.md",
        "removed_reason": None if status == "current" else "withdrawn_misclassified",
        "renderer_version": 1,
        "slug": slug,
        "source_dataset": "lokale-forskrifter",
        "status": status,
        "title": LOCAL_TITLE,
        "version": 1,
    }


def front_matter(doc_id: str = LOCAL_ID, authority_id: str = "9999", slug: str = LOCAL_SLUG) -> str:
    authority = (
        f'{{id: "{authority_id}", type: "kommune", name: "Prøvestad", klass_version: "2026"}}'
    )
    lines = [
        f'id: "{doc_id}"',
        f'slug: "{slug}"',
        'type: "lokal-forskrift"',
        "ref_id: null",
        f'title: "{LOCAL_TITLE}"',
        f"authority: {authority}",
        'hjemmel: ["lov om lekeplasser § 9"]',
        'vedtatt: "2024-06-17"',
        'vedtatt_av: "kommunestyret i Prøvestad kommune"',
        'ikraft: "2024-08-01"',
        "ikraft_text: null",
        "version: 1",
        f'content_hash: "{"c" * 64}"',
        f'observed_at_first: "{OBSERVED_FIRST}"',
        f'source_url: "{LOCAL_URL}"',
        f'source_sha256: "{"d" * 64}"',
        'source_provider: "Prøvestad kommune (observed)"',
        'source_license: "åndsverkloven § 14"',
        'basis: "observed"',
        "asserted: false",
        'language: "no"',
    ]
    return "---\n" + "\n".join(lines) + "\n---\n"


def observations(doc_id: str = LOCAL_ID, authority_id: str = "9999") -> dict[str, Any]:
    version = {
        "content_hash": "c" * 64,
        "corroborating_urls": [],
        "observation_count": 3,
        "observed_at_first": OBSERVED_FIRST,
        "observed_at_last": OBSERVED_LAST,
        "primary_url": LOCAL_URL,
        "promotion": {"decision": "approve", "reviewed_in_sample": True},
        "source_sha256s": ["d" * 64],
        "version": 1,
    }
    return {
        "authority_id": authority_id,
        "doc_id": doc_id,
        "schema_version": 1,
        "slug": LOCAL_SLUG,
        "source_status": {"http_status": 200, "observed_at": OBSERVED_LAST, "outcome": "retrieved"},
        "versions": [version],
    }


def write_local_document(root: Path, doc_id: str = LOCAL_ID, authority_id: str = "9999") -> None:
    base = root / "lokale-forskrifter" / authority_id
    base.mkdir(parents=True, exist_ok=True)
    markdown = front_matter(doc_id, authority_id) + f"\n# {LOCAL_TITLE}\n\n{LOCAL_BODY}"
    (base / f"{LOCAL_SLUG}.md").write_text(markdown, encoding="utf-8")
    _write_json(base / "observations" / f"{LOCAL_SLUG}.json", observations(doc_id, authority_id))
    history = {"doc_id": doc_id, "events": [], "schema_version": 1, "slug": LOCAL_SLUG}
    _write_json(base / "history" / f"{LOCAL_SLUG}.json", history)


def write_local_manifest(root: Path, documents: dict[str, dict[str, Any]]) -> None:
    payload = {"documents": documents, "generated_at": OBSERVED_FIRST, "version": 1}
    _write_json(root / "lokale-forskrifter" / "manifest.json", payload)


def add_local_dataset(root: Path) -> None:
    """The S4 shape: one current local regulation, beside the central corpus."""
    write_local_document(root)
    write_local_manifest(root, {LOCAL_ID: local_record()})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def wire(server: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    """What a client receives for one call: content blocks (+ structured), or the error."""
    try:
        result = asyncio.run(server.call_tool(name, arguments))
    except ToolError as error:
        return {"error": str(error)}
    if isinstance(result, tuple):
        blocks, structured = result
        return {"content": _dump(blocks), "structured": structured}
    return {"content": _dump(result)}


def _dump(blocks: Sequence[Any]) -> list[Any]:
    return [block.model_dump(mode="json", exclude_none=True) for block in blocks]
