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
language: "nb"
ref_id: "lov/2024-12-20-96"
retrieved_at: "2026-07-30T18:17:57.344275+00:00"
date_in_force: "2025-06-01"
last_updated: null
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
    def test_empty_manifest_produces_empty_inventory(self) -> None:
        inventory = build_inventory(_manifest({}), lambda _path: None)

        assert inventory.documents == ()

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
        assert [p.title for p in plan.provisions] == [
            "Formål",
            "Virkeområde",
            "Unntak",
        ]
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

    def test_empty_or_whitespace_slug_fails_closed(self) -> None:
        for bad in ("", "   "):
            manifest = _manifest({"doc-1": _record(slug=bad)})
            with pytest.raises(PublishError, match="no slug"):
                build_inventory(manifest, lambda _path: BODY)

    @pytest.mark.parametrize(
        "bad",
        [
            "a/b",
            "Abortloven",
            " abortloven",
            "abortloven ",
            "abort_loven",
            "abortloven?utgave=1",
            "-abortloven",
            "abortloven-",
            "abort--loven",
        ],
    )
    def test_noncanonical_slug_fails_closed(self, bad: str) -> None:
        manifest = _manifest({"doc-1": _record(slug=bad)})
        with pytest.raises(PublishError, match="canonical URL segment"):
            build_inventory(manifest, lambda _path: BODY)

    @pytest.mark.parametrize(
        "good",
        ["abortloven", "forskrift-om-sassen-bünsow-land-nasjonalpark", "nl-2"],
    )
    def test_attested_slug_shapes_pass(self, good: str) -> None:
        manifest = _manifest({"doc-1": _record(slug=good)})
        inventory = build_inventory(manifest, lambda _path: BODY)
        assert inventory.documents[0].slug == good

    def test_front_matter_fields_land_on_the_plan(self) -> None:
        manifest = _manifest({"doc-1": _record()})
        inventory = build_inventory(manifest, {"lover/abortloven.md": BODY}.get)

        plan = inventory.documents[0]
        assert plan.language == "nb"
        assert plan.ref_id == "lov/2024-12-20-96"
        assert plan.retrieved_at == "2026-07-30T18:17:57.344275+00:00"
        assert plan.date_in_force == "2025-06-01"
        assert plan.last_change_in_force is None

    def test_missing_language_fails_closed(self) -> None:
        body = BODY.replace('language: "nb"\n', "")
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="language"):
            build_inventory(manifest, lambda _path: body)

    def test_invalid_language_fails_closed(self) -> None:
        body = BODY.replace('language: "nb"', 'language: "xx"')
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="language"):
            build_inventory(manifest, lambda _path: body)

    def test_missing_ref_id_fails_closed(self) -> None:
        body = BODY.replace('ref_id: "lov/2024-12-20-96"\n', "")
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="ref_id"):
            build_inventory(manifest, lambda _path: body)

    def test_missing_retrieved_at_fails_closed(self) -> None:
        body = BODY.replace('retrieved_at: "2026-07-30T18:17:57.344275+00:00"\n', "")
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="retrieved_at"):
            build_inventory(manifest, lambda _path: body)

    @pytest.mark.parametrize("field", ["language", "ref_id", "retrieved_at"])
    def test_null_required_provenance_fails_closed(self, field: str) -> None:
        body = BODY.replace(f'{field}: "', f"{field}: null # ", 1)
        manifest = _manifest({"doc-1": _record()})

        with pytest.raises(PublishError, match=field):
            build_inventory(manifest, lambda _path: body)

    def test_unclosed_front_matter_fails_closed(self) -> None:
        body = BODY.replace("---\n\n# Lov om abort", "# Lov om abort", 1)
        manifest = _manifest({"doc-1": _record()})

        with pytest.raises(PublishError, match="language"):
            build_inventory(manifest, lambda _path: body)

    def test_conflicting_repeated_provenance_key_fails_closed(self) -> None:
        body = BODY.replace(
            'ref_id: "lov/2024-12-20-96"',
            'ref_id: "lov/2024-12-20-96"\nref_id: "lov/1999-01-01-1"',
        )
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="ref_id"):
            build_inventory(manifest, lambda _path: body)

    @pytest.mark.parametrize(
        "pair",
        [
            'ref_id: null\nref_id: "lov/2024-12-20-96"',
            'ref_id: "lov/2024-12-20-96"\nref_id: null',
        ],
    )
    def test_null_beside_a_value_for_one_key_fails_closed(self, pair: str) -> None:
        body = BODY.replace('ref_id: "lov/2024-12-20-96"', pair)
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="ref_id"):
            build_inventory(manifest, lambda _path: body)

    def test_identical_repeated_provenance_key_is_tolerated(self) -> None:
        body = BODY.replace(
            'ref_id: "lov/2024-12-20-96"',
            'ref_id: "lov/2024-12-20-96"\nref_id: "lov/2024-12-20-96"',
        )
        manifest = _manifest({"doc-1": _record()})
        plan = build_inventory(manifest, lambda _path: body).documents[0]
        assert plan.ref_id == "lov/2024-12-20-96"

    def test_unreadable_body_fails_closed(self) -> None:
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="cannot be read"):
            build_inventory(manifest, lambda _path: None)

    def test_duplicate_pids_measured_not_fatal(self) -> None:
        body = (
            '---\nlanguage: "nb"\nref_id: "lov/2020-01-01-1"\n'
            'retrieved_at: "2026-01-01T00:00:00+00:00"\n---\n\n# T\n\n'
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
            '---\nlanguage: "nb"\nref_id: "lov/2020-01-01-1"\n'
            'retrieved_at: "2026-01-01T00:00:00+00:00"\n---\n\n# T\n\n'
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
