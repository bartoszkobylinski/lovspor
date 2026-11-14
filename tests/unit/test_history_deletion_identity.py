"""A deletion revision is judged by the blob it deleted (#871, ADR-0026 point 4).

The tek17 shape at lovverk ``2ab982d2``: ``endr-i-byggteknisk-forskrift-tek17``
was held by ``sf-20260612-1085``, which was removed (``2032c09``, -49 lines),
and the slug was later handed to ``sf-20260911-1792``. The deletion commit has
no blob at the path, so the walk read its id as ``None`` and filed the old
document's removal under the new document.
"""

from pathlib import Path

from lovspor.history import _frontmatter_id_at, extract_history
from tests.unit.identity_git_fixtures import commit_all, doc_text, git, init_repo, write_doc

PATH = "forskrifter/endr-i-byggteknisk-forskrift-tek17.md"
SLUG = "endr-i-byggteknisk-forskrift-tek17"
OLD = "sf-20260612-1085"
NEW = "sf-20260911-1792"


def _tek17_repo(tmp_path: Path) -> dict[str, str]:
    repo = init_repo(tmp_path / "lovverk")
    write_doc(repo, PATH, doc_text(OLD, SLUG, "old body\n" * 49))
    shas = {"old_add": commit_all(repo, f"add(forskrift): {SLUG}")}
    (repo / PATH).unlink()
    shas["old_remove"] = commit_all(repo, f"remove(forskrift): {SLUG}")
    write_doc(repo, PATH, doc_text(NEW, SLUG, "new body\n" * 68))
    shas["new_add"] = commit_all(repo, f"add(forskrift): {SLUG}")
    write_doc(repo, PATH, doc_text(NEW, SLUG, "new body v2\n" * 68))
    shas["new_update"] = commit_all(repo, f"update(forskrift): {SLUG}")
    return {key: sha[:7] for key, sha in shas.items()}


def test_previous_holders_deletion_is_not_the_new_holders_event(tmp_path: Path) -> None:
    shas = _tek17_repo(tmp_path)

    record = extract_history(tmp_path / "lovverk", PATH, NEW, SLUG)

    assert [event.commit for event in record.events] == [shas["new_update"], shas["new_add"]]


def test_previous_holders_history_is_cut_at_its_deletion(tmp_path: Path) -> None:
    shas = _tek17_repo(tmp_path)

    record = extract_history(tmp_path / "lovverk", PATH, NEW, SLUG)

    commits = {event.commit for event in record.events}
    assert commits.isdisjoint({shas["old_remove"], shas["old_add"]})


def test_own_deletion_stays_in_the_lineage(tmp_path: Path) -> None:
    shas = _tek17_repo(tmp_path)
    repo = tmp_path / "lovverk"
    (repo / PATH).unlink()
    own_remove = commit_all(repo, f"remove(forskrift): {SLUG}")[:7]

    record = extract_history(repo, PATH, NEW, SLUG)

    assert [event.commit for event in record.events] == [
        own_remove,
        shas["new_update"],
        shas["new_add"],
    ]
    assert record.events[0].type == "removed"


def test_deletion_revision_reads_the_parents_blob(tmp_path: Path) -> None:
    shas = _tek17_repo(tmp_path)
    repo = tmp_path / "lovverk"
    full = git(repo, "rev-parse", shas["old_remove"])

    assert _frontmatter_id_at(repo, full, PATH) == OLD


def test_path_absent_in_revision_and_parent_has_no_id(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_doc(repo, "lover/real.md", "body\n")
    sha = commit_all(repo, "add(lov): real")

    assert _frontmatter_id_at(repo, sha, "lover/absent.md") is None
