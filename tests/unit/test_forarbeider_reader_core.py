"""The reader's synthetic proof, below the CLI (#738, ADR-0018 PR3B-3).

A manifest entry that the schema accepts can still name what the bundled
synthetic propositions never had; each such entry is refused, never served.
"""

from datetime import timedelta
from pathlib import Path

import pytest

from lovspor.forarbeider import corpus_reader
from lovspor.forarbeider.corpus_models import ManifestDocument
from lovspor.forarbeider.corpus_tree import read_dataset_files
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from tests.unit.forarbeider_reader_support import L_ID, SESSION, released_box
from tests.unit.forarbeider_release_support import Desk, at_a_terminal  # noqa: F401

pytestmark = pytest.mark.usefixtures("at_a_terminal")


@pytest.fixture
def released(tmp_path: Path) -> tuple[ManifestDocument, dict[str, bytes]]:
    box = released_box(Desk(tmp_path / "l"), tmp_path / "sandbox")
    dataset, files = read_dataset_files(box)
    return dataset.manifest.documents[0], files


def _refusal(entry: ManifestDocument, files: dict[str, bytes]) -> tuple[RefusalReason, str]:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        corpus_reader._verified(entry, files)
    return caught.value.reason, caught.value.detail


NOT_SYNTHETIC = (RefusalReason.INPUT_NOT_SYNTHETIC, "document")


def test_a_released_synthetic_entry_is_served_with_its_rendering(
    released: tuple[ManifestDocument, dict[str, bytes]],
) -> None:
    entry, files = released

    member = corpus_reader._verified(entry, files)

    assert member is not None
    assert member.entry == entry
    assert [item.result.chapter_number for item in member.rendering.chapters] == [2, 3]
    assert corpus_reader.lookup_of(entry) == corpus_reader.Lookup(L_ID, SESSION, "prop-l")


def test_a_type_the_bundle_does_not_hold_is_not_synthetic(
    released: tuple[ManifestDocument, dict[str, bytes]],
) -> None:
    entry, files = released
    document = entry.document.model_copy(update={"document_type": "prop-s"})

    assert _refusal(entry.model_copy(update={"document": document}), files) == NOT_SYNTHETIC


def test_a_chapter_the_bundle_does_not_hold_is_not_synthetic(
    released: tuple[ManifestDocument, dict[str, bytes]],
) -> None:
    entry, files = released
    first = entry.chapters[0].model_copy(update={"chapter_number": 1})

    changed = entry.model_copy(update={"chapters": (first, *entry.chapters[1:])})

    assert _refusal(changed, files) == NOT_SYNTHETIC


def test_an_entry_the_rendering_cannot_restate_is_not_synthetic(
    released: tuple[ManifestDocument, dict[str, bytes]],
) -> None:
    entry, files = released
    late = entry.times.owner_decided_at + timedelta(days=1)
    first = entry.chapters[0].model_copy(update={"review_decided_at": late})

    changed = entry.model_copy(update={"chapters": (first, *entry.chapters[1:])})

    assert _refusal(changed, files) == NOT_SYNTHETIC


def test_a_released_entry_whose_file_is_not_its_rendering_is_not_synthetic(
    released: tuple[ManifestDocument, dict[str, bytes]],
) -> None:
    entry, files = released

    assert _refusal(entry, {**files, entry.path: files[entry.path] + b"\n"}) == NOT_SYNTHETIC
    assert _refusal(entry, {}) == NOT_SYNTHETIC
