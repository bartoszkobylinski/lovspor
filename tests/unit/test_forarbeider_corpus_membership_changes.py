"""A forward change affects one member of the shared dataset (#738)."""

from datetime import datetime
from pathlib import Path

import pytest

from lovspor.forarbeider.corpus_models import INDEX_PATH
from lovspor.forarbeider.corpus_plan import release_draft, withdraw_draft
from lovspor.forarbeider.corpus_render import render_document
from lovspor.forarbeider.corpus_transaction import JOURNAL, STAGING, apply, locked
from lovspor.forarbeider.corpus_tree import create_sandbox, read_dataset
from tests.unit.test_forarbeider_corpus_golden import dated, partial


@pytest.mark.parametrize("action", ["replace", "withdraw"])
def test_changing_one_member_preserves_the_other(tmp_path: Path, action: str) -> None:
    """Release replaces one member; withdrawal keeps its receipt and removes its file."""
    box = create_sandbox(tmp_path / "box", ())
    first, second = partial(tmp_path), dated(tmp_path)
    with locked(box):
        for value in (first, second):
            apply(box, release_draft(read_dataset(box), value))

        before = read_dataset(box)
        target, sibling = before.manifest.documents
        sibling_bytes = (box / sibling.path).read_bytes()
        baseline = (box / "README.md").read_bytes()
        at = datetime.fromisoformat("2100-04-12T12:00:00Z")
        if action == "withdraw":
            draft = withdraw_draft(before, target.document.id, at)
            assert draft.removes == (target.path,)
        else:
            replacement = first._replace(chapters=first.chapters[:1])
            draft = release_draft(before, replacement)
            assert draft.removes == ()
        assert sibling.path not in draft.writes
        assert sibling.path not in draft.removes
        apply(box, draft)

    after = read_dataset(box)
    changed, unchanged = after.manifest.documents
    assert unchanged == sibling
    assert (box / sibling.path).read_bytes() == sibling_bytes
    assert (box / "README.md").read_bytes() == baseline
    index = (box / INDEX_PATH).read_text(encoding="utf-8")
    assert sibling.path.removeprefix("forarbeider/") in index
    if action == "withdraw":
        assert changed.release_status == "withdrawn"
        assert changed.times.withdrawn_at == at
        assert changed.chapters == target.chapters
        assert changed.markdown_sha256 is None
        assert not (box / target.path).exists()
        assert target.path.removeprefix("forarbeider/") not in index
    else:
        assert changed.release_status == "released"
        assert [chapter.chapter_number for chapter in changed.chapters] == [2]
        assert (box / target.path).read_bytes() == render_document(replacement).encode("utf-8")
        assert changed.markdown_sha256 != target.markdown_sha256
    assert not (box / JOURNAL).exists()
    assert not (box / STAGING).exists()
