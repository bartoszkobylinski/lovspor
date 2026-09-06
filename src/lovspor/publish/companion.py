"""Companion JSON envelopes (ADR-0013 Decisions 3-4, 8).

One explicit ``index.json`` beside every page — static hosting cannot
content-negotiate. The envelope keeps the three identities separate:
``source_revision`` (the document's own last corpus commit),
``representation_hash`` (SHA-256 of the emitted HTML page bytes, computed
by the emitter after rendering), and no global corpus commit anywhere in
per-document artifacts. ``text`` is the exact corpus body — no lossy
transformation. Serialisation is deterministic: sorted keys, UTF-8,
no wall-clock values.
"""

import json

from lovspor.publish.inventory import DocumentPlan, ProvisionRef
from lovspor.publish.pages import (
    SITE_ORIGIN,
    PageProvenance,
    document_url,
    provision_url,
)

SCHEMA_VERSION = "1"

_SOURCE_STATEMENT = (
    "Inneholder data under Norsk lisens for offentlige data (NLOD 2.0), "
    "tilgjengeliggjort av Lovdata. Informasjonen er transformert og "
    "strukturert av Lovspor og gjengis ikke i sin opprinnelige form."
)

_TEMPORAL_NOTE = "Current corpus state; not an applicability-at-date determination."


def document_companion(
    plan: DocumentPlan,
    text: str,
    provenance: PageProvenance,
    representation_hash: str,
) -> dict[str, object]:
    """The document page's machine-readable twin."""
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_id": plan.doc_id,
        "canonical_url": f"{SITE_ORIGIN}{document_url(plan)}",
        "type": plan.route,
        "ref_id": plan.ref_id,
        "slug": plan.slug,
        "title": plan.title,
        "language": plan.language,
        "text": text,
        "source": _source_block(plan),
        "provenance": _provenance_block(plan, provenance, representation_hash),
        "temporal": _temporal_block(plan),
        "links": {"parent": None},
    }


def provision_companion(
    context: tuple[DocumentPlan, ProvisionRef],
    text: str,
    provenance: PageProvenance,
    representation_hash: str,
) -> dict[str, object]:
    """One provision page's machine-readable twin."""
    plan, provision = context
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_id": f"{plan.doc_id}#paragraf-{provision.pid}",
        "canonical_url": f"{SITE_ORIGIN}{provision_url(plan, provision.pid)}",
        "type": "paragraf",
        "ref_id": plan.ref_id,
        "slug": plan.slug,
        "title": provision.title,
        "heading_id": provision.heading_id,
        "language": plan.language,
        "text": text,
        "source": _source_block(plan),
        "provenance": _provenance_block(plan, provenance, representation_hash),
        "temporal": _temporal_block(plan),
        "links": {"parent": f"{SITE_ORIGIN}{document_url(plan)}"},
    }


def companion_json_bytes(envelope: dict[str, object]) -> bytes:
    """Deterministic UTF-8 bytes: sorted keys, stable separators."""
    return (json.dumps(envelope, sort_keys=True, ensure_ascii=False, indent=1) + "\n").encode(
        "utf-8"
    )


def _source_block(plan: DocumentPlan) -> dict[str, object]:
    """Attribution per the NLOD transformation-disclosure requirement.

    No fabricated deep link: until a provably stable Lovdata URL pattern
    per document class exists, the block names provider and dataset only
    (ADR-0013 Decision 5).
    """
    return {
        "provider": "Lovdata",
        "dataset": plan.source_dataset,
        "license": "NLOD 2.0",
        "license_url": "https://data.norge.no/nlod/no/2.0",
        "statement": _SOURCE_STATEMENT,
    }


def _provenance_block(
    plan: DocumentPlan,
    provenance: PageProvenance,
    representation_hash: str,
) -> dict[str, object]:
    return {
        "source_revision": provenance.source_revision,
        "representation_hash": representation_hash,
        "xml_hash": provenance.xml_hash,
        "renderer_version": provenance.renderer_version,
        "retrieved_at": plan.retrieved_at,
    }


def _temporal_block(plan: DocumentPlan) -> dict[str, object]:
    return {
        "status": "current",
        "date_in_force": plan.date_in_force,
        "last_change_in_force": plan.last_change_in_force,
        "note": _TEMPORAL_NOTE,
    }
