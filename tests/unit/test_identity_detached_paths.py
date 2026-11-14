"""Boundary cases for the detached tombstone contract in data-model.md (#912)."""

import pytest

from lovspor.identity.migration import detach_shared_tombstones
from lovspor.identity.registry import SlugOwner, SlugRegistry, record_address
from lovspor.storage.manifest import ManifestRecord
from tests.unit.identity_git_fixtures import record


@pytest.mark.parametrize("directory", ["lover", "forskrifter"])
@pytest.mark.parametrize("owner_status", ["current", "removed"])
def test_only_removed_nonowners_of_shared_paths_are_detached(
    directory: str, owner_status: str
) -> None:
    documents = {
        doc_id: ManifestRecord.model_validate(record(doc_id, "shared", status, directory))
        for doc_id, status in (
            ("owner", owner_status),
            ("old-a", "removed"),
            ("old-b", "removed"),
            ("current-nonowner", "current"),
        )
    }
    before = {doc_id: r.model_dump() for doc_id, r in documents.items()}
    registry = SlugRegistry(
        slugs={directory: {"shared": SlugOwner(owner="owner", assigned_at="2026-01-01")}}
    )

    detached = detach_shared_tombstones(documents, registry)

    for doc_id in ("old-a", "old-b"):
        expected = {**before[doc_id], "markdown_path": f"{directory}/removed/{doc_id}.md"}
        assert detached[doc_id].model_dump() == expected
        assert record_address(detached[doc_id]) == (directory, "shared")
    for doc_id in ("owner", "current-nonowner"):
        assert detached[doc_id].model_dump() == before[doc_id]
    assert {doc_id: r.model_dump() for doc_id, r in documents.items()} == before
    assert detach_shared_tombstones(detached, registry) == detached
    assert detach_shared_tombstones(dict(reversed(list(documents.items()))), registry) == detached


def test_a_removed_nonowner_with_an_unshared_path_is_unchanged() -> None:
    tombstone = ManifestRecord.model_validate(record("old", "shared", "removed"))
    registry = SlugRegistry(
        slugs={"forskrifter": {"shared": SlugOwner(owner="other", assigned_at="2026-01-01")}}
    )
    # The same slug in another dataset is a different path and address.
    other = ManifestRecord.model_validate(record("other", "shared", directory="lover"))
    documents = {"old": tombstone, "other": other}

    assert detach_shared_tombstones(documents, registry) == documents


def test_detaching_a_legacy_slugless_record_preserves_its_address() -> None:
    old = ManifestRecord.model_validate({**record("old", "shared", "removed"), "slug": None})
    owner = ManifestRecord.model_validate(record("owner", "shared"))
    registry = SlugRegistry(
        slugs={"forskrifter": {"shared": SlugOwner(owner="owner", assigned_at="2026-01-01")}}
    )

    detached = detach_shared_tombstones({"old": old, "owner": owner}, registry)

    assert detached["old"].markdown_path == "forskrifter/removed/old.md"
    assert detached["old"].slug == "shared"
    assert record_address(detached["old"]) == record_address(old)


def test_empty_manifest_has_no_paths_to_detach() -> None:
    assert detach_shared_tombstones({}, SlugRegistry()) == {}
