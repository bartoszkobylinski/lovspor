"""Only the bundled synthetic pages let the simulator write anything (#738, PR3B-2).

``is_synthetic`` gates every write ``simulate`` makes: the metadata and every
chapter result it rests on must each be one of the four bundled pages, or the
plan is refused as ``input_not_synthetic``. The CLI-level tests vary a
chapter's page through ``Desk(extras=...)``, which also changes chapter 2's
page and therefore the metadata built from it, so the metadata-only mismatch
never gets its own case there. These pin that branch directly.
"""

from pathlib import Path

from lovspor.forarbeider.synthetic import is_synthetic, synthetic_digests
from tests.unit.forarbeider_corpus_support import metadata_of
from tests.unit.forarbeider_release_support import Desk

_FORGED = "0" * 64


def test_the_four_bundled_pages_are_distinct_digests() -> None:
    assert len(synthetic_digests()) == 4


def test_a_plain_desks_metadata_and_chapters_are_all_synthetic(tmp_path: Path) -> None:
    desk = Desk(tmp_path)

    assert is_synthetic(metadata_of(desk), [desk.results[2], desk.results[3]])


def test_metadata_off_the_bundled_pages_is_not_synthetic_even_with_synthetic_chapters(
    tmp_path: Path,
) -> None:
    desk = Desk(tmp_path)
    forged_metadata = metadata_of(desk).model_copy(update={"source_sha256": _FORGED})

    assert not is_synthetic(forged_metadata, [desk.results[2], desk.results[3]])


def test_one_chapter_off_the_bundled_pages_is_not_synthetic_even_with_synthetic_metadata(
    tmp_path: Path,
) -> None:
    desk = Desk(tmp_path)
    forged_chapter = desk.results[3].model_copy(update={"source_sha256": _FORGED})

    assert not is_synthetic(metadata_of(desk), [desk.results[2], forged_chapter])


def test_no_chapters_at_all_rests_on_the_metadata_alone(tmp_path: Path) -> None:
    desk = Desk(tmp_path)

    assert is_synthetic(metadata_of(desk), [])
