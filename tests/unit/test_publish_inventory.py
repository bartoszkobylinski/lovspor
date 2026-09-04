"""The publish inventory: which pages ADR-0013 allows this snapshot to emit.

The inventory is the fail-closed gate of the publication layer. It reads
the manifest's current records only, refuses ambiguity (duplicate slug,
unknown route, unreadable body) instead of guessing, and measures
intra-document pid duplicates so the generator can withhold exactly the
ambiguous provision surface (ADR-0013 Decision 1).
"""

from datetime import UTC, datetime

import pytest

from lovspor.publish.inventory import (
    PublishError,
    build_inventory,
    normalise_pid,
)
from lovspor.storage.manifest import Manifest, ManifestRecord


def _record(**overrides: object) -> ManifestRecord:
    base: dict[str, object] = {
        "doc_type": "lov",
        "slug": "abortloven",
        "title": "Lov om abort (abortloven)",
        "markdown_path": "lover/abortloven.md",
        "source_dataset": "gjeldende-lover",
        "status": "current",
        "xml_hash": "a" * 64,
        "renderer_version": 8,
        "last_seen": datetime(2026, 9, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return ManifestRecord.model_validate(base)


def _manifest(documents: dict[str, ManifestRecord]) -> Manifest:
    return Manifest(generated_at=datetime(2026, 9, 4, tzinfo=UTC), documents=documents)


BODY = """---
title: "Lov om abort (abortloven)"
weird: "# not a heading"
---

# Lov om abort (abortloven)

## Kapittel 1. Alminnelige bestemmelser

### § 1. Formål

Tekst.

### § 2. Virkeområde

Tekst.

### § 35 a. Unntak

Tekst.
"""


class TestBuildInventory:
    def test_reads_current_records_only(self) -> None:
        manifest = _manifest(
            {
                "doc-1": _record(),
                "doc-2": _record(
                    slug="gammel-lov",
                    markdown_path="lover/gammel-lov.md",
                    status="removed",
                ),
            },
        )
        inventory = build_inventory(manifest, {"lover/abortloven.md": BODY}.get)

        assert [plan.doc_id for plan in inventory.documents] == ["doc-1"]

    def test_provisions_parsed_in_order_with_normalised_pids(self) -> None:
        manifest = _manifest({"doc-1": _record()})
        inventory = build_inventory(manifest, {"lover/abortloven.md": BODY}.get)

        plan = inventory.documents[0]
        assert [p.pid for p in plan.provisions] == ["1", "2", "35a"]
        assert [p.heading_id for p in plan.provisions] == ["1", "2", "35 a"]
        assert plan.duplicate_pids == {}

    def test_route_follows_doc_type(self) -> None:
        manifest = _manifest(
            {
                "doc-1": _record(),
                "doc-2": _record(
                    doc_type="forskrift",
                    slug="pantelovforskriften",
                    markdown_path="forskrifter/pantelovforskriften.md",
                ),
            },
        )
        reader = {
            "lover/abortloven.md": BODY,
            "forskrifter/pantelovforskriften.md": BODY,
        }.get
        inventory = build_inventory(manifest, reader)

        routes = {plan.doc_id: plan.route for plan in inventory.documents}
        assert routes == {"doc-1": "lov", "doc-2": "forskrift"}

    def test_duplicate_slug_in_one_route_fails_closed(self) -> None:
        manifest = _manifest(
            {
                "doc-1": _record(),
                "doc-2": _record(markdown_path="lover/abortloven-2.md"),
            },
        )
        with pytest.raises(PublishError, match="duplicate slug 'abortloven'"):
            build_inventory(manifest, lambda _path: BODY)

    def test_same_slug_across_routes_is_no_collision(self) -> None:
        # The real corpus carries this shape: the 1925 Svalbard
        # bergverksordning exists as both a lov and a forskrift under one
        # slug. Their URLs differ by route prefix, so neither shadows the
        # other and publication must not refuse the pair.
        manifest = _manifest(
            {
                "doc-1": _record(slug="bergverksordning-for-svalbard"),
                "doc-2": _record(
                    doc_type="forskrift",
                    slug="bergverksordning-for-svalbard",
                    markdown_path="forskrifter/bergverksordning-for-svalbard.md",
                ),
            },
        )
        inventory = build_inventory(manifest, lambda _path: BODY)

        assert len(inventory.documents) == 2

    def test_unknown_doc_type_fails_closed(self) -> None:
        manifest = _manifest({"doc-1": _record(doc_type="dom")})
        with pytest.raises(PublishError, match="no publication route"):
            build_inventory(manifest, lambda _path: BODY)

    def test_missing_slug_fails_closed(self) -> None:
        manifest = _manifest({"doc-1": _record(slug=None)})
        with pytest.raises(PublishError, match="no slug"):
            build_inventory(manifest, lambda _path: BODY)

    def test_unreadable_body_fails_closed(self) -> None:
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="cannot be read"):
            build_inventory(manifest, lambda _path: None)

    def test_duplicate_pids_measured_not_fatal(self) -> None:
        body = (
            "---\nx: y\n---\n\n# T\n\n"
            "## Kapittel 1\n\n### § 1. En\n\nTekst.\n\n"
            "## Kapittel 2\n\n### § 1. To\n\nTekst.\n\n### § 2. Tre\n\nTekst.\n"
        )
        manifest = _manifest({"doc-1": _record()})
        inventory = build_inventory(manifest, lambda _path: body)

        plan = inventory.documents[0]
        assert plan.duplicate_pids == {"1": 2}
        assert [p.pid for p in plan.provisions] == ["1", "1", "2"]

    def test_normalisation_collision_counts_as_duplicate(self) -> None:
        body = (
            "---\nx: y\n---\n\n# T\n\n"
            "## Kapittel 1\n\n### § 35 a. En\n\nTekst.\n\n### § 35a. To\n\nTekst.\n"
        )
        manifest = _manifest({"doc-1": _record()})
        inventory = build_inventory(manifest, lambda _path: body)

        assert inventory.documents[0].duplicate_pids == {"35a": 2}


class TestNormalisePid:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1", "1"),
            ("5-12", "5-12"),
            ("35 a", "35a"),
            ("8-7 a", "8-7a"),
            ("2 A-1", "2a-1"),
            ("3-4 A", "3-4a"),
            ("10-4-1", "10-4-1"),
            ("x-1", "x-1"),
        ],
    )
    def test_lowercases_and_strips_spaces(self, raw: str, expected: str) -> None:
        assert normalise_pid(raw) == expected
