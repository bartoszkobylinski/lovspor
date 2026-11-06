"""Removed-heading evidence from git history (#747, ``publish.provision_history``)."""

from pathlib import Path

import pytest

from lovspor.publish.provision_history import (
    once_publishable_pids,
    parse_removed_pids,
    removed_pids_by_path,
)
from tests.unit.test_publish_redirects import _doc, _git, _head, _repo

MODIFIED = (
    "diff --git a/lover/a.md b/lover/a.md\n"
    "index 1..2 100644\n"
    "--- a/lover/a.md\n"
    "+++ b/lover/a.md\n"
    "@@ -9,1 +8,0 @@\n"
    "-### § 2. To\n"
    "@@ -12,1 +11,1 @@\n"
    "-### § 35 a. Tre\n"
    "+### § 35 a. Tre endret\n"
)


class TestParseRemovedPids:
    def test_removed_headings_are_attributed_to_the_old_path_in_url_form(self) -> None:
        assert parse_removed_pids(MODIFIED) == {"lover/a.md": frozenset({"2", "35a"})}

    def test_added_lines_and_non_heading_removals_are_no_evidence(self) -> None:
        text = MODIFIED.replace("-### § 2. To", "-Tekst.").replace("-### § 35 a.", "+### § 4.")
        assert parse_removed_pids(text) == {}

    def test_a_removed_body_line_shaped_like_a_header_does_not_switch_paths(self) -> None:
        text = MODIFIED + "--- a/lover/other.md\n-### § 7. Sju\n"
        assert parse_removed_pids(text) == {"lover/a.md": frozenset({"2", "35a", "7"})}

    def test_a_new_file_header_resets_the_hunk_state(self) -> None:
        text = (
            MODIFIED
            + "diff --git a/lover/b.md b/lover/b.md\n"
            + "deleted file mode 100644\n"
            + "--- a/lover/b.md\n"
            + "+++ /dev/null\n"
            + "@@ -1,2 +0,0 @@\n"
            + "-### § 1. En\n"
        )
        assert parse_removed_pids(text)["lover/b.md"] == frozenset({"1"})

    def test_a_unicode_line_separator_inside_text_does_not_split_a_line(self) -> None:
        text = MODIFIED.replace("-### § 2. To", "-Tekst\u2028### § 9. Ni")
        assert parse_removed_pids(text) == {"lover/a.md": frozenset({"35a"})}

    def test_a_header_line_before_any_diff_is_still_read_as_a_path(self) -> None:
        assert parse_removed_pids("--- a/lover/x.md\t\n@@ -1 +0,0 @@\n-### § 3. T\n") == {
            "lover/x.md": frozenset({"3"})
        }


class TestFromARealRepository:
    def test_unmerged_provision_versions_are_not_first_parent_evidence(
        self, tmp_path: Path
    ) -> None:
        """URL contract §4 limits retirement evidence to pinned first-parent history."""
        repo = _repo(tmp_path)
        path = "lover/a.md"
        document = repo / path
        document.write_text(_doc("A", "lov/2020-01-01-1", ("1",)), encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "original", day=1)
        _git(repo, "branch", "side")
        _git(repo, "checkout", "-qb", "published")
        document.write_text(_doc("A", "lov/2020-01-01-1", ("2",)), encoding="utf-8")
        _git(repo, "commit", "-qam", "published replacement", day=2)
        _git(repo, "checkout", "-q", "side")
        document.write_text(_doc("A", "lov/2020-01-01-1", ("99",)), encoding="utf-8")
        _git(repo, "commit", "-qam", "unpublished provision", day=3)
        document.write_text(_doc("A", "lov/2020-01-01-1", ("1",)), encoding="utf-8")
        _git(repo, "commit", "-qam", "withdraw unpublished provision", day=4)
        _git(repo, "checkout", "-q", "published")
        _git(repo, "merge", "--no-ff", "-m", "merge side history", "side", day=5)

        sha = _head(repo)
        assert removed_pids_by_path(repo, sha) == {path: frozenset({"1"})}
        assert once_publishable_pids(repo, sha, path) == frozenset({"1", "2"})

    @pytest.mark.parametrize("folder", ["lover", "forskrifter"])
    def test_history_is_pinned_and_a_deleting_commit_keeps_earlier_servable_pids(
        self, tmp_path: Path, folder: str
    ) -> None:
        """The URL contract uses pinned history, including paths absent at its tip."""
        repo = _repo(tmp_path)
        path = f"{folder}/a.md"
        document = repo / path
        document.write_text(_doc("A", "lov/2020-01-01-1", ("1", "35 a")), encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "original", day=1)
        pinned = _head(repo)
        document.write_text(_doc("A", "lov/2020-01-01-1", ("2",)), encoding="utf-8")
        _git(repo, "commit", "-q", "-am", "replace provisions", day=2)
        document.unlink()
        _git(repo, "commit", "-q", "-am", "delete document", day=3)

        assert removed_pids_by_path(repo, pinned) == {}
        assert once_publishable_pids(repo, pinned, path) == frozenset({"1", "35a"})
        assert removed_pids_by_path(repo, _head(repo)) == {path: frozenset({"1", "35a", "2"})}
        assert once_publishable_pids(repo, _head(repo), path) == frozenset({"1", "35a", "2"})

    def test_a_deletion_and_a_rename_both_count_against_the_old_path(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "lover/a.md").write_text(_doc("A", "lov/2020-01-01-1", ("1", "2")))
        (repo / "lover/b.md").write_text(_doc("B", "lov/2020-01-01-2", ("5",)))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one", day=1)
        _git(repo, "mv", "lover/a.md", "lover/c.md")
        (repo / "lover/b.md").unlink()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "two", day=2)

        removed = removed_pids_by_path(repo, _head(repo))
        assert removed == {"lover/a.md": frozenset({"1", "2"}), "lover/b.md": frozenset({"5"})}

    def test_only_versions_without_a_collision_count_as_once_publishable(
        self, tmp_path: Path
    ) -> None:
        repo = _repo(tmp_path)
        (repo / "lover/a.md").write_text(_doc("A", "lov/2020-01-01-1", ("1", "1", "8")))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "one", day=1)
        (repo / "lover/a.md").write_text(_doc("A", "lov/2020-01-01-1", ("1", "2")))
        _git(repo, "commit", "-q", "-am", "two", day=2)
        (repo / "lover/a.md").write_text(_doc("A", "lov/2020-01-01-1", ("1", "2", "2")))
        _git(repo, "commit", "-q", "-am", "three", day=3)

        assert once_publishable_pids(repo, _head(repo), "lover/a.md") == frozenset({"1", "2"})
