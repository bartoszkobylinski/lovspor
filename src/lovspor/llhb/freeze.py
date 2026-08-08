"""Freeze artifacts for the frozen 250 (FREEZE.md §3-§4).

The lock file is the pre-registration receipt: corpus pin, a per-cited-
document capture of ``xml_hash`` / ``renderer_version`` /
``embedding_space_id`` / ``embedding_hash`` from the pinned manifest,
the SHA-256 of the canonical dataset bytes, the freeze timestamp, and a
reference to the governing selection rule. Everything fails closed: a
cited document the pinned manifest cannot pin aborts the freeze.
"""

import hashlib
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.corpus_pin import CorpusPin
from lovspor.llhb.schema import canonical_jsonl
from lovspor.storage.manifest import Manifest, ManifestRecord


def cited_slugs(cases: list[dict[str, Any]]) -> set[str]:
    """Every act slug the dataset references: expected, claimed, quoted."""
    slugs: set[str] = set()
    for case in cases:
        for field in ("expected_act_slug", "claimed_act_slug"):
            if case.get(field):
                slugs.add(str(case[field]))
        quote_ref = case.get("quote_ref")
        if quote_ref and quote_ref.get("slug"):
            slugs.add(str(quote_ref["slug"]))
    return slugs


def _record_for_slug(manifest: Manifest, slug: str) -> ManifestRecord:
    for record in manifest.documents.values():
        if record.slug == slug and record.status == "current":
            return record
    raise LovsporError(f"cited document {slug!r} has no current record in the pinned manifest")


def _document_pin(record: ManifestRecord, slug: str) -> dict[str, Any]:
    pins = {
        "xml_hash": record.xml_hash,
        "renderer_version": record.renderer_version,
        "embedding_space_id": record.embedding_space_id,
        "embedding_hash": record.embedding_hash,
    }
    missing = [key for key, value in pins.items() if value is None]
    if missing:
        raise LovsporError(f"cited document {slug!r} lacks pin fields: {missing}")
    return pins


def build_lock(
    cases: list[dict[str, Any]],
    manifest: Manifest,
    pin: CorpusPin,
    *,
    lovspor_commit: str,
    selection_rule: str,
    timestamp: str,
) -> dict[str, Any]:
    """The ``llhb-v1.lock.json`` payload for an already-selected dataset."""
    documents = {
        slug: _document_pin(_record_for_slug(manifest, slug), slug)
        for slug in sorted(cited_slugs(cases))
    }
    return {
        "llhb_version": "1.0",
        "corpus_pin": {
            "lovverk_commit": pin.lovverk_commit,
            "manifest_generated_at": pin.manifest_generated_at.isoformat(),
        },
        "lovspor_commit": lovspor_commit,
        "case_count": len(cases),
        "documents": documents,
        "dataset_sha256": hashlib.sha256(canonical_jsonl(cases)).hexdigest(),
        "selection_rule": selection_rule,
        "freeze_timestamp": timestamp,
    }
