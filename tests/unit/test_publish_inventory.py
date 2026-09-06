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
    _front_matter_lines,
    body_lines,
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

    def test_inventory_preserves_manifest_order(self) -> None:
        manifest = _manifest(
            {
                "second": _record(slug="andre", markdown_path="lover/andre.md"),
                "first": _record(slug="forste", markdown_path="lover/forste.md"),
            },
        )
        second_body = BODY.replace(
            'ref_id: "lov/2024-12-20-96"',
            'ref_id: "lov/2021-01-01-2"',
        )
        reader = {
            "lover/andre.md": second_body,
            "lover/forste.md": BODY,
        }.get

        inventory = build_inventory(manifest, reader)

        assert [plan.doc_id for plan in inventory.documents] == ["second", "first"]

    def test_removed_record_does_not_hide_later_current_record(self) -> None:
        manifest = _manifest(
            {
                "removed": _record(status="removed"),
                "current": _record(),
            },
        )

        inventory = build_inventory(manifest, {"lover/abortloven.md": BODY}.get)

        assert [plan.doc_id for plan in inventory.documents] == ["current"]

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

    def test_section_shaped_front_matter_value_is_not_a_provision(self) -> None:
        body = BODY.replace(
            'weird: "# not a heading"',
            'weird: "ignored"\n### § 999. Not body content',
        )
        manifest = _manifest({"doc-1": _record()})

        plan = build_inventory(manifest, lambda _path: body).documents[0]

        assert [provision.pid for provision in plan.provisions] == ["1", "2", "35a"]

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
        forskrift_body = BODY.replace(
            'ref_id: "lov/2024-12-20-96"',
            'ref_id: "forskrift/2024-12-20-96"',
        )
        reader = {
            "lover/abortloven.md": BODY,
            "forskrifter/pantelovforskriften.md": forskrift_body,
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
        forskrift_body = BODY.replace(
            'ref_id: "lov/2024-12-20-96"',
            'ref_id: "forskrift/2024-12-20-96"',
        )
        reader = {
            "lover/abortloven.md": BODY,
            "forskrifter/bergverksordning-for-svalbard.md": forskrift_body,
        }.get
        inventory = build_inventory(manifest, reader)

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
            "abortloven\n",
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
        assert plan.source_dataset == "gjeldende-lover"
        assert plan.ref_id == "lov/2024-12-20-96"
        assert plan.retrieved_at == "2026-07-30T18:17:57.344275+00:00"
        assert plan.date_in_force == "2025-06-01"
        assert plan.last_change_in_force is None
        assert plan.renderer_version == 8

    def test_manifest_title_and_last_change_land_on_the_plan(self) -> None:
        body = BODY.replace(
            "last_updated: null",
            'last_updated: null\nlast_change_in_force: "2026-08-15"',
        )
        manifest = _manifest({"doc-1": _record(title="Manifestens tittel")})

        plan = build_inventory(manifest, lambda _path: body).documents[0]

        assert plan.title == "Manifestens tittel"
        assert plan.last_change_in_force == "2026-08-15"

    def test_document_id_is_preserved_in_provenance_error(self) -> None:
        body = BODY.replace('language: "nb"', 'language: "xx"')
        manifest = _manifest({"specific-doc-id": _record()})

        with pytest.raises(PublishError, match=r"document specific-doc-id carries language"):
            build_inventory(manifest, lambda _path: body)

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

    @pytest.mark.parametrize("field", ["ref_id", "retrieved_at"])
    def test_empty_required_provenance_fails_closed(self, field: str) -> None:
        body = BODY.replace(f'{field}: "', f'{field}: ""  # "', 1)
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match=field):
            build_inventory(manifest, lambda _path: body)

    def test_ref_id_with_trailing_newline_fails_closed(self) -> None:
        body = BODY.replace(
            'ref_id: "lov/2024-12-20-96"',
            'ref_id: "lov/2024-12-20-96\n"',
        )
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="ref_id"):
            build_inventory(manifest, lambda _path: body)

    def test_malformed_ref_id_fails_closed(self) -> None:
        body = BODY.replace(
            'ref_id: "lov/2024-12-20-96"',
            'ref_id: "urn:lex:nonsense"',
        )
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="ref_id"):
            build_inventory(manifest, lambda _path: body)

    @pytest.mark.parametrize(
        ("doc_type", "ref"),
        [("lov", "forskrift/2024-12-20-96"), ("forskrift", "lov/2024-12-20-96")],
    )
    def test_ref_id_type_must_match_publication_route(
        self,
        doc_type: str,
        ref: str,
    ) -> None:
        body = BODY.replace('ref_id: "lov/2024-12-20-96"', f'ref_id: "{ref}"')
        path = "lover/abortloven.md"
        manifest = _manifest({"doc-1": _record(doc_type=doc_type)})
        with pytest.raises(PublishError, match="different type"):
            build_inventory(manifest, {path: body}.get if doc_type == "lov" else (lambda _p: body))

    @pytest.mark.parametrize(
        "ref",
        ["lov/2024-02-30-1", "lov/2024-13-01-1", "forskrift/2024-00-10-2"],
    )
    def test_ref_id_requires_a_real_calendar_date(self, ref: str) -> None:
        body = BODY.replace('ref_id: "lov/2024-12-20-96"', f'ref_id: "{ref}"')
        manifest = _manifest(
            {"doc-1": _record(doc_type=ref.split("/", maxsplit=1)[0])},
        )
        with pytest.raises(PublishError, match="ref_id"):
            build_inventory(manifest, lambda _path: body)

    def test_duplicate_ref_id_same_language_fails_closed(self) -> None:
        manifest = _manifest(
            {
                "doc-1": _record(),
                "doc-2": _record(
                    slug="abortloven-kopi",
                    markdown_path="lover/abortloven-kopi.md",
                ),
            },
        )
        with pytest.raises(PublishError, match="identity error"):
            build_inventory(manifest, lambda _path: BODY)

    def test_language_editions_may_share_a_ref_id(self) -> None:
        # The corpus's real shape: grunnloven exists as bokmål and nynorsk
        # editions under one ref_id.
        nn_body = BODY.replace('language: "nb"', 'language: "nn"')
        manifest = _manifest(
            {
                "doc-1": _record(slug="grunnloven-bokmål-grl"),
                "doc-2": _record(
                    slug="grunnlova-nynorsk-grl",
                    markdown_path="lover/grunnlova.md",
                ),
            },
        )
        reader = {
            "lover/abortloven.md": BODY,
            "lover/grunnlova.md": nn_body,
        }.get
        inventory = build_inventory(manifest, reader)
        assert len(inventory.documents) == 2

    def test_pre1850_ref_id_without_number_passes(self) -> None:
        body = BODY.replace(
            'ref_id: "lov/2024-12-20-96"',
            'ref_id: "lov/1687-04-15"',
        )
        manifest = _manifest({"doc-1": _record()})
        plan = build_inventory(manifest, lambda _path: body).documents[0]
        assert plan.ref_id == "lov/1687-04-15"

    def test_date_without_time_is_not_a_retrieval_timestamp(self) -> None:
        body = BODY.replace(
            'retrieved_at: "2026-07-30T18:17:57.344275+00:00"',
            'retrieved_at: "2026-07-30"',
        )
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="retrieved_at") as caught:
            build_inventory(manifest, lambda _path: body)

        assert str(caught.value.__cause__) == "date without time"

    @pytest.mark.parametrize(
        "value",
        ["2026-07-30T18:17:57.344275", "2026-07-30T20:17:57.344275+02:00"],
    )
    def test_retrieved_at_must_be_an_explicit_utc_instant(self, value: str) -> None:
        body = BODY.replace(
            'retrieved_at: "2026-07-30T18:17:57.344275+00:00"',
            f'retrieved_at: "{value}"',
        )
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="retrieved_at") as caught:
            build_inventory(manifest, lambda _path: body)

        assert str(caught.value.__cause__) == "not an explicit UTC instant"

    def test_z_suffixed_utc_retrieved_at_passes(self) -> None:
        body = BODY.replace(
            'retrieved_at: "2026-07-30T18:17:57.344275+00:00"',
            'retrieved_at: "2026-07-30T18:17:57Z"',
        )
        manifest = _manifest({"doc-1": _record()})
        plan = build_inventory(manifest, lambda _path: body).documents[0]
        assert plan.retrieved_at == "2026-07-30T18:17:57Z"

    @pytest.mark.parametrize(
        "field",
        ["language", "ref_id", "retrieved_at", "date_in_force", "last_change_in_force"],
    )
    def test_malformed_provenance_line_syntax_fails_closed(self, field: str) -> None:
        body = BODY.replace(
            'date_in_force: "2025-06-01"',
            f"{field}: unquoted: junk",
        )
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(
            PublishError,
            match=rf"front matter line for {field} is malformed",
        ):
            build_inventory(manifest, lambda _path: body)

    @pytest.mark.parametrize("field", ["date_in_force", "last_change_in_force"])
    def test_malformed_optional_date_fails_closed(self, field: str) -> None:
        body = BODY.replace(
            'date_in_force: "2025-06-01"',
            f'{field}: "snart"',
        )
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match=field):
            build_inventory(manifest, lambda _path: body)

    def test_unparseable_retrieved_at_fails_closed(self) -> None:
        body = BODY.replace(
            'retrieved_at: "2026-07-30T18:17:57.344275+00:00"',
            'retrieved_at: "yesterday"',
        )
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="retrieved_at"):
            build_inventory(manifest, lambda _path: body)

    def test_identical_repeated_provenance_key_is_tolerated(self) -> None:
        body = BODY.replace(
            'ref_id: "lov/2024-12-20-96"',
            'ref_id: "lov/2024-12-20-96"\nref_id: "lov/2024-12-20-96"',
        )
        manifest = _manifest({"doc-1": _record()})
        plan = build_inventory(manifest, lambda _path: body).documents[0]
        assert plan.ref_id == "lov/2024-12-20-96"

    def test_front_matter_is_never_stolen_from_the_body(self) -> None:
        # A body that does NOT open with --- has no front matter, even when
        # provenance-shaped lines and a --- rule appear later in the text.
        # Treating them as front matter would publish provenance quoted
        # from the document's own prose.
        body = (
            "Innledning.\n"
            'language: "nb"\n'
            'ref_id: "lov/2020-01-01-1"\n'
            'retrieved_at: "2026-01-01T00:00:00+00:00"\n'
            "---\n"
            "# T\n"
        )
        manifest = _manifest({"doc-1": _record()})
        with pytest.raises(PublishError, match="language"):
            build_inventory(manifest, lambda _path: body)

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
            ("35\ta", "35a"),
        ],
    )
    def test_lowercases_and_strips_spaces(self, raw: str, expected: str) -> None:
        assert normalise_pid(raw) == expected


class TestFrontMatterBoundaries:
    def test_body_starts_immediately_after_closing_delimiter(self) -> None:
        source = '---\nlanguage: "nb"\n---\n### § 1. Første\nTekst.'

        assert body_lines(source) == ["### § 1. Første", "Tekst."]

    def test_front_matter_excludes_both_delimiters(self) -> None:
        source = '---\nlanguage: "nb"\nref_id: "lov/2024-12-20-96"\n---\nBody'

        assert _front_matter_lines(source) == [
            'language: "nb"',
            'ref_id: "lov/2024-12-20-96"',
        ]

    def test_later_delimiter_without_opening_delimiter_is_body_content(self) -> None:
        source = "Innledning\n---\n### § 1. Første\nTekst."

        assert body_lines(source) == source.split("\n")

    def test_later_delimiter_without_opening_delimiter_is_not_front_matter(self) -> None:
        source = 'Innledning\n---\nlanguage: "nb"\n---\nBody'

        assert _front_matter_lines(source) == []
