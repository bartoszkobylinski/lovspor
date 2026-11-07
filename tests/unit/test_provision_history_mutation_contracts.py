"""Behavioral probes for the remaining provision-history mutations."""

from pathlib import Path

import pytest

from lovspor.publish.provision_history import _blob, _git_text, parse_removed_pids
from tests.unit.test_publish_redirects import _doc, _git, _head, _repo


@pytest.mark.parametrize("prefix", ["", "-### § 99. Ignore\n", "diff --git a/x b/x\n"])
def test_initial_hunk_state_requires_a_header_and_hunk(prefix: str) -> None:
    text = prefix + "--- a/lover/å.md\n-### § 98. Ignore\n@@ -1 +0,0 @@\n-### § 3. Tre\n"
    assert parse_removed_pids(text) == {"lover/å.md": frozenset({"3"})}


def test_git_text_overrides_path_quoting_and_decodes_unicode(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = "lover/æøå.md"
    (repo / path).write_text("Blå § 3\n", encoding="utf-8")
    _git(repo, "config", "core.quotePath", "true")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "Blå", day=1)
    assert _git_text(repo, ["ls-tree", "-r", "--name-only", "HEAD"]) == path + "\n"
    assert _git_text(repo, ["log", "-1", "--format=%s"]) == "Blå\n"


def test_blob_reads_exact_unicode_body_and_absent_versions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = "lover/æøå.md"
    text = _doc("Blå", "lov/2020-01-01-1", ("3",))
    (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "config", "core.quotePath", "true")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "Blå", day=1)
    sha = _head(repo)
    assert _blob(repo, f"{sha}:{path}") == [
        "",
        "# Blå",
        "",
        "### § 3. Tittel",
        "",
        "Tekst.",
        "",
    ]
    assert _blob(repo, f"{sha}:lover/missing.md") == []
    (repo / path).unlink()
    _git(repo, "commit", "-qam", "deleted", day=2)
    assert _blob(repo, f"{_head(repo)}:{path}") == []
