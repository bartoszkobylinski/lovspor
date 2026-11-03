"""The bundled synthetic propositions the promotion simulator accepts (#738, PR3B-2).

Two invented propositions, Prop. 901 L and Prop. 902 LS of a session that has
not happened, built on the structural fixture the parser tests use: no real
government text. The simulator writes only what these pages prove, so a
staged input whose page digest is not one of them is refused as not
synthetic, whatever else it shows.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import ChapterResult, result_text
from lovspor.forarbeider.models import ForarbeidRecord, metadata_text
from lovspor.forarbeider.page import build_record
from lovspor.forarbeider.release_models import digest
from lovspor.forarbeider.release_store import refused_as
from lovspor.forarbeider.staging import (
    checked_directory,
    ensure_private_directory,
    write_observation,
)

_ROOT = Path(__file__).parent / "synthetic"
_BASE = "https://www.regjeringen.no/no/dokumenter/"
RETRIEVED_AT = datetime(2100, 4, 1, 12, tzinfo=UTC)
#: Each bundled page and the URL it stands for, by proposition.
PROPOSITIONS: dict[str, dict[int, str]] = {
    "prop-l": {
        chapter: f"{_BASE}prop.-901-l-20992100/id9000001/?ch={chapter}" for chapter in (2, 3)
    },
    "prop-ls": {
        chapter: f"{_BASE}prop.-902-ls-20992100/id9000003/?ch={chapter}" for chapter in (2, 3)
    },
}


def page_bytes(kind: str, chapter: int) -> bytes:
    return (_ROOT / f"{kind}-ch{chapter}.html").read_bytes()


def synthetic_digests() -> frozenset[str]:
    return frozenset(
        digest(page_bytes(kind, chapter))
        for kind, chapters in PROPOSITIONS.items()
        for chapter in chapters
    )


def is_synthetic(metadata: ForarbeidRecord, results: Sequence[ChapterResult]) -> bool:
    """Whether every page the inputs rest on is a bundled synthetic one."""
    known = synthetic_digests()
    return metadata.source_sha256 in known and all(
        result.source_sha256 in known for result in results
    )


def _staged(kind: str) -> dict[str, str]:
    """A proposition's staged files, by name: its pages, its metadata and one result per page."""
    chapters = PROPOSITIONS[kind]
    files = {f"{kind}-ch{n}.html": page_bytes(kind, n).decode() for n in chapters}
    first = min(chapters)
    record = build_record(page_bytes(kind, first), chapters[first], RETRIEVED_AT)
    files[f"{kind}-metadata.json"] = metadata_text(record)
    for number, url in chapters.items():
        result = build_chapter_result(page_bytes(kind, number), url, RETRIEVED_AT)
        files[f"{kind}-ch{number}.json"] = result_text(result)
    return files


def write_fixtures(raw: Path, forbidden: Sequence[Path]) -> list[str]:
    """Write both propositions' staged files to an owner-only directory outside Git.

    A file already there is left alone when it holds the same bytes.

    Raises:
        ForarbeiderRefusedError: an unsafe directory, or a file there with other bytes.
    """
    with refused_as("fixtures"):
        directory = checked_directory(raw, forbidden)
        ensure_private_directory(directory)
        written = {}
        for kind in PROPOSITIONS:
            written.update(_staged(kind))
        for name, text in written.items():
            write_observation(directory / name, text)
    return sorted(written)
