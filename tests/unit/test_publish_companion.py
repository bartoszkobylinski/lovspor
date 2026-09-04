"""Companion JSON beside every page (ADR-0013 Decisions 3-4).

The envelope carries three separated identities: the document's
source_revision (its own last corpus commit), the page's
representation_hash (SHA-256 of the emitted HTML bytes, supplied by the
emitter), and no global corpus commit anywhere. Serialisation must be
deterministic bytes.
"""

import json

from lovspor.publish.companion import companion_json_bytes, document_companion
from lovspor.publish.inventory import DocumentPlan, ProvisionRef
from lovspor.publish.pages import PageProvenance

PROVENANCE = PageProvenance(
    source_revision="ab388cbdeadbeef",
    xml_hash="c" * 64,
    renderer_version=8,
)


def _plan() -> DocumentPlan:
    return DocumentPlan.model_validate(
        {
            "doc_id": "nl-20241220-096",
            "slug": "abortloven",
            "route": "lov",
            "title": "Lov om abort (abortloven)",
            "markdown_path": "lover/abortloven.md",
            "source_dataset": "gjeldende-lover",
            "xml_hash": "c" * 64,
            "renderer_version": 8,
            "language": "nb",
            "ref_id": "lov/2024-12-20-96",
            "retrieved_at": "2026-07-30T18:17:57+00:00",
            "date_in_force": "2025-06-01",
            "last_change_in_force": None,
            "provisions": (ProvisionRef(pid="1", heading_id="1", title="Formål"),),
            "duplicate_pids": {},
        },
    )


class TestDocumentCompanion:
    def test_envelope_identity_and_source(self) -> None:
        doc = document_companion(_plan(), "Tekst.", PROVENANCE, "d" * 64)
        assert doc["schema_version"] == "1"
        assert doc["canonical_id"] == "nl-20241220-096"
        assert doc["canonical_url"] == "https://lovspor.no/lov/abortloven/"
        assert doc["type"] == "lov"
        assert doc["ref_id"] == "lov/2024-12-20-96"
        assert doc["text"] == "Tekst."
        assert doc["source"]["provider"] == "Lovdata"
        assert doc["source"]["dataset"] == "gjeldende-lover"
        assert doc["source"]["license"] == "NLOD 2.0"
        assert "transformert og strukturert av Lovspor" in doc["source"]["statement"]

    def test_provenance_separates_the_identities(self) -> None:
        doc = document_companion(_plan(), "Tekst.", PROVENANCE, "d" * 64)
        prov = doc["provenance"]
        assert prov["source_revision"] == "ab388cbdeadbeef"
        assert prov["representation_hash"] == "d" * 64
        assert prov["xml_hash"] == "c" * 64
        assert prov["renderer_version"] == 8
        assert prov["retrieved_at"] == "2026-07-30T18:17:57+00:00"
        assert "corpus_commit" not in prov

    def test_temporal_block_is_honest(self) -> None:
        doc = document_companion(_plan(), "Tekst.", PROVENANCE, "d" * 64)
        temporal = doc["temporal"]
        assert temporal["status"] == "current"
        assert temporal["date_in_force"] == "2025-06-01"
        assert "applicability" in temporal["note"]

    def test_no_fabricated_source_url(self) -> None:
        doc = document_companion(_plan(), "Tekst.", PROVENANCE, "d" * 64)
        assert "url" not in doc["source"]


class TestSerialisation:
    def test_bytes_are_deterministic_and_utf8(self) -> None:
        doc = document_companion(_plan(), "Tekst med æøå.", PROVENANCE, "d" * 64)
        first = companion_json_bytes(doc)
        second = companion_json_bytes(doc)
        assert first == second
        assert "æøå" in first.decode("utf-8")
        assert json.loads(first)["canonical_id"] == "nl-20241220-096"

    def test_keys_are_sorted(self) -> None:
        doc = document_companion(_plan(), "Tekst.", PROVENANCE, "d" * 64)
        parsed = json.loads(companion_json_bytes(doc))
        assert list(parsed) == sorted(parsed)
