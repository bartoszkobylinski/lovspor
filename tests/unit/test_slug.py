"""Permanent slug ownership (ADR-0003; RCA 2026-07-30 defect 3).

Covers the prior-ownership contract of ``resolve_collisions``, the
``_prior_slug_ownership`` map the orchestrator feeds it, and the
``evaluate_slug_ownership`` dry-run report. The historical intra-tarball
collision behaviour keeps its coverage in ``test_rendering_slug.py``.
"""

from datetime import UTC, datetime

from lovspor.rendering.slug import evaluate_slug_ownership, resolve_collisions
from lovspor.storage.manifest import Manifest, ManifestRecord
from lovspor.sync.orchestrator import _prior_slug_ownership

_FORSKRIFTER = "gjeldende-sentrale-forskrifter"
_LOVER = "gjeldende-lover"


def _record(
    markdown_path: str,
    *,
    dataset: str = _FORSKRIFTER,
    slug: str | None = None,
    status: str = "current",
) -> ManifestRecord:
    return ManifestRecord(
        doc_type="forskrift" if dataset == _FORSKRIFTER else "lov",
        xml_hash="0" * 64,
        markdown_path=markdown_path,
        source_dataset=dataset,
        last_seen=datetime(2026, 7, 1, tzinfo=UTC),
        status=status,
        slug=slug,
        title="Title",
        eu_basis=[],
    )


def _manifest(documents: dict[str, ManifestRecord]) -> Manifest:
    return Manifest(generated_at=datetime(2026, 7, 1, tzinfo=UTC), documents=documents)


# ---------- resolve_collisions with prior ownership ----------


def test_new_doc_deflected_to_ref_year_when_slug_owned_by_other() -> None:
    resolved = resolve_collisions(
        {"sf-20260710-1545": "forskrift-om-omregningsfaktorer"},
        {"forskrift-om-omregningsfaktorer": "sf-20090520-0534"},
    )
    assert resolved == {"sf-20260710-1545": "forskrift-om-omregningsfaktorer-2026"}


def test_deflection_falls_back_to_full_ref_date_when_year_slug_owned() -> None:
    resolved = resolve_collisions(
        {"sf-20260710-1545": "foo"},
        {"foo": "sf-20090520-0534", "foo-2026": "sf-20260101-0001"},
    )
    assert resolved == {"sf-20260710-1545": "foo-2026-07-10"}


def test_deflection_falls_back_to_doc_id_suffix_when_dates_owned() -> None:
    resolved = resolve_collisions(
        {"sf-20260710-1545": "foo"},
        {
            "foo": "sf-20090520-0534",
            "foo-2026": "sf-20260101-0001",
            "foo-2026-07-10": "sf-20260710-0002",
        },
    )
    assert resolved == {"sf-20260710-1545": "foo-sf-20260710-1545"}


def test_undated_doc_id_uses_doc_id_suffix() -> None:
    resolved = resolve_collisions({"lov-new": "foo"}, {"foo": "lov-old"})
    assert resolved == {"lov-new": "foo-lov-new"}


def test_same_doc_id_keeps_its_owned_slug() -> None:
    """Self-ownership always wins — tombstones and resurrections included."""
    resolved = resolve_collisions(
        {"sf-20090520-0534": "foo"},
        {"foo": "sf-20090520-0534"},
    )
    assert resolved == {"sf-20090520-0534": "foo"}


def test_owner_is_not_shuffled_off_its_slug_by_smaller_sibling() -> None:
    """Ownership trumps the doc_id sort order of the sibling pass."""
    resolved = resolve_collisions(
        {"sf-20200101-0001": "foo", "sf-20260710-1545": "foo"},
        {"foo": "sf-20260710-1545"},
    )
    assert resolved == {
        "sf-20260710-1545": "foo",
        "sf-20200101-0001": "foo-2020",
    }


def test_deflected_doc_keeps_its_own_prior_slug() -> None:
    """A doc blocked from its preferred slug stays where it already is."""
    resolved = resolve_collisions(
        {"sf-20260710-1545": "foo"},
        {"foo": "sf-20090520-0534", "bar": "sf-20260710-1545"},
    )
    assert resolved == {"sf-20260710-1545": "bar"}


def test_generated_sibling_suffix_skips_prior_owned_slug() -> None:
    resolved = resolve_collisions(
        {"lov-a": "foo", "lov-b": "foo"},
        {"foo-2": "lov-elsewhere"},
    )
    assert resolved == {"lov-a": "foo", "lov-b": "foo-3"}


def test_resolution_is_independent_of_input_order() -> None:
    slugs = {
        "sf-20090520-0534": "foo",
        "sf-20200101-0001": "foo",
        "sf-20260710-1545": "foo",
        "sf-20260711-0001": "bar",
    }
    owner = {"foo": "sf-20090520-0534", "bar-2026": "sf-19990101-0001"}
    forward = resolve_collisions(dict(slugs), dict(owner))
    reversed_slugs = dict(reversed(list(slugs.items())))
    reversed_owner = dict(reversed(list(owner.items())))
    assert resolve_collisions(reversed_slugs, reversed_owner) == forward
    # Same inputs -> same slugs, and the owner kept its slug.
    assert forward["sf-20090520-0534"] == "foo"
    assert forward["sf-20200101-0001"] == "foo-2020"
    assert forward["sf-20260710-1545"] == "foo-2026"
    assert forward["sf-20260711-0001"] == "bar"


