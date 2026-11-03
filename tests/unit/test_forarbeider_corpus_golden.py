"""The exact bytes of renderings, index and manifest (#738, ADR-0018 PR3B-2).

Every label, banner, line break and front matter key a reader relies on is
pinned here byte for byte, on the invented structural propositions only. A
change to the renderer is a change to these files, reviewed as one.
"""

import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

from lovspor.forarbeider.chapter_models import ChapterResult
from lovspor.forarbeider.corpus_models import (
    EMPTY_MANIFEST,
    DocumentTimes,
    ForarbeiderManifest,
    manifest_text,
)
from lovspor.forarbeider.corpus_render import (
    ReleasedChapter,
    Rendering,
    member_of,
    render_document,
    render_index,
)
from tests.unit.forarbeider_corpus_support import metadata_of, times
from tests.unit.forarbeider_release_support import REVIEWED, Desk

GOLDEN = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "corpus"
_DECIDED = datetime.fromisoformat(REVIEWED)


def _fresh(root: Path) -> Path:
    """A new directory for each desk: the bytes rendered never depend on where it is."""
    return Path(tempfile.mkdtemp(dir=root))


def _times() -> DocumentTimes:
    return DocumentTimes.model_validate(times())


def partial(root: Path) -> Rendering:
    """Prop. 901 L, chapters 2 and 3 of the four its page lists."""
    desk = Desk(_fresh(root))
    chapters = tuple(ReleasedChapter(desk.results[n], _DECIDED) for n in (2, 3))
    return Rendering(metadata_of(desk), chapters, _times())


def dated(root: Path) -> Rendering:
    """Prop. 902 LS, chapter 3 alone, no ministry, a stated publication date."""
    desk = Desk(_fresh(root), kind="ls")
    metadata = metadata_of(desk).model_copy(
        update={"ministry": None, "published_at": date(2099, 12, 1)}
    )
    return Rendering(metadata, (ReleasedChapter(desk.results[3], _DECIDED),), _times())


def _first_chapter(result: ChapterResult) -> ChapterResult:
    url = result.source_page_url.replace("?ch=2", "?ch=1")
    return result.model_copy(
        update={
            "chapter_number": 1,
            "id": result.id.replace("-ch2", "-ch1"),
            "source_page_url": url,
        }
    )


def complete(root: Path) -> Rendering:
    """Prop. 901 L as if its page listed two chapters and both were released."""
    desk = Desk(_fresh(root))
    metadata = metadata_of(desk)
    listed = metadata.model_copy(update={"chapters": metadata.chapters[:2]})
    first, second = _first_chapter(desk.results[2]), desk.results[2]
    chapters = (ReleasedChapter(first, _DECIDED), ReleasedChapter(second, _DECIDED))
    return Rendering(listed, chapters, _times())


SCENARIOS = {"partial-l.md": partial, "dated-ls.md": dated, "complete-l.md": complete}


def index_manifest(root: Path) -> ForarbeiderManifest:
    """Prop. 901 L with every listed chapter, Prop. 902 LS as a partial excerpt."""
    members = [member_of(build, render_document(build)) for build in (complete(root), dated(root))]
    documents = sorted((member.model_dump() for member in members), key=lambda item: item["path"])
    return ForarbeiderManifest.model_validate(
        {**EMPTY_MANIFEST.model_dump(), "documents": documents}
    )


def outputs(root: Path) -> dict[str, str]:
    """Every golden file and the bytes the code renders for it now."""
    rendered = {name: render_document(build(root)) for name, build in SCENARIOS.items()}
    manifest = index_manifest(root)
    rendered["INDEX.md"] = render_index(manifest)
    rendered["INDEX-empty.md"] = render_index(EMPTY_MANIFEST)
    rendered["manifest.json"] = manifest_text(manifest)
    rendered["manifest-empty.json"] = manifest_text(EMPTY_MANIFEST)
    return rendered


@pytest.mark.parametrize(
    "name", [*SCENARIOS, "INDEX.md", "INDEX-empty.md", "manifest.json", "manifest-empty.json"]
)
def test_the_bytes_are_exactly_the_reviewed_golden_file(tmp_path: Path, name: str) -> None:
    assert outputs(tmp_path)[name] == (GOLDEN / name).read_text(encoding="utf-8")


def test_every_golden_file_is_still_produced(tmp_path: Path) -> None:
    assert sorted(outputs(tmp_path)) == sorted(path.name for path in GOLDEN.iterdir())
