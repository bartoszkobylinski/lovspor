"""The slug ownership registry (#871, ADR-0023 draft point 2, guard 5b, U1)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.errors import CorpusStateError, ParseError, SlugOwnershipError
from lovspor.identity.registry import (
    DIRECTORY_OF_DATASET,
    REGISTRY_FILENAME,
    SlugOwner,
    SlugRegistry,
    ensure_append_only,
    read_registry,
    registered_ownership,
    registry_json,
    replace_registry,
    update_slug_registry,
    with_manifest,
)
from lovspor.rendering.slug import resolve_collisions
from lovspor.storage.manifest import Manifest, ManifestRecord
from lovspor.sync import document_io

AT = datetime(2026, 11, 1, tzinfo=UTC)
OWNER = SlugOwner(owner="sf-20251125-2415", assigned_at="2026-05-01T00:00:00+00:00")
SLUG = "endr-i-høstingsforskriften"


def _record(slug: str, status: str = "current", directory: str = "forskrifter") -> ManifestRecord:
    return ManifestRecord(
        doc_type="forskrift",
        xml_hash="a" * 64,
        markdown_path=f"{directory}/{slug}.md",
        source_dataset="gjeldende-sentrale-forskrifter",
        last_seen=AT,
        status=status,  # type: ignore[arg-type]
        slug=slug,
    )


def _registry() -> SlugRegistry:
    return SlugRegistry().assign("forskrifter", SLUG, OWNER)


def test_assign_records_the_first_holder() -> None:
    assert _registry().owner_of("forskrifter", SLUG) == OWNER.owner


def test_assign_to_the_same_owner_is_a_no_op() -> None:
    registry = _registry()

    again = registry.assign("forskrifter", SLUG, OWNER.model_copy(update={"assigned_at": "x"}))

    assert again == registry


def test_an_owner_change_is_refused() -> None:
    later = SlugOwner(owner="sf-20261002-1999", assigned_at="2026-10-10T00:00:00+00:00")

    with pytest.raises(SlugOwnershipError, match="sf-20251125-2415") as raised:
        _registry().assign("forskrifter", SLUG, later)

    assert isinstance(raised.value, CorpusStateError)


def test_the_same_slug_in_another_directory_is_a_different_address() -> None:
    other = SlugOwner(owner="nl-1", assigned_at="t")

    registry = _registry().assign("lover", SLUG, other)

    assert registry.owner_of("lover", SLUG) == "nl-1"
    assert registry.owner_of("forskrifter", SLUG) == OWNER.owner


def test_with_manifest_gives_a_new_shared_slug_to_the_smallest_id() -> None:
    manifest = Manifest(generated_at=AT, documents={"sf-2": _record("x"), "sf-1": _record("x")})

    with pytest.raises(SlugOwnershipError, match="sf-1"):
        with_manifest(SlugRegistry(), manifest)


def test_with_manifest_stamps_the_manifest_time() -> None:
    manifest = Manifest(generated_at=AT, documents={"sf-1": _record("x", "removed")})

    registry = with_manifest(SlugRegistry(), manifest)

    assert registry.slugs["forskrifter"]["x"] == SlugOwner(owner="sf-1", assigned_at=AT.isoformat())


def test_with_manifest_refuses_a_current_record_on_a_foreign_slug() -> None:
    manifest = Manifest(generated_at=AT, documents={"sf-20261002-1999": _record(SLUG)})

    with pytest.raises(SlugOwnershipError):
        with_manifest(_registry(), manifest)


def test_with_manifest_leaves_a_removed_later_holder_alone() -> None:
    manifest = Manifest(generated_at=AT, documents={"sf-20261002-1999": _record(SLUG, "removed")})

    assert with_manifest(_registry(), manifest) == _registry()


def test_ensure_append_only_refuses_a_dropped_entry() -> None:
    with pytest.raises(SlugOwnershipError, match="None"):
        ensure_append_only(_registry(), SlugRegistry())


def test_ensure_append_only_refuses_a_rewritten_owner() -> None:
    rewritten = SlugRegistry().assign("forskrifter", SLUG, SlugOwner(owner="sf-9", assigned_at="t"))

    with pytest.raises(SlugOwnershipError, match="sf-9"):
        ensure_append_only(_registry(), rewritten)


def test_ensure_append_only_accepts_additions() -> None:
    grown = _registry().assign("forskrifter", "ny", SlugOwner(owner="sf-9", assigned_at="t"))

    ensure_append_only(_registry(), grown)


def test_registry_round_trips_byte_identically(tmp_path: Path) -> None:
    path = replace_registry(_registry(), tmp_path)

    assert path == tmp_path / REGISTRY_FILENAME
    assert read_registry(tmp_path) == _registry()
    assert path.read_text(encoding="utf-8") == registry_json(_registry())
    assert registry_json(_registry()).endswith("}\n")
    assert SLUG in path.read_text(encoding="utf-8")


def test_replace_registry_refuses_a_rewrite_and_leaves_disk_untouched(tmp_path: Path) -> None:
    path = replace_registry(_registry(), tmp_path)
    before = path.read_bytes()

    with pytest.raises(SlugOwnershipError):
        replace_registry(SlugRegistry(), tmp_path)

    assert path.read_bytes() == before


def test_an_absent_registry_reads_as_none(tmp_path: Path) -> None:
    assert read_registry(tmp_path) is None


@pytest.mark.parametrize("text", ["{not json", '{"slugs": {"lover": {"x": {"owner": 1}}}}'])
def test_an_unreadable_registry_is_a_parse_error(tmp_path: Path, text: str) -> None:
    (tmp_path / REGISTRY_FILENAME).write_text(text, encoding="utf-8")

    with pytest.raises(ParseError, match=REGISTRY_FILENAME):
        read_registry(tmp_path)


def test_update_never_bootstraps_a_registry(tmp_path: Path) -> None:
    manifest = Manifest(generated_at=AT, documents={"sf-1": _record("x")})

    assert update_slug_registry(tmp_path, manifest) == []
    assert not (tmp_path / REGISTRY_FILENAME).exists()


def test_update_extends_an_existing_registry(tmp_path: Path) -> None:
    replace_registry(_registry(), tmp_path)
    manifest = Manifest(generated_at=AT, documents={"sf-1": _record("ny")})

    paths = update_slug_registry(tmp_path, manifest)

    assert paths == [tmp_path / REGISTRY_FILENAME]
    data = json.loads(paths[0].read_text(encoding="utf-8"))
    assert data["slugs"]["forskrifter"]["ny"]["owner"] == "sf-1"
    assert data["slugs"]["forskrifter"][SLUG]["owner"] == OWNER.owner


def test_registered_ownership_without_a_registry_is_the_manifest_ownership(tmp_path: Path) -> None:
    owners = {"gjeldende-sentrale-forskrifter": {SLUG: "sf-20261002-1999"}}

    assert registered_ownership(tmp_path, owners) == owners


def test_registered_owner_wins_over_the_manifest(tmp_path: Path) -> None:
    replace_registry(_registry(), tmp_path)
    owners = {"gjeldende-sentrale-forskrifter": {SLUG: "sf-20261002-1999"}}

    merged = registered_ownership(tmp_path, owners)

    assert merged["gjeldende-sentrale-forskrifter"][SLUG] == OWNER.owner
    assert merged["gjeldende-lover"] == {}


def test_a_newer_document_never_gets_a_registered_slug(tmp_path: Path) -> None:
    """U1: the owner is in no manifest at all (a lost record), yet its slug
    stays taken and the newer document is deflected by the collision rule."""
    replace_registry(_registry(), tmp_path)
    owners = registered_ownership(tmp_path, {})["gjeldende-sentrale-forskrifter"]

    resolved = resolve_collisions({"sf-20261002-1999": SLUG}, owners)

    assert resolved["sf-20261002-1999"] == f"{SLUG}-2026"


def test_directory_map_matches_where_documents_are_written(tmp_path: Path) -> None:
    for dataset, directory in DIRECTORY_OF_DATASET.items():
        assert document_io.dataset_dir(tmp_path, dataset) == tmp_path / directory