def test_no_prior_owner_matches_legacy_behaviour() -> None:
    assert resolve_collisions({"b": "foo", "a": "foo"}) == {"a": "foo", "b": "foo-2"}
    assert resolve_collisions({"b": "foo", "a": "foo"}, {}) == {"a": "foo", "b": "foo-2"}


# ---------- _prior_slug_ownership (the map the orchestrator builds) ----------


def test_ownership_map_includes_tombstones() -> None:
    """A tombstoned document never releases its slug."""
    prior = _manifest(
        {
            "sf-20090520-0534": _record(
                "forskrifter/foo.md",
                slug="foo",
                status="removed",
            ),
        },
    )
    assert _prior_slug_ownership(prior) == {_FORSKRIFTER: {"foo": "sf-20090520-0534"}}


def test_ownership_map_groups_by_dataset() -> None:
    prior = _manifest(
        {
            "sf-1": _record("forskrifter/foo.md", slug="foo"),
            "nl-1": _record("lover/foo.md", dataset=_LOVER, slug="foo"),
        },
    )
    assert _prior_slug_ownership(prior) == {
        _FORSKRIFTER: {"foo": "sf-1"},
        _LOVER: {"foo": "nl-1"},
    }


def test_ownership_map_conflicted_manifest_smallest_doc_id_wins() -> None:
    """On an already-damaged manifest the earliest act owns the slug."""
    prior = _manifest(
        {
            "sf-20260710-1545": _record("forskrifter/foo.md", slug="foo"),
            "sf-20090520-0534": _record(
                "forskrifter/foo.md",
                slug="foo",
                status="removed",
            ),
        },
    )
    assert _prior_slug_ownership(prior) == {_FORSKRIFTER: {"foo": "sf-20090520-0534"}}


def test_ownership_map_slugless_record_falls_back_to_path_stem() -> None:
    prior = _manifest({"nl-1": _record("lover/legacy.md", dataset=_LOVER, slug=None)})
    assert _prior_slug_ownership(prior) == {_LOVER: {"legacy": "nl-1"}}


def test_ownership_map_none_manifest_is_empty() -> None:
    assert _prior_slug_ownership(None) == {}


# ---------- evaluate_slug_ownership (dry-run report) ----------


def test_evaluate_no_conflicts_returns_empty() -> None:
    documents = {
        "sf-1": _record("forskrifter/foo.md", slug="foo"),
        "sf-2": _record("forskrifter/bar.md", slug="bar", status="removed"),
    }
    assert evaluate_slug_ownership(documents) == ()


def test_evaluate_reports_only_the_usurper_of_a_shared_path() -> None:
    """The defect-3 corpus shape: only the 2026 record would change path."""
    documents = {
        "sf-20090520-0534": _record(
            "forskrifter/forskrift-om-omregningsfaktorer.md",
            slug="forskrift-om-omregningsfaktorer",
            status="removed",
        ),
        "sf-20260710-1545": _record(
            "forskrifter/forskrift-om-omregningsfaktorer.md",
            slug="forskrift-om-omregningsfaktorer",
        ),
        "sf-20200101-0001": _record("forskrifter/unrelated.md", slug="unrelated"),
        "nl-19990326-014": _record("lover/skatteloven.md", dataset=_LOVER, slug="skatteloven"),
    }
    changes = evaluate_slug_ownership(documents)
    assert len(changes) == 1
    change = changes[0]
    assert change.doc_id == "sf-20260710-1545"
    assert change.owner_doc_id == "sf-20090520-0534"
    assert change.slug == "forskrift-om-omregningsfaktorer"
    assert change.new_slug == "forskrift-om-omregningsfaktorer-2026"
    assert change.markdown_path == "forskrifter/forskrift-om-omregningsfaktorer.md"
    assert change.new_markdown_path == ("forskrifter/forskrift-om-omregningsfaktorer-2026.md")


def test_evaluate_skips_reserved_candidate_paths() -> None:
    """A year candidate already reserved in the manifest is skipped."""
    documents = {
        "sf-20090520-0534": _record("forskrifter/foo.md", slug="foo", status="removed"),
        "sf-20260710-1545": _record("forskrifter/foo.md", slug="foo"),
        "sf-20260101-0001": _record("forskrifter/foo-2026.md", slug="foo-2026"),
    }
    changes = evaluate_slug_ownership(documents)
    assert len(changes) == 1
    assert changes[0].new_slug == "foo-2026-07-10"
    assert changes[0].new_markdown_path == "forskrifter/foo-2026-07-10.md"


def test_evaluate_is_independent_of_input_order() -> None:
    documents = {
        "sf-20090520-0534": _record("forskrifter/foo.md", slug="foo", status="removed"),
        "sf-20260710-1545": _record("forskrifter/foo.md", slug="foo"),
        "sf-20260101-0001": _record("forskrifter/bar.md", slug="bar"),
    }
    forward = evaluate_slug_ownership(documents)
    backward = evaluate_slug_ownership(dict(reversed(list(documents.items()))))
    assert forward == backward
